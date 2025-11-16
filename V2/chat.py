# chat.py
# Fixed: Chat input fixed at bottom, chat history retained between navigation
from flask import Flask, render_template_string, request, jsonify, g
import sqlite3
from difflib import get_close_matches, SequenceMatcher
import random
import re
import os
import threading

DB_FILE = 'knowledge_base.db'
app = Flask(__name__)

# Thread lock for database operations
db_lock = threading.Lock()

# -------------------- Normalization -------------------- #
_punct_re = re.compile(r'[^\w\s]', flags=re.UNICODE)
_space_re = re.compile(r'\s+', flags=re.UNICODE)

def normalize_text(text):
    if text is None:
        return ''
    text = text.lower()
    text = _punct_re.sub('', text)
    text = _space_re.sub(' ', text).strip()
    return text

# -------------------- Input Validation -------------------- #
def sanitize_text(text):
    """Sanitize text to prevent XSS and SQL injection"""
    if text is None:
        return ''
    # Remove potentially dangerous characters
    text = re.sub(r'[<>&\"\']', '', str(text))
    return text.strip()

def validate_inputs(inputs):
    """Validate and sanitize input list"""
    if not inputs:
        return []
    validated = []
    for item in inputs:
        if item is not None:
            sanitized = sanitize_text(item)
            if sanitized:
                validated.append(sanitized)
    return validated

# -------------------- Paragraph & sentence helpers -------------------- #
def split_paragraphs(text):
    if not text:
        return []
    parts = re.split(r'\r?\n\s*\r?\n', text.strip())
    parts = [p.rstrip() for p in parts if p.strip()]
    return parts

_sentence_split_re = re.compile(r'(?<=[\.\?\!\;\:])\s+')

def split_into_sentences(text):
    if not text or not re.search(r'[\.\?\!\;\:]', text):
        return [text.strip()]
    parts = [p.strip() for p in _sentence_split_re.split(text) if p.strip()]
    return parts

# -------------------- DB connection (thread-safe) -------------------- #
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_FILE)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

