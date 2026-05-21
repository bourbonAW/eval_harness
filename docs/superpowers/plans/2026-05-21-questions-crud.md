# Questions CRUD Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add question management (list/add/edit/delete) to the `/collect` page via a Tab switcher, backed by 4 REST endpoints that atomically rewrite `questions.jsonl`.

**Architecture:** Backend adds `GET/POST /api/questions` and `PUT/DELETE /api/questions/<id>` to `eval/web.py` using atomic `.tmp` → `Path.replace()` writes and a `_questions_lock`. Frontend adds a Tab switcher to `collect.html`'s header; the new「问题集」tab renders a CRUD list using DOM APIs (no innerHTML with user content).

**Tech Stack:** Flask, Python threading, JSONL, vanilla JS DOM API

---

## Task 1: Write failing backend tests (RED)

**Files:**
- Modify: `tests/test_web.py` (append after line 642)

- [ ] **Step 1: Append 8 tests to tests/test_web.py**

Add after the last line of the file:

```python
# ── Questions CRUD routes ─────────────────────────────────


def test_get_questions_returns_list(data_dir):
    c = _make_client(data_dir)
    body = c.get("/api/questions").get_json()
    assert isinstance(body, list)
    assert len(body) == 2
    assert body[0]["id"] == "q_001"
    assert "question" in body[0]
    assert "expected_answer" in body[0]


def test_get_questions_empty_when_no_file(tmp_path):
    app = create_app(
        traces_path=tmp_path / "traces.jsonl",
        questions_path=tmp_path / "questions.jsonl",
        dataset_path=tmp_path / "dataset.jsonl",
        judge_results_path=tmp_path / "judge_results.jsonl",
        annotator="tester",
    )
    app.testing = True
    body = app.test_client().get("/api/questions").get_json()
    assert body == []


def test_post_question_creates_with_auto_id(data_dir):
    c = _make_client(data_dir)
    resp = c.post("/api/questions", json={"question": "新问题？", "expected_answer": "新回答"})
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["id"] == "q_003"
    assert body["question"] == "新问题？"
    assert body["expected_answer"] == "新回答"
    assert body["knowledge_type"] == "文档"
    assert body["conversation_history"] == []
    assert len(c.get("/api/questions").get_json()) == 3


def test_post_question_validates_required_fields(data_dir):
    c = _make_client(data_dir)
    assert c.post("/api/questions", json={"question": "", "expected_answer": "x"}).status_code == 400
    assert c.post("/api/questions", json={"question": "x", "expected_answer": "  "}).status_code == 400
    assert c.post("/api/questions", json={"question": "x"}).status_code == 400


def test_put_question_updates_fields(data_dir):
    c = _make_client(data_dir)
    resp = c.put("/api/questions/q_001", json={"question": "修改后问题", "expected_answer": "修改后回答"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["question"] == "修改后问题"
    assert body["expected_answer"] == "修改后回答"
    assert body["knowledge_type"] == SAMPLE_QUESTIONS[0]["knowledge_type"]
    assert body["is_prohibited"] == SAMPLE_QUESTIONS[0]["is_prohibited"]


def test_put_question_unknown_id_returns_404(data_dir):
    c = _make_client(data_dir)
    resp = c.put("/api/questions/q_999", json={"question": "x", "expected_answer": "y"})
    assert resp.status_code == 404


def test_delete_question_removes_entry(data_dir):
    c = _make_client(data_dir)
    resp = c.delete("/api/questions/q_001")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    remaining = c.get("/api/questions").get_json()
    assert len(remaining) == 1
    assert remaining[0]["id"] == "q_002"


def test_delete_question_unknown_id_returns_404(data_dir):
    c = _make_client(data_dir)
    assert c.delete("/api/questions/q_999").status_code == 404
```

- [ ] **Step 2: Add atomic write smoke test**

Also append this test (verifies `.tmp` is cleaned up after successful write):

```python
def test_save_questions_no_tmp_after_success(tmp_path):
    from eval.web import _save_questions
    questions = [{"id": "q_001", "question": "q", "expected_answer": "a"}]
    path = tmp_path / "questions.jsonl"
    _save_questions(questions, path)
    assert path.exists()
    assert not (tmp_path / "questions.tmp").exists()
    assert _read_jsonl(path) == questions
```

