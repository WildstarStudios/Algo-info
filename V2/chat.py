# chat.py
# Fix: choose a single random answer per matched group (instead of returning all outputs)
from flask import Flask, render_template_string, request, jsonify, g
import sqlite3
from difflib import get_close_matches, SequenceMatcher
import random
import re
import os

DB_FILE = 'knowledge_base.db'
app = Flask(__name__)

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

# -------------------- DB connection (fast) -------------------- #
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_FILE, check_same_thread=False)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

# -------------------- Schema & init -------------------- #
def init_db():
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
    db = get_db()
    cur = db.execute('INSERT INTO groups (name) VALUES (?)', (name or None,))
    db.commit()
    gid = cur.lastrowid

    inputs_list = _ensure_inputs_list(inputs_raw)
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

    outputs_list = _ensure_outputs_list(outputs_raw)
    for out in outputs_list:
        if out.strip():
            db.execute('INSERT OR IGNORE INTO answers (group_id, answer) VALUES (?, ?)', (gid, out))
    db.commit()
    return gid

def update_group(gid, name, inputs_raw, outputs_raw):
    db = get_db()
    db.execute('UPDATE groups SET name=? WHERE id=?', (name or None, gid))
    db.execute('DELETE FROM questions WHERE group_id=?', (gid,))
    inputs_list = _ensure_inputs_list(inputs_raw)
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
    outputs_list = _ensure_outputs_list(outputs_raw)
    for out in outputs_list:
        if out.strip():
            db.execute('INSERT OR IGNORE INTO answers (group_id, answer) VALUES (?, ?)', (gid, out))
    db.commit()

def delete_group(gid):
    db = get_db()
    db.execute('DELETE FROM answers WHERE group_id=?', (gid,))
    db.execute('DELETE FROM questions WHERE group_id=?', (gid,))
    db.execute('DELETE FROM groups WHERE id=?', (gid,))
    db.commit()

def get_all_groups():
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
    if not msg:
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
    db = get_db()
    if request.method == 'POST':
        body = request.get_json(silent=True) or {}
        name = (body.get('name') or '').strip()
        inputs = body.get('inputs')
        outputs = body.get('outputs')
        gid = create_group(name, inputs, outputs)
        group = get_group_by_id(gid)
        return jsonify({'ok': True, 'group': group})

    # GET -> support fuzzy filter and scope
    q = (request.args.get('filter') or '').strip()
    scope = (request.args.get('scope') or 'group').lower()
    groups = get_all_groups()
    if not q:
        return jsonify({'ok': True, 'groups': groups})

    # For each group compute best similarity score depending on scope
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

    # Use separate thresholds to avoid tiny output false positives
    THRESHOLD_GROUP = 0.25
    THRESHOLD_IO = 0.45
    filtered = []
    for score, g in sorted(scored, key=lambda x: x[0], reverse=True):
        if scope == 'group' and score >= THRESHOLD_GROUP:
            filtered.append(g)
        elif scope in ('input', 'output') and score >= THRESHOLD_IO:
            filtered.append(g)
        elif scope == 'all':
            group_score = similarity(q, g.get('name') or '')
            io_best = 0.0
            for inp in g.get('inputs', []):
                io_best = max(io_best, similarity(q, inp))
            for out in g.get('outputs', []):
                io_best = max(io_best, similarity(q, out))
            if group_score >= THRESHOLD_GROUP or io_best >= THRESHOLD_IO:
                filtered.append(g)
    return jsonify({'ok': True, 'groups': filtered})

@app.route('/api/groups/<int:gid>', methods=['PATCH', 'DELETE'])
def api_group_item(gid):
    if request.method == 'PATCH':
        body = request.get_json(silent=True) or {}
        name = (body.get('name') or '').strip()
        inputs = body.get('inputs')
        outputs = body.get('outputs')
        update_group(gid, name, inputs, outputs)
        return jsonify({'ok': True})
    delete_group(gid)
    return jsonify({'ok': True})