# -------------------- Schema & init -------------------- #
def init_db():
    with db_lock:
        db = get_db()
        cur = db.cursor()

        cur.execute('''
            CREATE TABLE IF NOT EXISTS groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT UNIQUE,
                question_norm TEXT,
                group_id INTEGER
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS answers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER,
                answer TEXT,
                UNIQUE(group_id, answer)
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_questions_norm ON questions(question_norm)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_answers_gid ON answers(group_id)')
        db.commit()

# -------------------- Utilities -------------------- #
def similarity(a, b):
    """Return similarity score between 0.0 and 1.0 using SequenceMatcher on normalized strings."""
    if not a and not b:
        return 1.0
    a_n = normalize_text(a or '')
    b_n = normalize_text(b or '')
    if not a_n or not b_n:
        return 0.0
    return SequenceMatcher(None, a_n, b_n).ratio()

# -------------------- Group CRUD -------------------- #
def _ensure_inputs_list(inputs):
    if inputs is None:
        return []
    if isinstance(inputs, list):
        return [i.rstrip() for i in inputs if (i is not None and str(i).strip())]
    return split_paragraphs(inputs)

def _ensure_outputs_list(outputs):
    if outputs is None:
        return []
    if isinstance(outputs, list):
        return [o.rstrip() for o in outputs if (o is not None and str(o).strip())]
    return split_paragraphs(outputs)

def create_group(name, inputs_raw, outputs_raw):
    with db_lock:
        db = get_db()
        try:
            # Start transaction
            db.execute('BEGIN TRANSACTION')
            
            # Validate and sanitize inputs
            sanitized_name = sanitize_text(name) if name else None
            inputs_list = validate_inputs(_ensure_inputs_list(inputs_raw))
            outputs_list = validate_inputs(_ensure_outputs_list(outputs_raw))
            
            cur = db.execute('INSERT INTO groups (name) VALUES (?)', (sanitized_name,))
            gid = cur.lastrowid

            for para in inputs_list:
                sentences = split_into_sentences(para)
                if len(sentences) == 1:
                    q = sentences[0].strip()
                    if q:
                        db.execute(
                            'INSERT OR IGNORE INTO questions (question, question_norm, group_id) VALUES (?, ?, ?)',
                            (q, normalize_text(q), gid)
                        )
                else:
                    for s in sentences:
                        s = s.strip()
                        if s:
                            db.execute(
                                'INSERT OR IGNORE INTO questions (question, question_norm, group_id) VALUES (?, ?, ?)',
                                (s, normalize_text(s), gid)
                            )

            for out in outputs_list:
                if out.strip():
                    db.execute('INSERT OR IGNORE INTO answers (group_id, answer) VALUES (?, ?)', (gid, out))
            
            # Commit transaction
            db.commit()
            return gid
        except Exception as e:
            db.rollback()
            raise e

def update_group(gid, name, inputs_raw, outputs_raw):
    with db_lock:
        db = get_db()
        try:
            db.execute('BEGIN TRANSACTION')
            
            # Validate and sanitize
            sanitized_name = sanitize_text(name) if name else None
            inputs_list = validate_inputs(_ensure_inputs_list(inputs_raw))
            outputs_list = validate_inputs(_ensure_outputs_list(outputs_raw))
            
            db.execute('UPDATE groups SET name=? WHERE id=?', (sanitized_name, gid))
            db.execute('DELETE FROM questions WHERE group_id=?', (gid,))
            
            for para in inputs_list:
                sentences = split_into_sentences(para)
                if len(sentences) == 1:
                    q = sentences[0].strip()
                    if q:
                        db.execute(
                            'INSERT OR IGNORE INTO questions (question, question_norm, group_id) VALUES (?, ?, ?)',
                            (q, normalize_text(q), gid)
                        )
                else:
                    for s in sentences:
                        s = s.strip()
                        if s:
                            db.execute(
                                'INSERT OR IGNORE INTO questions (question, question_norm, group_id) VALUES (?, ?, ?)',
                                (s, normalize_text(s), gid)
                            )
            
            db.execute('DELETE FROM answers WHERE group_id=?', (gid,))
            for out in outputs_list:
                if out.strip():
                    db.execute('INSERT OR IGNORE INTO answers (group_id, answer) VALUES (?, ?)', (gid, out))
            
            db.commit()
        except Exception as e:
            db.rollback()
            raise e

def delete_group(gid):
    with db_lock:
        db = get_db()
        db.execute('DELETE FROM answers WHERE group_id=?', (gid,))
        db.execute('DELETE FROM questions WHERE group_id=?', (gid,))
        db.execute('DELETE FROM groups WHERE id=?', (gid,))
        db.commit()

def get_all_groups():
    with db_lock:
        db = get_db()
        groups = db.execute('SELECT id, name FROM groups ORDER BY id DESC').fetchall()
        out = []
        for g in groups:
            q_rows = db.execute('SELECT id, question FROM questions WHERE group_id=? ORDER BY id', (g['id'],)).fetchall()
            a_rows = db.execute('SELECT id, answer FROM answers WHERE group_id=? ORDER BY id', (g['id'],)).fetchall()
            out.append({
                'id': g['id'],
                'name': g['name'],
                'inputs': [q['question'] for q in q_rows],
                'input_ids': [q['id'] for q in q_rows],
                'outputs': [a['answer'] for a in a_rows],
                'output_ids': [a['id'] for a in a_rows],
            })
        return out

def get_group_by_id(gid):
    with db_lock:
        db = get_db()
        g = db.execute('SELECT id, name FROM groups WHERE id=?', (gid,)).fetchone()
        if not g:
            return None
        q_rows = db.execute('SELECT id, question FROM questions WHERE group_id=? ORDER BY id', (g['id'],)).fetchall()
        a_rows = db.execute('SELECT id, answer FROM answers WHERE group_id=? ORDER BY id', (g['id'],)).fetchall()
        return {
            'id': g['id'],
            'name': g['name'],
            'inputs': [q['question'] for q in q_rows],
            'input_ids': [q['id'] for q in q_rows],
            'outputs': [a['answer'] for a in a_rows],
            'output_ids': [a['id'] for a in a_rows],
        }

# -------------------- Matching logic -------------------- #
def find_best_matches_for_message(user_message):
    if not user_message:
        return []

    paragraphs = split_paragraphs(user_message)
    pieces = []
    for para in paragraphs:
        sents = split_into_sentences(para)
        for s in sents:
            t = s.strip()
            if t:
                pieces.append(t)

    with db_lock:
        db = get_db()
        rows = db.execute('SELECT question_norm, question, group_id FROM questions WHERE question_norm IS NOT NULL').fetchall()
        norm_map = {}
        for r in rows:
            norm = r['question_norm']
            if not norm:
                continue
            if norm not in norm_map:
                norm_map[norm] = (r['group_id'], r['question'])
        norm_list = list(norm_map.keys())

    results = []
    for piece in pieces:
        user_norm = normalize_text(piece)
        if not norm_list:
            results.append((piece, None, None))
            continue
        best = get_close_matches(user_norm, norm_list, n=1, cutoff=0.5)
        if best:
            gid, orig_q = norm_map[best[0]]
            results.append((piece, gid, orig_q))
        else:
            results.append((piece, None, None))
    return results

def get_random_answer_for_group(gid):
    """
    Return a single random answer string for the group, or None if no answers exist.
    This ensures the bot picks one reply per matched group instead of returning all outputs.
    """
    with db_lock:
        db = get_db()
        rows = db.execute('SELECT answer FROM answers WHERE group_id=?', (gid,)).fetchall()
        answers = [r['answer'] for r in rows]
        if not answers:
            return None
        return random.choice(answers)

# -------------------- Routes: Chat -------------------- #
@app.route('/')
def chat_page():
    return render_template_string(TPL_CHAT)

@app.route('/api/chat', methods=['POST'])
def api_chat():
    data = request.get_json(silent=True) or {}
    msg = (data.get('message') or '').strip()
    # Handle whitespace-only messages consistently
    if not msg or not re.search(r'\S', msg):
        return jsonify({'ok': False, 'error': 'Empty message'}), 400

    matches = find_best_matches_for_message(msg)
    replies = []
    for piece, gid, matched_q in matches:
        if gid:
            answer = get_random_answer_for_group(gid)
            if answer:
                replies.append(answer)
            else:
                replies.append("I know that question but don't have answers yet.")
        else:
            replies.append("I don't know that yet. Add it in Q&A.")
    # Keep order of pieces; separate multiple piece replies with two newlines
    final_reply = '\n\n'.join(replies)
    return jsonify({'ok': True, 'reply': final_reply, 'pieces': [{'text':p,'matched':bool(m)} for p,m,_ in matches]})

# -------------------- Manage: fuzzy filtering on server -------------------- #
@app.route('/manage')
def manage_page():
    return render_template_string(TPL_MANAGE)

@app.route('/api/groups', methods=['GET', 'POST'])
def api_groups():
    if request.method == 'POST':
        body = request.get_json(silent=True) or {}
        name = (body.get('name') or '').strip()
        inputs = body.get('inputs')
        outputs = body.get('outputs')
        
        # Validate required fields
        if not inputs and not outputs:
            return jsonify({'ok': False, 'error': 'At least one input or output is required'}), 400
            
        try:
            gid = create_group(name, inputs, outputs)
            group = get_group_by_id(gid)
            return jsonify({'ok': True, 'group': group})
        except Exception as e:
            return jsonify({'ok': False, 'error': 'Database error'}), 500

    # GET -> support fuzzy filter and scope
    q = (request.args.get('filter') or '').strip()
    scope = (request.args.get('scope') or 'group').lower()
    groups = get_all_groups()
    if not q:
        return jsonify({'ok': True, 'groups': groups})

    # Consistent similarity threshold
    THRESHOLD = 0.4
    scored = []
    for g in groups:
        best_score = 0.0
        if scope == 'group':
            best_score = max(best_score, similarity(q, g.get('name') or ''))
        elif scope == 'input':
            for inp in g.get('inputs', []):
                best_score = max(best_score, similarity(q, inp))
        elif scope == 'output':
            for out in g.get('outputs', []):
                best_score = max(best_score, similarity(q, out))
        else:  # all
            best_score = max(best_score, similarity(q, g.get('name') or ''))
            for inp in g.get('inputs', []):
                best_score = max(best_score, similarity(q, inp))
            for out in g.get('outputs', []):
                best_score = max(best_score, similarity(q, out))
        scored.append((best_score, g))

    filtered = []
    for score, g in sorted(scored, key=lambda x: x[0], reverse=True):
        if score >= THRESHOLD:
            filtered.append(g)
    return jsonify({'ok': True, 'groups': filtered})

@app.route('/api/groups/<int:gid>', methods=['PATCH', 'DELETE'])
def api_group_item(gid):
    if request.method == 'PATCH':
        body = request.get_json(silent=True) or {}
        name = (body.get('name') or '').strip()
        inputs = body.get('inputs')
        outputs = body.get('outputs')
        try:
            update_group(gid, name, inputs, outputs)
            return jsonify({'ok': True})
        except Exception as e:
            return jsonify({'ok': False, 'error': 'Database error'}), 500
    try:
        delete_group(gid)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': 'Database error'}), 500

# -------------------- Templates (chat + manage) -------------------- #
# Chat template: Fixed input box at bottom and chat history retention
TPL_CHAT = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Chat</title>
<style>
  :root{--bg:#0b0b0f;--panel:#0b0b0f;--text:#e6e6ea;--muted:#9aa0a6;--bubble-me:#0a84ff;--bubble-bot:#2c2c33;--input-bg: rgba(255,255,255,0.03);--input-border: rgba(255,255,255,0.06);}
  :root.light{--bg:#f5f5f7;--panel:#ffffffcc;--text:#111;--muted:#666;--bubble-me:#0a84ff;--bubble-bot:#e9e9ee;--input-bg:#fff;--input-border:#d1d1d6}
  *{box-sizing:border-box}html,body{height:100%}body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;background:var(--bg, #0b0b0f);color:var(--text, #e6e6ea);height:100dvh;display:flex;flex-direction:column;overflow:hidden;}
  header{padding:12px 16px;background:var(--panel, #0b0b0f);backdrop-filter: blur(12px) saturate(150%);display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid rgba(255,255,255,0.03);flex-shrink:0;position:sticky;top:0;z-index:100;}
  header h1{font-size:17px;margin:0;font-weight:600}.controls{display:flex;gap:8px;align-items:center}.link,.btn{background:none;border:none;padding:6px 10px;border-radius:12px;color:var(--bubble-me, #0a84ff);font-weight:600;text-decoration:none}.btn{background:transparent;cursor:pointer}#messagesContainer{flex:1;overflow:hidden;display:flex;flex-direction:column;position:relative;}#messages{flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:10px;}.msgRow{display:flex}.msgRow.me{justify-content:flex-end}.msgRow.bot{justify-content:flex-start}.bubble{display:inline-block;max-width:78%;padding:10px 12px;border-radius:18px;line-height:1.35;word-break:break-word;white-space:pre-wrap;box-shadow:0 1px 0 rgba(0,0,0,0.12) inset;font-family:inherit}.me .bubble{background:var(--bubble-me, #0a84ff);color:white;border-bottom-right-radius:6px;border-bottom-left-radius:18px}.bot .bubble{background:var(--bubble-bot, #2c2c33);color:var(--text, #e6e6ea);border-bottom-left-radius:6px;border-bottom-right-radius:18px}.typing-bubble{display:inline-block;padding:8px 12px;border-radius:18px;background:var(--bubble-bot, #2c2c33);color:var(--text, #e6e6ea)}.typing-dots{display:inline-block;width:32px;text-align:center}.typing-dots span{display:inline-block;width:6px;height:6px;margin:0 2px;border-radius:50%;background:var(--muted, #9aa0a6);opacity:0.8;transform:translateY(0);animation:blink 1s infinite}.typing-dots span:nth-child(2){animation-delay:0.12s}.typing-dots span:nth-child(3){animation-delay:0.24s}@keyframes blink{0%{opacity:0.15;transform:translateY(0)}50%{opacity:0.95;transform:translateY(-4px)}100%{opacity:0.15;transform:translateY(0)}}#composerWrap{position:fixed;bottom:0;left:0;right:0;padding:10px;border-top:1px solid rgba(255,255,255,0.03);background:var(--panel, #0b0b0f);display:flex;align-items:flex-end;gap:8px;flex-shrink:0;z-index:100;}#composer{display:flex;flex:1;gap:8px;align-items:flex-end}textarea#msg{flex:1;min-height:44px;max-height:180px;padding:10px 12px;border-radius:18px;border:1px solid var(--input-border, rgba(255,255,255,0.06));background:var(--input-bg, rgba(255,255,255,0.03));color:var(--text, #e6e6ea);outline:none;resize:none;line-height:1.2}button.send{border:none;border-radius:18px;padding:10px 14px;background:var(--bubble-me, #0a84ff);color:white;font-weight:600;cursor:pointer}.disabled{opacity:0.5;pointer-events:none}#offlineBanner{display:none;background:#ff3b30;color:white;padding:6px 12px;border-radius:10px;margin:8px;text-align:center;font-weight:600}.smallStatus{font-size:12px;color:var(--muted, #9aa0a6);margin-left:6px}textarea:focus,button:focus{outline:3px solid rgba(10,132,255,0.12);outline-offset:2px}@supports(padding:max(0px)){header{padding-top:max(12px, env(safe-area-inset-top));padding-left:max(16px, env(safe-area-inset-left));padding-right:max(16px, env(safe-area-inset-right));padding-bottom:max(12px, env(safe-area-inset-bottom))}#composerWrap{padding-bottom:max(10px, env(safe-area-inset-bottom));padding-left:max(10px, env(safe-area-inset-left));padding-right:max(10px, env(safe-area-inset-right))}}
  /* Ensure messages don't go behind the fixed input */
  #messages { padding-bottom: 80px; }
</style>
</head>
<body>
  <header role="banner">
    <h1 id="page-title">Messages</h1>
    <div class="controls">
      <button id="darkToggle" class="btn" title="Toggle dark/light mode" aria-label="Toggle theme">🌙</button>
      <a class="link" href="/manage" aria-label="Manage Q&A">Q&A</a>
      <span id="status" class="smallStatus" aria-live="polite"></span>
    </div>
  </header>

  <div id="messagesContainer">
    <div id="messages" aria-live="polite" role="main"></div>
    <div id="offlineBanner" role="alert">You appear to be offline — messages won't be sent.</div>
  </div>

  <div id="composerWrap">
    <form id="composer" autocomplete="off" style="display:flex;flex:1;gap:8px;align-items:flex-end">
      <textarea id="msg" inputmode="text" placeholder="Message (Shift+Enter newline, Enter send)" aria-label="Type your message"></textarea>
      <button type="submit" class="send" id="sendBtn" aria-label="Send message">Send</button>
    </form>
  </div>

<script>
const root = document.documentElement;
const darkToggle = document.getElementById('darkToggle');
function applyTheme(theme){ if(theme === 'light'){ document.documentElement.classList.add('light'); darkToggle.textContent='🌞'; darkToggle.setAttribute('aria-label', 'Switch to dark mode'); } else { document.documentElement.classList.remove('light'); darkToggle.textContent='🌙'; darkToggle.setAttribute('aria-label', 'Switch to light mode'); } localStorage.setItem('chat_theme', theme); }
const saved = localStorage.getItem('chat_theme') || 'dark'; applyTheme(saved);
darkToggle.addEventListener('click', ()=> applyTheme(document.documentElement.classList.contains('light') ? 'dark' : 'light'));

const messages = document.getElementById('messages');
const form = document.getElementById('composer');
const input = document.getElementById('msg');
const sendBtn = document.getElementById('sendBtn');
const offlineBanner = document.getElementById('offlineBanner');
const statusEl = document.getElementById('status');

let typingNode = null;
let isSending = false;
let viewportHandler = null;

// Chat history storage
const CHAT_HISTORY_KEY = 'chat_history';

function saveChatHistory() {
    const messagesHtml = messages.innerHTML;
    const chatData = {
        html: messagesHtml,
        timestamp: Date.now()
    };
    localStorage.setItem(CHAT_HISTORY_KEY, JSON.stringify(chatData));
}

function loadChatHistory() {
    try {
        const saved = localStorage.getItem(CHAT_HISTORY_KEY);
        if (saved) {
            const chatData = JSON.parse(saved);
            // Only load if less than 24 hours old
            if (Date.now() - chatData.timestamp < 24 * 60 * 60 * 1000) {
                messages.innerHTML = chatData.html;
                messages.scrollTop = messages.scrollHeight;
                return true;
            }
        }
    } catch (e) {
        console.error('Failed to load chat history:', e);
    }
    return false;
}

function clearChatHistory() {
    localStorage.removeItem(CHAT_HISTORY_KEY);
    messages.innerHTML = '';
}

function addBubble(text, who){
  const wrap = document.createElement('div');
  wrap.className = 'msgRow ' + (who === 'me' ? 'me' : 'bot');
  wrap.setAttribute('role', 'article');
  const b = document.createElement('div');
  b.className = 'bubble';
  b.textContent = text;
  wrap.appendChild(b);
  messages.appendChild(wrap);
  messages.scrollTop = messages.scrollHeight;
  saveChatHistory(); // Save after adding any message
  return wrap;
}

function showTypingIndicator(){
  if(typingNode) return;
  const wrap = document.createElement('div');
  wrap.className = 'msgRow bot typingRow';
  const b = document.createElement('div');
  b.className = 'typing-bubble';
  b.setAttribute('aria-label', 'AI is typing');
  const dots = document.createElement('span');
  dots.className = 'typing-dots';
  dots.innerHTML = '<span></span><span></span><span></span>';
  b.appendChild(dots);
  wrap.appendChild(b);
  messages.appendChild(wrap);
  messages.scrollTop = messages.scrollHeight;
  typingNode = wrap;
}

function hideTypingIndicator(){
  if(!typingNode) return;
  typingNode.remove();
  typingNode = null;
}

function setStatus(text){ statusEl.textContent = text || ''; }

function updateOnlineStatus(){
  if(navigator.onLine){
    offlineBanner.style.display = 'none';
    setStatus('');
  } else {
    offlineBanner.style.display = '';
    setStatus('offline');
  }
}

// Mobile keyboard handling with cleanup
function setupViewportHandler() {
  if (window.visualViewport) {
    // Remove existing handler if any
    if (viewportHandler) {
      window.visualViewport.removeEventListener('resize', viewportHandler);
    }
    
    viewportHandler = () => {
      const vh = window.visualViewport.height;
      const diff = window.innerHeight - vh;
      const composerHeight = document.getElementById('composerWrap').offsetHeight;
      messages.style.paddingBottom = (composerHeight + 20) + 'px';
      messages.scrollTop = messages.scrollHeight;
    };
    
    window.visualViewport.addEventListener('resize', viewportHandler);
  }
}

function cleanupViewportHandler() {
  if (viewportHandler && window.visualViewport) {
    window.visualViewport.removeEventListener('resize', viewportHandler);
    viewportHandler = null;
  }
}

window.addEventListener('online', updateOnlineStatus);
window.addEventListener('offline', updateOnlineStatus);
window.addEventListener('beforeunload', cleanupViewportHandler);
window.addEventListener('pagehide', saveChatHistory); // Save when leaving page
updateOnlineStatus();

input.addEventListener('keydown', function(e){
  if(e.key === 'Enter' && !e.shiftKey){
    e.preventDefault();
    if (!isSending) {
      form.requestSubmit();
    }
  }
});

// Focus behavior ensures composer & last message visible
input.addEventListener('focus', () => {
  setTimeout(()=> { messages.scrollTop = messages.scrollHeight; }, 120);
});

// Initialize viewport handler and set proper padding
setupViewportHandler();
// Set initial padding for fixed composer
setTimeout(() => {
    const composerHeight = document.getElementById('composerWrap').offsetHeight;
    messages.style.paddingBottom = (composerHeight + 20) + 'px';
}, 100);

let abortController = null;

async function sendMessage(text){
  if(!text || isSending) return;
  if(!navigator.onLine){
    addBubble("Unable to send — you are offline.", 'bot');
    return;
  }
  
  isSending = true;
  sendBtn.disabled = true;
  showTypingIndicator();
  setStatus('thinking…');

  // Cancel previous request if any
  if (abortController) {
    abortController.abort();
  }
  abortController = new AbortController();

  try {
    const timeout = setTimeout(()=> abortController.abort(), 30000);
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({message: text}),
      signal: abortController.signal
    });
    clearTimeout(timeout);
    
    if(!res.ok) throw new Error('server error');
    const data = await res.json();
    hideTypingIndicator();
    setStatus('');
    
    if(data && data.reply){
      addBubble(data.reply, 'bot');
    } else {
      addBubble("No reply.", 'bot');
    }
  } catch (err) {
    hideTypingIndicator();
    if (err.name !== 'AbortError') {
      setStatus('error');
      const wrap = document.createElement('div');
      wrap.className = 'msgRow bot';
      const b = document.createElement('div');
      b.className = 'bubble';
      b.textContent = 'Unable to reach AI — check your network or server.';
      const retry = document.createElement('button');
      retry.textContent = 'Retry';
      retry.style.marginLeft = '8px';
      retry.style.border = 'none';
      retry.style.background = 'transparent';
      retry.style.color = 'var(--bubble-me)';
      retry.style.fontWeight = '600';
      retry.addEventListener('click', ()=> {
        wrap.remove();
        sendMessage(text);
      });
      b.appendChild(retry);
      wrap.appendChild(b);
      messages.appendChild(wrap);
      messages.scrollTop = messages.scrollHeight;
      saveChatHistory();
    }
  } finally {
    isSending = false;
    sendBtn.disabled = false;
    hideTypingIndicator();
    abortController = null;
  }
}

form.addEventListener('submit', function(e){
  e.preventDefault();
  const text = (input.value || '').trim();
  if(!text || isSending) return;
  addBubble(text, 'me');
  input.value = '';
  sendMessage(text);
});

// Load chat history on page load
window.addEventListener('load', () => {
    loadChatHistory();
    updateOnlineStatus();
    input.focus();
});

// Save chat history when page is about to be unloaded
window.addEventListener('beforeunload', saveChatHistory);
</script>
</body>
</html>
"""

# Manage template: Added confirmation for unsaved changes
TPL_MANAGE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover" />
<title>Manage Q&A</title>
<style>
  :root{
    --bg:#0b0b0f;--panel:#0b0b0f;--text:#e6e6ea;--muted:#9aa0a6;--primary:#0a84ff;--danger:#ff3b30;
    --input-bg: rgba(255,255,255,0.06); --input-border: rgba(255,255,255,0.12);
  }
  :root.light{
    --bg:#f5f5f7;--panel:#ffffffcc;--text:#111;--muted:#666;--primary:#0a84ff;--danger:#ff3b30;
    --input-bg: #ffffff; --input-border: #d1d1d6;
  }
  *{box-sizing:border-box}
  body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;background:var(--bg, #0b0b0f);color:var(--text, #e6e6ea);min-height:100dvh}
  header{padding:12px 16px;background:var(--panel, #0b0b0f);display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid rgba(255,255,255,0.03)}
  a{color:var(--primary, #0a84ff);text-decoration:none;font-weight:600}
  main{padding:12px;padding-bottom:80px}
  .card{background:var(--panel, #0b0b0f);border:1px solid rgba(255,255,255,0.03);border-radius:12px;padding:12px;margin-bottom:12px}
  input,textarea,select{width:100%;padding:10px;border-radius:10px;border:1px solid var(--input-border, rgba(255,255,255,0.12));background:var(--input-bg, rgba(255,255,255,0.06));color:var(--text, #e6e6ea);font-size:15px}
  textarea{min-height:64px;white-space:pre-wrap;resize:vertical}
  button{background:var(--primary, #0a84ff);color:white;border:none;padding:8px 12px;border-radius:10px;font-weight:600;cursor:pointer}
  .ghost{background:transparent;border:1px solid rgba(255,255,255,0.04);color:var(--text, #e6e6ea)}
  .danger{background:var(--danger, #ff3b30)}
  .small{padding:6px 8px;border-radius:8px;font-size:13px}
  .input-item{display:flex;gap:8px;margin-bottom:8px;align-items:flex-start}
  .input-item textarea{flex:1;min-height:48px;background:var(--input-bg, rgba(255,255,255,0.06));border:1px solid var(--input-border, rgba(255,255,255,0.12))}
  .controls{display:flex;gap:8px;align-items:center}
  #searchBar{background:var(--panel, #0b0b0f);padding:12px 16px;border-bottom:1px solid rgba(255,255,255,0.03);position:sticky;top:0;z-index:10}
  .filterWrap{display:flex;gap:8px;align-items:center;width:100%}
  select.scope{width:160px;padding:10px;border-radius:10px;border:1px solid var(--input-border, rgba(255,255,255,0.12));background:var(--input-bg, rgba(255,255,255,0.06));color:var(--text, #e6e6ea)}
  .unsaved-warning{background:#ff9500;color:white;padding:8px 12px;border-radius:8px;margin:8px 0;font-size:14px;display:none;}
</style>
</head>
<body>
<header role="banner">
  <div style="display:flex;gap:12px;align-items:center">
    <a href="/" id="backToChat" aria-label="Back to chat">‹ Chat</a>
    <strong>Q&amp;A Manager</strong>
  </div>
  <div class="controls">
    <button id="darkToggle" class="ghost small" aria-label="Toggle theme">Theme</button>
    <span id="netStatus" style="color:var(--muted, #9aa0a6);font-size:13px" aria-live="polite"></span>
  </div>
</header>

<!-- Search bar at top -->
<div id="searchBar" role="search">
  <div class="filterWrap">
    <input id="filter" type="text" placeholder="Filter groups…" style="flex:1;padding:10px;border-radius:10px" aria-label="Filter groups" />
    <select id="filterScope" class="scope" title="Filter scope" aria-label="Filter scope">
      <option value="group" selected>Group name</option>
      <option value="input">Input</option>
      <option value="output">Output</option>
      <option value="all">All</option>
    </select>
  </div>
</div>

<main role="main">
  <div class="unsaved-warning" id="unsavedWarning">
    You have unsaved changes! Please save or discard your changes before leaving.
  </div>
  
  <div class="card" id="quickAdd">
    <div style="display:flex;gap:12px;align-items:flex-start">
      <div style="flex:1">
        <input id="newName" placeholder="Group name (optional)" aria-label="Group name" />
        <div style="margin-top:8px;font-size:13px;color:var(--muted, #9aa0a6)">Inputs — add items below (each item may contain newlines/indents)</div>
        <div id="quickInputs" style="margin-top:8px"></div>
        <div style="display:flex;gap:8px;margin-top:8px">
          <button id="quickAddInput" class="ghost small">Add Input</button>
          <button id="quickClearInputs" class="ghost small">Clear Inputs</button>
        </div>

        <div style="margin-top:12px;font-size:13px;color:var(--muted, #9aa0a6)">Outputs — add items below</div>
        <div id="quickOutputs" style="margin-top:8px"></div>
        <div style="display:flex;gap:8px;margin-top:8px">
          <button id="quickAddOutput" class="ghost small">Add Output</button>
          <button id="quickClearOutputs" class="ghost small">Clear Outputs</button>
        </div>
      </div>

      <div style="width:140px;display:flex;flex-direction:column;gap:8px">
        <button id="addGroup" class="small">Add</button>
        <button id="addGroupAndOpen" class="ghost small">Add & Edit</button>
        <button id="clearQuick" class="ghost small">Clear</button>
      </div>
    </div>
  </div>

  <div id="groups" aria-live="polite"></div>
</main>

<script>
/* Theme + network + quick add + manage logic
   - Search bar at top
   - Unsaved changes detection
   - Fixed duplicate event listeners
*/
const applyTheme=(theme)=>{ 
    if(theme==='light'){ 
        document.documentElement.classList.add('light'); 
        document.getElementById('darkToggle').setAttribute('aria-label', 'Switch to dark mode');
    } else { 
        document.documentElement.classList.remove('light'); 
        document.getElementById('darkToggle').setAttribute('aria-label', 'Switch to light mode');
    } 
    localStorage.setItem('chat_theme', theme); 
};
document.getElementById('darkToggle').addEventListener('click', ()=>{ 
    applyTheme(document.documentElement.classList.contains('light') ? 'dark' : 'light'); 
});
applyTheme(localStorage.getItem('chat_theme') || 'dark');

function updateNet(){ 
    document.getElementById('netStatus').textContent = navigator.onLine ? '' : 'offline'; 
}
window.addEventListener('online', updateNet); 
window.addEventListener('offline', updateNet); 
updateNet();

function makeItemNode(value){ 
    const wrap=document.createElement('div'); 
    wrap.className='input-item'; 
    const ta=document.createElement('textarea'); 
    ta.value=value||''; 
    ta.setAttribute('aria-label', 'Input text');
    // Track changes for unsaved detection
    ta.addEventListener('input', () => hasUnsavedChanges = true);
    const rm=document.createElement('button'); 
    rm.type='button'; 
    rm.className='small ghost'; 
    rm.textContent='Remove'; 
    rm.addEventListener('click',()=> {
        wrap.remove();
        hasUnsavedChanges = true;
    }); 
    wrap.appendChild(ta); 
    wrap.appendChild(rm); 
    return wrap; 
}

function escapeHtml(s){ 
    const div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
}

let currentAbortController = null;
let hasUnsavedChanges = false;
let originalValues = new Map();

async function fetchJSON(url,opts){ 
    // Cancel previous request
    if (currentAbortController) {
        currentAbortController.abort();
    }
    currentAbortController = new AbortController();
    
    const options = {
        signal: currentAbortController.signal,
        ...opts
    };
    
    const r = await fetch(url, options);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return await r.json(); 
}

const quickInputs=document.getElementById('quickInputs');
const quickOutputs=document.getElementById('quickOutputs');

// Track quick form changes
document.getElementById('newName').addEventListener('input', () => hasUnsavedChanges = true);

// FIXED: Remove duplicate event listeners - only keep one set
document.getElementById('quickAddInput').addEventListener('click', function(e){ 
    e.preventDefault(); 
    quickInputs.appendChild(makeItemNode('')); 
    hasUnsavedChanges = true;
});

document.getElementById('quickAddOutput').addEventListener('click', function(e){ 
    e.preventDefault(); 
    quickOutputs.appendChild(makeItemNode('')); 
    hasUnsavedChanges = true;
});

document.getElementById('quickClearInputs').addEventListener('click', function(){ 
    quickInputs.innerHTML=''; 
    hasUnsavedChanges = true;
});

document.getElementById('quickClearOutputs').addEventListener('click', function(){ 
    quickOutputs.innerHTML=''; 
    hasUnsavedChanges = true;
});

document.getElementById('clearQuick').addEventListener('click', function(){ 
    document.getElementById('newName').value=''; 
    quickInputs.innerHTML=''; 
    quickOutputs.innerHTML=''; 
    hasUnsavedChanges = false;
    updateUnsavedWarning();
});

const groupsEl=document.getElementById('groups');
const addGroupBtn=document.getElementById('addGroup');
const addGroupAndOpenBtn=document.getElementById('addGroupAndOpen');
const filter=document.getElementById('filter');
const filterScope=document.getElementById('filterScope');
const unsavedWarning = document.getElementById('unsavedWarning');
const backToChat = document.getElementById('backToChat');

function updateUnsavedWarning() {
    if (hasUnsavedChanges) {
        unsavedWarning.style.display = 'block';
    } else {
        unsavedWarning.style.display = 'none';
    }
}

// Warn before leaving with unsaved changes
window.addEventListener('beforeunload', (e) => {
    if (hasUnsavedChanges) {
        e.preventDefault();
        e.returnValue = 'You have unsaved changes. Are you sure you want to leave?';
        return 'You have unsaved changes. Are you sure you want to leave?';
    }
});

// Intercept navigation with unsaved changes
backToChat.addEventListener('click', (e) => {
    if (hasUnsavedChanges) {
        e.preventDefault();
        if (confirm('You have unsaved changes. Are you sure you want to leave?')) {
            hasUnsavedChanges = false;
            window.location.href = '/';
        }
    }
});

function groupCard(g){
  const wrap=document.createElement('div'); 
  wrap.className='card'; 
  wrap.dataset.gid=g.id;
  wrap.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;">
      <div style="flex:1;margin-right:12px">
        <input type="text" class="gname" value="${escapeHtml(g.name||'')}" aria-label="Group name" />
        <div style="font-size:13px;color:var(--muted)">ID: ${g.id}</div>
      </div>
      <div style="display:flex;flex-direction:column;gap:8px">
        <div style="display:flex;gap:8px">
          <button class="ghost small toggleInputs" aria-expanded="false">Show Inputs</button>
          <button class="ghost small toggleOutputs" aria-expanded="false">Show Outputs</button>
        </div>
        <div style="display:flex;gap:8px">
          <button class="small saveBtn">Save</button>
          <button class="small danger delBtn">Delete</button>
        </div>
      </div>
    </div>
    <div class="section inputs" style="margin-top:10px;display:none">
      <label style="font-weight:600">Inputs</label>
      <div class="inputsList"></div>
      <div style="display:flex;gap:8px;margin-top:8px">
        <button class="ghost small addInputBtn">Add Input</button>
        <button class="ghost small clearInputsBtn">Clear Inputs</button>
      </div>
    </div>
    <div class="section outputs" style="margin-top:10px;display:none">
      <label style="font-weight:600">Outputs</label>
      <div class="outputsList"></div>
      <div style="display:flex;gap:8px;margin-top:8px">
        <button class="ghost small addOutputBtn">Add Output</button>
        <button class="ghost small clearOutputsBtn">Clear Outputs</button>
      </div>
    </div>
  `;
  
  const nameInput = wrap.querySelector('.gname');
  const inputsList = wrap.querySelector('.inputsList');
  const outputsList = wrap.querySelector('.outputsList');
  
  // Store original values and track changes
  originalValues.set(g.id, {
    name: g.name || '',
    inputs: [...(g.inputs || [])],
    outputs: [...(g.outputs || [])]
  });
  
  nameInput.addEventListener('input', () => hasUnsavedChanges = true);
  
  (g.inputs || []).forEach(v => {
    const item = makeItemNode(v);
    inputsList.appendChild(item);
  });
  
  (g.outputs || []).forEach(v => {
    const item = makeItemNode(v);
    outputsList.appendChild(item);
  });
  
  return wrap;
}

async function insertGroupCardAtTop(g, expand=false){ 
    const card = groupCard(g); 
    groupsEl.insertBefore(card, groupsEl.firstChild); 
    if(expand){ 
        card.querySelector('.inputs').style.display='block'; 
        card.querySelector('.outputs').style.display='block'; 
        card.querySelector('.toggleInputs').textContent='Hide Inputs';
        card.querySelector('.toggleInputs').setAttribute('aria-expanded', 'true');
        card.querySelector('.toggleOutputs').textContent='Hide Outputs';
        card.querySelector('.toggleOutputs').setAttribute('aria-expanded', 'true');
        card.querySelector('.inputsList textarea')?.focus(); 
    } 
}

let currentFetchToken = 0;
async function loadGroups(){
  const q = (filter.value || '').trim();
  const scope = filterScope.value || 'group';
  const token = ++currentFetchToken;
  const url = '/api/groups' + (q ? `?filter=${encodeURIComponent(q)}&scope=${encodeURIComponent(scope)}` : '');
  
  try {
    const res = await fetchJSON(url);
    if(token !== currentFetchToken) return; // stale
    if(!res.ok) return;
    groupsEl.innerHTML = '';
    originalValues.clear();
    for(const g of res.groups){
      const card = groupCard(g);
      groupsEl.appendChild(card);
    }
    hasUnsavedChanges = false;
    updateUnsavedWarning();
  } catch (err) {
    if (err.name !== 'AbortError') {
      console.error('Failed to load groups:', err);
    }
  }
}

groupsEl.addEventListener('click', async (e) => {
  const el = e.target;
  const card = el.closest('.card');
  if(!card) return;
  const gid = card.dataset.gid;
  
  if(el.classList.contains('toggleInputs')){
    const sec = card.querySelector('.inputs');
    const isHidden = sec.style.display === 'none';
    sec.style.display = isHidden ? 'block' : 'none';
    el.textContent = isHidden ? 'Hide Inputs' : 'Show Inputs';
    el.setAttribute('aria-expanded', isHidden ? 'true' : 'false');
  } else if(el.classList.contains('toggleOutputs')){
    const sec = card.querySelector('.outputs');
    const isHidden = sec.style.display === 'none';
    sec.style.display = isHidden ? 'block' : 'none';
    el.textContent = isHidden ? 'Hide Outputs' : 'Show Outputs';
    el.setAttribute('aria-expanded', isHidden ? 'true' : 'false');
  } else if(el.classList.contains('addInputBtn')){
    card.querySelector('.inputsList').appendChild(makeItemNode(''));
    hasUnsavedChanges = true;
    updateUnsavedWarning();
  } else if(el.classList.contains('addOutputBtn')){
    card.querySelector('.outputsList').appendChild(makeItemNode(''));
    hasUnsavedChanges = true;
    updateUnsavedWarning();
  } else if(el.classList.contains('clearInputsBtn')){
    card.querySelector('.inputsList').innerHTML = '';
    hasUnsavedChanges = true;
    updateUnsavedWarning();
  } else if(el.classList.contains('clearOutputsBtn')){
    card.querySelector('.outputsList').innerHTML = '';
    hasUnsavedChanges = true;
    updateUnsavedWarning();
  } else if(el.classList.contains('saveBtn')){
    const name = card.querySelector('.gname').value.trim();
    const inputs = Array.from(card.querySelectorAll('.inputsList textarea')).map(t => t.value).filter(v => v.trim());
    const outputs = Array.from(card.querySelectorAll('.outputsList textarea')).map(t => t.value).filter(v => v.trim());
    
    try {
      await fetchJSON('/api/groups/'+gid, {
        method:'PATCH',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({name:name, inputs:inputs, outputs:outputs})
      });
      el.textContent = 'Saved';
      setTimeout(()=> el.textContent = 'Save', 900);
      hasUnsavedChanges = false;
      updateUnsavedWarning();
      await loadGroups();
    } catch (err) {
      if (err.name !== 'AbortError') {
        alert('Failed to save group: ' + err.message);
      }
    }
  } else if(el.classList.contains('delBtn')){
    if(!confirm('Delete this group?')) return;
    try {
      await fetchJSON('/api/groups/'+gid, { method:'DELETE' });
      await loadGroups();
    } catch (err) {
      if (err.name !== 'AbortError') {
        alert('Failed to delete group: ' + err.message);
      }
    }
  }
});

let isAddingGroup = false;

async function quickAdd(openAfter=false){
  if (isAddingGroup) return;
  
  const name = document.getElementById('newName').value.trim();
  const inputs = Array.from(quickInputs.querySelectorAll('textarea')).map(t => t.value).filter(v => v.trim());
  const outputs = Array.from(quickOutputs.querySelectorAll('textarea')).map(t => t.value).filter(v => v.trim());
  
  if(inputs.length === 0 && outputs.length === 0) {
    alert('Please add at least one input or output');
    return;
  }
  
  isAddingGroup = true;
  addGroupBtn.disabled = true;
  addGroupAndOpenBtn.disabled = true;
  
  try {
    const res = await fetchJSON('/api/groups', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({name:name, inputs:inputs, outputs:outputs})
    });
    
    if(res.ok && res.group){
      await insertGroupCardAtTop(res.group, openAfter);
      document.getElementById('newName').value = '';
      quickInputs.innerHTML = '';
      quickOutputs.innerHTML = '';
      addGroupBtn.textContent = 'Added';
      setTimeout(()=> addGroupBtn.textContent = 'Add', 900);
      hasUnsavedChanges = false;
      updateUnsavedWarning();
    } else {
      alert(res.error || 'Failed to create group');
    }
  } catch(err){
    if (err.name !== 'AbortError') {
      alert('Network error: ' + err.message);
    }
  } finally {
    isAddingGroup = false;
    addGroupBtn.disabled = false;
    addGroupAndOpenBtn.disabled = false;
  }
}

// FIXED: Only one event listener per button
document.getElementById('addGroup').addEventListener('click', async (e)=>{ 
  e.preventDefault(); 
  await quickAdd(false); 
});

document.getElementById('addGroupAndOpen').addEventListener('click', async (e)=>{ 
  e.preventDefault(); 
  await quickAdd(true); 
});

// Initialize with one empty input and output
quickInputs.appendChild(makeItemNode(''));
quickOutputs.appendChild(makeItemNode(''));

/* debounce filter input, immediate load on scope change */
let filterTimer = null;
filter.addEventListener('input', ()=>{
  clearTimeout(filterTimer);
  filterTimer = setTimeout(()=> loadGroups(), 300);
});
filterScope.addEventListener('change', ()=> loadGroups());

/* initial load */
loadGroups();
</script>
</body>
</html>
"""

# -------------------- Main -------------------- #
if __name__ == '__main__':
    if not os.path.exists(DB_FILE):
        open(DB_FILE, 'a').close()
    with app.app_context():
        init_db()
    app.run(debug=False, use_reloader=False, host='0.0.0.0', port=5000)