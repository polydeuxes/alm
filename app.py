import logging
from flask import Flask
from pathlib import Path
import os
import config
from utils.auth import get_profiles
from utils.library import load_library

class Exclude304Filter(logging.Filter):
    def filter(self, record):
        # Exclude log records containing ' 304 ' (for status code 304)
        return ' 304 ' not in record.getMessage()

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Apply the filter to the werkzeug logger
werkzeug_logger = logging.getLogger('werkzeug')
exclude_304_filter = Exclude304Filter()
werkzeug_logger.addFilter(exclude_304_filter)

def start_app():
    """Initialize the application"""
    config.logger.info("Starting Audible CLI Web App")
    # Load any existing library
    library = load_library()
    if library:
        config.logger.info(f"Loaded existing library with {len(library)} books")
    else:
        config.logger.info("No existing library found")

    # Get configured profiles
    profiles = get_profiles()
    if profiles:
        config.logger.info(f"Found {len(profiles)} configured profiles")
        for profile in profiles:
            config.logger.info(f"Profile: {profile['name']} ({profile['country']})")
    else:
        config.logger.info("No profiles configured")

# Import routes after app creation to avoid circular imports
from routes import *

if __name__ == '__main__':
    start_app()
