import os
import time
import subprocess
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

        # Build command
        cmd = ['audible', '-P', profile] + download_cfg.cli_args + ['--asin', asin, '--output-dir', str(download_cfg.output_dir)]
        cmd_str = ' '.join(cmd)
        config.logger.info(f"Executing command: {cmd_str}")

        # Run command and capture output
        result = run_command(cmd)
        
        # Log complete command output
        output = (result.get('output', '') or '') + (result.get('error', '') or '')
        config.logger.info(f"Command output: {output}")

        # Special handling for PDFs
        if download_type == DownloadType.PDF:
            no_pdf_indicators = [
                "no pdf available",
                "no downloadable content found",
                "no pdf found",
                "no companion pdf"
            ]
            if any(indicator in output.lower() for indicator in no_pdf_indicators):
                config.logger.info(f"No PDF available for '{book_title}'")
                library[asin]['pdf_available'] = False
                save_library(library)
                return {'success': False, 'message': 'No PDF available for this book'}
        
        # Check if file already exists message is present
        if "already exists" in output:
            # Extract filename from the output
            output_lines = output.split('\n')
            for line in output_lines:
                if "already exists" in line:
                    # Extract the file path from the message
                    file_path = line.split('File ')[-1].split(' already exists')[0].strip()
                    config.logger.info(f"Found existing file: {file_path}")
                    
                    path = Path(file_path)
                    if path.exists():
                        # Update library with file info
                        library[asin][download_cfg.db_path_field] = str(path)
                        
                        # Update size if configured
                        if download_cfg.db_size_field:
                            library[asin][download_cfg.db_size_field] = path.stat().st_size
                        
                        # Handle any extra fields
                        if download_cfg.extra_fields:
                            for field, value_func in download_cfg.extra_fields.items():
                                library[asin][field] = value_func(path)
                        
                        # For PDFs specifically, mark as available
                        if download_type == DownloadType.PDF:
                            library[asin]['pdf_available'] = True
                        
                        save_library(library)
                        return {'success': True, 'file': str(path), 'message': 'File already existed'}
        
        # Look for new downloads
        for line in output.split('\n'):
            if any(x in line.lower() for x in ['downloading to:', 'saved to:', 'saving to:']):
                file_path = line.split(':')[-1].strip()
                config.logger.info(f"Found new downloaded file: {file_path}")
                
                path = Path(file_path)
                if path.exists():
                    # Update library with file info
                    library[asin][download_cfg.db_path_field] = str(path)
                    
                    # Update size if configured
                    if download_cfg.db_size_field:
                        library[asin][download_cfg.db_size_field] = path.stat().st_size
                    
                    # Handle any extra fields
                    if download_cfg.extra_fields:
                        for field, value_func in download_cfg.extra_fields.items():
                            library[asin][field] = value_func(path)
                    
                    # For PDFs specifically, mark as available
                    if download_type == DownloadType.PDF:
                        library[asin]['pdf_available'] = True
                    
                    save_library(library)
                    return {'success': True, 'file': str(path)}

        # Search for files in the output directory
        output_dir = Path(download_cfg.output_dir)
        expected_patterns = [f"*{pattern}" for pattern in download_cfg.file_patterns]
        
        files_found = []
        for pattern in expected_patterns:
            found = list(output_dir.glob(pattern))
            config.logger.debug(f"Found {len(found)} files matching pattern {pattern}: {found}")
            files_found.extend(found)

        # Filter for recent files
        recent_files = [f for f in files_found if (time.time() - f.stat().st_mtime) < 300]

        if recent_files:
            file_path = recent_files[0]
            config.logger.info(f"Using recently modified file: {file_path}")
            
            # Update library with file info
            library[asin][download_cfg.db_path_field] = str(file_path)
            
            # Update size if configured
            if download_cfg.db_size_field:
                library[asin][download_cfg.db_size_field] = file_path.stat().st_size
            
            # Handle any extra fields
            if download_cfg.extra_fields:
                for field, value_func in download_cfg.extra_fields.items():
                    library[asin][field] = value_func(file_path)
            
            # For PDFs specifically, mark as available
            if download_type == DownloadType.PDF:
                library[asin]['pdf_available'] = True
            
            save_library(library)
            return {'success': True, 'file': str(file_path)}

        # If we get here for a PDF and haven't found anything, mark as unavailable
        if download_type == DownloadType.PDF:
            library[asin]['pdf_available'] = False
            save_library(library)
            
        return {'success': False, 'error': 'Could not find or verify file'}

    except Exception as e:
        config.logger.error(f"{download_type.value.capitalize()} download failed for ASIN {asin}: {e}", exc_info=True)
        # For PDFs, mark as unavailable on error
        if download_type == DownloadType.PDF:
            library[asin]['pdf_available'] = False
            save_library(library)
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