- [ ] **Step 3: Run tests to verify RED**

```bash
uv run pytest tests/test_web.py -v -k "question" --tb=short
```

Expected: **8 FAILED** with `404 Not Found` or similar (routes don't exist yet); `test_save_questions_no_tmp_after_success` **1 ERROR** (`ImportError: cannot import _save_questions`).

---

## Task 2: Implement backend routes (GREEN)

**Files:**
- Modify: `eval/web.py`

- [ ] **Step 3: Add `import re` to imports**

In `eval/web.py`, find:
```python
import argparse
import json
```
Replace with:
```python
import argparse
import json
import re
```

- [ ] **Step 4: Add module-level helpers after `save_judge_result()`**

In `eval/web.py`, find:
```python
def create_app(
```
Insert before it:

```python
_DEFAULT_QUESTION_FIELDS: dict = {
    "source_policy_url": "",
    "source_doc_url": "",
    "source_doc_name": "",
    "is_multi_intent": False,
    "knowledge_type": "文档",
    "is_prohibited": False,
    "conversation_history": [],
    "notes": "",
}


def _next_q_id(questions: list[dict]) -> str:
    nums = []
    for q in questions:
        m = re.match(r"q_(\d+)$", q.get("id", ""))
        if m:
            nums.append(int(m.group(1)))
    n = max(nums) + 1 if nums else 1
    return f"q_{n:03d}"


def _save_questions(questions: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for q in questions:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")
    tmp.replace(path)


```

- [ ] **Step 5: Add `_questions_lock` inside `create_app()`**

In `eval/web.py`, find:
```python
    _collect_lock = threading.Lock()
```
Replace with:
```python
    _collect_lock = threading.Lock()
    _questions_lock = threading.Lock()
```

- [ ] **Step 6: Add 4 question routes inside `create_app()`**

In `eval/web.py`, find:
```python
    return app
```
Insert before it:

```python
    @app.get("/api/questions")
    def get_questions():
        return jsonify(load_jsonl(app.config["QUESTIONS_PATH"]))

    @app.post("/api/questions")
    def post_question():
        body = request.get_json(silent=True) or {}
        question = (body.get("question") or "").strip()
        expected_answer = (body.get("expected_answer") or "").strip()
        if not question:
            return jsonify({"error": "question 不能为空"}), 400
        if not expected_answer:
            return jsonify({"error": "expected_answer 不能为空"}), 400
        with _questions_lock:
            questions = load_jsonl(app.config["QUESTIONS_PATH"])
            new_q = {
                "id": _next_q_id(questions),
                "question": question,
                "expected_answer": expected_answer,
                **_DEFAULT_QUESTION_FIELDS,
            }
            questions.append(new_q)
            _save_questions(questions, app.config["QUESTIONS_PATH"])
        return jsonify(new_q), 201

    @app.put("/api/questions/<qid>")
    def put_question(qid: str):
        body = request.get_json(silent=True) or {}
        question = (body.get("question") or "").strip()
        expected_answer = (body.get("expected_answer") or "").strip()
        if not question:
            return jsonify({"error": "question 不能为空"}), 400
        if not expected_answer:
            return jsonify({"error": "expected_answer 不能为空"}), 400
        with _questions_lock:
            questions = load_jsonl(app.config["QUESTIONS_PATH"])
            target = next((q for q in questions if q["id"] == qid), None)
            if target is None:
                return jsonify({"error": f"id {qid!r} 不存在"}), 404
            target["question"] = question
            target["expected_answer"] = expected_answer
            _save_questions(questions, app.config["QUESTIONS_PATH"])
        return jsonify(target)

    @app.delete("/api/questions/<qid>")
    def delete_question(qid: str):
        with _questions_lock:
            questions = load_jsonl(app.config["QUESTIONS_PATH"])
            filtered = [q for q in questions if q["id"] != qid]
            if len(filtered) == len(questions):
                return jsonify({"error": f"id {qid!r} 不存在"}), 404
            _save_questions(filtered, app.config["QUESTIONS_PATH"])
        return jsonify({"ok": True})

```

- [ ] **Step 7: Run tests to verify GREEN**

```bash
uv run pytest tests/test_web.py -v -k "question" --tb=short
```

Expected: **9 PASSED** (8 CRUD + 1 atomic write smoke test)

- [ ] **Step 8: Run full suite to verify no regression**

```bash
uv run pytest tests/test_web.py -v --tb=short
```

Expected: **45 passed** (36 existing + 9 new)

- [ ] **Step 9: Commit**

```bash
git add eval/web.py tests/test_web.py
git commit -m "feat: add questions CRUD API (GET/POST/PUT/DELETE /api/questions)"
```

---

## Task 3: Frontend — Tab switcher + Questions panel

**Files:**
- Modify: `eval/templates/collect.html`

The current file has:
- `</style>` at line 299
- `<div class="header">` block at lines 303–305
- `<main class="main">` block at lines 307–327
- `<script>` block starting at line 349

- [ ] **Step 10: Add Tab and Questions CSS before `</style>`**

In `eval/templates/collect.html`, find:
```css
    @media (max-width: 760px) {
```
Insert before it:

```css
    /* ─── Header tabs ─── */
    .header-tabs {
      display: flex;
      gap: 2px;
      margin-left: 16px;
    }
    .htab {
      font-family: 'JetBrains Mono', monospace;
      font-size: 10px;
      font-weight: 700;
      letter-spacing: .05em;
      padding: 4px 12px;
      border-radius: 5px;
      border: none;
      background: transparent;
      color: var(--text-3);
      cursor: pointer;
      transition: all .12s;
    }
    .htab:hover { background: var(--bg); color: var(--text-2); }
    .htab.htab-active {
      background: var(--blue);
      color: #fff;
      box-shadow: 0 1px 5px rgba(37,99,235,.3);
    }

    /* ─── Questions panel ─── */
    .q-toolbar {
      display: flex;
      align-items: center;
      gap: 12px;
      margin-bottom: 16px;
    }
    .q-count {
      font-size: 13px;
      font-weight: 700;
      color: var(--text);
      flex: 1;
    }
    .q-add-btn {
      font-family: inherit;
      font-size: 12px;
      font-weight: 700;
      padding: 6px 14px;
      background: var(--blue);
      color: #fff;
      border: none;
      border-radius: var(--radius);
      cursor: pointer;
      box-shadow: 0 2px 6px rgba(37,99,235,.25);
      transition: background .12s;
    }
    .q-add-btn:hover { background: #1d4ed8; }

    .q-add-form {
      background: var(--blue-light);
      border: 1px solid var(--blue-border);
      border-radius: var(--radius);
      padding: 12px 14px;
      margin-bottom: 8px;
    }

    .q-row {
      border: 1px solid var(--border);
      border-radius: var(--radius);
      background: var(--surface);
      margin-bottom: 6px;
      overflow: hidden;
    }
    .q-row-head {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 10px 14px;
    }
    .q-id {
      font-family: 'JetBrains Mono', monospace;
      font-size: 10px;
      font-weight: 700;
      color: var(--text-3);
      min-width: 44px;
      flex-shrink: 0;
    }
    .q-text {
      flex: 1;
      font-size: 13px;
      color: var(--text);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .q-btns { display: flex; gap: 4px; flex-shrink: 0; }
    .q-btn {
      font-family: inherit;
      font-size: 11px;
      padding: 3px 10px;
      border-radius: 4px;
      border: 1px solid var(--border);
      background: var(--surface-2);
      color: var(--text-2);
      cursor: pointer;
      transition: all .1s;
    }
    .q-btn:hover { background: var(--bg); }
    .q-btn.del:hover {
      color: var(--fail);
      border-color: #fca5a5;
      background: var(--fail-light);
    }
    .q-edit-form {
      padding: 12px 14px;
      background: var(--blue-light);
      border-top: 1px solid var(--blue-border);
    }
    .q-del-confirm {
      padding: 8px 14px;
      background: var(--fail-light);
      border-top: 1px solid #fca5a5;
      display: flex;
      align-items: center;
      gap: 10px;
      font-size: 12px;
      color: var(--fail);
      font-family: 'JetBrains Mono', monospace;
    }
    .q-del-btns { display: flex; gap: 6px; }
    .q-del-btn {
      font-family: inherit;
      font-size: 11px;
      font-weight: 700;
      padding: 3px 10px;
      border-radius: 4px;
      border: 1px solid;
      cursor: pointer;
    }
    .q-del-btn.ok { background: var(--fail); color: #fff; border-color: var(--fail); }
    .q-del-btn.ok:hover { background: #b91c1c; }
    .q-del-btn.cancel { background: var(--surface); color: var(--text-2); border-color: var(--border); }

    .form-field { margin-bottom: 10px; }
    .form-field label {
      display: block;
      font-size: 10px;
      font-weight: 700;
      letter-spacing: .06em;
      text-transform: uppercase;
      color: var(--text-3);
      margin-bottom: 4px;
    }
    .form-field textarea {
      width: 100%;
      padding: 7px 10px;
      border: 1px solid var(--blue-border);
      border-radius: 5px;
      font-size: 13px;
      font-family: inherit;
      background: #fff;
      color: var(--text);
      resize: vertical;
      min-height: 64px;
    }
    .form-actions { display: flex; gap: 8px; margin-top: 2px; }
    .form-btn-save {
      font-family: inherit;
      font-size: 12px;
      font-weight: 700;
      padding: 6px 16px;
      background: var(--blue);
      color: #fff;
      border: none;
      border-radius: 5px;
      cursor: pointer;
    }
    .form-btn-cancel {
      font-family: inherit;
      font-size: 12px;
      padding: 6px 14px;
      background: var(--surface);
      color: var(--text-2);
      border: 1px solid var(--border);
      border-radius: 5px;
      cursor: pointer;
    }

```

- [ ] **Step 11: Add Tab switcher to header**

In `eval/templates/collect.html`, find:
```html
<div class="header">
  <span class="header-label">Eval · Collect</span>
</div>
```
Replace with:
```html
<div class="header">
  <span class="header-label">Eval · Collect</span>
  <div class="header-tabs">
    <button class="htab htab-active" id="tabCollect" onclick="switchTab('collect')">采集</button>
    <button class="htab" id="tabQuestions" onclick="switchTab('questions')">问题集</button>
  </div>
</div>
```

- [ ] **Step 12: Wrap existing main content + add questions panel**

In `eval/templates/collect.html`, find:
```html
<main class="main">
  <h1 class="page-title">采集 Traces</h1>
```
Replace with:
```html
<main class="main">
<div id="collectPanel">
  <h1 class="page-title">采集 Traces</h1>
```

Then find:
```html
  <div class="status-area idle" id="statusArea">等待触发...</div>
</main>
```
Replace with:
```html
  <div class="status-area idle" id="statusArea">等待触发...</div>
</div><!-- #collectPanel -->

<div id="questionsPanel" style="display:none">
  <div class="q-toolbar">
    <span class="q-count" id="qCount">问题集 · 0 条</span>
    <button class="q-add-btn" onclick="startAdd()">+ 添加问题</button>
  </div>
  <div id="qAddRow" class="q-add-form" style="display:none">
    <div class="form-field">
      <label>问题</label>
      <textarea id="fQ_add" rows="3" placeholder="输入问题..."></textarea>
    </div>
    <div class="form-field">
      <label>期望回答</label>
      <textarea id="fA_add" rows="3" placeholder="输入期望回答..."></textarea>
    </div>
    <div class="form-actions">
      <button class="form-btn-save" onclick="saveAdd()">添加</button>
      <button class="form-btn-cancel" onclick="cancelAdd()">取消</button>
    </div>
  </div>
  <div id="questionsList"></div>
</div>
</main>
```

- [ ] **Step 13: Add CRUD JavaScript before `</script>`**

In `eval/templates/collect.html`, find:
```js
init();
</script>
```
Replace with:
```js
init();

// ─── Tab switching ────────────────────────────────────────
function switchTab(tab) {
  _closeActive();  // discard any unsaved form before switching tabs
  const toCollect = tab === 'collect';
  document.getElementById('collectPanel').style.display = toCollect ? '' : 'none';
  document.getElementById('questionsPanel').style.display = toCollect ? 'none' : '';
  document.getElementById('tabCollect').classList.toggle('htab-active', toCollect);
  document.getElementById('tabQuestions').classList.toggle('htab-active', !toCollect);
  if (!toCollect) renderQuestions();
}

// ─── Questions CRUD ───────────────────────────────────────
let _questions = [];
let _activeEditId = null; // null | 'add' | 'del_<id>' | '<id>'

async function loadQuestions() {
  try {
    _questions = await fetch('/api/questions').then(r => r.json());
  } catch (e) {
    console.error('loadQuestions failed', e);
  }
}

function renderQuestions() {
  const list = document.getElementById('questionsList');
  list.innerHTML = '';
  document.getElementById('qCount').textContent = `问题集 · ${_questions.length} 条`;
  _questions.forEach(q => list.appendChild(_buildRow(q)));
}

function _buildRow(q) {
  const row = document.createElement('div');
  row.className = 'q-row';
  row.id = 'row_' + q.id;

  // header line
  const head = document.createElement('div');
  head.className = 'q-row-head';
  const idSpan = document.createElement('span');
  idSpan.className = 'q-id';
  idSpan.textContent = q.id;
  const textSpan = document.createElement('span');
  textSpan.className = 'q-text';
  textSpan.title = q.question;
  textSpan.textContent = q.question;
  const btns = document.createElement('span');
  btns.className = 'q-btns';
  const editBtn = document.createElement('button');
  editBtn.className = 'q-btn';
  editBtn.textContent = '编辑';
  editBtn.onclick = () => startEdit(q.id);
  const delBtn = document.createElement('button');
  delBtn.className = 'q-btn del';
  delBtn.textContent = '删';
  delBtn.onclick = () => startDelete(q.id);
  btns.append(editBtn, delBtn);
  head.append(idSpan, textSpan, btns);
  row.appendChild(head);

  // inline edit form (hidden)
  const form = document.createElement('div');
  form.className = 'q-edit-form';
  form.id = 'editForm_' + q.id;
  form.style.display = 'none';
  const qField = _makeField('问题', 'fQ_' + q.id);
  const aField = _makeField('期望回答', 'fA_' + q.id);
  const formActions = document.createElement('div');
  formActions.className = 'form-actions';
  const saveBtn = document.createElement('button');
  saveBtn.className = 'form-btn-save';
  saveBtn.textContent = '保存';
  saveBtn.onclick = () => saveEdit(q.id);
  const cancelBtn = document.createElement('button');
  cancelBtn.className = 'form-btn-cancel';
  cancelBtn.textContent = '取消';
  cancelBtn.onclick = () => cancelEdit(q.id);
  formActions.append(saveBtn, cancelBtn);
  form.append(qField, aField, formActions);
  row.appendChild(form);

  // delete confirm (hidden)
  const delConfirm = document.createElement('div');
  delConfirm.className = 'q-del-confirm';
  delConfirm.id = 'delConfirm_' + q.id;
  delConfirm.style.display = 'none';
  const msg = document.createElement('span');
  msg.textContent = '确认删除？';
  const delBtns = document.createElement('span');
  delBtns.className = 'q-del-btns';
  const okBtn = document.createElement('button');
  okBtn.className = 'q-del-btn ok';
  okBtn.textContent = '✓';
  okBtn.onclick = () => confirmDelete(q.id);
  const cnclBtn = document.createElement('button');
  cnclBtn.className = 'q-del-btn cancel';
  cnclBtn.textContent = '✗';
  cnclBtn.onclick = () => cancelDelete(q.id);
  delBtns.append(okBtn, cnclBtn);
  delConfirm.append(msg, delBtns);
  row.appendChild(delConfirm);

  return row;
}

function _makeField(labelText, textareaId) {
  const field = document.createElement('div');
  field.className = 'form-field';
  const label = document.createElement('label');
  label.textContent = labelText;
  const ta = document.createElement('textarea');
  ta.id = textareaId;
  ta.rows = 3;
  field.append(label, ta);
  return field;
}

function _closeActive() {
  if (_activeEditId === null) return;
  if (_activeEditId === 'add') {
    document.getElementById('qAddRow').style.display = 'none';
  } else if (_activeEditId.startsWith('del_')) {
    document.getElementById('delConfirm_' + _activeEditId.slice(4)).style.display = 'none';
  } else {
    document.getElementById('editForm_' + _activeEditId).style.display = 'none';
  }
  _activeEditId = null;
}

function startEdit(qid) {
  if (_activeEditId === qid) return;
  _closeActive();
  const q = _questions.find(q => q.id === qid);
  document.getElementById('fQ_' + qid).value = q.question;
  document.getElementById('fA_' + qid).value = q.expected_answer;
  document.getElementById('editForm_' + qid).style.display = '';
  _activeEditId = qid;
}

function cancelEdit(qid) {
  document.getElementById('editForm_' + qid).style.display = 'none';
  _activeEditId = null;
}

async function saveEdit(qid) {
  const question = document.getElementById('fQ_' + qid).value.trim();
  const expected_answer = document.getElementById('fA_' + qid).value.trim();
  if (!question || !expected_answer) return;
  const resp = await fetch('/api/questions/' + qid, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, expected_answer }),
  });
  if (!resp.ok) return;
  const updated = await resp.json();
  const idx = _questions.findIndex(q => q.id === qid);
  if (idx !== -1) _questions[idx] = updated;
  _activeEditId = null;
  renderQuestions();
}

function startDelete(qid) {
  _closeActive();
  document.getElementById('delConfirm_' + qid).style.display = '';
  _activeEditId = 'del_' + qid;
}

function cancelDelete(qid) {
  document.getElementById('delConfirm_' + qid).style.display = 'none';
  _activeEditId = null;
}

async function confirmDelete(qid) {
  const resp = await fetch('/api/questions/' + qid, { method: 'DELETE' });
  if (!resp.ok) return;
  _questions = _questions.filter(q => q.id !== qid);
  _activeEditId = null;
  renderQuestions();
  await refreshInfo();
}

function startAdd() {
  _closeActive();
  document.getElementById('fQ_add').value = '';
  document.getElementById('fA_add').value = '';
  document.getElementById('qAddRow').style.display = '';
  _activeEditId = 'add';
}

function cancelAdd() {
  document.getElementById('qAddRow').style.display = 'none';
  _activeEditId = null;
}

async function saveAdd() {
  const question = document.getElementById('fQ_add').value.trim();
  const expected_answer = document.getElementById('fA_add').value.trim();
  if (!question || !expected_answer) return;
  const resp = await fetch('/api/questions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, expected_answer }),
  });
  if (!resp.ok) return;
  const newQ = await resp.json();
  _questions.push(newQ);
  _activeEditId = null;
  document.getElementById('qAddRow').style.display = 'none';
  renderQuestions();
  await refreshInfo();
}
</script>
```

- [ ] **Step 14: Patch `init()` to preload questions**

In `eval/templates/collect.html`, find:
```js
async function init() {
  try {
    const s = await fetch('/api/collect/status').then(r => r.json());
    renderState(s);
    if (s.status === 'running') startPolling();
    await refreshInfo();
  } catch (e) {
    renderState({ status: 'error', message: '加载失败：' + e.message, failed: [] });
  }
}
```
Replace with:
```js
async function init() {
  try {
    const s = await fetch('/api/collect/status').then(r => r.json());
    renderState(s);
    if (s.status === 'running') startPolling();
    await refreshInfo();
    await loadQuestions();
  } catch (e) {
    renderState({ status: 'error', message: '加载失败：' + e.message, failed: [] });
  }
}
```

- [ ] **Step 15: Run full test suite**

```bash
uv run pytest tests/test_web.py -v --tb=short
```

Expected: **45 passed**

- [ ] **Step 16: Manual smoke test**

```bash
uv run python -m eval.web --port 5000 --annotator test
```

Open http://127.0.0.1:5000/collect and verify:

1. **Tab 切换**：点「问题集」Tab → 显示问题列表，计数正确
2. **编辑**：点任意行「编辑」→ 行内展开表单，预填当前值；修改后「保存」→ 列表刷新
3. **切 Tab 丢弃**：打开编辑表单后切到「采集」Tab 再切回 → 表单已关闭，再次点「编辑」能正常打开（`_activeEditId` 已清空）
4. **删除确认**：点「删」→ 出现「确认删除？ ✓ ✗」；点 ✓ 删除，点 ✗ 恢复原状
5. **新增**：点「+ 添加问题」→ 顶部出现空表单；填写后「添加」→ 新问题出现在列表末尾，侧边栏「问题数」+1
6. **重置**：切到「采集」Tab → 侧边栏统计数值正确

- [ ] **Step 17: Commit**

```bash
git add eval/templates/collect.html
git commit -m "feat: add questions tab with CRUD UI to /collect page"
```
