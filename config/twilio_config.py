# Twilio Configuration for The Asia Project
# This file contains all Twilio-related settings and webhook handlers

import os
from flask import request, jsonify
from twilio.rest import Client
from datetime import datetime
import json

# Twilio Configuration
TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.environ.get('TWILIO_PHONE_NUMBER')

# Initialize Twilio client
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN) if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN else None

def get_twilio_twiml_responses():
    """Returns TwiML response templates for voice calls"""

    VOICE_WELCOME = '''<?xml version="1.0" encoding="UTF-8"?>
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

    VOICE_NEW_NOTE = '''<?xml version="1.0" encoding="UTF-8"?>
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

    VOICE_PAUSE = '''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice">Recording paused. Press 2 again to resume, or other keys for menu.</Say>
    <Gather action="/twilio/menu" method="POST" numDigits="1" timeout="30">
        <Say voice="alice">Press any key.</Say>
    </Gather>
</Response>'''

    VOICE_DELETE = '''<?xml version="1.0" encoding="UTF-8"?>
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

    VOICE_SAVE_END = '''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice">Saving recording. Goodbye!</Say>
    <Hangup/>
</Response>'''

    VOICE_INVALID = '''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice">Press 1 for new note, 2 to pause, 3 to restart, or 4 to save and end.</Say>
    <Gather action="/twilio/menu" method="POST" numDigits="1" timeout="30">
        <Say voice="alice">Press any key.</Say>
    </Gather>
</Response>'''

    return {
        'welcome': VOICE_WELCOME,
        'new_note': VOICE_NEW_NOTE,
        'pause': VOICE_PAUSE,
        'delete': VOICE_DELETE,
        'save_end': VOICE_SAVE_END,
        'invalid': VOICE_INVALID
    }

def handle_voice_menu(digit):
    """Handle voice menu selections"""
    responses = get_twilio_twiml_responses()

    menu_map = {
        '1': responses['new_note'],
        '2': responses['pause'],
        '3': responses['delete'],
        '4': responses['save_end']
    }

    return menu_map.get(digit, responses['invalid'])

def send_sms_confirmation(to_number, message="Got your idea! Check the site to work with it."):
    """Send SMS confirmation back to user"""
    if twilio_client and TWILIO_PHONE_NUMBER:
        try:
            message = twilio_client.messages.create(
                body=message,
                from_=TWILIO_PHONE_NUMBER,
                to=to_number
            )
            return message.sid
        except Exception as e:
            print(f"SMS send error: {e}")
            return None
    return None

def get_sms_response_xml(message="Got your idea! Check the site to work with it."):
    """Get TwiML response for SMS"""
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Message>{message}</Message>
</Response>'''