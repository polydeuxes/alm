from flask import render_template, request, jsonify, send_file
from app import app
import config
from utils.auth import get_profiles, handle_quickstart, handle_additional_profile
from utils.library import load_library, save_library, verify_files, update_book_database
from utils.files import download_content, get_file_status, download_status, DownloadType
from utils.converter import convert_book
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

@app.route('/book-status/<asin>')
def book_status(asin):
    """Get current status of a book"""
    library = load_library()
    if asin in library:
        return jsonify(library[asin])
    else:
        return jsonify({'error': 'Book not found'}), 404

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
    from utils.converter import conversion_status

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

@app.route('/download-status/<asin>')
def download_status_route(asin):
    """Get status of a download in progress"""
    # Check if book exists in library
    library = load_library()
    if asin not in library:
        return jsonify({
            'error': 'Book not found',
            'complete': False
        })
    
    # Check if book is already downloaded
    if library[asin].get('audible_file'):
        return jsonify({
            'complete': True,
            'success': True
        })
    
    # Check if book is locked
    if library[asin].get('locked'):
        return jsonify({
            'error': 'Book is locked or not available',
            'status': 'locked',
            'complete': True
        })
    
    # Get progress from download_status global
    status_info = download_status.get(asin, {})
    
    # If progress exists, return it
    if isinstance(status_info, dict) and 'progress' in status_info:
        return jsonify({
            'progress': status_info['progress'],
            'complete': False
        })
    
    # Default response for books with unknown status
    return jsonify({
        'progress': 0,
        'complete': False
    })

@app.route('/convert/<asin>', methods=['POST'])
def convert_route(asin):
    """Handle book conversion request"""
    return jsonify(convert_book(asin))

