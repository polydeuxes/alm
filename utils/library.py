import json
import fcntl
import tempfile
import os
from pathlib import Path
from collections import defaultdict
import config
from utils.common import run_command

def load_library():
    """Load library JSON with file locking"""
    try:
        if not os.path.exists(config.LIBRARY_FILE):
            config.logger.info("Library file not found, initializing empty library")
            return {}

        with open(config.LIBRARY_FILE, 'r') as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                library = json.load(f)
                return library
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except Exception as e:
        config.logger.error(f"Error loading library: {e}")
        return {}

def save_library(library_data, log_save=False):
    """Save library JSON with atomic operation and locking"""
    try:
        os.makedirs(config.CONFIG_DIR, exist_ok=True)

        temp_fd, temp_path = tempfile.mkstemp(dir=config.CONFIG_DIR)
        with os.fdopen(temp_fd, 'w') as temp_file:
            fcntl.flock(temp_fd, fcntl.LOCK_EX)
            try:
                json.dump(library_data, temp_file, indent=2)
                if log_save:
                    config.logger.info(f"Library saved with {len(library_data)} entries")
            finally:
                fcntl.flock(temp_fd, fcntl.LOCK_UN)
        os.rename(temp_path, config.LIBRARY_FILE)
        return True
    except Exception as e:
        config.logger.error(f"Error saving library: {e}")
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        return False

def update_book_database(profile_name):
    """Update library from Audible CLI export"""
    try:
        # Export library to TSV
        destination_path = Path(config.CONFIG_DIR) / f"library-{profile_name}.tsv"
        result = run_command(f'audible -P {profile_name} library export -o {destination_path}')
        
        if not result['success']:
            config.logger.error(f"Failed to export library for {profile_name}: {result['error']}")
            return []

        books = []
        with open(destination_path, 'r') as f:
            lines = f.readlines()
            
        if len(lines) < 2:
            config.logger.warning(f"No books found for profile {profile_name}")
            return []
            
        headers = lines[0].strip().split('\t')
        
        for line in lines[1:]:
            fields = line.strip().split('\t')
            if len(fields) != len(headers):
                config.logger.warning(f"Skipping malformed line: {line}")
                continue
                
            book_data = dict(zip(headers, fields))
            books.append({
                'asin': book_data['asin'],
                'amazon_title': book_data['title'],
                'author': book_data['authors'],
                'subtitle': book_data.get('subtitle', ''),
                'series': book_data.get('series_title', ''),
                'series_sequence': book_data.get('series_sequence', ''),
                'runtime_minutes': book_data.get('runtime_length_min', '0'),
                'genres': book_data.get('genres', '').split(', '),
                'narrators': book_data.get('narrators', ''),
                'release_date': book_data.get('release_date', ''),
                'purchase_date': book_data.get('purchase_date', ''),
                'cover_url': book_data.get('cover_url', ''),
                'locked': False  # Initialize as unlocked, will be set true if download fails
            })
            
        return books
        
    except Exception as e:
        config.logger.error(f"Error updating database for {profile_name}: {e}")
        return []

def merge_book_data(existing_book, new_book, profile_name):
    """Merge new book data with existing book data, preserving important fields"""
    # Fields that should never be overwritten if they exist
    preserve_fields = [
        'audible_file', 'audible_size', 'audible_format',
        'm4b_file', 'm4b_size', 'cover_path', 'locked'  # Add locked to preserved fields
    ]
    
    # Create merged book starting with new data
    merged_book = new_book.copy()
    
    # Handle profiles as a list
    existing_profiles = existing_book.get('profiles', [])
    if profile_name not in existing_profiles:
        existing_profiles.append(profile_name)
    merged_book['profiles'] = existing_profiles
    
    # Preserve existing fields that shouldn't be overwritten
    for field in preserve_fields:
        if field in existing_book:
            merged_book[field] = existing_book[field]
            
    return merged_book

def verify_files():
    """Verify stored files exist and update sizes"""
    library = load_library()
    changes = False
    
    config.logger.info(f"Starting verification of {len(library)} books")
    
    audible_verified = 0
    m4b_verified = 0
    missing_files = []
    
    for asin, book in library.items():
        book_title = book.get('amazon_title', 'Unknown')
        
        # Check Audible file
        if book.get('audible_file'):
            path = Path(book['audible_file'])
            if not path.exists():
                config.logger.warning(f"Audible file missing for '{book_title}' ({asin}): {path}")
                missing_files.append(f"Audible: {book_title}")
                del book['audible_file']
                del book['audible_size']
                del book['audible_format']
                changes = True
            else:
                current_size = path.stat().st_size
                if current_size != book.get('audible_size'):
                    book['audible_size'] = current_size
                    changes = True
                audible_verified += 1
                    
        # Check M4B file
        if book.get('m4b_file'):
            path = Path(book['m4b_file'])
            if not path.exists():
                config.logger.warning(f"M4B file missing for '{book_title}' ({asin}): {path}")
                missing_files.append(f"M4B: {book_title}")
                del book['m4b_file']
                del book['m4b_size']
                changes = True
            else:
                current_size = path.stat().st_size
                if current_size != book.get('m4b_size'):
                    book['m4b_size'] = current_size
                    changes = True
                m4b_verified += 1
    
    if changes:
        save_library(library)
        
    config.logger.info(
        f"Verification complete:\n"
        f"- Verified {audible_verified} Audible files\n"
        f"- Verified {m4b_verified} M4B files\n"
        f"- Found {len(missing_files)} missing files"
    )
    if missing_files:
        config.logger.info("Missing files:\n" + "\n".join(missing_files))
