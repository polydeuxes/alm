import subprocess
import time
import os
import re
import json
from pathlib import Path
import config
from utils.library import load_library, save_library

conversion_status = {}

def convert_book(asin):
    """Convert a book to M4B format with cover image embedding"""
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

        # Check if this is a multi-part book
        is_multi_part = book.get('is_multi_part', False)
        has_parts = 'parts' in book and len(book['parts']) > 1

        # Create the output filename, removing Part_X from multi-part books
        output_stem = Path(book['audible_file']).stem
        if is_multi_part and "_Part_" in output_stem:
            # Remove the Part_X suffix from the filename
            output_stem = output_stem.split("_Part_")[0]
        output_file = Path(config.M4B_DIR) / f"{output_stem}.m4b"

        # Check if output file already exists
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

        # Check if we have a cover image
        cover_path = book.get('cover_path')
        has_cover = cover_path and Path(cover_path).exists()
        
        if has_cover:
            config.logger.info(f"Found cover image for '{book_title}': {cover_path}")
        else:
            config.logger.info(f"No cover image found for '{book_title}'")

        # Handle multi-part book
        if is_multi_part and has_parts:
            # For multi-part books, we need a different approach
            if book['audible_format'] == 'aax':
                # Get activation bytes as in the original code
                profiles = book.get('profiles', [])
                if not profiles:
                    error_msg = f"No profile found for '{book_title}'"
                    config.logger.error(error_msg)
                    return {'success': False, 'error': error_msg}

                activation_bytes = None
                for profile in profiles:
                    activation_bytes = get_activation_bytes_clean(profile)
                    if activation_bytes:
                        break

                if not activation_bytes:
                    error_msg = f"Could not get activation bytes for '{book_title}'"
                    config.logger.error(error_msg)
                    return {'success': False, 'error': error_msg}
                
                # Alternative approach: Process each AAX file to temporary m4a files while preserving the audio codec
                import os
                
                # Create temporary directory for intermediate files
                temp_dir = config.TMP_DIR
                temp_files = []
                
                config.logger.info(f"Processing {len(book['parts'])} parts for multi-part book '{book_title}'")
                
                # Process each part separately
                for i, part in enumerate(sorted(book['parts'], key=lambda p: os.path.basename(p['file_path']))):
                    part_file = part['file_path']
                    temp_output = os.path.join(temp_dir, f"temp_part_{i}_{asin}.m4a")
                    
                    # Decode each part to m4a while preserving the original audio codec
                    decode_cmd = [
                        'ffmpeg', '-y',
                        '-activation_bytes', activation_bytes,
                        '-i', part_file,
                        '-c:a', 'copy',  # Copy audio stream to preserve quality
                        '-vn',  # No video
                        temp_output
                    ]
                    
                    config.logger.info(f"Decoding part {i+1}/{len(book['parts'])}: {os.path.basename(part_file)}")
                    part_result = run_ffmpeg_conversion(decode_cmd)
                    
                    if not part_result['success']:
                        config.logger.error(f"Failed to decode part {i+1}: {part_result['error']}")
                        # Clean up temporary files
                        for tf in temp_files:
                            try:
                                os.unlink(tf)
                            except:
                                pass
                        return {'success': False, 'error': f"Failed to decode part {i+1}"}
                    
                    temp_files.append(temp_output)
                
                # Create concat file for the decoded parts
                concat_file = os.path.join(temp_dir, f"concat_decoded_{asin}.txt")
                with open(concat_file, 'w') as f:
                    for temp_file in temp_files:
                        f.write(f"file '{temp_file}'\n")
                
                # Concatenate decoded parts into final m4b
                if has_cover:
                    concat_cmd = [
                        'ffmpeg', '-y',
                        '-f', 'concat',
                        '-safe', '0',
                        '-i', concat_file,
                        '-i', cover_path,
                        '-map', '0:a',
                        '-map', '1:v',
                        '-c:a', 'copy',  # Preserve audio codec
                        '-c:v', 'copy',
                        '-id3v2_version', '3',
                        '-metadata:s:v', 'title="Album cover"',
                        '-metadata:s:v', 'comment="Cover (front)"',
                        '-disposition:v', 'attached_pic',
                        str(output_file)
                    ]
                else:
                    concat_cmd = [
                        'ffmpeg', '-y',
                        '-f', 'concat',
                        '-safe', '0',
                        '-i', concat_file,
                        '-c:a', 'copy',  # Preserve audio codec
                        str(output_file)
                    ]
                
                config.logger.info(f"Concatenating {len(temp_files)} decoded parts into final M4B")
                result = run_ffmpeg_conversion(concat_cmd)
                
                # Clean up temporary files
                try:
                    os.unlink(concat_file)
                    for tf in temp_files:
                        os.unlink(tf)
                except Exception as e:
                    config.logger.warning(f"Error cleaning up temp files: {e}")
                
                # Return early as we've already done the conversion
                if result['success']:
                    library[asin]['m4b_file'] = str(output_file)
                    library[asin]['m4b_size'] = output_file.stat().st_size
                    save_library(library)
                    conversion_status[asin] = 'completed'
                    return {'success': True, 'file': str(output_file)}
                else:
                    conversion_status[asin] = 'failed'
                    error_msg = f"Concatenation failed: {result['error']}"
                    config.logger.error(error_msg)
                    return {'success': False, 'error': result['error']}
            
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

                # Similar approach for AAXC - decode parts first, then concatenate
                import os
                
                # Create temporary directory for intermediate files
                temp_dir = config.TMP_DIR
                temp_files = []
                
                config.logger.info(f"Processing {len(book['parts'])} parts for multi-part book '{book_title}'")
                
                # Process each part separately
                for i, part in enumerate(sorted(book['parts'], key=lambda p: os.path.basename(p['file_path']))):
                    part_file = part['file_path']
                    temp_output = os.path.join(temp_dir, f"temp_part_{i}_{asin}.m4a")
                    
                    # Decode each part to m4a while preserving the original audio codec
                    decode_cmd = [
                        'ffmpeg', '-y',
                        '-audible_key', key,
                        '-audible_iv', iv,
                        '-i', part_file,
                        '-c:a', 'copy',  # Copy audio stream to preserve quality
                        '-vn',  # No video
                        temp_output
                    ]
                    
                    config.logger.info(f"Decoding part {i+1}/{len(book['parts'])}: {os.path.basename(part_file)}")
                    part_result = run_ffmpeg_conversion(decode_cmd)
                    
                    if not part_result['success']:
                        config.logger.error(f"Failed to decode part {i+1}: {part_result['error']}")
                        # Clean up temporary files
                        for tf in temp_files:
                            try:
                                os.unlink(tf)
                            except:
                                pass
                        return {'success': False, 'error': f"Failed to decode part {i+1}"}
                    
                    temp_files.append(temp_output)
                
                # Create concat file for the decoded parts
                concat_file = os.path.join(temp_dir, f"concat_decoded_{asin}.txt")
                with open(concat_file, 'w') as f:
                    for temp_file in temp_files:
                        f.write(f"file '{temp_file}'\n")
                
                # Concatenate decoded parts into final m4b
                if has_cover:
                    concat_cmd = [
                        'ffmpeg', '-y',
                        '-f', 'concat',
                        '-safe', '0',
                        '-i', concat_file,
                        '-i', cover_path,
                        '-map', '0:a',
                        '-map', '1:v',
                        '-c:a', 'copy',
                        '-c:v', 'copy',
                        '-id3v2_version', '3',
                        '-metadata:s:v', 'title="Album cover"',
                        '-metadata:s:v', 'comment="Cover (front)"',
                        '-disposition:v', 'attached_pic',
                        str(output_file)
                    ]
                else:
                    concat_cmd = [
                        'ffmpeg', '-y',
                        '-f', 'concat',
                        '-safe', '0',
                        '-i', concat_file,
                        '-c:a', 'copy',
                        str(output_file)
                    ]
                
                config.logger.info(f"Concatenating {len(temp_files)} decoded parts into final M4B")
                result = run_ffmpeg_conversion(concat_cmd)
                
                # Clean up temporary files
                try:
                    os.unlink(concat_file)
                    for tf in temp_files:
                        os.unlink(tf)
                except Exception as e:
                    config.logger.warning(f"Error cleaning up temp files: {e}")
                
                # Return early as we've already done the conversion
                if result['success']:
                    library[asin]['m4b_file'] = str(output_file)
                    library[asin]['m4b_size'] = output_file.stat().st_size
                    save_library(library)
                    conversion_status[asin] = 'completed'
                    return {'success': True, 'file': str(output_file)}
                else:
                    conversion_status[asin] = 'failed'
                    error_msg = f"Concatenation failed: {result['error']}"
                    config.logger.error(error_msg)
                    return {'success': False, 'error': result['error']}
        # Handle single part books
        else:
            # Handle AAX files with activation bytes
            if book['audible_format'] == 'aax':
                profiles = book.get('profiles', [])
                if not profiles:
                    error_msg = f"No profile found for '{book_title}'"
                    config.logger.error(error_msg)
                    return {'success': False, 'error': error_msg}

                activation_bytes = None
                for profile in profiles:
                    activation_bytes = get_activation_bytes_clean(profile)
                    if activation_bytes:
                        break

                if not activation_bytes:
                    error_msg = f"Could not get activation bytes for '{book_title}'"
                    config.logger.error(error_msg)
                    return {'success': False, 'error': error_msg}

                config.logger.debug(f"Using activation bytes for conversion")
                
                # Use ffmpeg with cover image if available
                if has_cover:
                    cmd = [
                        'ffmpeg', '-y',
                        '-activation_bytes', activation_bytes,
                        '-i', book['audible_file'],
                        '-i', cover_path,
                        '-map', '0:a',  # map audio from first input
                        '-map', '1:v',  # map video from second input (the cover)
                        '-c:a', 'copy',
                        '-c:v', 'copy',
                        '-id3v2_version', '3',
                        '-metadata:s:v', 'title="Album cover"',
                        '-metadata:s:v', 'comment="Cover (front)"',
                        '-disposition:v', 'attached_pic',
                        str(output_file)
                    ]
                else:
                    cmd = [
                        'ffmpeg', '-y',
                        '-activation_bytes', activation_bytes,
                        '-i', book['audible_file'],
                        '-c:a', 'copy', 
                        '-c:s', 'copy', 
                        '-c:v', 'copy',
                        str(output_file)
                    ]
                
                result = run_ffmpeg_conversion(cmd)
                
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

                if has_cover:
                    cmd = [
                        'ffmpeg', '-y',
                        '-audible_key', key,
                        '-audible_iv', iv,
                        '-i', book['audible_file'],
                        '-i', cover_path,
                        '-map', '0:a',  # map audio from first input
                        '-map', '1:v',  # map video from second input (the cover)
                        '-c:a', 'copy',
                        '-c:v', 'copy',
                        '-id3v2_version', '3',
                        '-metadata:s:v', 'title="Album cover"',
                        '-metadata:s:v', 'comment="Cover (front)"',
                        '-disposition:v', 'attached_pic',
                        str(output_file)
                    ]
                else:
                    cmd = [
                        'ffmpeg', '-y',
                        '-audible_key', key,
                        '-audible_iv', iv,
                        '-i', book['audible_file'],
                        '-c:a', 'copy', 
                        '-c:s', 'copy', 
                        '-c:v', 'copy',
                        str(output_file)
                    ]
                    
                result = run_ffmpeg_conversion(cmd)
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