def get_activation_bytes(profile_name):
    """Get activation bytes for a profile from disk or fetch and save them"""
    try:
        activation_file = Path(config.CONFIG_DIR) / f"activation_bytes_{profile_name}"

        # Check if we already have the activation bytes saved
        if activation_file.exists():
            config.logger.debug(f"Loading existing activation bytes for profile {profile_name}")
            with open(activation_file) as f:
                content = f.read().strip()
                # If file contains multiple lines, get the last line which should be the hex code
                activation_bytes = content.split('\n')[-1].strip()
                # Verify it looks like a valid activation bytes string
                if len(activation_bytes) == 8 and all(c in '0123456789abcdefABCDEF' for c in activation_bytes):
                    return activation_bytes
                else:
                    config.logger.warning(f"Invalid activation bytes in file, refetching")

        # Fetch activation bytes from Audible CLI
        config.logger.info(f"Fetching new activation bytes for profile {profile_name}")
        result = run_command(['audible', '-P', profile_name, 'activation-bytes'])

        if result['success']:
            # Parse output to get just the activation bytes
            output_lines = result['output'].strip().split('\n')
            # Get the last non-empty line which should be the hex code
            activation_bytes = next((line.strip() for line in reversed(output_lines) if line.strip()), None)
            
            if activation_bytes and len(activation_bytes) == 8 and all(c in '0123456789abcdefABCDEF' for c in activation_bytes):
                # Save only the hex code
                with open(activation_file, 'w') as f:
                    f.write(activation_bytes)
                config.logger.debug(f"Saved new activation bytes for profile {profile_name}")
                return activation_bytes
            else:
                config.logger.error(f"Invalid activation bytes format: {activation_bytes}")
                return None

        config.logger.error(f"Failed to get activation bytes for profile {profile_name}: {result['error']}")
        return None
    except Exception as e:
        config.logger.error(f"Error managing activation bytes for {profile_name}: {e}")
        return None

def convert_book(asin):
    """Convert a book to M4B format"""
    try:
        library = load_library()
        if asin not in library:
            config.logger.error(f"Book not found in library: {asin}")
            return {'success': False, 'error': 'Book not found'}

        book = library[asin]
        book_title = book.get('amazon_title', 'Unknown Title')

        if not book.get('audible_file'):
            config.logger.error(f"No Audible file found for '{book_title}'")
            return {'success': False, 'error': 'Audible file not found'}

        # Infer format if not explicitly set
        if 'audible_format' not in book:
            if book['audible_file'].endswith('.aax'):
                book['audible_format'] = 'aax'
            elif book['audible_file'].endswith('.aaxc'):
                book['audible_format'] = 'aaxc'
            else:
                error_msg = f"Unsupported file format for '{book_title}'"
                config.logger.error(error_msg)
                return {'success': False, 'error': error_msg}

        # Check if output file already exists
        output_file = Path(config.M4B_DIR) / f"{Path(book['audible_file']).stem}.m4b"
        if output_file.exists():
            # Verify file size
            source_size = Path(book['audible_file']).stat().st_size
            m4b_size = output_file.stat().st_size
            size_ratio = m4b_size / source_size

            if size_ratio < 0.9:  # If M4B is less than 90% of source size
                config.logger.warning(f"M4B file for '{book_title}' appears incomplete (size ratio: {size_ratio:.2%}). Deleting.")
                output_file.unlink()
            else:
                config.logger.info(f"Existing M4B file found for '{book_title}': {output_file}")
                library[asin]['m4b_file'] = str(output_file)
                library[asin]['m4b_size'] = m4b_size
                save_library(library)
                return {'success': True, 'file': str(output_file)}

        conversion_status[asin] = 'converting'
        config.logger.info(f"Starting conversion for '{book_title}'")

        # Handle AAX files with activation bytes
        if book['audible_format'] == 'aax':
            profiles = book.get('profiles', [])
            if not profiles:
                error_msg = f"No profile found for '{book_title}'"
                config.logger.error(error_msg)
                return {'success': False, 'error': error_msg}

            activation_bytes = None
            for profile in profiles:
                activation_bytes = get_activation_bytes(profile)
                if activation_bytes:
                    break

            if not activation_bytes:
                error_msg = f"Could not get activation bytes for '{book_title}'"
                config.logger.error(error_msg)
                return {'success': False, 'error': error_msg}

            config.logger.debug(f"Using activation bytes for conversion: {activation_bytes}")

            # Use list format for command
            result = run_command([
                'ffmpeg', '-y',
                '-activation_bytes', activation_bytes,
                '-i', book['audible_file'],
                '-c:a', 'copy',
                '-c:s', 'copy',
                '-c:v', 'copy',
                str(output_file)
            ])
            
        elif book['audible_format'] == 'aaxc':
            # Handle AAXC files with voucher
            if not book.get('voucher_file'):
                error_msg = f"No voucher file found for AAXC format: '{book_title}'"
                config.logger.error(error_msg)
                return {'success': False, 'error': error_msg}

            with open(book['voucher_file'], 'r') as vf:
                voucher = json.load(vf)

            license_response = voucher.get("content_license", {}).get("license_response", {})
            key = license_response.get("key")
            iv = license_response.get("iv")

            if not key or not iv:
                error_msg = f"Missing key or iv in voucher file for '{book_title}'"
                config.logger.error(error_msg)
                return {'success': False, 'error': error_msg}

            result = run_command([
                'ffmpeg', '-y',
                '-audible_key', key,
                '-audible_iv', iv,
                '-i', book['audible_file'],
                '-c:a', 'copy',
                '-c:v', 'copy',
                '-c:s', 'copy',
                str(output_file)
            ])
        else:
            error_msg = f"Unsupported format: {book['audible_format']}"
            config.logger.error(error_msg)
            return {'success': False, 'error': error_msg}

        if result['success']:
            library[asin]['m4b_file'] = str(output_file)
            library[asin]['m4b_size'] = output_file.stat().st_size
            save_library(library)
            conversion_status[asin] = 'completed'
            return {'success': True, 'file': str(output_file)}
        else:
            conversion_status[asin] = 'failed'
            error_msg = f"Conversion failed: {result['error']}"
            config.logger.error(error_msg)
            return {'success': False, 'error': result['error']}

    except Exception as e:
        conversion_status[asin] = 'failed'
        config.logger.error(f"Conversion failed: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}

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
