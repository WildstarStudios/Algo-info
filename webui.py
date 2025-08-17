from flask import Flask, request, jsonify, render_template_string
import chatbot  # your chatbot.py file (unchanged)
import difflib

app = Flask(__name__)

# Load KB once (shared in-memory; save_knowledge will persist to knowledge.json)
kb = chatbot.load_knowledge()

HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no" />
  <title>Chatbot Web UI (Dark)</title>
  <style>
    :root{
      --bg-dark:#121212; --text:#ddd; --user:#3399ff; --user-press:#1e7fe0; --bot:#3a3a3a;
      --input-bg:#222; --border:#444; --shadow: rgba(0,0,0,0.8);
    }
    html,body{height:100%;margin:0;}
    body{
      background:var(--bg-dark);
      color:var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial;
      display:flex; flex-direction:column; height:100vh;
    }
    header{display:flex;align-items:center;justify-content:space-between;padding:12px 16px;background:var(--user);color:#fff;font-weight:600;}
    header h1{margin:0;font-size:16px;}
    #train-btn { background:transparent;border:1px solid rgba(255,255,255,0.18); color:white; padding:6px 10px;border-radius:12px; cursor:pointer; }
    #train-btn:hover{background:rgba(255,255,255,0.06)}
    #container{display:flex;flex-direction:column;flex:1;min-height:0;} /* min-height:0 for proper scrolling */
    #chatbox{
      flex:1; overflow-y:auto; -webkit-overflow-scrolling:touch; padding:16px; background:var(--bot); border-top:1px solid var(--border); border-bottom:1px solid var(--border); display:flex; flex-direction:column;
    }
    .message{display:block; margin-bottom:10px; max-width:75%; padding:8px 12px; border-radius:18px; line-height:1.35; font-size:17px; box-shadow:0 1px 3px var(--shadow); white-space:pre-wrap; text-align:left;}
    .user{background:var(--user); color:#fff; margin-left:auto; border-bottom-right-radius:4px; border-bottom-left-radius:18px;}
    .bot{background:#2f2f2f; color:var(--text); margin-right:auto; border-bottom-left-radius:4px; border-bottom-right-radius:18px;}
    #input-area{display:flex;padding:12px;background:var(--input-bg);border-top:1px solid var(--border);}
    #user_input{flex:1; font-size:17px; padding:10px 14px;border-radius:20px;border:1px solid #666;background:#444;color:#fff; outline:none; -webkit-appearance:none;}
    #user_input:focus{border-color:var(--user);}
    button.send{margin-left:10px;padding:10px 18px;border-radius:18px;background:var(--user);border:none;color:#fff;font-size:16px;cursor:pointer;}
    button.send:active{background:var(--user-press)}
    /* training modal */
    .modal{position:fixed;right:12px;top:62px;width:360px;max-width:calc(100% - 24px);background:#171717;border:1px solid #333;border-radius:12px;box-shadow:0 8px 30px rgba(0,0,0,0.6);z-index:40;padding:12px;display:none;flex-direction:column;}
    .modal.visible{display:flex;}
    .modal h3{margin:4px 0 10px 0;color:#fff;font-size:15px;}
    .form-row{display:flex;gap:8px;margin-bottom:8px;}
    .form-row label{flex:0 0 90px;color:#bbb;font-size:13px;align-self:center;}
    .form-row input[type="text"], .form-row textarea, .form-row select{
      flex:1;padding:8px;border-radius:8px;border:1px solid #444;background:#222;color:#ddd;font-size:14px;
    }
    .outputs-list{display:flex;flex-direction:column;gap:6px;margin-bottom:8px;}
    .outputs-list .out-item{background:#252525;padding:8px;border-radius:8px;color:#ddd;display:flex;justify-content:space-between;align-items:center;font-size:14px;}
    .mini{padding:6px 10px;border-radius:10px;background:var(--user);color:#fff;border:none;cursor:pointer;}
    .muted{color:#888;font-size:13px;margin-bottom:10px;}
    .kb-list{max-height:200px;overflow:auto;padding:8px;border-top:1px dashed #333;margin-top:8px;}
    .kb-item{font-size:13px;color:#ccc;padding:6px;border-bottom:1px solid rgba(255,255,255,0.02)}
    .controls-row{display:flex;gap:8px;justify-content:flex-end;margin-top:8px;}
    /* responsive */
    @media (max-width:420px){ .modal{left:12px;right:12px;width:auto;top:60px;} }
  </style>
</head>
<body>
  <header>
    <h1>Chatbot</h1>
    <div>
      <button id="train-btn">Train</button>
    </div>
  </header>

  <div id="container">
    <div id="chatbox" aria-live="polite"></div>

    <div id="input-area">
      <input id="user_input" type="text" placeholder="Type your message (type 'train' to open training)..." autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false"/>
      <button class="send" onclick="onSend()">Send</button>
    </div>
  </div>

  <!-- Training modal -->
  <div id="train-modal" class="modal" role="dialog" aria-modal="true" aria-hidden="true">
    <h3>Training — add new knowledge</h3>
    <div class="muted">Choose a type and fill fields. Click Add to save to knowledge.json</div>

    <div class="form-row">
      <label for="t-type">Type</label>
      <select id="t-type">
        <option value="fact">fact</option>
        <option value="concept">concept</option>
        <option value="rule">rule</option>
        <option value="instruction">instruction</option>
        <option value="example">example</option>
        <option value="association">association</option>
        <option value="phrase">phrase</option>
      </select>
    </div>

    <!-- generic content -->
    <div id="field-content" class="form-row" style="display:flex">
      <label>Content</label>
      <textarea id="t-content" rows="2" style="resize:vertical"></textarea>
    </div>

    <!-- example fields -->
    <div id="field-example" style="display:none">
      <div class="form-row"><label>Input</label><input id="t-ex-input" type="text"/></div>
      <div class="form-row"><label>Output</label><input id="t-ex-output" type="text"/></div>
    </div>

    <!-- association fields -->
    <div id="field-assoc" style="display:none">
      <div class="form-row"><label>Subject</label><input id="t-subject" type="text"/></div>
      <div class="form-row"><label>Predicate</label><input id="t-predicate" type="text"/></div>
      <div class="form-row"><label>Object</label><input id="t-object" type="text"/></div>
    </div>

    <!-- phrase fields -->
    <div id="field-phrase" style="display:none">
      <div class="form-row"><label>Phrase</label><input id="t-phrase-input" type="text"/></div>
      <div class="form-row" style="align-items:flex-start">
        <label>Outputs</label>
        <div style="flex:1">
          <div style="display:flex;gap:6px;margin-bottom:8px">
            <input id="t-phrase-output" type="text" placeholder="Add one output then click +"/>
            <button id="add-output" class="mini">+</button>
          </div>
          <div id="outputs" class="outputs-list"></div>
        </div>
      </div>
    </div>

    <div class="controls-row">
      <button id="add-knowledge" class="mini">Add</button>
      <button id="train-close" class="mini" style="background:#555">Close</button>
    </div>

    <div class="kb-list" id="kb-list">
      <!-- knowledge items loaded here -->
    </div>
  </div>

<script>
  const chatbox = document.getElementById('chatbox');
  const input = document.getElementById('user_input');
  const trainBtn = document.getElementById('train-btn');
  const trainModal = document.getElementById('train-modal');
  const tType = document.getElementById('t-type');
  const fieldContent = document.getElementById('field-content');
  const fieldExample = document.getElementById('field-example');
  const fieldAssoc = document.getElementById('field-assoc');
  const fieldPhrase = document.getElementById('field-phrase');
  const tContent = document.getElementById('t-content');
  const tExInput = document.getElementById('t-ex-input');
  const tExOutput = document.getElementById('t-ex-output');
  const tSubject = document.getElementById('t-subject');
  const tPredicate = document.getElementById('t-predicate');
  const tObject = document.getElementById('t-object');
  const tPhraseInput = document.getElementById('t-phrase-input');
  const tPhraseOutput = document.getElementById('t-phrase-output');
  const outputsDiv = document.getElementById('outputs');
  const addOutputBtn = document.getElementById('add-output');
  const addKnowledgeBtn = document.getElementById('add-knowledge');
  const closeBtn = document.getElementById('train-close');
  const kbList = document.getElementById('kb-list');

  // helper: append messages
  function addMessage(sender, text){
    const div = document.createElement('div');
    div.className = 'message ' + (sender === 'You' ? 'user':'bot');
    div.textContent = text;
    chatbox.appendChild(div);
    chatbox.scrollTop = chatbox.scrollHeight;
  }

  // send normal chat (intercept exact "train")
  async function onSend(){
    const text = input.value.trim();
    if(!text) return;
    // intercept exact 'train' (case-insensitive)
    if(text.toLowerCase() === 'train'){
      openTrain();
      input.value = '';
      return;
    }

    addMessage('You', text);
    input.value = '';
    input.focus();

    try{
      const res = await fetch('/chat', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({message:text})
      });
      const j = await res.json();
      addMessage('Bot', j.reply);
    }catch(e){
      addMessage('Bot', 'Error: could not get response.');
    }
  }

  input.addEventListener('keydown', e => { if(e.key === 'Enter') { e.preventDefault(); onSend(); } });
  trainBtn.addEventListener('click', openTrain);
  closeBtn.addEventListener('click', closeTrain);

  function openTrain(){
    trainModal.classList.add('visible');
    trainModal.setAttribute('aria-hidden','false');
    loadKnowledge(); // refresh list
  }
  function closeTrain(){
    trainModal.classList.remove('visible');
    trainModal.setAttribute('aria-hidden','true');
    // clear form
    tContent.value=''; tExInput.value=''; tExOutput.value=''; tSubject.value=''; tPredicate.value=''; tObject.value='';
    tPhraseInput.value=''; tPhraseOutput.value=''; outputsDiv.innerHTML='';
  }

  // dynamic form switching
  tType.addEventListener('change', () => {
    const v = tType.value;
    fieldContent.style.display = (v==='fact' || v==='concept' || v==='rule' || v==='instruction') ? 'flex' : 'none';
    fieldExample.style.display = (v==='example') ? 'block' : 'none';
    fieldAssoc.style.display = (v==='association') ? 'block' : 'none';
    fieldPhrase.style.display = (v==='phrase') ? 'block' : 'none';
  });

  // add phrase output UI
  addOutputBtn.addEventListener('click', (e) => {
    e.preventDefault();
    const val = tPhraseOutput.value.trim();
    if(!val) return;
    const item = document.createElement('div');
    item.className = 'out-item';
    item.innerHTML = '<span class="out-text"></span><button class="mini remove">x</button>';
    item.querySelector('.out-text').textContent = val;
    outputsDiv.appendChild(item);
    tPhraseOutput.value = '';
    // remove handler
    item.querySelector('.remove').addEventListener('click', () => item.remove());
  });

  // handle Add knowledge
  addKnowledgeBtn.addEventListener('click', async () => {
    const type = tType.value;
    let payload = { type: type };

    if(type === 'association'){
      const subject = tSubject.value.trim();
      const predicate = tPredicate.value.trim();
      const object = tObject.value.trim();
      if(!subject || !predicate || !object){ alert('Subject, predicate, and object are required.'); return; }
      payload['content'] = { subject: subject.toLowerCase(), predicate: predicate.toLowerCase(), object: object.toLowerCase() };
    } else if(type === 'phrase'){
      const phr = tPhraseInput.value.trim().toLowerCase();
      if(!phr){ alert('Phrase input required.'); return; }
      const outs = Array.from(outputsDiv.querySelectorAll('.out-item .out-text')).map(n => n.textContent);
      if(outs.length === 0){ alert('At least one phrase output required.'); return; }
      payload['input'] = phr;
      payload['outputs'] = outs;
    } else if(type === 'example'){
      const exi = tExInput.value.trim().toLowerCase();
      const exo = tExOutput.value.trim();
      if(!exi || !exo){ alert('Example input and output required.'); return; }
      payload['input'] = exi;
      payload['output'] = exo;
    } else { // fact/concept/rule/instruction
      const content = tContent.value.trim();
      if(!content){ alert('Content required.'); return; }
      payload['content'] = content;
    }

    try{
      const res = await fetch('/train/add', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify(payload)
      });
      const j = await res.json();
      if(j.status === 'ok'){
        alert('Added ✓');
        loadKnowledge();
        // clear appropriate fields
        tContent.value=''; tExInput.value=''; tExOutput.value=''; tSubject.value=''; tPredicate.value=''; tObject.value='';
        tPhraseInput.value=''; outputsDiv.innerHTML='';
      } else {
        alert('Error: ' + (j.message || 'unknown'));
      }
    }catch(err){
      alert('Network error');
    }
  });

  // fetch and display knowledge
  async function loadKnowledge(){
    try{
      const res = await fetch('/knowledge');
      const j = await res.json();
      kbList.innerHTML = '';
      j.knowledge.slice().reverse().forEach((item,i) => {
        const div = document.createElement('div');
        div.className = 'kb-item';
        div.textContent = JSON.stringify(item);
        kbList.appendChild(div);
      });
    }catch(e){
      kbList.innerHTML = '<div class="kb-item">Could not load knowledge</div>';
    }
  }

  // initial welcome message
  addMessage('Bot', 'Hello — ask me things, or type "train" to add knowledge.');
</script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML)

@app.route('/chat', methods=['POST'])
def chat():
    data = request.get_json() or {}
    user_msg = data.get("message", "")
    # Do NOT treat "train" here — web UI intercepts it. But if someone posts 'train' directly,
    # we still return a friendly note.
    if isinstance(user_msg, str) and user_msg.strip().lower() == "train":
        return jsonify({"reply": "Training mode opened in the web UI. Use the Train button or type 'train' in the input."})
    reply = chatbot.answer_question(kb, user_msg)
    return jsonify({"reply": reply})

@app.route('/train/add', methods=['POST'])
def train_add():
    """
    Expected JSON payloads:
    - association: { "type": "association", "content": {"subject": "...", "predicate":"...", "object":"..."} }
    - phrase: { "type":"phrase", "input":"exact phrase", "outputs": ["out1","out2"] }
    - example: { "type":"example", "input":"...", "output":"..." }
    - fact/concept/rule/instruction: { "type":"fact", "content":"..." }
    """
    payload = request.get_json() or {}
    t = payload.get("type")
    if not t:
        return jsonify({"status":"error","message":"type required"}), 400

    try:
        if t == "association":
            content = payload.get("content")
            if not content or not all(k in content for k in ("subject","predicate","object")):
                return jsonify({"status":"error","message":"association needs subject, predicate, object"}), 400
            new_entry = {"type":"association", "content": {"subject": content["subject"], "predicate": content["predicate"], "object": content["object"]}}
            chatbot.add_or_merge_entry(kb, new_entry)

        elif t == "phrase":
            inp = payload.get("input")
            outs = payload.get("outputs")
            if not inp or not outs or not isinstance(outs, list):
                return jsonify({"status":"error","message":"phrase needs input and outputs list"}), 400
            new_entry = {"type":"phrase", "input": inp.lower().strip(), "outputs": outs}
            chatbot.add_or_merge_entry(kb, new_entry)

        elif t == "example":
            inp = payload.get("input")
            out = payload.get("output")
            if not inp or out is None:
                return jsonify({"status":"error","message":"example needs input and output"}), 400
            new_entry = {"type":"example", "input": inp.lower().strip(), "output": out}
            chatbot.add_or_merge_entry(kb, new_entry)

        elif t in ["fact","concept","rule","instruction"]:
            content = payload.get("content")
            if not content:
                return jsonify({"status":"error","message":"content is required"}), 400
            new_entry = {"type": t, "content": content}
            chatbot.add_or_merge_entry(kb, new_entry)

        else:
            return jsonify({"status":"error","message":"unknown type"}), 400

        # persist
        chatbot.save_knowledge(kb)
        return jsonify({"status":"ok"})
    except Exception as e:
        return jsonify({"status":"error","message": str(e)}), 500

@app.route('/knowledge', methods=['GET'])
def knowledge():
    # return entire knowledge list (useful for UI)
    return jsonify(kb)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
