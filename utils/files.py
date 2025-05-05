import os
import time
import subprocess
import re
from enum import Enum
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any
import json
import config
from utils.library import load_library, save_library, verify_files
from utils.common import run_command

# Types and Configuration
class DownloadType(Enum):
    BOOK = 'book'
    COVER = 'cover'
    PDF = 'pdf'

@dataclass
class DownloadConfig:
    cli_args: list[str]
    output_dir: str
    db_path_field: str
    db_size_field: Optional[str]
    file_patterns: list[str]
    timeout: Optional[str] = None
    extra_fields: Optional[Dict[str, str]] = None

# Shared state for UI status updates
download_status = {}
conversion_status = {}

def get_download_config(download_type: DownloadType) -> DownloadConfig:
    """Get configuration for different download types"""
    configs = {
        DownloadType.BOOK: DownloadConfig(
            cli_args=['download', '--aax-fallback', '--no-confirm', '--timeout', '0'],
            output_dir=config.AAX_DIR,
            db_path_field='audible_file',
            db_size_field='audible_size',
            file_patterns=['.aax', '.aaxc'],
            timeout='0',
            extra_fields={'audible_format': lambda path: path.suffix[1:]}
        ),
        DownloadType.COVER: DownloadConfig(
            cli_args=['download', '--cover'],
            output_dir=config.IMAGES_DIR,
            db_path_field='cover_path',
            db_size_field=None,
            file_patterns=['.jpg']
        ),
        DownloadType.PDF: DownloadConfig(
            cli_args=['download', '--pdf'],
            output_dir=config.PDF_DIR,
            db_path_field='pdf_file',
            db_size_field='pdf_size',  # Added size field for PDFs
            file_patterns=['.pdf'],
            extra_fields={
                'pdf_available': lambda _: True,
                'pdf_size': lambda path: path.stat().st_size  # Add function to get PDF size
            }
        )
    }
    return configs[download_type]

