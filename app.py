from flask import Flask, render_template, request, jsonify, send_file
import os
import json
import sqlite3
from datetime import datetime
import subprocess
import traceback
import urllib.request
import boto3
from botocore.exceptions import ClientError

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max

# Create directories
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs('static/spliced', exist_ok=True)

def init_db():
    conn = sqlite3.connect('songs.db')
    c = conn.cursor()
    
    # Only create table if it doesn't exist (don't drop existing data)
    # c.execute("DROP TABLE IF EXISTS songs")  # REMOVED for production safety
    
    c.execute('''CREATE TABLE IF NOT EXISTS songs
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  title TEXT, 
                  lyrics TEXT, 
                  notes TEXT, 
                  audio_files TEXT,
                  voice_notes TEXT,
                  spliced_file TEXT,
                  source TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()
    print("Database initialized with correct schema")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/songs')
def get_songs():
    try:
        conn = sqlite3.connect('songs.db')
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM songs ORDER BY created_at DESC")
        rows = c.fetchall()
        
        songs = []
        for row in rows:
            song = dict(row)
            # Parse JSON fields
            try:
                song['audio_files'] = json.loads(song.get('audio_files', '[]'))
            except:
                song['audio_files'] = []
            try:
                song['voice_notes'] = json.loads(song.get('voice_notes', '[]'))
            except:
                song['voice_notes'] = []
            songs.append(song)
            
        conn.close()
        return jsonify({'songs': songs})
    except Exception as e:
        print(f"Error loading songs: {e}")
        traceback.print_exc()
        return jsonify({'songs': []})

@app.route('/api/upload', methods=['POST'])
def upload_files():
    try:
        if 'audio_files' not in request.files:
            return jsonify({'success': False, 'error': 'No files'})
        
        files = request.files.getlist('audio_files')
        uploaded = []
        
        for file in files:
            if file and file.filename:
                # Save with timestamp to avoid conflicts
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
                # Keep original extension
                ext = os.path.splitext(file.filename)[1]
                safe_filename = f"{timestamp}_{file.filename}".replace(' ', '_')
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], safe_filename)
                file.save(filepath)
                
                uploaded.append({
                    'name': file.filename,
                    'saved_name': safe_filename,
                    'path': f'/static/uploads/{safe_filename}',
                    'size': os.path.getsize(filepath)
                })
        
        return jsonify({'success': True, 'files': uploaded})
    except Exception as e:
        print(f"Upload error: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/splice', methods=['POST'])
def splice_audio():
    try:
        data = request.json
        files = data.get('files', [])
        
        if not files:
            return jsonify({'success': False, 'error': 'No files to splice'})
        
        # Try using pydub if available
        try:
            from pydub import AudioSegment
            combined = AudioSegment.empty()
            
            for file_info in files:
                # Handle both path string and file object
                if isinstance(file_info, str):
                    filepath = file_info.replace('/static/', 'static/')
                    saved_name = os.path.basename(filepath)
                else:
                    saved_name = file_info.get('saved_name', '')
                    filepath = os.path.join('static/uploads', saved_name)
                
                if os.path.exists(filepath):
                    if filepath.endswith('.opus'):
                        audio = AudioSegment.from_ogg(filepath)
                    else:
                        audio = AudioSegment.from_file(filepath)
                    combined += audio
            
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_filename = f'spliced_{timestamp}.mp3'
            output_path = os.path.join('static/spliced', output_filename)
            combined.export(output_path, format="mp3")
            
        except ImportError:
            # Fallback: just copy first file as placeholder
            import shutil
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_filename = f'spliced_{timestamp}.mp3'
            output_path = os.path.join('static/spliced', output_filename)
            
            if isinstance(files[0], str):
                first_file = files[0].replace('/static/', 'static/')
            else:
                first_file = os.path.join('static/uploads', files[0]['saved_name'])
            
            shutil.copy(first_file, output_path)
        
        return jsonify({
            'success': True, 
            'spliced_file': f'/static/spliced/{output_filename}',
            'filename': output_filename
        })
    except Exception as e:
        print(f"Splice error: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/save_song', methods=['POST'])
def save_song():
    try:
        # Get JSON data
        data = request.get_json(force=True)
        
        # Extract fields with defaults
        song_id = data.get('id')
        title = data.get('title', 'Untitled')
        lyrics = data.get('lyrics', '')
        notes = data.get('notes', '')
        audio_files = data.get('audio_files', [])
        voice_notes = data.get('voice_notes', [])
        spliced_file = data.get('spliced_file', '')
        source = data.get('source', '')
        
        # Convert lists to JSON strings for storage
        audio_files_json = json.dumps(audio_files)
        voice_notes_json = json.dumps(voice_notes)
        
        conn = sqlite3.connect('songs.db')
        c = conn.cursor()
        
        if song_id:
            # Update existing song
            c.execute("""UPDATE songs SET 
                         title=?, lyrics=?, notes=?, audio_files=?, 
                         voice_notes=?, spliced_file=?, source=? 
                         WHERE id=?""",
                      (title, lyrics, notes, audio_files_json, 
                       voice_notes_json, spliced_file, source, song_id))
            result_id = song_id
        else:
            # Create new song
            c.execute("""INSERT INTO songs 
                         (title, lyrics, notes, audio_files, voice_notes, spliced_file, source) 
                         VALUES (?, ?, ?, ?, ?, ?, ?)""",
                      (title, lyrics, notes, audio_files_json, 
                       voice_notes_json, spliced_file, source))
            result_id = c.lastrowid
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'id': result_id})
        
    except Exception as e:
        print(f"Save error: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/merge_songs', methods=['POST'])
def merge_songs():
    """Merge audio from one song into another"""
    try:
        data = request.get_json(force=True)
        source_song_id = data.get('source_id')
        target_song_id = data.get('target_id')
        
        if not source_song_id or not target_song_id:
            return jsonify({'success': False, 'error': 'Missing song IDs'})
        
        conn = sqlite3.connect('songs.db')
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        # Get both songs
        c.execute("SELECT * FROM songs WHERE id=?", (source_song_id,))
        source_song = dict(c.fetchone())
        
        c.execute("SELECT * FROM songs WHERE id=?", (target_song_id,))
        target_song = dict(c.fetchone())
        
        # Parse JSON fields
        source_audio = json.loads(source_song.get('audio_files', '[]'))
        target_audio = json.loads(target_song.get('audio_files', '[]'))
        
        source_voice = json.loads(source_song.get('voice_notes', '[]'))
        target_voice = json.loads(target_song.get('voice_notes', '[]'))
        
        # Merge audio files
        target_audio.extend(source_audio)
        target_voice.extend(source_voice)
        
        # Update target song
        c.execute("""UPDATE songs SET audio_files=?, voice_notes=? WHERE id=?""",
                  (json.dumps(target_audio), json.dumps(target_voice), target_song_id))
        
        # Delete source song if requested
        if data.get('delete_source', False):
            c.execute("DELETE FROM songs WHERE id=?", (source_song_id,))
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True})
        
    except Exception as e:
        print(f"Merge error: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/delete_song/<int:song_id>', methods=['DELETE'])
def delete_song(song_id):
    try:
        conn = sqlite3.connect('songs.db')
        c = conn.cursor()
        c.execute("DELETE FROM songs WHERE id=?", (song_id,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/promote_phrase', methods=['POST'])
def promote_phrase():
    """Promote a phrase to a full song by adding a type field or metadata"""
    try:
        data = request.get_json(force=True)
        phrase_id = data.get('phrase_id')
        
        if not phrase_id:
            return jsonify({'success': False, 'error': 'Missing phrase ID'})
        
        conn = sqlite3.connect('songs.db')
        c = conn.cursor()
        
        # For now, we'll just add a note to indicate it's been promoted
        # In future we could add a 'type' column to distinguish songs/phrases
        c.execute("SELECT notes FROM songs WHERE id=?", (phrase_id,))
        result = c.fetchone()
        
        if result:
            current_notes = result[0] or ''
            # Remove phrase markers and add promotion marker
            updated_notes = current_notes.replace('[DETECTED AS PHRASE - May need development]', '').strip()
            if not 'PROMOTED TO SONG' in updated_notes:
                updated_notes = updated_notes + '\n\n[PROMOTED TO SONG]' if updated_notes else '[PROMOTED TO SONG]'
                c.execute("UPDATE songs SET notes=? WHERE id=?", (updated_notes, phrase_id))
                conn.commit()
        
        conn.close()
        return jsonify({'success': True})
        
    except Exception as e:
        print(f"Promote error: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/inbox')
def get_inbox():
    """Get organized inbox content by sender and date"""
    try:
        conn = sqlite3.connect('songs.db')
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        # Get all inbox items organized by sender and date
        c.execute("""SELECT * FROM inbox ORDER BY date_folder DESC, created_at DESC""")
        rows = c.fetchall()

        # Organize by sender and date
        organized = {}
        for row in rows:
            item = dict(row)
            sender = item['sender_name']
            date = item['date_folder']

            if sender not in organized:
                organized[sender] = {}
            if date not in organized[sender]:
                organized[sender][date] = []

            organized[sender][date].append(item)

        conn.close()
        return jsonify({'inbox': organized})

    except Exception as e:
        print(f"Error loading inbox: {e}")
        traceback.print_exc()
        return jsonify({'inbox': {}})

@app.route('/api/debug_song/<int:song_id>')
def debug_song(song_id):
    """Debug endpoint to see what's actually saved"""
    conn = sqlite3.connect('songs.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM songs WHERE id=?", (song_id,))
    row = c.fetchone()

    if row:
        song = dict(row)
        # Parse JSON fields
        song['audio_files'] = json.loads(song.get('audio_files', '[]'))
        song['voice_notes'] = json.loads(song.get('voice_notes', '[]'))

        print(f"\n=== DEBUG SONG {song_id} ===")
        print(f"Title: {song['title']}")
        print(f"Audio files: {len(song['audio_files'])}")
        for i, audio in enumerate(song['audio_files']):
            print(f"  {i+1}. {audio}")
        print("="*30)

        return jsonify(song)

    return jsonify({'error': 'Song not found'})

# Twilio Configuration
TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
AWS_BUCKET_NAME = os.environ.get('AWS_BUCKET_NAME', 'ladyembersongs-recordings')

# S3 client
s3_client = boto3.client('s3')

def upload_to_s3(file_url, filename):
    """Download recording from Twilio and upload to S3"""
    try:
        # Download from Twilio
        with urllib.request.urlopen(file_url) as response:
            audio_data = response.read()
        
        # Upload to S3
        s3_key = f"recordings/{datetime.now().strftime('%Y/%m/%d')}/{filename}"
        s3_client.put_object(
            Bucket=AWS_BUCKET_NAME,
            Key=s3_key,
            Body=audio_data,
            ContentType='audio/wav'
        )
        
        return f"https://{AWS_BUCKET_NAME}.s3.amazonaws.com/{s3_key}"
    except Exception as e:
        print(f"S3 upload error: {e}")
        return None

@app.route('/twilio/voice', methods=['POST'])
def handle_incoming_call():
    """Handle incoming Twilio voice calls"""
    from_number = request.values.get('From', '')
    
    # TwiML response for call menu
    twiml_response = '''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice">Welcome to Lady Ember Songs! You are now recording. Press 1 for new voice note, 2 to pause, 3 to delete and restart, or 4 to save and end.</Say>
    <Record 
        action="/twilio/recording" 
        method="POST"
        maxLength="300"
        playBeep="true"
        recordingStatusCallback="/twilio/recording-status"
        timeout="10"
    />
    <Gather action="/twilio/menu" method="POST" numDigits="1" timeout="30">
        <Say voice="alice">Press any key for menu options.</Say>
    </Gather>
</Response>'''
    
    return twiml_response, 200, {'Content-Type': 'application/xml'}

@app.route('/twilio/menu', methods=['POST'])
def handle_menu():
    """Handle keypress menu during call"""
    digit = request.values.get('Digits', '')
    
    if digit == '1':
        # New voice note - stop current recording and start new one
        twiml_response = '''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice">Starting new voice note.</Say>
    <Record 
        action="/twilio/recording" 
        method="POST"
        maxLength="300"
        playBeep="true"
        recordingStatusCallback="/twilio/recording-status"
        timeout="10"
    />
    <Gather action="/twilio/menu" method="POST" numDigits="1" timeout="30">
        <Say voice="alice">Press any key for options.</Say>
    </Gather>
</Response>'''
    
    elif digit == '2':
        # Pause/unpause - for now just give feedback
        twiml_response = '''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice">Recording paused. Press 2 again to resume, or other keys for menu.</Say>
    <Gather action="/twilio/menu" method="POST" numDigits="1" timeout="30">
        <Say voice="alice">Press any key.</Say>
    </Gather>
</Response>'''
    
    elif digit == '3':
        # Delete and restart
        twiml_response = '''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice">Deleting previous recording. Starting fresh.</Say>
    <Record 
        action="/twilio/recording" 
        method="POST"
        maxLength="300"
        playBeep="true"
        recordingStatusCallback="/twilio/recording-status"
        timeout="10"
    />
    <Gather action="/twilio/menu" method="POST" numDigits="1" timeout="30">
        <Say voice="alice">Press any key for options.</Say>
    </Gather>
</Response>'''
    
    elif digit == '4':
        # Save and end
        twiml_response = '''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice">Saving recording. Goodbye!</Say>
    <Hangup/>
</Response>'''
    
    else:
        # Invalid option
        twiml_response = '''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice">Press 1 for new note, 2 to pause, 3 to restart, or 4 to save and end.</Say>
    <Gather action="/twilio/menu" method="POST" numDigits="1" timeout="30">
        <Say voice="alice">Press any key.</Say>
    </Gather>
</Response>'''
    
    return twiml_response, 200, {'Content-Type': 'application/xml'}

@app.route('/twilio/recording', methods=['POST'])
def handle_recording():
    """Handle completed recordings"""
    try:
        recording_url = request.values.get('RecordingUrl', '')
        recording_sid = request.values.get('RecordingSid', '')
        from_number = request.values.get('From', '')
        call_sid = request.values.get('CallSid', '')
        
        if not recording_url:
            return "No recording URL", 400
        
        # Generate filename
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"voice_note_{timestamp}_{recording_sid}.wav"
        
        # Upload to S3 
        s3_url = upload_to_s3(recording_url, filename)
        
        if s3_url:
            # Detect sender and organize by date
            sender_name = detect_sender_name(from_number)
            date_folder = datetime.now().strftime('%Y-%m-%d')
            title = f"{sender_name} - Voice {datetime.now().strftime('%H:%M')}"

            # Save to inbox system
            conn = sqlite3.connect('songs.db')
            c = conn.cursor()

            c.execute("""INSERT INTO inbox
                         (sender_name, sender_phone, content_type, title, content, s3_url, date_folder)
                         VALUES (?, ?, ?, ?, ?, ?, ?)""",
                      (sender_name, from_number, 'voice', title, f"Voice recording - {filename}", s3_url, date_folder))

            conn.commit()
            conn.close()

            print(f"🎤 Voice recording from {sender_name}: {filename} -> {s3_url}")
        
        return "Recording processed", 200
        
    except Exception as e:
        print(f"Recording error: {e}")
        traceback.print_exc()
        return "Error processing recording", 500

@app.route('/twilio/recording-status', methods=['POST'])
def handle_recording_status():
    """Handle recording status updates"""
    status = request.values.get('RecordingStatus', '')
    recording_sid = request.values.get('RecordingSid', '')
    
    print(f"Recording {recording_sid} status: {status}")
    return "OK", 200

@app.route('/twilio/sms', methods=['POST'])
def handle_sms():
    """Handle incoming SMS messages - Simple inbox system"""
    try:
        message_body = request.values.get('Body', '')
        from_number = request.values.get('From', '')

        if message_body.strip():
            # Detect sender name from phone number
            sender_name = detect_sender_name(from_number)

            # Organize by sender/date
            date_folder = datetime.now().strftime('%Y-%m-%d')
            title = f"{sender_name} - {datetime.now().strftime('%H:%M')}"

            # Save to database with sender info
            conn = sqlite3.connect('songs.db')
            c = conn.cursor()

            # Add sender and date fields to database
            c.execute("""CREATE TABLE IF NOT EXISTS inbox
                         (id INTEGER PRIMARY KEY AUTOINCREMENT,
                          sender_name TEXT,
                          sender_phone TEXT,
                          content_type TEXT,
                          title TEXT,
                          content TEXT,
                          s3_url TEXT,
                          date_folder TEXT,
                          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")

            c.execute("""INSERT INTO inbox
                         (sender_name, sender_phone, content_type, title, content, date_folder)
                         VALUES (?, ?, ?, ?, ?, ?)""",
                      (sender_name, from_number, 'text', title, message_body, date_folder))

            conn.commit()
            conn.close()

            print(f"📱 SMS from {sender_name}: {message_body[:50]}...")

            response = '''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Message>📥 Got it! Saved to your inbox.</Message>
</Response>'''

            return response, 200, {'Content-Type': 'application/xml'}

        return "No message", 400

    except Exception as e:
        print(f"SMS error: {e}")
        traceback.print_exc()
        return "Error processing SMS", 500

def detect_sender_name(phone_number):
    """Detect sender name from phone number - you can customize this"""
    phone_map = {
        # Team phone numbers
        '+16783614280': 'Asia',      # Asia's personal number
        '+17707582471': 'Twilio',    # Your Twilio number (for testing)
        # Add more team members like:
        # '+15551234567': 'Sebastian',
        # '+15559876543': 'TeamMember3',
    }

    # Clean up the phone number
    clean_phone = phone_number.replace('+1', '').replace('-', '').replace(' ', '').replace('(', '').replace(')', '')

    # Check if we know this number
    for known_phone, name in phone_map.items():
        if known_phone.replace('+1', '') in clean_phone:
            return name

    # Default to last 4 digits if unknown
    return f"User-{clean_phone[-4:]}"

if __name__ == '__main__':
    # Initialize database on startup (safe for production - won't drop existing data)
    init_db()
    port = int(os.environ.get('PORT', 5002))
    debug_mode = os.environ.get('FLASK_ENV') != 'production'
    print(f"Starting The Asia Project server on port {port}...")
    app.run(host='0.0.0.0', port=port, debug=debug_mode)