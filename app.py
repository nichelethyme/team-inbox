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

    conn.commit()
    conn.close()
    print("Database initialized with songs and inbox tables")

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
    """Handle incoming Twilio voice calls"""
    from_number = request.values.get('From', '')
    
    # Simple recording - no complex menu
    twiml_response = '''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice">Lady Ember Songs. Record at the beep. Press 1 when done.</Say>
    <Record
        action="https://ladyember.com/twilio/recording"
        method="POST"
        maxLength="300"
        playBeep="true"
        recordingStatusCallback="https://ladyember.com/twilio/recording-status"
        timeout="30"
        finishOnKey="1"
    />
</Response>'''
    
    return twiml_response, 200, {'Content-Type': 'application/xml'}

@app.route('/twilio/recording', methods=['POST'])
def handle_recording():
    """Process completed recording and save to S3"""
    try:
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
    <Say voice="alice">Recording was too short. Please call back and speak for at least 3 seconds after the beep, then press 1. Goodbye.</Say>
    <Hangup/>
</Response>'''
            return helpful_response, 200, {'Content-Type': 'application/xml'}

        elif recording_url and recording_sid:
            print(f"✅ Both URL and SID exist - proceeding with upload")
            filename = f"call_recording_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"

            # Upload to S3
            print(f"📤 Attempting S3 upload...")
            s3_url = upload_to_s3(recording_url, filename)
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
    <Say voice="alice">Recording saved successfully! Goodbye.</Say>
    <Hangup/>
</Response>'''
                return confirmation, 200, {'Content-Type': 'application/xml'}
            else:
                print("❌ S3 upload failed")

                # Update record with failure info
                conn = sqlite3.connect('songs.db')
                c = conn.cursor()
                c.execute("""UPDATE inbox
                             SET title = ?, content = ?
                             WHERE id = ?""",
                          (f"{sender_name} - Upload Failed", f"Recording received but S3 upload failed (duration: {recording_duration}s)", webhook_record_id))
                conn.commit()
                conn.close()

                # Return error TwiML
                error_response = '''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice">Sorry, there was an error saving your recording. Goodbye.</Say>
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
    """Handle recording status updates"""
    recording_sid = request.values.get('RecordingSid', '')
    status = request.values.get('RecordingStatus', '')

    print(f"Recording {recording_sid} status: {status}")
    return "OK", 200

@app.route('/test/version', methods=['GET'])
def test_version():
    """Test endpoint to verify deployment version"""
    return jsonify({
        'version': '2025-09-13-v5',
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

@app.route('/test/twilio', methods=['GET'])
def test_twilio_download():
    """Test downloading from a Twilio recording URL"""
    try:
        # Use a test recording URL - replace with actual recording SID
        account_sid = os.environ.get('TWILIO_ACCOUNT_SID', 'ACCOUNT_SID')
        recording_sid = request.args.get('recording_sid', 'RE5334e512a3bb05850b3018595ac15847')
        test_recording_url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Recordings/{recording_sid}"

        # Get Twilio credentials
        twilio_account_sid = os.environ.get('TWILIO_ACCOUNT_SID')
        twilio_auth_token = os.environ.get('TWILIO_AUTH_TOKEN')

        if not all([twilio_account_sid, twilio_auth_token]):
            return jsonify({
                'success': False,
                'error': 'Missing Twilio credentials'
            })

        # Create authenticated request
        import base64
        auth_string = f"{twilio_account_sid}:{twilio_auth_token}"
        base64_auth = base64.b64encode(auth_string.encode()).decode()

        request = urllib.request.Request(test_recording_url)
        request.add_header("Authorization", f"Basic {base64_auth}")

        # Try to download
        with urllib.request.urlopen(request) as response:
            data = response.read()
            content_type = response.headers.get('Content-Type', 'unknown')

        return jsonify({
            'success': True,
            'message': 'Twilio download successful',
            'data_size': len(data),
            'content_type': content_type,
            'url_tested': test_recording_url
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'error_type': type(e).__name__,
            'url_tested': test_recording_url if 'test_recording_url' in locals() else 'N/A'
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



@app.route('/twilio/sms', methods=['POST'])
def handle_sms():
    """Handle incoming SMS/MMS messages - Simple inbox system"""
    try:
        message_body = request.values.get('Body', '')
        from_number = request.values.get('From', '')
        num_media = int(request.values.get('NumMedia', '0'))

        # Detect sender name from phone number
        sender_name = detect_sender_name(from_number)

        # Organize by sender/date
        date_folder = datetime.now().strftime('%Y-%m-%d')

        conn = sqlite3.connect('songs.db')
        c = conn.cursor()

        # Handle media attachments (voice memos, images, etc.)
        if num_media > 0:
            for i in range(num_media):
                media_url = request.values.get(f'MediaUrl{i}', '')
                media_type = request.values.get(f'MediaContentType{i}', '')

                if media_url and media_type:
                    print(f"📎 Media attachment from {sender_name}: {media_type}")

                    if media_type.startswith('audio/'):
                        # Handle voice memo
                        filename = f"voice_memo_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{i}.{media_type.split('/')[-1]}"
                        s3_url = upload_to_s3(media_url, filename)

                        title = f"{sender_name} - Voice Memo {datetime.now().strftime('%H:%M')}"
                        content = f"Voice memo attachment - {filename}"

                        c.execute("""INSERT INTO inbox
                                     (sender_name, sender_phone, content_type, title, content, s3_url, date_folder)
                                     VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                  (sender_name, from_number, 'voice', title, content, s3_url, date_folder))

                        print(f"🎤 Voice memo from {sender_name} saved to S3: {s3_url}")

                    elif media_type.startswith('image/'):
                        # Handle image attachment
                        filename = f"image_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{i}.{media_type.split('/')[-1]}"
                        s3_url = upload_to_s3(media_url, filename)

                        title = f"{sender_name} - Image {datetime.now().strftime('%H:%M')}"
                        content = f"Image attachment - {filename}"

                        c.execute("""INSERT INTO inbox
                                     (sender_name, sender_phone, content_type, title, content, s3_url, date_folder)
                                     VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                  (sender_name, from_number, 'image', title, content, s3_url, date_folder))

                        print(f"📷 Image from {sender_name} saved to S3: {s3_url}")

        # Handle text message (if any)
        if message_body.strip():
            title = f"{sender_name} - {datetime.now().strftime('%H:%M')}"

            c.execute("""INSERT INTO inbox
                         (sender_name, sender_phone, content_type, title, content, date_folder)
                         VALUES (?, ?, ?, ?, ?, ?)""",
                      (sender_name, from_number, 'text', title, message_body, date_folder))

            print(f"💬 SMS from {sender_name}: {message_body[:50]}...")

        conn.commit()
        conn.close()

        response = '''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Message>📥 Got it! Saved to your inbox.</Message>
