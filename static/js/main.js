// Main JavaScript file for The Asia Project - Simplified Inbox System
// Separated from HTML for easier editing

// Global data storage
let inboxData = {};

// Tab functionality
function showTab(tabName) {
    // Hide all tabs
    document.querySelectorAll('.tab-content').forEach(tab => {
        tab.classList.remove('active');
    });
    document.querySelectorAll('.tab-button').forEach(btn => {
        btn.classList.remove('active');
    });

    // Show selected tab
    document.getElementById(tabName).classList.add('active');
    event.target.classList.add('active');

    // Load content based on tab
    if (tabName === 'inbox') {
        loadInbox();
    } else if (tabName === 'settings') {
        loadSettings();
    }
}

// Inbox Functions
function loadInbox() {
    const inboxContent = document.getElementById('inboxContent');
    inboxContent.innerHTML = '<div style="text-align: center; color: var(--gray-medium); padding: 20px;">Loading inbox...</div>';

    fetch('/api/inbox')
        .then(response => response.json())
        .then(data => {
            inboxData = data.inbox || {};
            renderInbox();
        })
        .catch(error => {
            console.error('Error loading inbox:', error);
            inboxContent.innerHTML = '<div style="text-align: center; color: var(--gray-medium); padding: 40px;">❌ Error loading inbox</div>';
        });
}

function renderInbox() {
    const inboxContent = document.getElementById('inboxContent');

    if (Object.keys(inboxData).length === 0) {
        inboxContent.innerHTML = '<div style="text-align: center; color: var(--gray-medium); padding: 40px;">📥 No messages yet. Send a text or call your Twilio number to get started!</div>';
        return;
    }

    let html = '';

    // Organize by sender
    Object.keys(inboxData).forEach(senderName => {
        const senderData = inboxData[senderName];

        html += `
            <div style="background: var(--bg-secondary); padding: 20px; margin-bottom: 20px; border: 2px solid var(--border-color);">
                <h3 style="color: var(--white); margin-bottom: 15px;">👤 ${senderName}</h3>
        `;

        // Organize by date within sender
        Object.keys(senderData).forEach(date => {
            const items = senderData[date];
            const dateFormatted = new Date(date).toLocaleDateString('en-US', {
                weekday: 'long',
                year: 'numeric',
                month: 'long',
                day: 'numeric'
            });

            html += `
                <div style="margin-bottom: 15px;">
                    <h4 style="color: var(--burgundy-light); margin-bottom: 10px; font-size: 1rem;">📅 ${dateFormatted}</h4>
                    <div style="display: grid; gap: 10px;">
            `;

            items.forEach(item => {
                const time = new Date(item.created_at).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
                const icon = item.content_type === 'voice' ? '🎤' : '💬';
                const bgColor = item.content_type === 'voice' ? 'var(--burgundy-muted)' : 'var(--bg-primary)';

                html += `
                    <div style="background: ${bgColor}; padding: 15px; border: 1px solid var(--border-color); cursor: pointer;" onclick="viewItem(${item.id})">
                        <div style="display: flex; justify-content: space-between; align-items: start;">
                            <div style="flex: 1;">
                                <div style="color: var(--white); font-weight: bold; margin-bottom: 5px;">
                                    ${icon} ${item.title}
                                </div>
                                <div style="color: var(--gray-medium); font-size: 0.9rem; margin-bottom: 8px;">
                                    ${time} • ${item.content_type === 'voice' ? 'Voice recording' : 'Text message'}
                                </div>
                                ${item.content_type === 'text' ? `
                                    <div style="color: var(--gray-light); font-size: 0.9rem; line-height: 1.4; max-height: 60px; overflow: hidden;">
                                        ${item.content.length > 100 ? item.content.substring(0, 100) + '...' : item.content}
                                    </div>
                                ` : ''}
                            </div>
                            <div style="display: flex; gap: 8px;">
                                ${item.content_type === 'voice' && item.s3_url ? `
                                    <button class="btn-small" onclick="event.stopPropagation(); playAudio('${item.s3_url}')" style="padding: 6px 12px;">▶️</button>
                                ` : ''}
                                <button class="btn-small" onclick="event.stopPropagation(); deleteItem(${item.id})" style="padding: 6px 12px; background: #dc3545;">🗑️</button>
                            </div>
                        </div>
                    </div>
                `;
            });

            html += '</div></div>';
        });

        html += '</div>';
    });

    inboxContent.innerHTML = html;
}