def get_activation_bytes_clean(profile_name):
    """Get activation bytes for a profile from disk or fetch and save them - clean implementation"""
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
        
        # Simple clean subprocess call without using common.py
        process = subprocess.Popen(
            ['audible', '-P', profile_name, 'activation-bytes'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        stdout, stderr = process.communicate()
        
        if process.returncode == 0:
            # Parse output to get just the activation bytes
            output_lines = stdout.strip().split('\n')
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

        config.logger.error(f"Failed to get activation bytes for profile {profile_name}: {stderr}")
        return None
    except Exception as e:
        config.logger.error(f"Error managing activation bytes for {profile_name}: {e}")
        return None


def run_ffmpeg_conversion(command):
    """Run ffmpeg conversion with clean logging and error handling"""
    try:
        # Log minimal information about the command
        cmd_name = command[0] if isinstance(command, list) else command.split()[0]
        config.logger.info(f"Starting {cmd_name} conversion process")
        
        # Create process 
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1  # Line buffered
        )
        
        # Track progress
        last_progress = 0
        start_time = time.time()
        
        stderr_output = ""
        # Read stderr in real-time to capture progress
        for line in iter(process.stderr.readline, ''):
            stderr_output += line
            # Only log progress at 10% intervals
            if 'time=' in line and 'bitrate=' in line:
                # For duration-based progress, need to parse duration and current time
                duration_match = re.search(r'Duration: (\d+):(\d+):(\d+)', line)
                time_match = re.search(r'time=(\d+):(\d+):(\d+)', line)
                
                if duration_match and time_match:
                    h1, m1, s1 = map(int, duration_match.groups())
                    h2, m2, s2 = map(int, time_match.groups())
                    
                    total_seconds = h1*3600 + m1*60 + s1
                    current_seconds = h2*3600 + m2*60 + s2
                    
                    if total_seconds > 0:
                        progress = int(current_seconds / total_seconds * 100)
                        
                        # Only log at 10% intervals or at 100%
                        if progress >= last_progress + 10 or progress == 100:
                            config.logger.info(f"Conversion progress: {progress}%")
                            last_progress = progress // 10 * 10  # Round down to nearest 10
            
            # Log errors
            elif 'error' in line.lower() or 'failed' in line.lower():
                config.logger.error(f"FFmpeg error: {line.strip()}")
        
        # Get return code
        process.wait()
        
        elapsed = time.time() - start_time
        config.logger.info(f"Conversion completed in {elapsed:.1f} seconds with status: {process.returncode}")
        
        if process.returncode == 0:
            return {'success': True}
        else:
            stderr = process.stderr.read()
            config.logger.error(f"FFmpeg failed with error: {stderr_output}")
            return {'success': False, 'error': stderr}
            
    except Exception as e:
        config.logger.error(f"Conversion process error: {e}")
        return {'success': False, 'error': str(e)}
