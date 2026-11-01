from flask import Flask, render_template, request, jsonify, session, g
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import os
import uuid
import datetime
import PyPDF2
import docx
import sqlite3
import json
import logging
from main import summarize_text
from videosummary import (
    extract_video_id, 
    get_transcript, 
    get_video_info, 
    generate_summary,
    format_time
)
from file_summary import UltimateSummarizer


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
app.logger = logging.getLogger(__name__)

app.secret_key = os.environ.get('SECRET_KEY', 'dev-only-change-me')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  


UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'docx', 'mp4', 'mov', 'avi'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


DATABASE = os.path.join(BASE_DIR, 'summarizeai.db')


whisper_model = None


ultimate_summarizer = UltimateSummarizer()


def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

def init_db():
    with app.app_context():
        db = get_db()
        cursor = db.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT,
                content_type TEXT,
                content TEXT,
                summary TEXT,
                video_info TEXT,
                filename TEXT,
                timestamp TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                email TEXT UNIQUE,
                password TEXT,
                token TEXT
            )
        ''')
        db.commit()


init_db()

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def extract_text_from_pdf(filepath):
    with open(filepath, 'rb') as file:
        reader = PyPDF2.PdfReader(file)
        text = ""
        for page in reader.pages:
            text += page.extract_text()
    return text

def extract_text_from_docx(filepath):
    doc = docx.Document(filepath)
    return "\n".join([para.text for para in doc.paragraphs])

def extract_text_from_video(filepath):
    global whisper_model
    if whisper_model is None:
        from faster_whisper import WhisperModel
        whisper_model = WhisperModel("tiny", device="cpu", compute_type="int8")
    segments, info = whisper_model.transcribe(filepath)
    return " ".join(seg.text.strip() for seg in segments)

def format_transcript(transcript_list):
    """Format transcript entries with timestamps"""
    formatted = []
    for entry in transcript_list:
        if isinstance(entry, dict):
            text = entry['text']
            timestamp = entry['start']
        else:
            text, timestamp = entry
        
        formatted.append(f"{format_time(timestamp)} {text}")
    
    return "\n".join(formatted)

def merge_transcript_text(transcript_list):
    """Combine transcript entries into continuous text"""
    return " ".join([entry['text'] if isinstance(entry, dict) else entry[0] for entry in transcript_list])

def get_youtube_summary(url):
    try:
        video_id = extract_video_id(url)
        if not video_id:
            return None, "Could not extract video ID from URL"
        
        video_info = get_video_info(video_id)
        if not video_info:
            return None, "Could not get video information"
        
        transcript_list = get_transcript(video_id)
        if not transcript_list:
            return None, "No transcript available for this video"
        
        formatted_transcript = format_transcript(transcript_list)
        summary = generate_summary(video_info, transcript_list)
        
        return {
            'transcript': formatted_transcript,
            'summary': summary,
            'video_info': video_info
        }, None
    except Exception as e:
        return None, str(e)

def create_session(user_id, content_type, content, summary, video_info=None, filename=None):
    session_id = str(uuid.uuid4())
    timestamp = datetime.datetime.now().isoformat()
    
    
    if isinstance(summary, dict):
        summary_json = json.dumps(summary)
    else:
        summary_json = summary
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        INSERT INTO sessions (id, user_id, content_type, content, summary, video_info, filename, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        session_id,
        user_id,
        content_type,
        content,
        summary_json,
        json.dumps(video_info) if video_info else None,
        filename,
        timestamp
    ))
    db.commit()
    
    return session_id


@app.route('/api/login', methods=['POST'])
def login():
    if not request.is_json:
        return jsonify({'success': False, 'message': 'Request must be JSON'}), 400
    
    try:
        data = request.get_json()
        username = data.get('username')
        password = data.get('password')
        
        if not username or not password:
            return jsonify({'success': False, 'message': 'Username and password are required'}), 400
        
        db = get_db()
        cursor = db.cursor()
        cursor.execute("PRAGMA table_info(users)")
        columns = [column[1] for column in cursor.fetchall()]
        if 'token' not in columns:
            cursor.execute('ALTER TABLE users ADD COLUMN token TEXT')
            db.commit()
        
        cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
        user = cursor.fetchone()
        
        if not user or not check_password_hash(user['password'], password):
            return jsonify({'success': False, 'message': 'Invalid username or password'}), 401
        
        token = str(uuid.uuid4())
        cursor.execute('UPDATE users SET token = ? WHERE username = ?', (token, username))
        db.commit()
        
        return jsonify({
            'success': True,
            'message': 'Login successful',
            'token': token,
            'username': username
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/register', methods=['POST'])
def register():
    if not request.is_json:
        return jsonify({'success': False, 'message': 'Request must be JSON'}), 400
    
    try:
        data = request.get_json()
        username = data.get('username')
        email = data.get('email')
        password = data.get('password')
        
        if not username or not email or not password:
            return jsonify({'success': False, 'message': 'All fields are required'}), 400
        
        db = get_db()
        cursor = db.cursor()
        cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
        if cursor.fetchone():
            return jsonify({'success': False, 'message': 'Username already taken'}), 400
        
        cursor.execute('''
            INSERT INTO users (username, email, password)
            VALUES (?, ?, ?)
        ''', (username, email, generate_password_hash(password)))
        db.commit()
        
        return jsonify({
            'success': True,
            'message': 'Registration successful'
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/text-summary')
def text_summary():
    return render_template('text-summary.html')

@app.route('/video-summary')
def video_summary():
    return render_template('video-summary.html')

@app.route('/file-summary')
def file_summary():
    return render_template('file-summary.html')

@app.route('/history')
def history():
    return render_template('history.html')

@app.route('/signup-login')
def signup_login():
    return render_template('SignUp_LogIn_Form.html')

@app.route('/summarize', methods=['POST'])
def summarize():
    try:
        text = request.form.get('text', '').strip()
        num_sentences = int(request.form.get('num_sentences', 2))
        
        if not text:
            return jsonify({'error': 'No text provided'}), 400
        
        
        summary = summarize_text(text, num_sentences)
        user_id = session.get('username', 'anonymous')
        session_id = create_session(user_id, 'text', text, summary)
        
        return jsonify({
            'summary': summary,
            'session_id': session_id
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
@app.route('/get-session/<session_id>')
def get_session(session_id):
    try:
        db = get_db()
        cursor = db.cursor()
        
        
        cursor.execute('SELECT * FROM sessions WHERE id = ? AND user_id = ?', 
                      (session_id, session.get('username', 'anonymous')))
        session_data = cursor.fetchone()
        
        if not session_data:
            return jsonify({'error': 'Session not found or not authorized'}), 404
            
        try:
            summary = json.loads(session_data['summary'])
        except json.JSONDecodeError:
            summary = session_data['summary']
        
       
        video_info = {}
        if session_data['video_info']:
            try:
                video_info = json.loads(session_data['video_info'])
            except:
                video_info = {'title': session_data['filename']} if session_data['filename'] else {}
        
        return jsonify({
            'success': True,
            'session_id': session_data['id'],
            'content_type': session_data['content_type'],
            'content': session_data['content'],
            'summary': summary,
            'video_info': video_info,
            'timestamp': session_data['timestamp'],
            'filename': session_data['filename']
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/get-all-sessions')
def get_all_sessions():
    user_id = session.get('username', 'anonymous')
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT * FROM sessions WHERE user_id = ? ORDER BY timestamp DESC', (user_id,))
    sessions = []
    
    for row in cursor.fetchall():
        try:
            summary = json.loads(row['summary'])
        except json.JSONDecodeError:
            summary = row['summary']
        
        sessions.append({
            'id': row['id'],
            'content_type': row['content_type'],
            'summary': summary,
            'timestamp': row['timestamp'],
            'video_info': json.loads(row['video_info']) if row['video_info'] else {},
            'filename': row['filename']
        })
    
    return jsonify({'sessions': sessions})
@app.route('/delete-session/<session_id>', methods=['DELETE'])
def delete_session(session_id):
    try:
        db = get_db()
        cursor = db.cursor()
        
       
        cursor.execute('SELECT * FROM sessions WHERE id = ? AND user_id = ?', 
                      (session_id, session.get('username', 'anonymous')))
        session_data = cursor.fetchone()
        
        if not session_data:
            return jsonify({'success': False, 'message': 'Session not found or not authorized'}), 404
            
        
        cursor.execute('DELETE FROM sessions WHERE id = ?', (session_id,))
        db.commit()
        
        return jsonify({'success': True, 'message': 'Session deleted successfully'})
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/summarize-file', methods=['POST'])
def summarize_file():
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400
            
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No selected file'}), 400
            
        if not allowed_file(file.filename):
            return jsonify({'error': 'File type not allowed'}), 400
            
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        file.save(filepath)
        
        try:
            if filename.endswith('.pdf'):
                text = extract_text_from_pdf(filepath)
            elif filename.endswith('.docx'):
                text = extract_text_from_docx(filepath)
            elif filename.endswith('.txt'):
                with open(filepath, 'r', encoding='utf-8') as f:
                    text = f.read()
            else:
                text = extract_text_from_video(filepath)
            
            summary_result = ultimate_summarizer.summarize_file(filepath)
            user_id = session.get('username', 'anonymous')
            session_id = create_session(user_id, 'file', text, summary_result, None, filename)
            
            return jsonify({
                'text': text,
                'summary': summary_result,
                'session_id': session_id,
                'filename': filename
            })
            
        except Exception as e:
            app.logger.error(f"Error processing file: {str(e)}")
            return jsonify({'error': f"Error processing file: {str(e)}"}), 500
            
        finally:
            if os.path.exists(filepath):
                os.unlink(filepath)
                
    except Exception as e:
        app.logger.error(f"Error in summarize_file: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/summarize-url', methods=['POST'])
def summarize_url():
    try:
        data = request.get_json()
        url = data.get('url', '').strip()
        
        if not url:
            return jsonify({'error': 'No URL provided'}), 400
            
        if 'youtube.com' in url or 'youtu.be' in url:
            result, error = get_youtube_summary(url)
            if error:
                return jsonify({'error': error}), 400
                
            user_id = session.get('username', 'anonymous')
            session_id = create_session(
                user_id, 
                'video', 
                result['transcript'], 
                result['summary'],
                result['video_info']
            )
            
            return jsonify({
                'transcript': result['transcript'],
                'summary': result['summary'],
                'video_info': result['video_info'],
                'session_id': session_id
            })
        else:
            return jsonify({'error': 'Only YouTube URLs are currently supported'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/process-video', methods=['POST'])
def process_video():
    try:
        youtube_url = request.form.get('youtube_url', '').strip()
        if youtube_url:
            result, error = get_youtube_summary(youtube_url)
            if error:
                return jsonify({'error': error}), 400
                
            user_id = session.get('username', 'anonymous')
            session_id = create_session(
                user_id, 
                'video', 
                result['transcript'], 
                result['summary'],
                result['video_info']
            )
            
            return jsonify({
                'transcript': result['transcript'],
                'summary': result['summary'],
                'video_title': result['video_info']['title'],
                'video_info': result['video_info'],
                'session_id': session_id
            })
        
        if 'video_file' not in request.files:
            return jsonify({'error': 'No video file uploaded'}), 400
            
        file = request.files['video_file']
        if file.filename == '':
            return jsonify({'error': 'No selected file'}), 400
            
        if not allowed_file(file.filename):
            return jsonify({'error': 'File type not allowed'}), 400
            
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        transcript = extract_text_from_video(filepath)
        summary = ultimate_summarizer.summarize_text(transcript)
        user_id = session.get('username', 'anonymous')
        session_id = create_session(user_id, 'video', transcript, summary, None, filename)
        
        os.unlink(filepath)
        
        return jsonify({
            'transcript': transcript,
            'summary': summary,
            'video_title': filename,
            'session_id': session_id
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/get-file-session/<session_id>')
def get_file_session(session_id):
    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT * FROM sessions WHERE id = ?', (session_id,))
    session_data = cursor.fetchone()
    
    if not session_data:
        return jsonify({'error': 'Session not found'}), 404
        
    try:
        summary = json.loads(session_data['summary'])
    except json.JSONDecodeError:
        summary = {
            'overview': session_data['summary'],
            'key_points': session_data['summary'].split('. ')[:3]
        }
        
    return jsonify({
        'session_info': {
            'filename': session_data['filename'],
            'timestamp': session_data['timestamp']
        },
        'summary': summary,
        'video_info': json.loads(session_data['video_info']) if session_data['video_info'] else {}
    })

if __name__ == '__main__':  
    app.run(debug=os.environ.get('FLASK_DEBUG') == '1', host='127.0.0.1', port=5000)
    