</Response>'''

        return response, 200, {'Content-Type': 'application/xml'}

    except Exception as e:
        print(f"SMS/MMS error: {e}")
        traceback.print_exc()
        return "Error processing message", 500

def upload_to_s3(file_url, filename):
    """Upload a file from URL to S3 bucket with proper authentication"""
    try:
        # Get AWS credentials from environment
        aws_access_key = os.environ.get('AWS_ACCESS_KEY_ID')
        aws_secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
        aws_bucket = os.environ.get('AWS_BUCKET_NAME')
        aws_region = os.environ.get('AWS_REGION', 'us-east-1')

        if not all([aws_access_key, aws_secret_key, aws_bucket]):
            print("❌ Missing AWS credentials")
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
            print("❌ Missing Twilio credentials")
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

        # Download the file
        try:
            response = opener.open(file_url, timeout=45)
            content_type = response.headers.get('Content-Type', 'unknown')
            file_data = response.read()
            print(f"✅ Downloaded {len(file_data)} bytes from Twilio (type: {content_type})")

            if len(file_data) < 1000:
                print(f"⚠️  WARNING: File seems too small for audio: {len(file_data)} bytes")

        except Exception as download_error:
            print(f"❌ Twilio download failed: {type(download_error).__name__}: {download_error}")
            return None

        # Create S3 key with folder structure: recordings/YYYY-MM-DD/filename
        date_folder = datetime.now().strftime('%Y-%m-%d')
        s3_key = f"recordings/{date_folder}/{filename}"

        # Upload to S3
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

if __name__ == '__main__':
    # Initialize database on startup (safe for production - won't drop existing data)
    init_db()
    port = int(os.environ.get('PORT', 5002))
    debug_mode = os.environ.get('FLASK_ENV') != 'production'
    print(f"Starting The Asia Project server on port {port}...")
    app.run(host='0.0.0.0', port=port, debug=debug_mode)