function viewItem(id) {
    // Find the item in our inbox data
    let foundItem = null;
    Object.values(inboxData).forEach(senderData => {
        Object.values(senderData).forEach(dateItems => {
            const item = dateItems.find(i => i.id === id);
            if (item) foundItem = item;
        });
    });

    if (foundItem) {
        if (foundItem.content_type === 'voice') {
            if (foundItem.s3_url) {
                playAudio(foundItem.s3_url);
            } else {
                alert('Voice recording not available');
            }
        } else {
            alert(`${foundItem.title}\\n\\n${foundItem.content}`);
        }
    }
}

function playAudio(url) {
    const audio = new Audio(url);
    audio.play().catch(error => {
        console.error('Audio playback error:', error);
        alert('Unable to play audio. Check S3 URL permissions.');
    });
}

function deleteItem(id) {
    if (confirm('Delete this item permanently?')) {
        fetch(`/api/inbox/${id}`, {
            method: 'DELETE'
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                alert('Item deleted!');
                loadInbox(); // Refresh the inbox
            } else {
                alert('Failed to delete: ' + (data.error || 'Unknown error'));
            }
        })
        .catch(error => {
            console.error('Delete error:', error);
            alert('Delete failed. Please try again.');
        });
    }
}

// Settings Functions
function loadSettings() {
    // Load saved settings from localStorage or server
    const savedNumber = localStorage.getItem('twilioNumber');
    const savedContacts = localStorage.getItem('teamContacts');

    if (savedNumber) {
        document.getElementById('twilioNumberInput').value = savedNumber;
        document.getElementById('twilioNumber').textContent = savedNumber;
    }

    if (savedContacts) {
        // TODO: Load team contacts into the interface
    }
}

function saveTwilioNumber() {
    const number = document.getElementById('twilioNumberInput').value;
    if (number) {
        localStorage.setItem('twilioNumber', number);
        document.getElementById('twilioNumber').textContent = number;
        alert('📱 Twilio number saved!');
    } else {
        alert('Please enter a phone number');
    }
}

function addTeamContact(button) {
    const row = button.parentElement;
    const nameInput = row.querySelector('input[placeholder*="Name"]');
    const phoneInput = row.querySelector('input[placeholder*="Phone"]');

    if (nameInput.value && phoneInput.value) {
        // Add new row
        const newRow = document.createElement('div');
        newRow.style.cssText = 'display: grid; grid-template-columns: 1fr 1fr auto; gap: 10px; margin-bottom: 10px;';
        newRow.innerHTML = `
            <input type="text" placeholder="Name (e.g., Asia)" style="padding: 10px; background: var(--bg-primary); border: 1px solid var(--border-color); color: var(--white);">
            <input type="text" placeholder="Phone (e.g., +1234567890)" style="padding: 10px; background: var(--bg-primary); border: 1px solid var(--border-color); color: var(--white);">
            <button class="btn-small" onclick="addTeamContact(this)" style="padding: 10px 15px;">➕</button>
        `;

        // Add saved contact display
        const savedContact = document.createElement('div');
        savedContact.style.cssText = 'display: grid; grid-template-columns: 1fr 1fr auto; gap: 10px; margin-bottom: 10px; background: var(--burgundy-muted); padding: 10px; border-radius: 4px;';
        savedContact.innerHTML = `
            <div style="color: var(--white); padding: 10px 0;">${nameInput.value}</div>
            <div style="color: var(--white); padding: 10px 0;">${phoneInput.value}</div>
            <button class="btn-small" onclick="removeTeamContact(this)" style="padding: 10px 15px; background: #dc3545;">❌</button>
        `;

        document.getElementById('teamContacts').insertBefore(savedContact, row);
        document.getElementById('teamContacts').appendChild(newRow);

        // Clear current row
        nameInput.value = '';
        phoneInput.value = '';
    } else {
        alert('Please enter both name and phone number');
    }
}

function removeTeamContact(button) {
    button.parentElement.remove();
}

function saveTeamContacts() {
    alert('👥 Team contacts saved!');
    // TODO: Save contacts to server or localStorage
}

// Quick Capture Functions
function loadQuickCapture() {
    console.log('Quick capture loaded');
    loadRecentCaptures();
}

function toggleRecording() {
    const recordButton = document.getElementById('recordButton');
    const recordingStatus = document.getElementById('recordingStatus');

    if (!mediaRecorder || mediaRecorder.state === 'inactive') {
        startRecording(recordButton, recordingStatus);
    } else {
        stopRecording(recordButton, recordingStatus);
    }
}

