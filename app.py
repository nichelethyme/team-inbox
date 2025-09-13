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

# Global variable to store last error for voice feedback
last_download_error = None

# Create directories
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs('static/spliced', exist_ok=True)

def init_db():
    conn = sqlite3.connect('songs.db')
    c = conn.cursor()

    # Create songs table
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

    # Create inbox table for team messages
    c.execute('''CREATE TABLE IF NOT EXISTS inbox
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  sender_name TEXT,
                  sender_phone TEXT,
                  content_type TEXT,
                  title TEXT,
                  content TEXT,
                  s3_url TEXT,
                  date_folder TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    # Create projects table
    c.execute('''CREATE TABLE IF NOT EXISTS projects
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  name TEXT NOT NULL,
                  notes TEXT,
                  lyrics TEXT,
                  track_count INTEGER DEFAULT 0,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    # Create phrases table
    c.execute('''CREATE TABLE IF NOT EXISTS phrases
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  title TEXT NOT NULL,
                  content TEXT,
                  s3_url TEXT,
                  duration TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    # Create project_items table for linking inbox items to projects
    c.execute('''CREATE TABLE IF NOT EXISTS project_items
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  project_id INTEGER NOT NULL,
                  inbox_id INTEGER NOT NULL,
                  position INTEGER DEFAULT 0,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (project_id) REFERENCES projects (id),
                  FOREIGN KEY (inbox_id) REFERENCES inbox (id))''')

    conn.commit()
    conn.close()
    print("Database initialized with songs, inbox, projects, and phrases tables")

@app.route('/')
def index():
    """Main page with all inbox content loaded"""
    try:
        conn = sqlite3.connect('songs.db')
        c = conn.cursor()
        c.execute('''SELECT id, sender_name, sender_phone, content_type, title, content, s3_url, date_folder, created_at
                     FROM inbox
                     ORDER BY created_at DESC''')
        inbox_items = []
        for row in c.fetchall():
            inbox_items.append({
                'id': row[0],
                'sender_name': row[1],
                'sender_phone': row[2],
                'content_type': row[3],
                'title': row[4],
                'content': row[5],
                's3_url': row[6],
                'date_folder': row[7],
                'created_at': row[8]
            })
        conn.close()

        # Auto-import desktop files on load
        auto_import_desktop_files()

        return render_template('index.html', inbox_items=inbox_items)
    except Exception as e:
        print(f"Error loading inbox: {e}")
        return render_template('index.html', inbox_items=[])

def auto_import_desktop_files():
    """Auto-import audio files from desktop without user action"""
    try:
        import os
        import glob

        desktop_patterns = [
            '/Users/asiamurray/Desktop/*.mp3',
            '/Users/asiamurray/Desktop/*.wav',
            '/Users/asiamurray/Desktop/*.m4a'
        ]

        conn = sqlite3.connect('songs.db')
        c = conn.cursor()

        # Get existing files to avoid duplicates
        c.execute("SELECT content FROM inbox WHERE sender_name = 'Desktop Import'")
        existing = {row[0] for row in c.fetchall()}

        imported = 0
        for pattern in desktop_patterns:
            for file_path in glob.glob(pattern):
                filename = os.path.basename(file_path)
                content_key = f"Desktop: {filename}"

                if content_key in existing:
                    continue

                try:
                    with open(file_path, 'rb') as f:
                        file_data = f.read()

                    # Upload to S3
                    s3_client = boto3.client(
                        's3',
                        aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
                        aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
                        region_name=os.environ.get('AWS_REGION', 'us-east-1')
                    )

                    date_folder = datetime.now().strftime('%Y-%m-%d')
                    s3_key = f"recordings/{date_folder}/desktop_{filename}"

                    s3_client.put_object(
                        Bucket=os.environ.get('AWS_BUCKET_NAME'),
                        Key=s3_key,
                        Body=file_data,
                        ContentType='audio/mpeg' if filename.endswith('.mp3') else 'audio/wav'
                    )

                    signed_url = s3_client.generate_presigned_url(
                        'get_object',
                        Params={'Bucket': os.environ.get('AWS_BUCKET_NAME'), 'Key': s3_key},
                        ExpiresIn=3600
                    )

                    # Add to inbox
                    c.execute("""INSERT INTO inbox
                                 (sender_name, sender_phone, content_type, title, content, s3_url, date_folder)
                                 VALUES (?, ?, ?, ?, ?, ?, ?)""",
                              ('Desktop Import', 'LOCAL', 'voice', content_key, content_key, signed_url, date_folder))

                    imported += 1
                    print(f"✅ Auto-imported: {filename}")

                except Exception as e:
                    print(f"❌ Import failed {filename}: {e}")

        if imported:
            conn.commit()
            print(f"📁 Auto-imported {imported} desktop files")
        conn.close()

    except Exception as e:
        print(f"❌ Auto-import error: {e}")

@app.route('/api/inbox')
def api_inbox():
    """Real-time inbox API for auto-refresh"""
    try:
        conn = sqlite3.connect('songs.db')
        c = conn.cursor()
        c.execute('''SELECT id, sender_name, sender_phone, content_type, title, content, s3_url, date_folder, created_at
                     FROM inbox ORDER BY created_at DESC''')

        items = []
        for row in c.fetchall():
            items.append({
                'id': row[0], 'sender_name': row[1], 'sender_phone': row[2], 'content_type': row[3],
                'title': row[4], 'content': row[5], 's3_url': row[6], 'date_folder': row[7], 'created_at': row[8]
            })
        conn.close()
        return jsonify({'success': True, 'items': items})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/update-title', methods=['POST'])
def update_title():
    """Update item title with save button"""
    try:
        data = request.json
        item_id = data.get('id')
        new_title = data.get('title')

        conn = sqlite3.connect('songs.db')
        c = conn.cursor()
        c.execute("UPDATE inbox SET title = ? WHERE id = ?", (new_title, item_id))
        conn.commit()
        conn.close()

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/delete-item', methods=['POST'])
def delete_item():
    """One-click delete without confirmation"""
    try:
        data = request.json
        item_id = data.get('id')

        conn = sqlite3.connect('songs.db')
        c = conn.cursor()
        c.execute("DELETE FROM inbox WHERE id = ?", (item_id,))
        conn.commit()
        conn.close()

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/send-to-phrases', methods=['POST'])
def send_to_phrases():
    """Send voice note to phrases collection"""
    try:
        data = request.json
        item_id = data.get('id')

        conn = sqlite3.connect('songs.db')
        c = conn.cursor()

        # Get original item
        c.execute("SELECT * FROM inbox WHERE id = ?", (item_id,))
        row = c.fetchone()

        if row:
            # Add to phrases with special marker
            c.execute("""INSERT INTO inbox
                         (sender_name, sender_phone, content_type, title, content, s3_url, date_folder)
                         VALUES (?, ?, ?, ?, ?, ?, ?)""",
                      ('PHRASES', row[2], row[3], f"📝 {row[4]}", row[5], row[6], row[7]))

        conn.commit()
        conn.close()

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

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

@app.route('/api/inbox/<int:item_id>', methods=['DELETE'])
def delete_inbox_item(item_id):
    """Delete an item from the inbox"""
    try:
        conn = sqlite3.connect('songs.db')
        c = conn.cursor()
        c.execute("DELETE FROM inbox WHERE id=?", (item_id,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        print(f"Delete error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/refresh-url/<int:item_id>')
def refresh_url(item_id):
    """Generate a fresh signed URL for an S3 item"""
    try:
        conn = sqlite3.connect('songs.db')
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM inbox WHERE id=?", (item_id,))
        item = c.fetchone()

        if item and item['s3_url']:
            try:
                # Extract S3 key from the stored URL
                import urllib.parse
                parsed = urllib.parse.urlparse(item['s3_url'])

                # Handle both old format and new signed URLs
                if 'amazonaws.com' in parsed.netloc:
                    # Extract key from path, removing leading slash
                    s3_key = parsed.path.lstrip('/')

                    # Remove bucket name if it's in the path (for old URLs)
                    if s3_key.startswith(f"{AWS_BUCKET_NAME}/"):
                        s3_key = s3_key[len(f"{AWS_BUCKET_NAME}/"):]
                else:
                    # If URL format is unexpected, try using content field as fallback
                    s3_key = f"recordings/{datetime.now().strftime('%Y/%m/%d')}/unknown_file.wav"

                # Generate new signed URL
                new_url = s3_client.generate_presigned_url(
                    'get_object',
                    Params={'Bucket': AWS_BUCKET_NAME, 'Key': s3_key},
                    ExpiresIn=86400  # 24 hours
                )
            except Exception as parse_error:
                print(f"URL parsing error: {parse_error}")
                return jsonify({'success': False, 'error': f'Invalid URL format: {parse_error}'})

            return jsonify({'success': True, 'url': new_url})

        return jsonify({'success': False, 'error': 'Item not found'})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/test-aws')
def test_aws():
    """Test AWS S3 connection"""
    try:
        # Test S3 connection by listing bucket
        response = s3_client.list_objects_v2(Bucket=AWS_BUCKET_NAME, MaxKeys=1)

        return jsonify({
            'success': True,
            'bucket': AWS_BUCKET_NAME,
            'accessible': True,
            'objects_found': response.get('KeyCount', 0)
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'bucket': AWS_BUCKET_NAME
        })

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
AWS_BUCKET_NAME = os.environ.get('AWS_BUCKET_NAME', 'ladyembertest1')

# S3 client
s3_client = boto3.client(
    's3',
    aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
    region_name=os.environ.get('AWS_REGION', 'us-east-1')
)


@app.route('/twilio/voice', methods=['POST'])
def handle_incoming_call():
    """Handle incoming Twilio voice calls with system check"""
    from_number = request.values.get('From', '')

    # Test system before letting user record to avoid wasted time
    try:
        aws_access_key = os.environ.get('AWS_ACCESS_KEY_ID')
        aws_secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
        aws_bucket = os.environ.get('AWS_BUCKET_NAME')
        twilio_account_sid = os.environ.get('TWILIO_ACCOUNT_SID')
        twilio_auth_token = os.environ.get('TWILIO_AUTH_TOKEN')

        if not all([aws_access_key, aws_secret_key, aws_bucket]):
            # AWS not ready - inform caller and hang up immediately
            twiml_response = '''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice">Sorry, the system is temporarily unavailable. Please try again in a few minutes. Goodbye.</Say>
    <Hangup/>
</Response>'''
            return twiml_response, 200, {'Content-Type': 'application/xml'}

        if not all([twilio_account_sid, twilio_auth_token]):
            twiml_response = '''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice">Sorry, the recording system is temporarily unavailable. Please try again in a few minutes. Goodbye.</Say>
    <Hangup/>
</Response>'''
            return twiml_response, 200, {'Content-Type': 'application/xml'}

    except Exception as e:
        # System error - don't let user waste time recording
        twiml_response = '''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice">Sorry, there's a system error. Please try again later. Goodbye.</Say>
    <Hangup/>
</Response>'''
        return twiml_response, 200, {'Content-Type': 'application/xml'}

    # System is ready - proceed with recording
    twiml_response = '''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice">Welcome to Lady Ember Studio - where inspiration becomes art. Record your creative spark after the beep. Press star when done.</Say>
    <Record
        action="https://ladyember.com/twilio/recording"
        method="POST"
        maxLength="300"
        playBeep="true"
        recordingStatusCallback="https://ladyember.com/twilio/recording-status"
        timeout="30"
        finishOnKey="*"
    />
    <Say voice="alice">Capturing your creativity... standby for magic.</Say>
    <Pause length="3"/>
    <Say voice="alice">Your inspiration has been preserved. Create something beautiful!</Say>
    <Hangup/>
</Response>'''
    
    return twiml_response, 200, {'Content-Type': 'application/xml'}

@app.route('/twilio/recording', methods=['POST'])
def handle_recording():
    """Process completed recording and save to S3"""
    try:
        # Clear any previous error
        global last_download_error
        last_download_error = None

        # Always save a record first so we can see the webhook was called
        sender_name = detect_sender_name(request.values.get('From', ''))
        date_folder = datetime.now().strftime('%Y-%m-%d')

        conn = sqlite3.connect('songs.db')
        c = conn.cursor()
        c.execute("""INSERT INTO inbox
                     (sender_name, sender_phone, content_type, title, content, s3_url, date_folder)
                     VALUES (?, ?, ?, ?, ?, ?, ?)""",
                  (sender_name, request.values.get('From', ''), 'voice',
                   f"{sender_name} - Processing Recording", "Recording webhook received - processing...", None, date_folder))
        webhook_record_id = c.lastrowid
        conn.commit()
        conn.close()

        recording_url = request.values.get('RecordingUrl', '')
        recording_sid = request.values.get('RecordingSid', '')
        call_sid = request.values.get('CallSid', '')
        from_number = request.values.get('From', '')
        recording_duration = request.values.get('RecordingDuration', '0')

        # Handle case where recording was too short or silent
        if not recording_url:
            print("⚠️  No recording URL - recording may have been too short or silent")

            # Still save a record in the inbox for tracking
            sender_name = detect_sender_name(from_number)
            date_folder = datetime.now().strftime('%Y-%m-%d')
            title = f"{sender_name} - No Recording ({datetime.now().strftime('%H:%M')})"

            conn = sqlite3.connect('songs.db')
            c = conn.cursor()
            c.execute("""INSERT INTO inbox
                         (sender_name, sender_phone, content_type, title, content, s3_url, date_folder)
                         VALUES (?, ?, ?, ?, ?, ?, ?)""",
                      (sender_name, from_number, 'voice', title, f"Call received but no recording captured (duration: {recording_duration}s)", None, date_folder))
            conn.commit()
            conn.close()

            # Return helpful TwiML
            helpful_response = '''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice">Your creative moment was too brief to capture. Call back, speak your inspiration after the beep, then press star. Create fearlessly!</Say>
    <Hangup/>
</Response>'''
            return helpful_response, 200, {'Content-Type': 'application/xml'}

        elif recording_url and recording_sid:
            print(f"✅ Both URL and SID exist - proceeding with upload")
            filename = f"call_recording_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"

            # Upload to S3 - Add .wav extension to Twilio URL for proper download
            if not recording_url.endswith('.wav'):
                download_url = recording_url + '.wav'
                print(f"📤 Using download URL: {download_url}")
            else:
                download_url = recording_url
                print(f"📤 Using original URL: {download_url}")

            print(f"📤 Attempting S3 upload...")
            s3_url = upload_to_s3(download_url, filename)
            print(f"📤 S3 upload result: {s3_url}")

            if s3_url:
                # Update the existing record with success info
                title = f"{sender_name} - Voice {datetime.now().strftime('%H:%M')}"

                conn = sqlite3.connect('songs.db')
                c = conn.cursor()

                c.execute("""UPDATE inbox
                             SET title = ?, content = ?, s3_url = ?
                             WHERE id = ?""",
                          (title, f"Voice recording - {filename}", s3_url, webhook_record_id))

                conn.commit()
                conn.close()

                print(f"🎤 Voice recording from {sender_name}: {filename} -> {s3_url}")

                # Return confirmation TwiML
                confirmation = '''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice">Recording received and saved successfully! You can view it on the website. Goodbye.</Say>
    <Hangup/>
</Response>'''
                return confirmation, 200, {'Content-Type': 'application/xml'}
            else:
                print("❌ S3 upload failed")

                # Update record with failure info
                conn = sqlite3.connect('songs.db')
                c = conn.cursor()

                # Include the actual error in the database
                error_detail = f"S3 upload failed. Error: {last_download_error if last_download_error else 'Unknown error'}"

                c.execute("""UPDATE inbox
                             SET title = ?, content = ?
                             WHERE id = ?""",
                          (f"{sender_name} - Upload Failed", error_detail, webhook_record_id))
                conn.commit()
                conn.close()

                # Get last error from database
                error_to_speak = "Unknown error occurred"
                try:
                    conn = sqlite3.connect('songs.db')
                    c = conn.cursor()
                    c.execute("SELECT content FROM inbox WHERE id = 99999")
                    result = c.fetchone()
                    if result:
                        error_to_speak = result[0]
                    conn.close()
                except:
                    pass

                error_response = f'''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice">Sorry, upload failed. Error: {error_to_speak}. Goodbye.</Say>
    <Hangup/>
</Response>'''
                return error_response, 200, {'Content-Type': 'application/xml'}

        else:
            # Case where there's a URL but no SID (unusual but possible)
            print(f"⚠️  Unusual case: recording_url='{recording_url}', recording_sid='{recording_sid}'")

            hangup = '''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice">Recording incomplete. Goodbye.</Say>
    <Hangup/>
</Response>'''
            return hangup, 200, {'Content-Type': 'application/xml'}

    except Exception as e:
        print(f"Recording error: {e}")
        traceback.print_exc()

        error_response = '''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice">Sorry, there was an error saving your recording. Goodbye.</Say>
    <Hangup/>
</Response>'''
        return error_response, 200, {'Content-Type': 'application/xml'}

@app.route('/twilio/recording-status', methods=['POST'])
def handle_recording_status():
    """Handle recording status updates and process completed recordings"""
    recording_sid = request.values.get('RecordingSid', '')
    status = request.values.get('RecordingStatus', '')
    recording_url = request.values.get('RecordingUrl', '')
    call_sid = request.values.get('CallSid', '')
    from_number = request.values.get('From', '')
    recording_duration = request.values.get('RecordingDuration', '0')

    print(f"📻 Recording {recording_sid} status: {status}")
    print(f"📻 Recording URL: {recording_url}")
    print(f"📻 Duration: {recording_duration}s")

    if status == 'completed' and recording_url and recording_sid:
        try:
            # Get sender info
            sender_name = detect_sender_name(from_number)
            date_folder = datetime.now().strftime('%Y-%m-%d')

            # Create database record first
            conn = sqlite3.connect('songs.db')
            c = conn.cursor()
            c.execute("""INSERT INTO inbox
                         (sender_name, sender_phone, content_type, title, content, s3_url, date_folder)
                         VALUES (?, ?, ?, ?, ?, ?, ?)""",
                      (sender_name, from_number, 'voice',
                       f"{sender_name} - Processing Recording", "Recording status webhook received - processing...", None, date_folder))
            webhook_record_id = c.lastrowid
            conn.commit()
            conn.close()

            print(f"✅ Created database record {webhook_record_id}")

            # Generate filename
            filename = f"call_recording_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"

            # Add .wav extension to Twilio URL for proper download
            if not recording_url.endswith('.wav'):
                download_url = recording_url + '.wav'
                print(f"📤 Using download URL: {download_url}")
            else:
                download_url = recording_url
                print(f"📤 Using original URL: {download_url}")

            print(f"📤 Attempting S3 upload...")
            s3_url = upload_to_s3(download_url, filename)
            print(f"📤 S3 upload result: {s3_url}")

            if s3_url:
                # Update the existing record with success info
                title = f"{sender_name} - Voice {datetime.now().strftime('%H:%M')}"

                conn = sqlite3.connect('songs.db')
                c = conn.cursor()

                c.execute("""UPDATE inbox
                             SET title = ?, content = ?, s3_url = ?
                             WHERE id = ?""",
                          (title, f"Voice recording - {filename}", s3_url, webhook_record_id))

                conn.commit()
                conn.close()

                print(f"🎤 Voice recording from {sender_name}: {filename} -> {s3_url}")
                return "SUCCESS: Recording uploaded to S3", 200

            else:
                # Upload failed - update record with error
                conn = sqlite3.connect('songs.db')
                c = conn.cursor()

                # Get the last error
                error_msg = last_download_error if last_download_error else "Upload failed - unknown error"

                c.execute("""UPDATE inbox
                             SET title = ?, content = ?
                             WHERE id = ?""",
                          (f"{sender_name} - Upload Failed", f"S3 upload failed. Error: {error_msg}", webhook_record_id))

                conn.commit()
                conn.close()

                print(f"❌ S3 upload failed for {sender_name}: {error_msg}")
                return f"FAILED: Upload error - {error_msg}", 200

        except Exception as e:
            print(f"❌ Recording status processing error: {e}")
            import traceback
            traceback.print_exc()
            return f"ERROR: {str(e)}", 200

    return "OK", 200

@app.route('/twilio/sms', methods=['POST'])
def handle_sms():
    """Handle incoming SMS and MMS messages with voice message support"""
    from_number = request.values.get('From', '')
    body = request.values.get('Body', '')
    num_media = int(request.values.get('NumMedia', 0))

    sender_name = detect_sender_name(from_number)
    print(f"📱 SMS/MMS from {sender_name}: {body} (Media: {num_media})")

    if num_media > 0:
        # Handle MMS with media attachments
        for i in range(num_media):
            media_url = request.values.get(f'MediaUrl{i}', '')
            media_content_type = request.values.get(f'MediaContentType{i}', '')

            # Check if it's an audio file
            if media_content_type and media_content_type.startswith('audio/'):
                try:
                    file_extension = '.m4a' if 'mp4' in media_content_type else '.wav'
                    filename = f"mms_audio_{datetime.now().strftime('%Y%m%d_%H%M%S')}{file_extension}"

                    s3_url = upload_to_s3(media_url, filename)

                    if s3_url:
                        conn = sqlite3.connect('songs.db')
                        c = conn.cursor()
                        c.execute("""INSERT INTO inbox
                                     (sender_name, sender_phone, content_type, title, content, s3_url, date_folder)
                                     VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                  (sender_name, from_number, 'voice',
                                   f"{sender_name} - Voice Message {datetime.now().strftime('%H:%M')}",
                                   f"Voice message via MMS{' - ' + body if body else ''}",
                                   s3_url, datetime.now().strftime('%Y-%m-%d')))
                        conn.commit()
                        conn.close()
                        print(f"🎤 Voice message from {sender_name}: {filename}")
                except Exception as e:
                    print(f"❌ MMS audio error: {e}")

    # Handle text part if present
    if body:
        conn = sqlite3.connect('songs.db')
        c = conn.cursor()
        c.execute("""INSERT INTO inbox
                     (sender_name, sender_phone, content_type, title, content, date_folder)
                     VALUES (?, ?, ?, ?, ?, ?)""",
                  (sender_name, from_number, 'text',
                   f"{sender_name} - Text {datetime.now().strftime('%H:%M')}",
                   body, datetime.now().strftime('%Y-%m-%d')))
        conn.commit()
        conn.close()

    return "OK", 200

@app.route('/api/analyze-audio', methods=['POST'])
def analyze_audio():
    """Analyze audio for pitch, key detection, and frequency content (Melodyne-style)"""
    try:
        import numpy as np
        import scipy.signal
        import librosa
        import tempfile
        import requests

        data = request.json
        audio_url = data.get('audio_url')
        analysis_type = data.get('analysis_type', 'full')  # 'pitch', 'key', 'full'

        if not audio_url:
            return jsonify({'success': False, 'error': 'No audio URL provided'}), 400

        # Download audio file to temporary location
        response = requests.get(audio_url, timeout=30)
        if response.status_code != 200:
            return jsonify({'success': False, 'error': 'Failed to download audio'}), 400

        with tempfile.NamedTemporaryFile(delete=False, suffix='.wav') as temp_file:
            temp_file.write(response.content)
            temp_path = temp_file.name

        try:
            # Load audio with librosa (handles various formats)
            y, sr = librosa.load(temp_path)

            results = {}

            # Pitch tracking (fundamental frequency over time)
            if analysis_type in ['pitch', 'full']:
                pitches, magnitudes = librosa.piptrack(y=y, sr=sr, threshold=0.1)

                # Extract fundamental frequency over time
                pitch_track = []
                times = librosa.frames_to_time(np.arange(pitches.shape[1]), sr=sr)

                for t in range(pitches.shape[1]):
                    index = magnitudes[:, t].argmax()
                    pitch = pitches[index, t]

                    if pitch > 0:
                        # Convert Hz to MIDI note
                        midi_note = librosa.hz_to_midi(pitch)
                        note_name = librosa.midi_to_note(midi_note)
                        pitch_track.append({
                            'time': float(times[t]),
                            'frequency': float(pitch),
                            'midi_note': float(midi_note),
                            'note_name': note_name,
                            'confidence': float(magnitudes[index, t])
                        })

                results['pitch_track'] = pitch_track

            # Key detection
            if analysis_type in ['key', 'full']:
                # Use chroma features for key detection
                chroma = librosa.feature.chroma_stft(y=y, sr=sr)
                chroma_mean = np.mean(chroma, axis=1)

                # Simple key detection using chroma vector correlation
                key_profiles = {
                    'C': [1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 0, 1],
                    'C#': [1, 1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 0],
                    'D': [0, 1, 1, 0, 1, 0, 1, 1, 0, 1, 0, 1],
                    'D#': [1, 0, 1, 1, 0, 1, 0, 1, 1, 0, 1, 0],
                    'E': [0, 1, 0, 1, 1, 0, 1, 0, 1, 1, 0, 1],
                    'F': [1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 1, 0],
                    'F#': [0, 1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 1],
                    'G': [1, 0, 1, 0, 1, 0, 1, 1, 0, 1, 0, 1],
                    'G#': [1, 1, 0, 1, 0, 1, 0, 1, 1, 0, 1, 0],
                    'A': [0, 1, 1, 0, 1, 0, 1, 0, 1, 1, 0, 1],
                    'A#': [1, 0, 1, 1, 0, 1, 0, 1, 0, 1, 1, 0],
                    'B': [0, 1, 0, 1, 1, 0, 1, 0, 1, 0, 1, 1]
                }

                correlations = {}
                for key, profile in key_profiles.items():
                    correlation = np.corrcoef(chroma_mean, profile)[0, 1]
                    correlations[key] = float(correlation) if not np.isnan(correlation) else 0

                detected_key = max(correlations, key=correlations.get)
                key_confidence = correlations[detected_key]

                results['key_detection'] = {
                    'detected_key': detected_key,
                    'confidence': key_confidence,
                    'all_correlations': correlations
                }

            # Spectral analysis
            if analysis_type in ['spectral', 'full']:
                # Get spectral features
                spectral_centroids = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
                spectral_rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr)[0]
                mfccs = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)

                results['spectral_analysis'] = {
                    'spectral_centroid_mean': float(np.mean(spectral_centroids)),
                    'spectral_rolloff_mean': float(np.mean(spectral_rolloff)),
                    'mfcc_features': mfccs.tolist(),
                    'tempo': float(librosa.beat.tempo(y=y, sr=sr)[0]),
                    'duration': float(len(y) / sr)
                }

            # Clean up temp file
            import os
            os.unlink(temp_path)

            return jsonify({
                'success': True,
                'analysis_results': results,
                'sample_rate': sr,
                'duration': float(len(y) / sr)
            })

        except Exception as analysis_error:
            import os
            os.unlink(temp_path)
            raise analysis_error

    except Exception as e:
        print(f"❌ Audio analysis error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/transpose-audio', methods=['POST'])
def transpose_audio():
    """Transpose audio to a different key (pitch shifting)"""
    try:
        import librosa
        import soundfile as sf
        import tempfile
        import requests

        data = request.json
        audio_url = data.get('audio_url')
        semitones = float(data.get('semitones', 0))  # Number of semitones to transpose

        if not audio_url:
            return jsonify({'success': False, 'error': 'No audio URL provided'}), 400

        # Download original audio
        response = requests.get(audio_url, timeout=30)
        if response.status_code != 200:
            return jsonify({'success': False, 'error': 'Failed to download audio'}), 400

        with tempfile.NamedTemporaryFile(delete=False, suffix='.wav') as temp_input:
            temp_input.write(response.content)
            input_path = temp_input.name

        # Load audio
        y, sr = librosa.load(input_path)

        # Apply pitch shifting
        y_shifted = librosa.effects.pitch_shift(y, sr=sr, n_steps=semitones)

        # Save transposed audio to temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix='.wav') as temp_output:
            output_path = temp_output.name

        sf.write(output_path, y_shifted, sr)

        # Upload transposed audio to S3
        with open(output_path, 'rb') as f:
            transposed_data = f.read()

        from datetime import datetime
        filename = f"transposed_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{semitones}st.wav"

        # Upload to S3 using existing infrastructure
        s3_client = boto3.client(
            's3',
            aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
            region_name=os.environ.get('AWS_REGION', 'us-east-1')
        )

        aws_bucket = os.environ.get('AWS_BUCKET_NAME')
        date_folder = datetime.now().strftime('%Y-%m-%d')
        s3_key = f"recordings/{date_folder}/transposed_{filename}"

        s3_client.put_object(
            Bucket=aws_bucket,
            Key=s3_key,
            Body=transposed_data,
            ContentType='audio/wav'
        )

        # Generate signed URL
        signed_url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': aws_bucket, 'Key': s3_key},
            ExpiresIn=3600
        )

        # Clean up temp files
        import os
        os.unlink(input_path)
        os.unlink(output_path)

        return jsonify({
            'success': True,
            'transposed_url': signed_url,
            'original_semitones': semitones,
            'filename': filename
        })

    except Exception as e:
        print(f"❌ Audio transpose error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/import/s3-zip', methods=['POST'])
def import_s3_zip():
    """Import audio files from S3 zip archive"""
    try:
        import zipfile
        import tempfile
        import os
        from datetime import datetime

        # Get S3 zip file path from request
        zip_key = request.json.get('zip_key', 'your-zip-file.zip')

        print(f"📦 Starting S3 zip import: {zip_key}")

        # Download zip from S3
        s3_client = boto3.client(
            's3',
            aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
            region_name=os.environ.get('AWS_REGION', 'us-east-1')
        )

        aws_bucket = os.environ.get('AWS_BUCKET_NAME')

        # Download zip to temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as temp_zip:
            print(f"📥 Downloading {zip_key} from S3...")
            s3_client.download_fileobj(aws_bucket, zip_key, temp_zip)
            temp_zip_path = temp_zip.name

        imported_count = 0
        skipped_count = 0

        # Extract and process audio files
        with zipfile.ZipFile(temp_zip_path, 'r') as zip_ref:
            for file_info in zip_ref.filelist:
                if not file_info.filename.lower().endswith(('.mp3', '.wav', '.m4a', '.flac', '.ogg')):
                    continue

                try:
                    # Extract file to temporary location
                    with zip_ref.open(file_info) as zip_file:
                        audio_data = zip_file.read()

                    # Generate new filename and S3 key
                    original_name = os.path.basename(file_info.filename)
                    date_folder = datetime.now().strftime('%Y-%m-%d')
                    s3_key = f"recordings/{date_folder}/imported_{original_name}"

                    # Upload to S3
                    s3_client.put_object(
                        Bucket=aws_bucket,
                        Key=s3_key,
                        Body=audio_data,
                        ContentType='audio/mpeg' if original_name.lower().endswith('.mp3') else 'audio/wav'
                    )

                    # Generate signed URL
                    signed_url = s3_client.generate_presigned_url(
                        'get_object',
                        Params={'Bucket': aws_bucket, 'Key': s3_key},
                        ExpiresIn=3600
                    )

                    # Create database record
                    conn = sqlite3.connect('songs.db')
                    c = conn.cursor()
                    c.execute("""INSERT INTO inbox
                                 (sender_name, sender_phone, content_type, title, content, s3_url, date_folder)
                                 VALUES (?, ?, ?, ?, ?, ?, ?)""",
                              ('Lady Ember', 'IMPORTED', 'voice',
                               f"Imported: {original_name}",
                               f"Imported from zip archive - {original_name}",
                               signed_url, date_folder))
                    conn.commit()
                    conn.close()

                    imported_count += 1
                    print(f"✅ Imported: {original_name}")

                except Exception as e:
                    print(f"❌ Failed to import {file_info.filename}: {e}")
                    skipped_count += 1
                    continue

        # Clean up temp file
        os.unlink(temp_zip_path)

        return jsonify({
            'success': True,
            'imported_count': imported_count,
            'skipped_count': skipped_count,
            'message': f'Successfully imported {imported_count} audio files'
        })

    except Exception as e:
        print(f"❌ S3 zip import error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/test/version', methods=['GET'])
def test_version():
    """Test endpoint to verify deployment version"""
    return jsonify({
        'version': '2025-09-13-v6',
        'message': 'Latest code deployed successfully',
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')
    })

@app.route('/test/upload', methods=['GET'])
def test_upload():
    """Test S3 upload with a dummy file"""
    try:
        # Test with a simple text file
        test_content = b"Test recording file"
        filename = f"test_recording_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

        # Get AWS credentials
        aws_access_key = os.environ.get('AWS_ACCESS_KEY_ID')
        aws_secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
        aws_bucket = os.environ.get('AWS_BUCKET_NAME')
        aws_region = os.environ.get('AWS_REGION', 'us-east-1')

        if not all([aws_access_key, aws_secret_key, aws_bucket]):
            return jsonify({
                'success': False,
                'error': 'Missing AWS credentials'
            })

        # Create S3 client
        s3_client = boto3.client(
            's3',
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
            region_name=aws_region
        )

        # Create S3 key with folder structure
        date_folder = datetime.now().strftime('%Y-%m-%d')
        s3_key = f"recordings/{date_folder}/{filename}"

        # Upload test file
        s3_client.put_object(
            Bucket=aws_bucket,
            Key=s3_key,
            Body=test_content,
            ContentType='text/plain'
        )

        # Generate signed URL
        signed_url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': aws_bucket, 'Key': s3_key},
            ExpiresIn=3600
        )

        return jsonify({
            'success': True,
            'message': 'Test upload successful',
            'bucket': aws_bucket,
            's3_key': s3_key,
            'signed_url': signed_url
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'error_type': type(e).__name__
        })

@app.route('/test/full-upload', methods=['GET'])
def test_full_upload():
    """Test the full upload_to_s3 function in production"""
    try:
        # Use the most recent recording SID
        account_sid = os.environ.get('TWILIO_ACCOUNT_SID', 'ACCOUNT_SID')
        recording_sid = request.args.get('recording_sid', 'RE7815a166ce8a5984b3514193572545d9')
        test_recording_url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Recordings/{recording_sid}"

        # Test the actual upload_to_s3 function
        result = upload_to_s3(test_recording_url, f"test_upload_{datetime.now().strftime('%H%M%S')}.wav")

        if result:
            return jsonify({
                'success': True,
                'message': 'Full upload successful',
                's3_url': result,
                'url_tested': test_recording_url
            })
        else:
            return jsonify({
                'success': False,
                'error': last_download_error if last_download_error else 'No error captured',
                'url_tested': test_recording_url
            })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'error_type': type(e).__name__
        })

@app.route('/test/aws', methods=['GET'])
def test_aws_connection():
    """Test endpoint to verify AWS S3 connection"""
    try:
        aws_access_key = os.environ.get('AWS_ACCESS_KEY_ID')
        aws_secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
        aws_bucket = os.environ.get('AWS_BUCKET_NAME')
        aws_region = os.environ.get('AWS_REGION', 'us-east-1')

        if not all([aws_access_key, aws_secret_key, aws_bucket]):
            return jsonify({
                'success': False,
                'error': 'Missing AWS credentials',
                'has_access_key': bool(aws_access_key),
                'has_secret_key': bool(aws_secret_key),
                'has_bucket': bool(aws_bucket)
            })

        # Test S3 connection
        s3_client = boto3.client(
            's3',
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
            region_name=aws_region
        )

        # Try to list bucket (this tests basic connectivity)
        response = s3_client.head_bucket(Bucket=aws_bucket)

        return jsonify({
            'success': True,
            'message': 'AWS S3 connection successful',
            'bucket': aws_bucket,
            'region': aws_region
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        })

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
        action="https://ladyember.com/twilio/recording" 
        method="POST"
        maxLength="300"
        playBeep="true"
        recordingStatusCallback="https://ladyember.com/twilio/recording-status"
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
        action="https://ladyember.com/twilio/recording" 
        method="POST"
        maxLength="300"
        playBeep="true"
        recordingStatusCallback="https://ladyember.com/twilio/recording-status"
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



def upload_to_s3(file_url, filename):
    """Upload a file from URL to S3 bucket with proper authentication"""
    global last_download_error
    try:
        # Get AWS credentials from environment
        aws_access_key = os.environ.get('AWS_ACCESS_KEY_ID')
        aws_secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
        aws_bucket = os.environ.get('AWS_BUCKET_NAME')
        aws_region = os.environ.get('AWS_REGION', 'us-east-1')

        if not all([aws_access_key, aws_secret_key, aws_bucket]):
            error_msg = "Missing AWS credentials"
            print(f"❌ {error_msg}")
            last_download_error = error_msg
            return None

        # Create S3 client
        s3_client = boto3.client(
            's3',
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
            region_name=aws_region
        )

        print(f"📥 Attempting to download from Twilio: {file_url}")

        # Download file from Twilio with authentication
        import urllib.request
        import urllib.parse
        import ssl

        # Extract credentials
        twilio_account_sid = os.environ.get('TWILIO_ACCOUNT_SID')
        twilio_auth_token = os.environ.get('TWILIO_AUTH_TOKEN')

        if not all([twilio_account_sid, twilio_auth_token]):
            error_msg = "Missing Twilio credentials"
            print(f"❌ {error_msg}")
            last_download_error = error_msg
            return None

        # Create SSL context for HTTPS
        ssl_context = ssl.create_default_context()

        # Create authentication handler for Twilio
        password_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        password_mgr.add_password(None, "https://api.twilio.com", twilio_account_sid, twilio_auth_token)
        auth_handler = urllib.request.HTTPBasicAuthHandler(password_mgr)
        https_handler = urllib.request.HTTPSHandler(context=ssl_context)
        opener = urllib.request.build_opener(auth_handler, https_handler)

        print(f"🔐 Attempting authenticated download from: {file_url[:80]}...")

        # Download the file with retry for 404 errors (timing issue)
        max_retries = 5  # More generous retry count
        retry_delay = 3  # Start with longer delay

        for attempt in range(max_retries):
            try:
                print(f"📥 Download attempt {attempt + 1}/{max_retries}...")
                response = opener.open(file_url, timeout=45)
                content_type = response.headers.get('Content-Type', 'unknown')
                file_data = response.read()
                print(f"✅ Downloaded {len(file_data)} bytes from Twilio (type: {content_type})")

                if len(file_data) < 1000:
                    print(f"⚠️  WARNING: File seems too small for audio: {len(file_data)} bytes")

                # Success - break out of retry loop
                break

            except urllib.error.HTTPError as http_error:
                if http_error.code == 404 and attempt < max_retries - 1:
                    # 404 error - recording might not be ready yet, wait and retry
                    print(f"⏳ Recording not ready (404), waiting {retry_delay}s before retry {attempt + 2}...")
                    import time
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                    continue
                else:
                    # Final attempt failed or non-404 error
                    raise http_error
            except Exception as download_error:
                # Other non-HTTP errors - final attempt
                if attempt == max_retries - 1:
                    # This was the last attempt, handle the error
                    error_msg = f"{type(download_error).__name__}: {str(download_error)}"
                    print(f"❌ Twilio download failed after {max_retries} attempts: {error_msg}")

                    # Set the global error variable
                    last_download_error = error_msg

                    # Store error and return None
                    raise download_error
                else:
                    # Not the last attempt, retry
                    raise download_error

    except Exception as download_error:
        error_msg = f"{type(download_error).__name__}: {str(download_error)}"
        print(f"❌ Twilio download failed: {error_msg}")

        # Set the global error variable
        last_download_error = error_msg

        # Store error in database for cross-process access
        try:
            conn = sqlite3.connect('songs.db')
            c = conn.cursor()
            c.execute("""INSERT OR REPLACE INTO inbox
                         (id, sender_name, sender_phone, content_type, title, content, s3_url, date_folder)
                         VALUES (99999, 'SYSTEM', 'ERROR', 'error', 'Last Error', ?, NULL, ?)""",
                      (error_msg, datetime.now().strftime('%Y-%m-%d')))
            conn.commit()
            conn.close()
        except:
            pass

        return None

    # Create S3 key with folder structure: recordings/YYYY-MM-DD/filename
    date_folder = datetime.now().strftime('%Y-%m-%d')
    s3_key = f"recordings/{date_folder}/{filename}"

    # Upload to S3
    try:
        print(f"📤 Uploading to S3: s3://{aws_bucket}/{s3_key}")
        s3_client.put_object(
            Bucket=aws_bucket,
            Key=s3_key,
            Body=file_data,
            ContentType='audio/wav'
        )

        # Generate signed URL for private access (expires in 1 hour)
        signed_url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': aws_bucket, 'Key': s3_key},
            ExpiresIn=3600  # 1 hour
        )

        print(f"✅ Successfully uploaded to S3: {s3_key}")
        return signed_url

    except Exception as e:
        error_msg = f"S3 upload error: {type(e).__name__}: {str(e)}"
        print(f"❌ {error_msg}")
        traceback.print_exc()

        # Save error for voice feedback
        last_download_error = error_msg

        # Save error to database for debugging
        try:
            conn = sqlite3.connect('songs.db')
            c = conn.cursor()
            c.execute("""INSERT INTO inbox
                         (sender_name, sender_phone, content_type, title, content, s3_url, date_folder)
                         VALUES (?, ?, ?, ?, ?, ?, ?)""",
                      ("System", "DEBUG", "error", "S3 Upload Error", error_msg, None, datetime.now().strftime('%Y-%m-%d')))
            conn.commit()
            conn.close()
        except:
            pass  # Don't let debug logging break the main flow

        return None

def detect_sender_name(phone_number):
    """Detect sender name from phone number - you can customize this"""
    phone_map = {
        # Team phone numbers
        '+16783614280': 'Asia',      # Asia's personal number
        '+17707582471': 'Asia',      # Twilio number (770) 758-2471
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

@app.route('/api/fix-missing-recording', methods=['POST'])
def fix_missing_recording():
    """Add the missing recording that exists in S3 to the inbox"""
    try:
        # The S3 URL that exists but isn't in the inbox
        s3_url = "https://ladyembertest1.s3.us-east-1.amazonaws.com/recordings/2025-09-13/call_recording_20250913_174113.wav"

        conn = sqlite3.connect('songs.db')
        c = conn.cursor()

        # Check if this recording already exists in the inbox
        c.execute("""SELECT id FROM inbox WHERE s3_url = ?""", (s3_url,))
        existing = c.fetchone()

        if existing:
            conn.close()
            return jsonify({
                'success': False,
                'message': f'Recording already exists in inbox with ID {existing[0]}'
            })

        # Add the missing recording to the inbox
        c.execute("""INSERT INTO inbox
                     (sender_name, sender_phone, content_type, title, content, s3_url, date_folder)
                     VALUES (?, ?, ?, ?, ?, ?, ?)""",
                  ("Asia", "+16783614280", "voice", "Asia - Voice 17:41",
                   "Voice recording - call_recording_20250913_174113.wav", s3_url, "2025-09-13"))

        new_record_id = c.lastrowid
        conn.commit()
        conn.close()

        return jsonify({
            'success': True,
            'message': f'Added missing recording as record {new_record_id}',
            's3_url': s3_url
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        })

if __name__ == '__main__':
    # Initialize database on startup (safe for production - won't drop existing data)
    init_db()
    port = int(os.environ.get('PORT', 5002))
    debug_mode = os.environ.get('FLASK_ENV') != 'production'
    print(f"Starting The Asia Project server on port {port}...")
    app.run(host='0.0.0.0', port=port, debug=debug_mode)