#!/bin/bash
# Production startup script for Digital Ocean

# Install dependencies
pip install -r requirements.txt

# Create necessary directories
mkdir -p static/uploads
mkdir -p static/spliced

# Set environment for production
export FLASK_ENV=production
export PORT=${PORT:-8080}

# Start the application with gunicorn
gunicorn --config gunicorn.conf.py app:app