async function startRecording(button, statusDiv) {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        mediaRecorder = new MediaRecorder(stream);
        recordingChunks = [];

        mediaRecorder.ondataavailable = (event) => {
            recordingChunks.push(event.data);
        };

        mediaRecorder.onstop = () => {
            const audioBlob = new Blob(recordingChunks, { type: 'audio/wav' });
            saveVoiceRecording(audioBlob);
        };

        mediaRecorder.start();
        recordingStartTime = Date.now();

        // Update UI
        button.innerHTML = '⏹️ Stop Recording';
        button.style.background = 'var(--burgundy-primary)';
        statusDiv.style.display = 'block';

        // Start timer
        recordingTimer = setInterval(updateRecordingTimer, 1000);

    } catch (error) {
        console.error('Error accessing microphone:', error);
        alert('Unable to access microphone. Please check permissions.');
    }
}

function stopRecording(button, statusDiv) {
    if (mediaRecorder && mediaRecorder.state === 'recording') {
        mediaRecorder.stop();
        mediaRecorder.stream.getTracks().forEach(track => track.stop());

        // Update UI
        button.innerHTML = '🎙️ Start Recording';
        button.style.background = 'var(--success-green)';
        statusDiv.style.display = 'none';

        // Clear timer
        if (recordingTimer) {
            clearInterval(recordingTimer);
            recordingTimer = null;
        }
    }
}

function updateRecordingTimer() {
    const elapsed = Math.floor((Date.now() - recordingStartTime) / 1000);
    const minutes = Math.floor(elapsed / 60);
    const seconds = elapsed % 60;

    const timerElement = document.getElementById('recordingTimer');
    if (timerElement) {
        timerElement.textContent = `${minutes}:${seconds.toString().padStart(2, '0')}`;
    }
}

function saveVoiceRecording(audioBlob) {
    const titleInput = document.querySelector('#quick input[placeholder*="title"]');
    const title = titleInput ? titleInput.value || 'Voice Note' : 'Voice Note';

    // Create FormData for upload
    const formData = new FormData();
    formData.append('audio_files', audioBlob, `voice_note_${Date.now()}.wav`);
    formData.append('title', title);
    formData.append('source', 'web_recording');

    // Upload to server
    fetch('/api/upload', {
        method: 'POST',
        body: formData
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            alert('Voice note saved!');
            if (titleInput) titleInput.value = '';
            loadRecentCaptures();
        } else {
            alert('Failed to save: ' + (data.error || 'Unknown error'));
        }
    })
    .catch(error => {
        console.error('Upload error:', error);
        alert('Upload failed. Please try again.');
    });
}

function saveTextIdea() {
    const titleInput = document.querySelector('#quick input[placeholder*="title"]');
    const textArea = document.querySelector('#quick textarea[placeholder*="thoughts"]');

    const title = titleInput ? titleInput.value || 'Text Idea' : 'Text Idea';
    const content = textArea ? textArea.value : '';

    if (!content.trim()) {
        alert('Please enter some text for your idea');
        return;
    }

    const ideaData = {
        title: title,
        lyrics: content,
        notes: 'Created via Quick Capture',
        audio_files: [],
        voice_notes: [],
        source: 'web_text'
    };

    fetch('/api/save_song', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(ideaData)
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            alert('Text idea saved!');
            if (titleInput) titleInput.value = '';
            if (textArea) textArea.value = '';
            loadRecentCaptures();
        } else {
            alert('Failed to save: ' + (data.error || 'Unknown error'));
        }
    })
    .catch(error => {
        console.error('Save error:', error);
        alert('Save failed. Please try again.');
    });
}

function testTwilioCall() {
    alert('To test: Call your Twilio number and follow the voice prompts to record a message.');
}

function testTwilioSMS() {
    alert('To test: Text your Twilio number with song lyrics or ideas.');
}