# -------------------- Templates (chat + manage) -------------------- #
# Chat template: improved mobile keyboard handling (visualViewport) and typing indicator
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
  *{box-sizing:border-box}html,body{height:100%}body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text);height:100dvh;display:flex;flex-direction:column}
  header{padding:12px 16px;background:var(--panel);backdrop-filter: blur(12px) saturate(150%);display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid rgba(255,255,255,0.03)}
  header h1{font-size:17px;margin:0;font-weight:600}.controls{display:flex;gap:8px;align-items:center}.link,.btn{background:none;border:none;padding:6px 10px;border-radius:12px;color:var(--bubble-me);font-weight:600;text-decoration:none}.btn{background:transparent;cursor:pointer}#messages{flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:10px}.msgRow{display:flex}.msgRow.me{justify-content:flex-end}.msgRow.bot{justify-content:flex-start}.bubble{display:inline-block;max-width:78%;padding:10px 12px;border-radius:18px;line-height:1.35;word-break:break-word;white-space:pre-wrap;box-shadow:0 1px 0 rgba(0,0,0,0.12) inset;font-family:inherit}.me .bubble{background:var(--bubble-me);color:white;border-bottom-right-radius:6px;border-bottom-left-radius:18px}.bot .bubble{background:var(--bubble-bot);color:var(--text);border-bottom-left-radius:6px;border-bottom-right-radius:18px}.typing-bubble{display:inline-block;padding:8px 12px;border-radius:18px;background:var(--bubble-bot);color:var(--text)}.typing-dots{display:inline-block;width:32px;text-align:center}.typing-dots span{display:inline-block;width:6px;height:6px;margin:0 2px;border-radius:50%;background:var(--muted);opacity:0.8;transform:translateY(0);animation:blink 1s infinite}.typing-dots span:nth-child(2){animation-delay:0.12s}.typing-dots span:nth-child(3){animation-delay:0.24s}@keyframes blink{0%{opacity:0.15;transform:translateY(0)}50%{opacity:0.95;transform:translateY(-4px)}100%{opacity:0.15;transform:translateY(0)}}#composerWrap{padding:10px;border-top:1px solid rgba(255,255,255,0.03);background:var(--panel);display:flex;align-items:flex-end;gap:8px}#composer{display:flex;flex:1;gap:8px;align-items:flex-end}textarea#msg{flex:1;min-height:44px;max-height:180px;padding:10px 12px;border-radius:18px;border:1px solid var(--input-border);background:var(--input-bg);color:var(--text);outline:none;resize:none;line-height:1.2}button.send{border:none;border-radius:18px;padding:10px 14px;background:var(--bubble-me);color:white;font-weight:600;cursor:pointer}.disabled{opacity:0.5;pointer-events:none}#offlineBanner{display:none;background:#ff3b30;color:white;padding:6px 12px;border-radius:10px;margin:8px;text-align:center;font-weight:600}.smallStatus{font-size:12px;color:var(--muted);margin-left:6px}textarea:focus,button:focus{outline:3px solid rgba(10,132,255,0.12);outline-offset:2px}@supports(padding:max(0px)){header{padding-top:calc(12px + env(safe-area-inset-top));padding-bottom:calc(12px + env(safe-area-inset-bottom))}#composerWrap{padding-bottom:calc(10px + env(safe-area-inset-bottom))}}
</style>
</head>
<body>
  <header>
    <h1>Messages</h1>
    <div class="controls">
      <button id="darkToggle" class="btn" title="Toggle dark/light">🌙</button>
      <a class="link" href="/manage">Q&A</a>
      <span id="status" class="smallStatus"></span>
    </div>
  </header>

  <div id="messages" aria-live="polite"></div>
  <div id="offlineBanner">You appear to be offline — messages won't be sent.</div>

  <div id="composerWrap">
    <form id="composer" autocomplete="off" style="display:flex;flex:1;gap:8px;align-items:flex-end">
      <textarea id="msg" inputmode="text" placeholder="Message (Shift+Enter newline, Enter send)"></textarea>
      <button type="submit" class="send" id="sendBtn">Send</button>
    </form>
  </div>

<script>
const root = document.documentElement;
const darkToggle = document.getElementById('darkToggle');
function applyTheme(theme){ if(theme === 'light'){ document.documentElement.classList.add('light'); darkToggle.textContent='🌞'; } else { document.documentElement.classList.remove('light'); darkToggle.textContent='🌙'; } localStorage.setItem('chat_theme', theme); }
const saved = localStorage.getItem('chat_theme') || 'dark'; applyTheme(saved);
darkToggle.addEventListener('click', ()=> applyTheme(document.documentElement.classList.contains('light') ? 'dark' : 'light'));

const messages = document.getElementById('messages');
const form = document.getElementById('composer');
const input = document.getElementById('msg');
const sendBtn = document.getElementById('sendBtn');
const offlineBanner = document.getElementById('offlineBanner');
const statusEl = document.getElementById('status');

let typingNode = null;

function addBubble(text, who){
  const wrap = document.createElement('div');
  wrap.className = 'msgRow ' + (who === 'me' ? 'me' : 'bot');
  const b = document.createElement('div');
  b.className = 'bubble';
  b.textContent = text;
  wrap.appendChild(b);
  messages.appendChild(wrap);
  messages.scrollTop = messages.scrollHeight;
  return wrap;
}