@app.route('/download-cover/<profile>/<asin>', methods=['POST'])
def download_cover_route(profile, asin):
    """Handle cover download request"""
    return jsonify(download_content(profile, asin, DownloadType.COVER))

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

        # Get books missing Audible files and not locked
        to_download = [
            {
                'asin': asin,
                'title': book.get('amazon_title', 'Unknown')
            }
            for asin, book in library.items()
            if profile in book.get('profiles', []) 
            and not book.get('audible_file')
            and not book.get('locked', False)  # Skip already locked books
        ]
        
        # Count locked books
        locked_books = [
            {
                'asin': asin,
                'title': book.get('amazon_title', 'Unknown')
            }
            for asin, book in library.items()
            if profile in book.get('profiles', []) 
            and not book.get('audible_file')
            and book.get('locked', False)  # Count locked books
        ]

        if not to_download and not locked_books:
            return jsonify({
                'success': True,
                'message': 'No new books to download'
            })

        # Return initial response to start the process
        return jsonify({
            'success': True,
            'total': len(to_download),
            'locked_count': len(locked_books),
            'asin_list': [book['asin'] for book in to_download],
            'message': f'Starting download of {len(to_download)} books. Skipping {len(locked_books)} locked books.'
        })

    except Exception as e:
        config.logger.error(f"Batch download failed: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        })

@app.route('/download-book-batch', methods=['POST'])
def download_book_batch():
    """Process a batch of books one by one with status updates"""
    try:
        data = request.get_json()
        profile = data.get('profile')
        asin_list = data.get('asin_list', [])
        current_index = data.get('current_index', 0)
        results = data.get('results', {
            'total': len(asin_list),
            'downloaded': 0,
            'failed': 0,
            'failures': [],
            'books': []
        })
        
        # If all books processed, return final results
        if current_index >= len(asin_list):
            return jsonify({
                'success': True,
                'complete': True,
                'results': results
            })
            
        # Process the current book
        asin = asin_list[current_index]
        
        # Get the book title for better logging
        library = load_library()
        book_title = library.get(asin, {}).get('amazon_title', 'Unknown')
        
        config.logger.info(f"Batch processing book {current_index+1}/{len(asin_list)}: {book_title} ({asin})")
        
        # Use the existing download_content function
        result = download_content(profile, asin, DownloadType.BOOK)
        
        book_info = {
            'asin': asin,
            'title': book_title,
            'index': current_index
        }

        # Reload library to get updated file sizes
        updated_library = load_library()
        
        if result['success']:
            results['downloaded'] += 1
            book_info['status'] = 'downloaded'
            book_info['file'] = result.get('file', '')

            # Get updated file size from library
            if asin in updated_library and updated_library[asin].get('audible_size'):
                book_info['size'] = updated_library[asin]['audible_size']
            else:
                # If size not in library, try to get it from the file
                if result.get('file') and os.path.exists(result['file']):
                    book_info['size'] = os.path.getsize(result['file'])
                else:
                    book_info['size'] = 0
        else:
            results['failed'] += 1
            book_info['status'] = 'failed'
            book_info['error'] = result.get('error', 'Unknown error')
            
            if result.get('status') == 'locked' or 'locked' in result.get('error', '').lower():
                book_info['locked'] = True
                
            results['failures'].append({
                'asin': asin,
                'title': book_title,
                'error': result.get('error', 'Unknown error')
            })
            
        results['books'].append(book_info)
        
        # Return progress update
        return jsonify({
            'success': True,
            'complete': False,
            'current_index': current_index + 1,
            'profile': profile,
            'asin_list': asin_list,
            'current_book': book_info,
            'results': results
        })
        
    except Exception as e:
        config.logger.error(f"Book batch processing failed: {e}", exc_info=True)
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
            {
                'asin': asin,
                'title': book.get('amazon_title', 'Unknown')
            }
            for asin, book in library.items()
            if profile in book.get('profiles', []) and not book.get('cover_path')
        ]

        if not to_download:
            return jsonify({
                'success': True,
                'message': 'No covers to download'
            })

        # Return initial response to start the process
        return jsonify({
            'success': True,
            'total': len(to_download),
            'asin_list': [book['asin'] for book in to_download],
            'message': f'Starting download of {len(to_download)} covers'
        })

    except Exception as e:
        config.logger.error(f"Bulk cover download failed: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)})

@app.route('/download-cover-batch', methods=['POST'])
def download_cover_batch():
    """Process a batch of cover downloads one by one with status updates"""
    try:
        data = request.get_json()
        profile = data.get('profile')
        asin_list = data.get('asin_list', [])
        current_index = data.get('current_index', 0)
        results = data.get('results', {
            'total': len(asin_list),
            'downloaded': 0,
            'failed': 0,
            'failures': [],
            'books': []
        })
        
        # If all covers processed, return final results
        if current_index >= len(asin_list):
            return jsonify({
                'success': True,
                'complete': True,
                'results': results
            })
            
        # Process the current cover
        asin = asin_list[current_index]
        
        # Get the book title for better logging
        library = load_library()
        book_title = library.get(asin, {}).get('amazon_title', 'Unknown')
        
        config.logger.info(f"Batch cover download {current_index+1}/{len(asin_list)}: {book_title} ({asin})")
        
        # Use the existing download_content function with DownloadType.COVER
        result = download_content(profile, asin, DownloadType.COVER)
        
        book_info = {
            'asin': asin,
            'title': book_title,
            'index': current_index
        }
        
        # Reload library to get updated file path
        updated_library = load_library()
        
        if result['success']:
            results['downloaded'] += 1
            book_info['status'] = 'downloaded'
            book_info['file'] = result.get('file', '')
            
            # Get path from updated library
            if asin in updated_library and updated_library[asin].get('cover_path'):
                book_info['cover_path'] = updated_library[asin]['cover_path']
        else:
            results['failed'] += 1
            book_info['status'] = 'failed'
            book_info['error'] = result.get('error', 'Unknown error')
            
            results['failures'].append({
                'asin': asin,
                'title': book_title,
                'error': result.get('error', 'Unknown error')
            })
            
        results['books'].append(book_info)
        
        # Return progress update
        return jsonify({
            'success': True,
            'complete': False,
            'current_index': current_index + 1,
            'profile': profile,
            'asin_list': asin_list,
            'current_book': book_info,
            'results': results
        })
        
    except Exception as e:
        config.logger.error(f"Cover batch processing failed: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        })

@app.route('/download-pdf/<profile>/<asin>', methods=['POST'])
def download_pdf_route(profile, asin):
    """Handle PDF download request"""
    result = download_content(profile, asin, DownloadType.PDF)
    
    # If no PDF is available, return a properly formatted response
    if not result['success'] and 'message' in result and 'No PDF available' in result['message']:
        return jsonify({
            'success': False,
            'error': f"No PDF available for this book"
        })
    
    # For other failures, return the error message
    if not result['success']:
        return jsonify({
            'success': False,
            'error': result.get('error', 'Unknown error during PDF download')
        })
    
    # Get the file size from the library for successful downloads
    library = load_library()
    pdf_size = 0
    if asin in library and library[asin].get('pdf_size'):
        pdf_size = library[asin]['pdf_size']

    # For successful downloads
    return jsonify({
        'success': True,
        'file': result.get('file', ''),
        'message': result.get('message', 'PDF downloaded successfully')
    })

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
            'failures': [],
            'books': []
        }

        for asin in to_download:
            result = download_content(profile, asin, DownloadType.PDF)
            book_info = {
                'asin': asin,
                'title': library[asin].get('amazon_title', 'Unknown')
            }

            if result['success']:
                results['downloaded'] += 1
                book_info['pdf_file'] = result.get('file')
                book_info['pdf_size'] = library[asin].get('pdf_size', 0)
            elif result.get('message') == 'No PDF available for this book' or 'No PDF found' in result.get('error', ''):
                results['not_available'] += 1
                book_info['pdf_available'] = False
            else:
                results['failed'] += 1
                book_info['error'] = result.get('error', 'Unknown error')
                results['failures'].append({
                    'asin': asin,
                    'title': library[asin].get('amazon_title', 'Unknown'),
                    'error': result.get('error', 'Unknown error')
                })
            
            results['books'].append(book_info)

        return jsonify(results)

    except Exception as e:
        config.logger.error(f"Bulk PDF download failed: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)})

@app.route('/list-missing-pdfs/<profile>')
def list_missing_pdfs(profile):
    """Get list of books that need PDF processing"""
    try:
        library = load_library()
        
        # Get books that don't have PDF status set or don't have the file
        books_to_process = [
            {
                'asin': asin,
                'title': book.get('amazon_title', 'Unknown')
            }
            for asin, book in library.items()
            if profile in book.get('profiles', [])
            and ('pdf_available' not in book or
                 (book.get('pdf_available', True) and not book.get('pdf_file')))
        ]

        return jsonify({
            'success': True,
            'books': books_to_process,
            'message': f'Found {len(books_to_process)} books to process'
        })

    except Exception as e:
        config.logger.error(f"Error listing missing PDFs: {e}", exc_info=True)
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
    """Delete a file and any associated files (like vouchers), updating library."""
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
        files_deleted = []

        if file_type == 'audible':
            # Delete main audio file
            if book.get('audible_file'):
                try:
                    audio_path = Path(book['audible_file'])
                    if audio_path.exists():
                        audio_path.unlink()
                        config.logger.info(f"Deleted audio file: {audio_path}")
                        files_deleted.append(str(audio_path))
                except Exception as e:
                    config.logger.error(f"Error deleting audio file: {e}")

                # Clean up associated fields
                del book['audible_file']
                del book['audible_size']
                if 'audible_format' in book:
                    del book['audible_format']

            # Always check and clean up voucher file if it exists
            if book.get('voucher_file'):
                try:
                    voucher_path = Path(book['voucher_file'])
                    if voucher_path.exists():
                        voucher_path.unlink()
                        config.logger.info(f"Deleted voucher file: {voucher_path}")
                        files_deleted.append(str(voucher_path))
                except Exception as e:
                    config.logger.error(f"Error deleting voucher file: {e}")
                del book['voucher_file']

        elif file_type == 'm4b':
            if book.get('m4b_file'):
                try:
                    m4b_path = Path(book['m4b_file'])
                    if m4b_path.exists():
                        m4b_path.unlink()
                        config.logger.info(f"Deleted M4B file: {m4b_path}")
                        files_deleted.append(str(m4b_path))
                except Exception as e:
                    config.logger.error(f"Error deleting M4B file: {e}")

                del book['m4b_file']
                del book['m4b_size']

        save_library(library)
        return jsonify({'success': True, 'deleted': files_deleted})

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
        from utils.converter import convert_book, conversion_status

        library = load_library()
        to_convert = [
            {
                'asin': asin,
                'title': book.get('amazon_title', 'Unknown')
            }
            for asin, book in library.items()
            if profile in book.get('profiles', []) 
            and book.get('audible_file') 
            and not book.get('m4b_file')
        ]

        if not to_convert:
            return jsonify({
                'success': True,
                'message': 'No books to convert'
            })

        # Return initial response to start the process
        return jsonify({
            'success': True,
            'total': len(to_convert),
            'asin_list': [book['asin'] for book in to_convert],
            'message': f'Starting conversion of {len(to_convert)} books'
        })

    except Exception as e:
        config.logger.error(f"Batch conversion failed: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        })

@app.route('/convert-book-batch', methods=['POST'])
def convert_book_batch():
    """Process a batch of book conversions one by one with status updates"""
    try:
        from utils.converter import convert_book, conversion_status
        
        data = request.get_json()
        asin_list = data.get('asin_list', [])
        current_index = data.get('current_index', 0)
        results = data.get('results', {
            'total': len(asin_list),
            'converted': 0,
            'failed': 0,
            'failures': [],
            'books': []
        })
        
        # If all books processed, return final results
        if current_index >= len(asin_list):
            return jsonify({
                'success': True,
                'complete': True,
                'results': results
            })
            
        # Process the current book
        asin = asin_list[current_index]
        
        # Get the book title for better logging
        library = load_library()
        book_title = library.get(asin, {}).get('amazon_title', 'Unknown')
        
        config.logger.info(f"Batch conversion {current_index+1}/{len(asin_list)}: {book_title} ({asin})")
        
        # Use the existing convert_book function
        result = convert_book(asin)
        
        book_info = {
            'asin': asin,
            'title': book_title,
            'index': current_index
        }
        
        # Reload library to get updated file sizes
        updated_library = load_library()
        
        if result['success']:
            results['converted'] += 1
            book_info['status'] = 'converted'
            book_info['file'] = result.get('file', '')
            
            # Get updated file size from library
            if asin in updated_library and updated_library[asin].get('m4b_size'):
                book_info['size'] = updated_library[asin]['m4b_size']
            else:
                # If size not in library, try to get it from the file
                if result.get('file') and os.path.exists(result['file']):
                    book_info['size'] = os.path.getsize(result['file'])
                else:
                    book_info['size'] = 0
        else:
            results['failed'] += 1
            book_info['status'] = 'failed'
            book_info['error'] = result.get('error', 'Unknown error')
            
            results['failures'].append({
                'asin': asin,
                'title': book_title,
                'error': result.get('error', 'Unknown error')
            })
            
        results['books'].append(book_info)
        
        # Return progress update
        return jsonify({
            'success': True,
            'complete': False,
            'current_index': current_index + 1,
            'asin_list': asin_list,
            'current_book': book_info,
            'results': results
        })
        
    except Exception as e:
        config.logger.error(f"Book conversion batch processing failed: {e}", exc_info=True)
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
