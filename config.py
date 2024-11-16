# config.py
import os
import logging

# Configuration
COUNTRY = os.getenv('AUDIBLE_COUNTRY', 'us').lower()
CONFIG_DIR = '/home/user/.audible'
AAX_DIR = '/books/aax'
M4B_DIR = '/books/m4b'
IMAGES_DIR = '/books/images'
PDF_DIR = '/books/pdfs'  # Add PDF directory
LIBRARY_FILE = f"{CONFIG_DIR}/library.json"
KEY_FILE = f"{CONFIG_DIR}/activation.txt"

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Ensure directories exist
for directory in [CONFIG_DIR, AAX_DIR, M4B_DIR, IMAGES_DIR, PDF_DIR]:  # Add PDF_DIR
    os.makedirs(directory, exist_ok=True)