function showTypingIndicator(){
  if(typingNode) return;
  const wrap = document.createElement('div');
  wrap.className = 'msgRow bot typingRow';
  const b = document.createElement('div');
  b.className = 'typing-bubble';
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
window.addEventListener('online', updateOnlineStatus);
window.addEventListener('offline', updateOnlineStatus);
updateOnlineStatus();

input.addEventListener('keydown', function(e){
  if(e.key === 'Enter' && !e.shiftKey){
    e.preventDefault();
    form.requestSubmit();
  }
});

// mobile keyboard handling: adjust padding and scroll when visualViewport changes
if (window.visualViewport) {
  const composerWrap = document.getElementById('composerWrap');
  let lastHeight = window.visualViewport.height;
  window.visualViewport.addEventListener('resize', () => {
    const vh = window.visualViewport.height;
    const diff = window.innerHeight - vh;
    // when keyboard opens, diff > 0; set bottom padding to keep messages visible
    messages.style.paddingBottom = (diff + 20) + 'px';
    // scroll to bottom
    messages.scrollTop = messages.scrollHeight;
    lastHeight = vh;
  });
  // on blur, reset padding
  input.addEventListener('blur', () => {
    setTimeout(()=> { messages.style.paddingBottom = ''; }, 50);
  });
}

// focus behavior ensures composer & last message visible
input.addEventListener('focus', () => {
  setTimeout(()=> { messages.scrollTop = messages.scrollHeight; }, 120);
});

async function sendMessage(text){
  if(!text) return;
  if(!navigator.onLine){
    addBubble("Unable to send — you are offline.", 'bot');
    return;
  }
  sendBtn.disabled = true;
  showTypingIndicator();
  setStatus('thinking…');

  try {
    const controller = new AbortController();
    const timeout = setTimeout(()=> controller.abort(), 30000);
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({message: text}),
      signal: controller.signal
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
  } finally {
    sendBtn.disabled = false;
    hideTypingIndicator();
  }
}

form.addEventListener('submit', function(e){
  e.preventDefault();
  const text = (input.value || '').trim();
  if(!text) return;
  addBubble(text, 'me');
  input.value = '';
  sendMessage(text);
});

updateOnlineStatus();
input.focus();
</script>
</body>
</html>
"""

# Manage template: removed reload button, immediate scope filtering, better contrast variables
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
  body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text);min-height:100dvh}
  header{padding:12px 16px;background:var(--panel);display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid rgba(255,255,255,0.03)}
  a{color:var(--primary);text-decoration:none;font-weight:600}
  main{padding:12px;padding-bottom:120px}
  .card{background:var(--panel);border:1px solid rgba(255,255,255,0.03);border-radius:12px;padding:12px;margin-bottom:12px}
  input,textarea,select{width:100%;padding:10px;border-radius:10px;border:1px solid var(--input-border);background:var(--input-bg);color:var(--text);font-size:15px}
  textarea{min-height:64px;white-space:pre-wrap;resize:vertical}
  button{background:var(--primary);color:white;border:none;padding:8px 12px;border-radius:10px;font-weight:600;cursor:pointer}
  .ghost{background:transparent;border:1px solid rgba(255,255,255,0.04);color:var(--text)}
  .danger{background:var(--danger)}
  .small{padding:6px 8px;border-radius:8px;font-size:13px}
  .input-item{display:flex;gap:8px;margin-bottom:8px;align-items:flex-start}
  .input-item textarea{flex:1;min-height:48px;background:var(--input-bg);border:1px solid var(--input-border)}
  .controls{display:flex;gap:8px;align-items:center}
  #bar{position:fixed;left:0;right:0;bottom:0;background:var(--panel);padding:8px 8px calc(8px + env(safe-area-inset-bottom,0px));border-top:1px solid rgba(255,255,255,0.03);display:flex;gap:8px;align-items:center}
  .filterWrap{display:flex;gap:8px;align-items:center;width:100%}
  select.scope{width:160px;padding:10px;border-radius:10px;border:1px solid var(--input-border);background:var(--input-bg);color:var(--text)}
</style>
</head>
<body>
<header>
  <div style="display:flex;gap:12px;align-items:center">
    <a href="/">‹ Chat</a>
    <strong>Q&amp;A Manager</strong>
  </div>
  <div class="controls">
    <button id="darkToggle" class="ghost small">Theme</button>
    <span id="netStatus" style="color:var(--muted);font-size:13px"></span>
  </div>
</header>

<main>
  <div class="card" id="quickAdd">
    <div style="display:flex;gap:12px;align-items:flex-start">
      <div style="flex:1">
        <input id="newName" placeholder="Group name (optional)" />
        <div style="margin-top:8px;font-size:13px;color:var(--muted)">Inputs — add items below (each item may contain newlines/indents)</div>
        <div id="quickInputs" style="margin-top:8px"></div>
        <div style="display:flex;gap:8px;margin-top:8px">
          <button id="quickAddInput" class="ghost small">Add Input</button>
          <button id="quickClearInputs" class="ghost small">Clear Inputs</button>
        </div>

        <div style="margin-top:12px;font-size:13px;color:var(--muted)">Outputs — add items below</div>
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

  <div id="groups"></div>
</main>

<div id="bar">
  <div class="filterWrap">
    <input id="filter" type="text" placeholder="Filter…" style="flex:1;padding:10px;border-radius:10px" />
    <select id="filterScope" class="scope" title="Filter scope">
      <option value="group" selected>Group name</option>
      <option value="input">Input</option>
      <option value="output">Output</option>
      <option value="all">All</option>
    </select>
  </div>
</div>

<script>
/* Theme + network + quick add + manage logic
   - Removed "reload" button; scope changes trigger immediate server search.
   - Debounced filter calls server-side fuzzy search.
*/
const applyTheme=(theme)=>{ if(theme==='light'){ document.documentElement.classList.add('light'); } else { document.documentElement.classList.remove('light'); } localStorage.setItem('chat_theme', theme); };
document.getElementById('darkToggle').addEventListener('click', ()=>{ applyTheme(document.documentElement.classList.contains('light') ? 'dark' : 'light'); });
applyTheme(localStorage.getItem('chat_theme') || 'dark');

function updateNet(){ document.getElementById('netStatus').textContent = navigator.onLine ? '' : 'offline'; }
window.addEventListener('online', updateNet); window.addEventListener('offline', updateNet); updateNet();

function makeItemNode(value){ const wrap=document.createElement('div'); wrap.className='input-item'; const ta=document.createElement('textarea'); ta.value=value||''; const rm=document.createElement('button'); rm.type='button'; rm.className='small ghost'; rm.textContent='Remove'; rm.addEventListener('click',()=>wrap.remove()); wrap.appendChild(ta); wrap.appendChild(rm); return wrap; }
function escapeHtml(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;'); }
async function fetchJSON(url,opts){ const r = await fetch(url,opts||{}); return await r.json(); }

const quickInputs=document.getElementById('quickInputs');
const quickOutputs=document.getElementById('quickOutputs');
document.getElementById('quickAddInput').addEventListener('click', e=>{ e.preventDefault(); quickInputs.appendChild(makeItemNode('')); });
document.getElementById('quickAddOutput').addEventListener('click', e=>{ e.preventDefault(); quickOutputs.appendChild(makeItemNode('')); });
document.getElementById('quickClearInputs').addEventListener('click', ()=> quickInputs.innerHTML='');
document.getElementById('quickClearOutputs').addEventListener('click', ()=> quickOutputs.innerHTML='');
document.getElementById('clearQuick').addEventListener('click', ()=>{ document.getElementById('newName').value=''; quickInputs.innerHTML=''; quickOutputs.innerHTML=''; });

const groupsEl=document.getElementById('groups');
const addGroupBtn=document.getElementById('addGroup');
const addGroupAndOpenBtn=document.getElementById('addGroupAndOpen');
const filter=document.getElementById('filter');
const filterScope=document.getElementById('filterScope');

function groupCard(g){
  const wrap=document.createElement('div'); wrap.className='card'; wrap.dataset.gid=g.id;
  wrap.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;">
      <div style="flex:1;margin-right:12px">
        <input type="text" class="gname" value="${escapeHtml(g.name||'')}" />
        <div style="font-size:13px;color:var(--muted)">ID: ${g.id}</div>
      </div>
      <div style="display:flex;flex-direction:column;gap:8px">
        <div style="display:flex;gap:8px">
          <button class="ghost small toggleInputs">Show Inputs</button>
          <button class="ghost small toggleOutputs">Show Outputs</button>
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
  const inputsList = wrap.querySelector('.inputsList');
  const outputsList = wrap.querySelector('.outputsList');
  (g.inputs || []).forEach(v => inputsList.appendChild(makeItemNode(v)));
  (g.outputs || []).forEach(v => outputsList.appendChild(makeItemNode(v)));
  return wrap;
}

async function insertGroupCardAtTop(g, expand=false){ const card = groupCard(g); groupsEl.insertBefore(card, groupsEl.firstChild); if(expand){ card.querySelector('.inputs').style.display='block'; card.querySelector('.outputs').style.display='block'; card.querySelector('.toggleInputs').textContent='Hide Inputs'; card.querySelector('.toggleOutputs').textContent='Hide Outputs'; card.querySelector('.inputsList textarea')?.focus(); } }

let currentFetchToken = 0;
async function loadGroups(){
  const q = (filter.value || '').trim();
  const scope = filterScope.value || 'group';
  const token = ++currentFetchToken;
  const url = '/api/groups' + (q ? `?filter=${encodeURIComponent(q)}&scope=${encodeURIComponent(scope)}` : '');
  const res = await fetchJSON(url);
  if(token !== currentFetchToken) return; // stale
  if(!res.ok) return;
  groupsEl.innerHTML = '';
  for(const g of res.groups){
    const card = groupCard(g);
    groupsEl.appendChild(card);
  }
}

groupsEl.addEventListener('click', async (e) => {
  const el = e.target;
  const card = el.closest('.card');
  if(!card) return;
  const gid = card.dataset.gid;
  if(el.classList.contains('toggleInputs')){
    const sec = card.querySelector('.inputs');
    sec.style.display = sec.style.display === 'none' ? 'block' : 'none';
    el.textContent = sec.style.display === 'none' ? 'Show Inputs' : 'Hide Inputs';
  } else if(el.classList.contains('toggleOutputs')){
    const sec = card.querySelector('.outputs');
    sec.style.display = sec.style.display === 'none' ? 'block' : 'none';
    el.textContent = sec.style.display === 'none' ? 'Show Outputs' : 'Hide Outputs';
  } else if(el.classList.contains('addInputBtn')){
    card.querySelector('.inputsList').appendChild(makeItemNode(''));
  } else if(el.classList.contains('addOutputBtn')){
    card.querySelector('.outputsList').appendChild(makeItemNode(''));
  } else if(el.classList.contains('clearInputsBtn')){
    card.querySelector('.inputsList').innerHTML = '';
  } else if(el.classList.contains('clearOutputsBtn')){
    card.querySelector('.outputsList').innerHTML = '';
  } else if(el.classList.contains('saveBtn')){
    const name = card.querySelector('.gname').value.trim();
    const inputs = Array.from(card.querySelectorAll('.inputsList textarea')).map(t => t.value);
    const outputs = Array.from(card.querySelectorAll('.outputsList textarea')).map(t => t.value);
    await fetchJSON('/api/groups/'+gid, {
      method:'PATCH',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({name:name, inputs:inputs, outputs:outputs})
    });
    el.textContent = 'Saved';
    setTimeout(()=> el.textContent = 'Save', 900);
    await loadGroups();
  } else if(el.classList.contains('delBtn')){
    if(!confirm('Delete this group?')) return;
    await fetchJSON('/api/groups/'+gid, { method:'DELETE' });
    await loadGroups();
  }
});

async function quickAdd(openAfter=false){
  const name = document.getElementById('newName').value.trim();
  const inputs = Array.from(quickInputs.querySelectorAll('textarea')).map(t => t.value);
  const outputs = Array.from(quickOutputs.querySelectorAll('textarea')).map(t => t.value);
  if(!name && inputs.length === 0 && outputs.length === 0) return;
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
    } else {
      alert(res.error || 'Failed to create group');
    }
  } catch(err){
    alert('Network error');
  } finally {
    addGroupBtn.disabled = false;
    addGroupAndOpenBtn.disabled = false;
  }
}

document.getElementById('addGroup').addEventListener('click', async (e)=>{ e.preventDefault(); quickAdd(false); });
document.getElementById('addGroupAndOpen').addEventListener('click', async (e)=>{ e.preventDefault(); quickAdd(true); });

document.getElementById('quickAddInput').addEventListener('click', (e)=>{ e.preventDefault(); quickInputs.appendChild(makeItemNode('')); });
document.getElementById('quickAddOutput').addEventListener('click', (e)=>{ e.preventDefault(); quickOutputs.appendChild(makeItemNode('')); });
document.getElementById('quickClearInputs').addEventListener('click', ()=> quickInputs.innerHTML = '');
document.getElementById('quickClearOutputs').addEventListener('click', ()=> quickOutputs.innerHTML = '');
document.getElementById('clearQuick').addEventListener('click', ()=> { document.getElementById('newName').value=''; quickInputs.innerHTML=''; quickOutputs.innerHTML=''; });

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
