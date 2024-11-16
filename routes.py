from flask import render_template, request, jsonify, send_file
from app import app
import config
from utils.auth import get_profiles, handle_quickstart, handle_additional_profile
from utils.library import load_library, save_library, verify_files, update_book_database
from utils.files import (
    download_content, convert_book, get_file_status,
    download_status, conversion_status, DownloadType
)
import os
import subprocess
from utils.common import run_command
from werkzeug.utils import secure_filename
from pathlib import Path

# Global state for auth process
process = None

@app.route('/')
def index():
    """Main page view"""
    config_file = Path(config.CONFIG_DIR) / 'config.toml'
    library = load_library()
    profiles = get_profiles()

    return render_template('index.html',
                         initialized=config_file.exists(),
                         library=library,
                         profiles=profiles)

def get_process_file():
    """Get path to auth process file"""
    return os.path.join(config.CONFIG_DIR, '.auth_process')

@app.route('/init', methods=['POST'])
def init_profile():
    """Initialize a new profile"""
    global process
    try:
        profile = request.form.get('profile_name')
        country = request.form.get('country_code', 'us')
        pre_amazon = request.form.get('pre_amazon', '')

        if not profile:
            return jsonify({'success': False, 'error': 'Profile name required'})

        result = handle_quickstart(profile, country, pre_amazon)
        
        if result['success'] and result['process']:
            process = result['process']
            open(get_process_file(), 'w').close()
            
            return jsonify({
                'success': True,
                'auth_url': result['auth_url'],
                'instructions': result['instructions']
            })
            
        return jsonify(result)

    except Exception as e:
        config.logger.error(f"Profile initialization failed: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)})

@app.route('/complete-auth', methods=['POST'])
def complete_auth():
    """Complete the authentication process"""
    global process
    try:
        url = request.form.get('activation_url', '')
        
        if not os.path.exists(get_process_file()):
            return jsonify({
                'success': False,
                'error': 'No authentication process in progress'
            })

        if not url:
            return jsonify({'success': False, 'error': 'URL required'})

        if not process:
            return jsonify({
                'success': False,
                'error': 'Authentication process lost - please try again'
            })

        # Send URL to existing process
        config.logger.info("Sending URL to process")
        process.stdin.write(f"{url}\n")
        process.stdin.flush()

        # Wait for completion
        output = ""
        error_output = ""
        
        # Read any remaining output
        for line in process.stdout:
            output += line
            config.logger.debug(f"Auth output: {line.strip()}")
            
        # Read any error output
        for line in process.stderr:
            error_output += line
            config.logger.error(f"Auth error: {line.strip()}")

        result = process.wait()
        
        # Clean up
        process = None
        try:
            os.remove(get_process_file())
        except:
            pass

        if result == 0:
            return jsonify({
                'success': True,
                'message': 'Authentication completed successfully'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Authentication failed',
                'output': output,
                'error_output': error_output
            })

    except Exception as e:
        process = None
        config.logger.error(f"Auth completion failed: {e}", exc_info=True)
        try:
            os.remove(get_process_file())
        except:
            pass
        return jsonify({'success': False, 'error': str(e)})

@app.route('/add-profile', methods=['POST'])
def add_profile():
    """Add an additional profile"""
    global process
    try:
        log_auth_state("Starting add_profile")
        
        data = request.get_json() or request.form
        profile = data.get('profile')
        country = data.get('country', 'us')

        config.logger.info(f"Add profile request - Profile: {profile}, Country: {country}")

        if not profile:
            return jsonify({'success': False, 'error': 'Profile name required'})

        result = handle_additional_profile(profile, country)
        config.logger.info(f"Additional profile result: {result}")
        
        if result['success']:
            process = result['process']
            log_auth_state("Additional profile setup successful")
            return jsonify(result)
        return jsonify(result)

    except Exception as e:
        config.logger.error(f"Additional profile setup failed: {e}", exc_info=True)
        log_auth_state("Add profile failed with exception")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/library')
def view_library():
    """View library and manage books"""
    library = load_library()
    profiles = get_profiles()

    # Initialize stats
    total_audible_files = 0
    total_m4b_files = 0
    total_pdf_files = 0
    total_audible_size = 0
    total_m4b_size = 0
    total_pdf_size = 0
    total_locked = 0

    # Initialize profile stats
    profile_stats = {profile['name']: {
        'audible_total': 0,
        'audible_size': 0,
        'm4b_total': 0,
        'm4b_size': 0,
        'pdf_total': 0,
        'pdf_size': 0,
        'locked': 0
    } for profile in profiles}

    # Initialize collections
    libraries = {profile['name']: [] for profile in profiles}
    unassigned_books = []

    # Process all books
    for asin, book in library.items():
        # Count locked books
        if book.get('locked', False):
            total_locked += 1

        # Calculate sizes and counts
        if book.get('audible_file'):
            total_audible_files += 1
            total_audible_size += book.get('audible_size', 0) or 0

        if book.get('m4b_file'):
            total_m4b_files += 1
            total_m4b_size += book.get('m4b_size', 0) or 0

        if book.get('pdf_file'):
            total_pdf_files += 1
            total_pdf_size += book.get('pdf_size', 0) or 0

        # Sort into collections
        book_profiles = book.get('profiles', [])
        if book_profiles:
            for profile_name in book_profiles:
                if profile_name in libraries:
                    libraries[profile_name].append(book)
                    # Add to profile stats
                    if book.get('audible_file'):
                        profile_stats[profile_name]['audible_total'] += 1
                        profile_stats[profile_name]['audible_size'] += book.get('audible_size', 0) or 0
                    if book.get('m4b_file'):
                        profile_stats[profile_name]['m4b_total'] += 1
                        profile_stats[profile_name]['m4b_size'] += book.get('m4b_size', 0) or 0
                    if book.get('pdf_file'):
                        profile_stats[profile_name]['pdf_total'] += 1
                        profile_stats[profile_name]['pdf_size'] += book.get('pdf_size', 0) or 0
                    if book.get('locked', False):
                        profile_stats[profile_name]['locked'] += 1
        else:
            unassigned_books.append(book)

    # Calculate unassigned stats
    unassigned_stats = {
        'audible_size': sum(book.get('audible_size', 0) or 0 for book in unassigned_books),
        'm4b_size': sum(book.get('m4b_size', 0) or 0 for book in unassigned_books),
        'pdf_size': sum(book.get('pdf_size', 0) or 0 for book in unassigned_books)
    }

    return render_template(
        'library.html',
        profiles=profiles,
        libraries=libraries,
        download_status=download_status,
        conversion_status=conversion_status,
        total_audible_files=total_audible_files,
        total_m4b_files=total_m4b_files,
        total_pdf_files=total_pdf_files,
        total_audible_size=total_audible_size,
        total_m4b_size=total_m4b_size,
        total_pdf_size=total_pdf_size,
        total_books=len(library),
        total_locked=total_locked,
        unassigned_books=unassigned_books,
        unassigned_count=len(unassigned_books),
        profile_stats=profile_stats,
        unassigned_stats=unassigned_stats
    )

@app.route('/update-library', methods=['POST'])
def update_library():
    """Update library from Audible for a specific profile"""
    try:
        data = request.get_json() if request.is_json else request.form
        profile_name = data.get('profile')

        if not profile_name:
            return jsonify({
                'success': False,
                'error': 'No profile specified'
            })

        config.logger.info(f"Updating library for profile: {profile_name}")

        # Get current library
        library = load_library()

        # Get books from Audible
        books = update_book_database(profile_name)
        if not books:
            return jsonify({
                'success': False,
                'error': 'No books found or export failed'
            })

        # Update each book
        changes_made = False
        for book in books:
            asin = book['asin']
            if asin in library:
                # Update existing book
                existing_profiles = library[asin].get('profiles', [])
                if profile_name not in existing_profiles:
                    existing_profiles.append(profile_name)
                    changes_made = True

                # Update metadata while preserving file info
                for key, value in book.items():
                    if key not in ['audible_file', 'audible_size', 'audible_format',
                                 'm4b_file', 'm4b_size', 'cover_path']:
                        if library[asin].get(key) != value:
                            library[asin][key] = value
                            changes_made = True

                library[asin]['profiles'] = existing_profiles
            else:
                # Add new book
                book['profiles'] = [profile_name]
                library[asin] = book
                changes_made = True

        if changes_made:
            if save_library(library):
                return jsonify({
                    'success': True,
                    'message': f'Library updated for profile {profile_name}'
                })
            else:
                return jsonify({
                    'success': False,
                    'error': 'Failed to save library after updates'
                })
        else:
            return jsonify({
                'success': True,
                'message': 'No changes needed'
            })

    except Exception as e:
        config.logger.error("Failed to update library", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        })