def download_content(profile: str, asin: str, download_type: DownloadType, options: Dict[str, Any] = None) -> Dict[str, Any]:
    try:
        library = load_library()
        if asin not in library:
            config.logger.error(f"ASIN '{asin}' not found in library.")
            return {'success': False, 'error': 'Book not found'}

        book_title = library[asin].get('amazon_title', 'Unknown')
        config.logger.info(f"Starting {download_type.value} download for '{book_title}' (ASIN: {asin})")
        download_cfg = get_download_config(download_type)

        cmd_base = ['audible']
        if download_type == DownloadType.BOOK:
            cmd_base.extend(['-v', 'DEBUG'])
        cmd = cmd_base + ['-P', profile] + download_cfg.cli_args + ['--asin', asin, '--output-dir', str(download_cfg.output_dir)]

        # Stream the output for real-time progress
        import subprocess
        
        # Use PIPE for stdout and stderr to capture output
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            universal_newlines=True
        )
        
        output_lines = []
        error_lines = []
        downloaded_file = None
        is_locked = False
        last_progress_log = 0
        
        # Flag to indicate if download has started
        download_started = False
        
        # Flag to indicate a multi-part book
        multi_part_download = False
        
        # Function to process a line of output
        def process_line(line, is_stderr=False):
            nonlocal downloaded_file, is_locked, last_progress_log, download_started, multi_part_download
            
            line = line.strip()
            if is_stderr:
                error_lines.append(line)
                
                # Log progress updates at a reasonable rate
                if '%' in line and '|' in line:
                    # Extract progress percentage
                    progress_match = re.search(r'(\d+)%', line)
                    if progress_match:
                        progress = int(progress_match.group(1))
                        download_status[asin] = {'progress': progress}
                
                    current_time = time.time()
                    if (current_time - last_progress_log > 1.0 and  # Log at most once per second
                        len(line.strip()) > 10):                    # Ensure it's not an empty line
                        config.logger.info(f"Progress: {line}")
                        last_progress_log = current_time
                        download_started = True
                elif "is not downloadable" in line:
                    is_locked = True
                    config.logger.warning(f"Book '{book_title}' is locked or not downloadable")
                elif len(line.strip()) > 0:  # Only log non-empty error lines
                    # Skip logging empty error lines
                    config.logger.info(f"Error: {line}")
            else:
                output_lines.append(line)
                config.logger.info(f"Output: {line}")
                
                # Check for download completion or existing file
                if "downloaded in" in line:
                    match = re.search(r'File (.*?) downloaded in', line)
                    if match:
                        downloaded_file = match.group(1).strip()
                elif "already exists" in line:
                    match = re.search(r'File (.*?) already exists', line)
                    if match:
                        downloaded_file = match.group(1).strip()
                elif "No PDF found for" in line and download_type == DownloadType.PDF:
                    # Mark PDF as not available
                    library[asin]['pdf_available'] = False
                    save_library(library)
                elif "be downloaded in parts" in line:
                    # This indicates a larger download that will be processed in chunks
                    download_started = True
                    multi_part_download = True
                    config.logger.info(f"Large file detected, will be downloaded in parts: {asin}")
        
        # Read from stdout and stderr simultaneously using threads and queues
        import threading
        import queue
        
        stdout_queue = queue.Queue()
        stderr_queue = queue.Queue()
        
        def enqueue_output(out, queue):
            for line in iter(out.readline, ''):
                queue.put(line)
            out.close()
        
        # Start threads to read stdout and stderr
        stdout_thread = threading.Thread(target=enqueue_output, args=(process.stdout, stdout_queue))
        stderr_thread = threading.Thread(target=enqueue_output, args=(process.stderr, stderr_queue))
        stdout_thread.daemon = True
        stderr_thread.daemon = True
        stdout_thread.start()
        stderr_thread.start()
        
        # Process output from both streams while checking if process is still running
        timeout_counter = 0
        max_timeout = 3600  # Maximum wait time in seconds (1 hour)
        
        while process.poll() is None or not stdout_queue.empty() or not stderr_queue.empty():
            # Check if we've been waiting too long with no progress
            if download_started and timeout_counter > max_timeout:
                config.logger.error(f"Download timeout exceeded for {asin}")
                process.terminate()
                return {'success': False, 'error': 'Download timeout exceeded'}
            
            # Process available stdout
            try:
                while True:
                    line = stdout_queue.get_nowait()
                    process_line(line, is_stderr=False)
                    timeout_counter = 0  # Reset timeout counter on activity
            except queue.Empty:
                pass
            
            # Process available stderr
            try:
                while True:
                    line = stderr_queue.get_nowait()
                    process_line(line, is_stderr=True)
                    timeout_counter = 0  # Reset timeout counter on activity
            except queue.Empty:
                pass
            
            # Wait a little before checking again
            time.sleep(0.1)
            
            # Increment timeout counter if download has started
            if download_started:
                timeout_counter += 0.1
        
        # Process any remaining output after process completion
        returncode = process.poll()
        
        # Handle locked books
        if is_locked:
            library[asin]['locked'] = True
            save_library(library)
            return {
                'success': False, 
                'error': 'Book is locked or not available for download',
                'status': 'locked',
                'asin': asin
            }
        
        # Check for multi-part books - fixed variable reference
        if download_type == DownloadType.BOOK and (multi_part_download or (downloaded_file and "_Part_" in str(downloaded_file))):
            # Look for all parts of this book - using a simpler pattern
            safe_title = book_title.replace(' ', '_')
            
            # List all files in the directory and filter for the pattern we want
            aax_dir = Path(download_cfg.output_dir)
            part_files = []
            
            # Find files matching the pattern manually
            for file_path in aax_dir.iterdir():
                if safe_title in file_path.name and "_Part_" in file_path.name and file_path.suffix in ('.aax', '.aaxc'):
                    part_files.append(file_path)
            
            # Sort the files
            part_files.sort(key=lambda x: x.name)
            
            if len(part_files) > 1:
                config.logger.info(f"Found {len(part_files)} parts for book '{book_title}'")
                
                # Initialize parts array
                library[asin]['parts'] = []
                total_size = 0
                
                # Process each part file
                for part_file in part_files:
                    part_size = part_file.stat().st_size
                    total_size += part_size
                    
                    part_info = {
                        'file_path': str(part_file),
                        'file_size': part_size,
                        'format': part_file.suffix[1:]  # Remove the leading dot
                    }
                    
                    library[asin]['parts'].append(part_info)
                
                # Mark as multi-part in the library
                library[asin]['is_multi_part'] = True
                library[asin]['parts_count'] = len(part_files)
                
                # Set the first part as the main file for backward compatibility
                # but use the total size of all parts
                library[asin]['audible_file'] = str(part_files[0])
                library[asin]['audible_size'] = total_size
                library[asin]['audible_format'] = part_files[0].suffix[1:]
                
                # Save the updated library
                save_library(library)
                
                return {
                    'success': True,
                    'is_multi_part': True,
                    'parts_count': len(part_files),
                    'total_size': total_size,
                    'file': str(part_files[0])  # Return the first part for compatibility
                }
        
        # Process the file if we found one
        if downloaded_file:
            path = Path(downloaded_file)
            if path.exists():
                if path.suffix == '.aaxc':
                    size = path.stat().st_size
                    # This is the AAXC audio file
                    library[asin]['audible_file'] = str(path)
                    library[asin]['audible_size'] = size
                    library[asin]['audible_format'] = 'aaxc'

                    # Check if the corresponding voucher file exists
                    voucher_path = path.with_suffix('.voucher')
                    if voucher_path.exists():
                        library[asin]['voucher_file'] = str(voucher_path)
                        config.logger.info(f"Auto-linked voucher file: {voucher_path}")
                    save_library(library)
                    return {'success': True, 'file': str(path)}
                elif path.suffix == '.voucher':
                    # This is a voucher file
                    library[asin]['voucher_file'] = str(path)
                    config.logger.info(f"Recorded voucher file: {path}")
            
                    # Check if the corresponding AAXC file exists
                    aaxc_path = path.with_suffix('.aaxc')
                    if aaxc_path.exists():
                        library[asin]['audible_file'] = str(aaxc_path)
                        library[asin]['audible_size'] = aaxc_path.stat().st_size
                        library[asin]['audible_format'] = 'aaxc'
                        config.logger.info(f"Auto-linked AAXC file: {aaxc_path}")
                    save_library(library)
                    return {'success': True, 'file': str(path)}
                elif path.suffix == '.aax':
                    library[asin]['audible_file'] = str(path)
                    library[asin]['audible_size'] = path.stat().st_size
                    library[asin]['audible_format'] = 'aax'
                    save_library(library)
                    return {'success': True, 'file': str(path)}
                elif path.suffix == '.pdf':
                    library[asin]['pdf_file'] = str(path)
                    library[asin]['pdf_size'] = path.stat().st_size
                    library[asin]['pdf_available'] = True
                    save_library(library)
                    return {'success': True, 'file': str(path)}
                elif path.suffix == '.jpg':
                    library[asin]['cover_path'] = str(path)
                    save_library(library)
                    return {'success': True, 'file': str(path)}

        # Check for large files downloaded in parts
        # AAX files downloaded in parts might be named differently
        if download_started:
            # Look for any AAX files in the directory that match this book's pattern
            possible_files = list(Path(config.AAX_DIR).glob(f"*{asin}*.aax"))
            if not possible_files:
                possible_files = list(Path(config.AAX_DIR).glob(f"*{book_title.replace(' ', '_')}*.aax"))
            
            if possible_files:
                largest_file = max(possible_files, key=lambda p: p.stat().st_size)
                # Check if file size is reasonable (not just a stub)
                if largest_file.stat().st_size > 1024 * 1024:  # Larger than 1MB
                    library[asin]['audible_file'] = str(largest_file)
                    library[asin]['audible_size'] = largest_file.stat().st_size
                    library[asin]['audible_format'] = 'aax'
                    save_library(library)
                    return {'success': True, 'file': str(largest_file)}

        # Check specifically for PDF not available
        if download_type == DownloadType.PDF and any("No PDF found for" in line for line in output_lines):
            config.logger.info(f"No PDF available for book: {book_title}")
            library[asin]['pdf_available'] = False
            save_library(library)
            return {
                'success': False,
                'message': 'No PDF available for this book',
                'pdf_available': False
            }

        # If we reach here with no file and no lock, check if "No new files downloaded" was in output
        if any("No new files downloaded" in line for line in output_lines):
            # This might happen if we tried to download a book that's not available
            # Mark it as locked since we couldn't download it
            library[asin]['locked'] = True
            save_library(library)
            return {'success': False, 'error': 'No downloadable file found - book may be locked'}

        # If no other condition was met, mark as locked by default for failed downloads
        library[asin]['locked'] = True
        save_library(library)
        return {'success': False, 'error': 'No downloadable file found - book may not be in your library'}

    except Exception as e:
        config.logger.error(f"Download failed for ASIN {asin}: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}

def get_file_status(asin):
    """Get status of a book's download and conversion"""
    library = load_library()
    status = {
        'download': 'not_started',
        'conversion': 'not_started',
        'files': {}
    }

    if asin in library:
        book = library[asin]

        # Check download status
        if book.get('audible_file'):
            status['download'] = 'completed'
            status['files']['audible'] = {
                'path': book['audible_file'],
                'format': book['audible_format']
            }
        elif asin in download_status:
            status['download'] = download_status[asin]

        # Check conversion status
        if book.get('m4b_file'):
            status['conversion'] = 'completed'
            status['files']['m4b'] = book['m4b_file']
        elif asin in conversion_status:
            status['conversion'] = conversion_status[asin]

        # Add cover info if available
        if book.get('cover_path'):
            status['files']['cover'] = book['cover_path']

    return status

def delete_book(asin):
    try:
        library = load_library()
        if asin not in library:
            return {'success': False, 'error': 'Book not found'}

        book = library[asin]
        book_title = book.get('amazon_title', 'Unknown Title')

        # Delete Audible file
        audible_file = book.get('audible_file')
        if audible_file:
            try:
                os.remove(audible_file)
                config.logger.info(f"Deleted Audible file for '{book_title}': {audible_file}")
            except Exception as e:
                config.logger.error(f"Error deleting Audible file: {e}")

        # Delete M4B file
        m4b_file = book.get('m4b_file')
        if m4b_file:
            try:
                os.remove(m4b_file)
                config.logger.info(f"Deleted M4B file for '{book_title}': {m4b_file}")
            except Exception as e:
                config.logger.error(f"Error deleting M4B file: {e}")

        # Remove book entry from library
        del library[asin]
        save_library(library)

        return {'success': True, 'message': f"Deleted '{book_title}'"}
    except Exception as e:
        config.logger.error(f"Error deleting book: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}
