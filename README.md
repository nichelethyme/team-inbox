# Lady Ember Songs

A Flask web application for managing song demos and WhatsApp voice note imports.

## Features
- WhatsApp chat import with automatic audio file grouping
- Sequential audio playback with timeline markers
- Song vs Phrase categorization system
- Individual audio file notes
- Audio splicing and management

## Digital Ocean Deployment

### Quick Deploy with App Platform
1. Push this code to GitHub: `https://github.com/nichelethyme/ladyember`
2. In Digital Ocean, go to Apps → Create App
3. Connect your GitHub repo: `nichelethyme/ladyember`
4. Digital Ocean will auto-detect the app.yaml configuration
5. Deploy!

### Manual Deploy Steps
If you prefer manual configuration:

1. **Environment Variables:**
   - `FLASK_ENV=production`
   - `PORT=8080`

2. **Run Command:**
   ```bash
   gunicorn --config gunicorn.conf.py app:app
   ```

3. **Build Command:**
   ```bash
   pip install -r requirements.txt
   ```

## Local Development
```bash
python app.py
# Visit http://localhost:5002
```

## File Structure
- `app.py` - Main Flask application
- `import_whatsapp.py` - WhatsApp import functionality  
- `templates/index.html` - Frontend interface
- `static/uploads/` - Uploaded audio files
- `static/spliced/` - Processed/spliced audio files
- `songs.db` - SQLite database

## Production Notes
- Database is SQLite (suitable for single-user app)
- File uploads are stored locally (consider object storage for scale)
- FFMPEG required for audio processing (auto-installed on Digital Ocean)