function loadRecentCaptures() {
    fetch('/api/songs')
        .then(response => response.json())
        .then(data => {
            const recentCaptures = document.getElementById('recentCaptures');
            const songs = data.songs || [];
            const recent = songs.slice(0, 5); // Show last 5

            if (recent.length === 0) {
                recentCaptures.innerHTML = '<div style="text-align: center; color: var(--gray-medium); padding: 20px;">No recent captures yet.</div>';
                return;
            }

            recentCaptures.innerHTML = recent.map(song => {
                const date = new Date(song.created_at);
                const timeAgo = getTimeAgo(date);
                const sourceIcon = song.source === 'phone_call' ? '📞' :
                                 song.source === 'sms' ? '💬' :
                                 song.source === 'web_recording' ? '🎙️' : '📝';

                return `
                    <div style="background: var(--bg-primary); padding: 12px; border: 1px solid var(--border-color); cursor: pointer;" onclick="viewCapture(${song.id})">
                        <div style="display: flex; justify-content: space-between; align-items: start;">
                            <div style="flex: 1;">
                                <div style="color: var(--white); font-weight: bold; margin-bottom: 4px;">
                                    ${sourceIcon} ${song.title}
                                </div>
                                <div style="color: var(--gray-medium); font-size: 0.85rem;">
                                    ${timeAgo} • ${song.source.replace('_', ' ')}
                                </div>
                            </div>
                            <button class="btn-small" onclick="event.stopPropagation(); editSong(${song.id})" style="padding: 4px 8px; font-size: 0.8rem;">✏️</button>
                        </div>
                    </div>
                `;
            }).join('');
        })
        .catch(error => {
            console.error('Error loading recent captures:', error);
        });
}

function getTimeAgo(date) {
    const now = new Date();
    const diffMs = now - date;
    const diffMins = Math.floor(diffMs / (1000 * 60));
    const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

    if (diffMins < 1) return 'Just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    return `${diffDays}d ago`;
}

function viewCapture(id) {
    // For now, just show a preview
    const song = songsData.find(s => s.id === id);
    if (song) {
        alert(`${song.title}\n\n${song.lyrics || song.notes || 'No content'}`);
    }
}

// Songs functionality (existing code)
function loadSongs() {
    const songsList = document.getElementById('songs-list');
    songsList.innerHTML = '<div style="text-align: center; color: var(--gray-medium); padding: 40px;">No songs yet. Click "+ New Song" to get started!</div>';

    fetch('/api/songs')
        .then(response => response.json())
        .then(data => {
            songsData = data.songs || [];
            if (songsData.length > 0) {
                songsList.innerHTML = '';
                songsData.forEach((song, index) => {
                    const songCard = createSongCard(song, index + 1);
                    songsList.appendChild(songCard);
                });
            }
        })
        .catch(error => {
            console.error('Error loading songs:', error);
        });
}

function createSongCard(song, number) {
    const card = document.createElement('div');
    card.className = 'song-card';

    const dateObj = new Date(song.created_at);
    const date = dateObj.toLocaleDateString();
    const time = dateObj.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
    const sourceIcon = song.source === 'phone_call' ? '📞' :
                      song.source === 'sms' ? '💬' :
                      song.source === 'web_recording' ? '🎙️' : '📝';

    card.innerHTML = `
        <div class="song-info" onclick="editSong(${song.id})" style="cursor: pointer;">
            <div class="song-header">
                <div>
                    <div class="song-title">${number}. "${song.title || 'Untitled'}" ${sourceIcon}</div>
                    <div style="color: var(--gray-medium); font-size: 0.85rem; margin-top: 4px; font-weight: normal;">
                        📅 ${date} ${time}
                    </div>
                </div>
                <div style="margin-top: 8px; display: flex; gap: 10px; justify-content: flex-end;">
                    <button class="play-btn" onclick="event.stopPropagation(); playSong(${song.id})" title="Play all clips">▶</button>
                    <button class="btn-small" onclick="event.stopPropagation(); editSong(${song.id})">✏️ Edit</button>
                </div>
            </div>
        </div>
    `;

    return card;
}

function playSong(id) {
    alert(`Playing song ${id} - all audio clips in sequence`);
}

function editSong(id) {
    const song = songsData.find(s => s.id == id);
    if (song) {
        showEditPage(song);
    } else {
        alert('Song not found');
    }
}

// Additional existing functions would go here...
function showEditPage(song) {
    alert('Edit page functionality - implement as needed');
}

function openNewSongEditor() {
    const newSong = {
        id: null,
        title: '',
        author: 'Asia',
        audio_files: ''
    };
    showEditPage(newSong);
}

function loadBulkFiles() {
    console.log('Bulk files loaded');
}

function loadMoodBoard() {
    console.log('Mood board loaded');
}

// Initialize
document.addEventListener('DOMContentLoaded', function() {
    // Set Quick as default tab
    showTab('quick');
});