@app.route('/download/<profile>/<asin>', methods=['POST'])
def download_route(profile, asin):
    """Handle book download request"""
    config.logger.info(f"Download requested for ASIN {asin} with profile {profile}")

    if not profile or not asin:
        return jsonify({'success': False, 'error': 'Missing profile or ASIN'})

    # Validate profile exists
    profiles = get_profiles()
    if not any(p['name'] == profile for p in profiles):
        return jsonify({'success': False, 'error': f'Profile {profile} not found'})

    # Get download options
    options = request.get_json() if request.is_json else {}

    return jsonify(download_content(profile, asin, DownloadType.BOOK, options))

@app.route('/convert/<asin>', methods=['POST'])
def convert_route(asin):
    """Handle book conversion request"""
    return jsonify(convert_book(asin))

@app.route('/download-cover/<profile>/<asin>', methods=['POST'])
def download_cover_route(profile, asin):
    """Handle cover download request"""
    return jsonify(download_content(profile, asin, DownloadType.COVER))

@app.route('/download-pdf/<profile>/<asin>', methods=['POST'])
def download_pdf_route(profile, asin):
    """Handle PDF download request"""
    return jsonify(download_content(profile, asin, DownloadType.PDF))

@app.route('/download-all/<profile>', methods=['POST'])
def download_all(profile):
    """Download all missing books for a profile"""
    try:
        library = load_library()
        profiles = get_profiles()

        if not any(p['name'] == profile for p in profiles):
            return jsonify({
                'success': False,
                'error': f'Profile {profile} not found'
            })

        # Get books missing Audible files
        to_download = [
            asin for asin, book in library.items()
            if profile in book.get('profiles', []) and not book.get('audible_file')
        ]

        if not to_download:
            return jsonify({
                'success': True,
                'message': 'No new books to download'
            })

        results = {
            'success': True,
            'total': len(to_download),
            'downloaded': 0,
            'failed': 0,
            'failures': []
        }

        for asin in to_download:
            result = download_content(profile, asin, DownloadType.BOOK)
            if result['success']:
                results['downloaded'] += 1
            else:
                results['failed'] += 1
                book = library.get(asin, {})
                results['failures'].append({
                    'asin': asin,
                    'title': book.get('amazon_title', 'Unknown'),
                    'error': result.get('error', 'Unknown error')
                })

        return jsonify(results)

    except Exception as e:
        config.logger.error(f"Batch download failed: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        })

@app.route('/download-all-covers/<profile>', methods=['POST'])
def download_all_covers(profile):
    """Download all missing covers for a profile"""
    try:
        library = load_library()
        to_download = [
            asin for asin, book in library.items()
            if profile in book.get('profiles', []) and not book.get('cover_path')
        ]

        results = {
            'success': True,
            'total': len(to_download),
            'downloaded': 0,
            'failed': 0,
            'failures': []
        }

        for asin in to_download:
            result = download_content(profile, asin, DownloadType.COVER)
            if result['success']:
                results['downloaded'] += 1
            else:
                results['failed'] += 1
                results['failures'].append({
                    'asin': asin,
                    'error': result.get('error', 'Unknown error')
                })

        return jsonify(results)

    except Exception as e:
        config.logger.error(f"Bulk cover download failed: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)})

@app.route('/download-all-pdfs/<profile>', methods=['POST'])
def download_all_pdfs(profile):
    """Download all missing PDFs for a profile"""
    try:
        library = load_library()
        # Get books that don't have PDF status set or don't have the file
        to_download = [
            asin for asin, book in library.items()
            if profile in book.get('profiles', [])
            and ('pdf_available' not in book or 
                 (book.get('pdf_available', True) and not book.get('pdf_file')))
        ]

        results = {
            'success': True,
            'total': len(to_download),
            'downloaded': 0,
            'not_available': 0,
            'failed': 0,
            'failures': []
        }

        for asin in to_download:
            result = download_content(profile, asin, DownloadType.PDF)
            if result['success']:
                results['downloaded'] += 1
            elif "No PDF available" in result.get('message', ''):
                results['not_available'] += 1
            else:
                results['failed'] += 1
                results['failures'].append({
                    'asin': asin,
                    'error': result.get('error', 'Unknown error')
                })

        return jsonify(results)

    except Exception as e:
        config.logger.error(f"Bulk PDF download failed: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)})

@app.route('/download-complete/<profile>', methods=['POST'])
def download_complete(profile):
    """Download all missing content (books, covers, PDFs) for a profile"""
    try:
        # First download books
        book_result = download_all(profile)
        if not isinstance(book_result, dict):
            book_result = book_result.get_json()

        # Then covers
        cover_result = download_all_covers(profile)
        if not isinstance(cover_result, dict):
            cover_result = cover_result.get_json()

        # Finally PDFs
        pdf_result = download_all_pdfs(profile)
        if not isinstance(pdf_result, dict):
            pdf_result = pdf_result.get_json()

        return jsonify({
            'success': True,
            'books': book_result,
            'covers': cover_result,
            'pdfs': pdf_result
        })

    except Exception as e:
        config.logger.error(f"Complete download failed: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)})

@app.route('/rescan', methods=['POST'])
def rescan():
    """Verify all files exist and update sizes"""
    try:
        config.logger.info("Starting file verification")
        verify_files()  # This updates the library if needed
        return jsonify({'success': True})
    except Exception as e:
        config.logger.error(f"Rescan failed: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        })

@app.route('/assign-book', methods=['POST'])
def assign_book():
    """Assign a book to a profile"""
    try:
        data = request.get_json()
        asin = data.get('asin')
        profile = data.get('profile')

        if not asin or not profile:
            return jsonify({'success': False, 'error': 'Missing ASIN or profile'})

        profiles = get_profiles()
        if not any(p['name'] == profile for p in profiles):
            return jsonify({
                'success': False,
                'error': f'Profile {profile} not found'
            })

        library = load_library()
        if asin not in library:
            return jsonify({'success': False, 'error': 'Book not found'})

        if 'profiles' not in library[asin]:
            library[asin]['profiles'] = []

        if profile not in library[asin]['profiles']:
            library[asin]['profiles'].append(profile)

            if save_library(library):
                return jsonify({
                    'success': True,
                    'profiles': library[asin]['profiles']
                })
            else:
                return jsonify({'success': False, 'error': 'Failed to save library'})
        else:
            return jsonify({
                'success': True,
                'message': 'Profile already assigned',
                'profiles': library[asin]['profiles']
            })

    except Exception as e:
        config.logger.error(f"Failed to assign book: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)})

@app.route('/download-file')
def download_file():
    """Serve a file download"""
    file_path = request.args.get('path')
    try:
        return send_file(file_path, as_attachment=True)
    except Exception as e:
        config.logger.error(f"Error downloading file: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/delete-file', methods=['POST'])
def delete_file():
    """Delete a file and update library"""
    try:
        data = request.get_json()
        asin = data.get('asin')
        file_type = data.get('file_type')  # 'audible' or 'm4b'

        if not asin or not file_type:
            return jsonify({'success': False, 'error': 'Missing ASIN or file type'})

        library = load_library()
        if asin not in library:
            return jsonify({'success': False, 'error': 'Book not found'})

        book = library[asin]
        file_path = None
        file_to_delete = None

        if file_type == 'audible':
            if book.get('audible_file'):
                file_to_delete = Path(book['audible_file'])
                # Also delete voucher file if it exists
                if book.get('voucher_file'):
                    try:
                        voucher_path = Path(book['voucher_file'])
                        if voucher_path.exists():
                            voucher_path.unlink()
                        del book['voucher_file']
                    except Exception as e:
                        config.logger.error(f"Error deleting voucher file: {e}")

                del book['audible_file']
                del book['audible_size']
                if 'audible_format' in book:
                    del book['audible_format']

        elif file_type == 'm4b':
            if book.get('m4b_file'):
                file_to_delete = Path(book['m4b_file'])
                del book['m4b_file']
                del book['m4b_size']

        if file_to_delete:
            try:
                if file_to_delete.exists():
                    file_to_delete.unlink()
                save_library(library)
                return jsonify({'success': True})
            except Exception as e:
                config.logger.error(f"Error deleting file: {e}")
                return jsonify({'success': False, 'error': str(e)})

        return jsonify({'success': False, 'error': 'File not found'})

    except Exception as e:
        config.logger.error(f"Error in delete_file: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/cover/<asin>')
def get_cover(asin):
    """Serve a book's cover image"""
    library = load_library()
    if asin in library and library[asin].get('cover_path'):
        return send_file(library[asin]['cover_path'])
    return '', 404

@app.route('/pdf/<asin>')
def get_pdf(asin):
    """Serve a book's PDF if available"""
    library = load_library()
    if asin in library and library[asin].get('pdf_file'):
        filename = os.path.basename(library[asin]['pdf_file'])
        return send_file(
            library[asin]['pdf_file'],
            download_name=secure_filename(filename),
            as_attachment=True
        )
    return '', 404

@app.template_filter('filesize')
def filesize_filter(size):
    """Convert bytes to human readable format"""
    try:
        size = int(size or 0)
        if size == 0:
            return "0 B"

        if size > 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024 * 1024):.1f}GB"
        elif size > 1024 * 1024:
            return f"{int(size / (1024 * 1024))}MB"
        elif size > 1024:
            return f"{int(size / 1024)}KB"
        else:
            return f"{size}B"
    except (TypeError, ValueError):
        return "0B"

@app.route('/convert-all/<profile>', methods=['POST'])
def convert_all_route(profile):
    """Convert all eligible books for a profile"""
    try:
        library = load_library()
        to_convert = [
            asin for asin, book in library.items()
            if profile in book.get('profiles', []) 
            and book.get('audible_file') 
            and not book.get('m4b_file')
        ]

        results = {
            'success': True,
            'total': len(to_convert),
            'converted': 0,
            'failed': 0,
            'failures': []
        }

        for asin in to_convert:
            result = convert_book(asin)
            if result['success']:
                results['converted'] += 1
            else:
                results['failed'] += 1
                book = library.get(asin, {})
                results['failures'].append({
                    'asin': asin,
                    'title': book.get('amazon_title', 'Unknown'),
                    'error': result.get('error', 'Unknown error')
                })

        return jsonify(results)

    except Exception as e:
        config.logger.error(f"Batch conversion failed: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        })

@app.route('/unlock-book', methods=['POST'])
def unlock_book():
    """Remove locked status from a book"""
    try:
        data = request.get_json()
        asin = data.get('asin')
        
        if not asin:
            return jsonify({'success': False, 'error': 'Missing ASIN'})
            
        library = load_library()
        if asin not in library:
            return jsonify({'success': False, 'error': 'Book not found'})
            
        if 'locked' in library[asin]:
            del library[asin]['locked']
            save_library(library)
            
        return jsonify({'success': True})
        
    except Exception as e:
        config.logger.error(f"Error unlocking book: {e}")
        return jsonify({'success': False, 'error': str(e)})
