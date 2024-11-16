# ALM (Audible Library Manager)

A Flask-based web application for managing your Audible library, handling downloads, and converting audiobooks to M4B format.

## Features

- Download audiobooks from your Audible library (.aax and .aaxc formats)
- Convert audiobooks to M4B format
- Download companion PDFs and cover images
- Multiple profile support for different Audible accounts
- Batch operations for downloading and converting
- Track locked/unlocked status of books
- File integrity validation
- Clean, modern web interface

## Requirements

- Python 3.9+
- FFmpeg (for audio conversion)
- Docker and Docker Compose

## Setup

1. Clone the repository:
```bash
git clone https://github.com/polydexues/alm
cd alm
```

2. Build and run with Docker Compose:
```bash
docker-compose up --build -d
```

## Directory Structure

```
/books
  /aax      # For downloaded Audible files
  /m4b      # For converted M4B files
  /pdf      # For companion PDFs
  /images   # For cover images
/audible_config    # For configuration files and profiles
```

## First Time Setup

1. Access the web interface at `http://localhost:5000`
2. Add at least one Audible profile
3. Follow the authentication process
4. Start managing your library

## Credits

Built with [audible-cli](https://github.com/mkb79/audible-cli), Flask, FFmpeg and Tailwind CSS
