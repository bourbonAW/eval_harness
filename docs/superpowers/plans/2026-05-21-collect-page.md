# /collect Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/collect` page that lets users trigger `workflow_collector` from the browser, with idle/running/success/warning/error states and a 3-tab FAB across all pages.

**Architecture:** `create_app()` gains an optional `collector_fn` param for test injection. Three new routes (`POST /api/collect`, `GET /api/collect/status`, `GET /api/collect/info`) use a closure-held state dict guarded by `threading.Lock`. The background thread writes to a `.tmp` file then atomically `replace()`s `traces.jsonl` on success/warning; on all-fail it deletes the tmp and leaves the original untouched. FAB expands from 2 to 3 tabs in all templates.

**Tech Stack:** Flask, threading, pathlib, pytest, Python 3.11+, uv

---

## File Map

| Action | File |
|--------|------|
| **Modify** | `eval/web.py` |
| **Create** | `eval/templates/collect.html` |
| **Modify** | `eval/templates/annotate.html` |
| **Modify** | `eval/templates/judge.html` |
| **Modify** | `tests/test_web.py` |

---

## Task 1: Write failing tests (RED)

**Files:**
- Modify: `tests/test_web.py`

- [ ] **Step 1: Add imports at top of `tests/test_web.py`**

After the existing `import pytest` block, add:

```python
import threading
import time
```

- [ ] **Step 2: Add helper and fixtures after the existing `client` fixture**

```python
def _make_client(data_dir, collector_fn=None):
    app = create_app(
        traces_path=data_dir / "traces.jsonl",
        questions_path=data_dir / "questions.jsonl",
        dataset_path=data_dir / "dataset.jsonl",
        judge_results_path=data_dir / "judge_results.jsonl",
        annotator="tester",
        collector_fn=collector_fn,
    )
    app.testing = True
    return app.test_client()


@pytest.fixture
def mock_collector_success():
    """2 successes, 0 failures — completes synchronously."""
    ready = threading.Event()

    def _collect(questions_path, output_path):
        output_path.write_text(
            json.dumps({"id": "q_001"}, ensure_ascii=False) + "\n"
            + json.dumps({"id": "q_002"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        ready.set()
        return {"succeeded": 2, "failed": []}

    _collect.ready = ready
    return _collect


@pytest.fixture
def mock_collector_warning():
    """1 success, 1 failure."""
    ready = threading.Event()

    def _collect(questions_path, output_path):
        output_path.write_text(
            json.dumps({"id": "q_001"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        ready.set()
        return {"succeeded": 1, "failed": ["q_002"]}

    _collect.ready = ready
    return _collect


@pytest.fixture
def mock_collector_error():
    """0 successes — all fail, does not write output file."""
    ready = threading.Event()

    def _collect(questions_path, output_path):
        ready.set()
        return {"succeeded": 0, "failed": ["q_001", "q_002"]}

    _collect.ready = ready
    return _collect
```

- [ ] **Step 3: Append collect tests at end of `tests/test_web.py`**

```python
# ── Collect routes ────────────────────────────────────────


def test_get_collect_status_initial_idle(data_dir):
    c = _make_client(data_dir)
    body = c.get("/api/collect/status").get_json()
    assert body["status"] == "idle"
    assert body["succeeded"] == 0
    assert body["failed"] == []


def test_get_collect_info_returns_counts(data_dir):
    c = _make_client(data_dir)
    body = c.get("/api/collect/info").get_json()
    assert body["question_count"] == 2   # SAMPLE_QUESTIONS has 2 entries
    assert body["trace_count"] == 2      # SAMPLE_TRACES has 2 entries


def test_get_collect_page_returns_html(data_dir):
    c = _make_client(data_dir)
    resp = c.get("/collect")
    assert resp.status_code == 200
    assert resp.mimetype == "text/html"
    html = resp.get_data(as_text=True)
    assert "Collect" in html
    assert 'href="/"' in html       # link to 标注
    assert 'href="/judge"' in html  # link to Judge


def test_post_collect_returns_started(data_dir, mock_collector_success):
    c = _make_client(data_dir, mock_collector_success)
    resp = c.post("/api/collect")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "started"
    mock_collector_success.ready.wait(timeout=5)


def test_post_collect_returns_409_when_running(data_dir):
    released = threading.Event()

    def _slow_collect(questions_path, output_path):
        released.wait(timeout=10)
        return {"succeeded": 0, "failed": []}

    c = _make_client(data_dir, _slow_collect)
    # state becomes "running" in main thread before Thread.start(), so
    # a second POST immediately after sees "running" and gets 409
    c.post("/api/collect")
    resp2 = c.post("/api/collect")
    assert resp2.status_code == 409
    released.set()


def test_post_collect_success_state(data_dir, mock_collector_success):
    c = _make_client(data_dir, mock_collector_success)
    c.post("/api/collect")
    mock_collector_success.ready.wait(timeout=5)
    time.sleep(0.05)   # let thread write final state under lock
    body = c.get("/api/collect/status").get_json()
    assert body["status"] == "success"
    assert body["succeeded"] == 2
    assert body["failed"] == []
    assert (data_dir / "traces.jsonl").exists()


def test_post_collect_warning_state(data_dir, mock_collector_warning):
    c = _make_client(data_dir, mock_collector_warning)
    c.post("/api/collect")
    mock_collector_warning.ready.wait(timeout=5)
    time.sleep(0.05)
    body = c.get("/api/collect/status").get_json()
    assert body["status"] == "warning"
    assert body["succeeded"] == 1
    assert body["failed"] == ["q_002"]
    assert (data_dir / "traces.jsonl").exists()


def test_post_collect_error_preserves_old_traces(data_dir, mock_collector_error):
    original = json.dumps({"id": "q_old"}, ensure_ascii=False) + "\n"
    (data_dir / "traces.jsonl").write_text(original, encoding="utf-8")

    c = _make_client(data_dir, mock_collector_error)
    c.post("/api/collect")
    mock_collector_error.ready.wait(timeout=5)
    time.sleep(0.05)

    body = c.get("/api/collect/status").get_json()
    assert body["status"] == "error"
    assert body["succeeded"] == 0
    assert (data_dir / "traces.jsonl").read_text(encoding="utf-8") == original


def test_collect_info_updates_after_collection(data_dir, mock_collector_success):
    c = _make_client(data_dir, mock_collector_success)
    c.post("/api/collect")
    mock_collector_success.ready.wait(timeout=5)
    time.sleep(0.05)
    body = c.get("/api/collect/info").get_json()
    assert body["trace_count"] == 2   # mock wrote 2 traces
```

- [ ] **Step 4: Run tests to verify RED**

```bash
uv run pytest tests/test_web.py -v -k "collect"
```

Expected: collection errors or 404s — none of the routes exist yet.

- [ ] **Step 5: Commit RED**

```bash
git add tests/test_web.py
git commit -m "test: add collect route tests (RED)"
```

---

## Task 2: Implement backend routes in `eval/web.py` (GREEN)

**Files:**
- Modify: `eval/web.py`

- [ ] **Step 1: Add imports at top of `eval/web.py`**

After the existing `import json` line, add:

```python
import threading
import time
```

After the existing `from eval.annotate import ...` line, add:

```python
from eval.collectors.workflow_collector import collect_all as _collect_all_default
```

- [ ] **Step 2: Add `collector_fn=None` to `create_app()` signature**

Change the function signature from:

```python
def create_app(
    *,
    traces_path: Path,
    questions_path: Path,
    dataset_path: Path,
    judge_results_path: Path,
    annotator: str = "unknown",
) -> Flask:
```

To:

```python
def create_app(
    *,
    traces_path: Path,
    questions_path: Path,
    dataset_path: Path,
    judge_results_path: Path,
    annotator: str = "unknown",
    collector_fn=None,
) -> Flask:
```

- [ ] **Step 3: Add collect state and background runner inside `create_app()`**

Immediately after the `app.config["ANNOTATOR"] = annotator` line, add:

```python
    _collector = collector_fn if collector_fn is not None else _collect_all_default
    _cstate: dict = {
        "status": "idle", "message": "", "elapsed_s": 0,
        "succeeded": 0, "failed": [],
    }
    _clock = threading.Lock()

    def _run_collector() -> None:
        tmp = Path(traces_path).with_suffix(".tmp")
        t0 = time.monotonic()
        try:
            summary = _collector(Path(questions_path), tmp)
            elapsed = round(time.monotonic() - t0)
            succeeded: int = summary["succeeded"]
            failed: list = summary["failed"]
            if succeeded == 0:
                tmp.unlink(missing_ok=True)
                new_status = "error"
                msg = f"全部 {len(failed)} 题采集失败，旧文件未修改"
            else:
                tmp.replace(Path(traces_path))
                if failed:
                    new_status = "warning"
                    msg = f"成功 {succeeded} / 失败 {len(failed)}，已写入 traces.jsonl"
                else:
                    new_status = "success"
                    msg = f"成功采集 {succeeded} 条 traces"
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            elapsed = round(time.monotonic() - t0)
            new_status, msg, succeeded, failed = "error", str(exc)[:200], 0, []
        with _clock:
            _cstate.update({
                "status": new_status, "message": msg, "elapsed_s": elapsed,
                "succeeded": succeeded, "failed": failed,
            })
```

- [ ] **Step 4: Add four new routes inside `create_app()`, after `post_judge`**

```python
    @app.get("/collect")
    def index_collect():
        return render_template("collect.html")

    @app.get("/api/collect/status")
    def get_collect_status():
        with _clock:
            return jsonify(dict(_cstate))

    @app.get("/api/collect/info")
    def get_collect_info():
        q_count = sum(1 for _ in load_jsonl(app.config["QUESTIONS_PATH"]))
        t_count = sum(1 for _ in load_jsonl(app.config["TRACES_PATH"]))
        return jsonify({"question_count": q_count, "trace_count": t_count})

    @app.post("/api/collect")
    def post_collect():
        with _clock:
            if _cstate["status"] == "running":
                return jsonify({"error": "already running"}), 409
            _cstate.update({
                "status": "running", "message": "", "elapsed_s": 0,
                "succeeded": 0, "failed": [],
            })
        threading.Thread(target=_run_collector, daemon=True).start()
        return jsonify({"status": "started"})
```

- [ ] **Step 5: Run collect tests to verify GREEN**

```bash
uv run pytest tests/test_web.py -v -k "collect"
```

Expected: all 9 collect tests pass.

- [ ] **Step 6: Run full test suite — no regressions**

```bash
uv run pytest tests/test_web.py -v
```

Expected: all pass.

- [ ] **Step 7: Commit GREEN**

```bash
git add eval/web.py
git commit -m "feat: add collect backend — POST /api/collect, GET /api/collect/status|info"
```

---

## Task 3: Create `eval/templates/collect.html`

**Files:**
- Create: `eval/templates/collect.html`

- [ ] **Step 1: Create the template**

```html
<!DOCTYPE html>
<html lang="zh">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Eval · Collect</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --bg:          #f4f2ee;
      --surface:     #ffffff;
      --surface-2:   #faf9f7;
      --border:      #e8e3da;
      --border-soft: #f0ece4;
      --text:        #1c1a17;
      --text-2:      #6b6356;
      --text-3:      #a8a099;
      --pass:        #16a34a;
      --pass-light:  #f0fdf4;
      --pass-border: #86efac;
      --fail:        #dc2626;
      --fail-light:  #fef2f2;
      --fail-border: #fca5a5;
      --skip:        #c2690a;
      --skip-light:  #fff7ed;
      --skip-border: #fdba74;
      --blue:        #2563eb;
      --blue-light:  #eff6ff;
      --blue-border: #bfdbfe;
      --radius:      8px;
      --sidebar-w:   320px;
    }
    html, body { height: 100%; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "PingFang SC",
                   "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
      background: var(--bg); color: var(--text);
      display: grid;
      grid-template-columns: 1fr var(--sidebar-w);
      grid-template-rows: auto 1fr;
      height: 100vh; overflow: hidden;
    }

    .header {
      grid-column: 1 / -1;
      display: flex; align-items: center; gap: 10px;
      padding: 10px 20px;
      background: var(--surface); border-bottom: 1px solid var(--border);
    }
    .header-label {
      font-size: 10px; font-weight: 700; letter-spacing: .12em;
      text-transform: uppercase; color: var(--text-2);
    }

    .main { overflow-y: auto; padding: 28px 32px; position: relative; }
    .sidebar {
      background: var(--surface); border-left: 1px solid var(--border);
      padding: 20px 18px; overflow-y: auto;
    }

    .page-title { font-size: 18px; font-weight: 700; color: var(--text); margin-bottom: 6px; }
    .page-desc  { font-size: 13px; color: var(--text-2); line-height: 1.65; margin-bottom: 24px; }

    .path-row {
      display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
      font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--text-2);
      background: var(--surface); border: 1px solid var(--border);
      border-radius: 6px; padding: 8px 12px; margin-bottom: 20px;
    }
    .path-badge {
      font-size: 9px; font-weight: 700; letter-spacing: .08em;
      text-transform: uppercase; color: var(--text-3); min-width: 36px;
    }
    .path-arrow { color: var(--text-3); }

    .collect-btn {
      width: 100%; max-width: 400px;
      padding: 14px; font-size: 14px; font-weight: 700;
      background: var(--blue); color: #fff; border: none;
      border-radius: var(--radius); cursor: pointer; font-family: inherit;
      box-shadow: 0 3px 10px rgba(37,99,235,.30);
      transition: all .14s; margin-bottom: 10px; letter-spacing: .01em;
      display: flex; align-items: center; justify-content: center; gap: 8px;
    }
    .collect-btn:hover:not(:disabled) {
      background: #1d4ed8; box-shadow: 0 5px 16px rgba(37,99,235,.38);
      transform: translateY(-1px);
    }
    .collect-btn:active:not(:disabled) { transform: translateY(0); }
    .collect-btn:disabled { opacity: .6; cursor: not-allowed; transform: none; box-shadow: none; }
    .collect-btn.done-success { background: var(--pass); box-shadow: 0 3px 10px rgba(22,163,74,.28); }
    .collect-btn.done-warning { background: var(--skip); box-shadow: 0 3px 10px rgba(194,105,10,.28); }

    @keyframes spin { to { transform: rotate(360deg); } }
    .spinner {
      width: 14px; height: 14px;
      border: 2px solid rgba(255,255,255,.4); border-top-color: #fff;
      border-radius: 50%; animation: spin .7s linear infinite; flex-shrink: 0;
    }

    .status-area {
      max-width: 400px; padding: 10px 14px; border-radius: 6px; border: 1px solid;
      font-size: 12px; line-height: 1.6; font-family: 'JetBrains Mono', monospace;
    }
    .status-area.idle    { background: var(--surface-2); border-color: var(--border);       color: var(--text-3); font-style: italic; }
    .status-area.running { background: var(--blue-light); border-color: var(--blue-border); color: var(--blue); }
    .status-area.success { background: var(--pass-light); border-color: var(--pass-border); color: var(--pass); }
    .status-area.warning { background: var(--skip-light); border-color: var(--skip-border); color: var(--skip); }
    .status-area.error   { background: var(--fail-light); border-color: var(--fail-border); color: var(--fail); }

    .sidebar-label {
      font-size: 9px; font-weight: 700; letter-spacing: .1em;
      text-transform: uppercase; color: var(--text-3);
      margin-bottom: 8px; display: block;
    }
    .stat-card {
      background: var(--surface-2); border: 1px solid var(--border);
      border-radius: 6px; padding: 10px 12px; margin-bottom: 8px;
    }
    .stat-label {
      font-size: 9px; font-weight: 700; letter-spacing: .08em;
      text-transform: uppercase; color: var(--text-3); margin-bottom: 4px;
    }
    .stat-val  { font-size: 22px; font-weight: 700; color: var(--text); font-family: 'JetBrains Mono', monospace; }
    .stat-sub  { font-size: 10px; color: var(--text-3); margin-top: 2px; }

    .page-nav-fab {
      position: fixed; bottom: 20px; right: calc(var(--sidebar-w) + 16px);
      background: var(--surface); border: 1.5px solid var(--border);
      border-radius: 8px; padding: 4px;
      box-shadow: 0 3px 14px rgba(0,0,0,.13);
      display: flex; gap: 3px; z-index: 50;
    }
    .page-nav-fab span, .page-nav-fab a {
      font-family: 'JetBrains Mono', monospace; font-size: 10px; font-weight: 700;
      padding: 5px 12px; border-radius: 5px;
      letter-spacing: .04em; text-decoration: none; transition: all .12s;
    }
    .page-nav-fab .fab-active { background: var(--blue); color: #fff; box-shadow: 0 1px 5px rgba(37,99,235,.3); }
    .page-nav-fab a.fab-link  { color: var(--text-2); }
    .page-nav-fab a.fab-link:hover { background: var(--bg); color: var(--text); }
  </style>
</head>
<body>

<div class="header">
  <span class="header-label">Eval · Collect</span>
</div>

<main class="main">
  <h1 class="page-title">采集 Traces</h1>
  <p class="page-desc">
    从问题集生成 bot 对话 traces，写入 data/traces.jsonl。<br>
    采集完成后前往标注页开始人工标注。
  </p>

  <div class="path-row">
    <span class="path-badge">输入</span>
    <span>data/questions.jsonl</span>
    <span class="path-arrow">→</span>
    <span>data/traces.jsonl</span>
    <span class="path-badge" style="margin-left:auto">输出</span>
  </div>

  <button class="collect-btn" id="collectBtn" onclick="startCollect()">
    ▶  开始采集
  </button>

  <div class="status-area idle" id="statusArea">等待触发…</div>
</main>

<aside class="sidebar">
  <span class="sidebar-label">当前数据集</span>
  <div class="stat-card">
    <div class="stat-label">问题数</div>
    <div class="stat-val" id="questionCount">—</div>
    <div class="stat-sub">data/questions.jsonl</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">已有 Traces</div>
    <div class="stat-val" id="traceCount">—</div>
    <div class="stat-sub">data/traces.jsonl</div>
  </div>
</aside>

<div class="page-nav-fab">
  <span class="fab-active">Collect</span>
  <a href="/" class="fab-link">标注 →</a>
  <a href="/judge" class="fab-link">Judge →</a>
</div>

<script>
  let pollTimer = null;

  async function init() {
    const s = await fetch('/api/collect/status').then(r => r.json());
    renderState(s);
    if (s.status === 'running') startPolling();
    await refreshInfo();
  }

  async function startCollect() {
    const resp = await fetch('/api/collect', { method: 'POST' });
    if (resp.status === 409) return;
    renderState({ status: 'running' });
    startPolling();
  }

  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(async () => {
      const s = await fetch('/api/collect/status').then(r => r.json());
      renderState(s);
      if (s.status !== 'running') {
        clearInterval(pollTimer);
        pollTimer = null;
        await refreshInfo();
      }
    }, 3000);
  }

  async function refreshInfo() {
    const info = await fetch('/api/collect/info').then(r => r.json());
    document.getElementById('questionCount').textContent = info.question_count;
    document.getElementById('traceCount').textContent    = info.trace_count;
  }

  function renderState(s) {
    const btn  = document.getElementById('collectBtn');
    const area = document.getElementById('statusArea');
    area.className = 'status-area ' + s.status;
    btn.className  = 'collect-btn';
    btn.disabled   = false;

    switch (s.status) {
      case 'idle':
        btn.innerHTML   = '▶  开始采集';
        area.textContent = '等待触发…';
        break;
      case 'running':
        btn.innerHTML   = '<div class="spinner"></div>正在采集…';
        btn.disabled    = true;
        area.textContent = '采集中，请稍候（约 1–3 分钟）';
        break;
      case 'success':
        btn.className   = 'collect-btn done-success';
        btn.innerHTML   = '✓  采集完成';
        area.textContent = s.message + (s.elapsed_s ? ` · 耗时 ${s.elapsed_s}s` : '');
        break;
      case 'warning':
        btn.className  = 'collect-btn done-warning';
        btn.innerHTML  = '⚠  部分完成';
        area.innerHTML = s.message
          + (s.failed && s.failed.length
              ? '<br><span style="opacity:.7;font-size:10px">失败：' + s.failed.join(', ') + '</span>'
              : '');
        break;
      case 'error':
        btn.innerHTML   = '▶  重新采集';
        area.textContent = s.message;
        break;
    }
  }

  init();
</script>
</body>
</html>
```

- [ ] **Step 2: Verify template test passes**

```bash
uv run pytest tests/test_web.py::test_get_collect_page_returns_html -v
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add eval/templates/collect.html
git commit -m "feat: add collect.html template with 5-state UI and 3-tab FAB"
```

---

## Task 4: Update FABs to 3 tabs + final verification

**Files:**
- Modify: `eval/templates/annotate.html`
- Modify: `eval/templates/judge.html`

- [ ] **Step 1: Update FAB in `eval/templates/annotate.html`**

Find:
```html
  <div class="page-nav-fab">
    <span class="fab-active">标注</span>
    <a href="/judge" class="fab-link">Judge →</a>
  </div>
```

Replace with:
```html
  <div class="page-nav-fab">
    <a href="/collect" class="fab-link">← Collect</a>
    <span class="fab-active">标注</span>
    <a href="/judge" class="fab-link">Judge →</a>
  </div>
```

- [ ] **Step 2: Update FAB in `eval/templates/judge.html`**

Find:
```html
  <div class="page-nav-fab">
    <a href="/" class="fab-link">← 标注</a>
    <span class="fab-active">Judge</span>
  </div>
```

Replace with:
```html
  <div class="page-nav-fab">
    <a href="/collect" class="fab-link">← Collect</a>
    <a href="/" class="fab-link">← 标注</a>
    <span class="fab-active">Judge</span>
  </div>
```

- [ ] **Step 3: Run full test suite**

```bash
uv run pytest tests/test_web.py -v
```

Expected: all pass. Key regressions to confirm:
- `test_get_root_html_uses_human_annotation_not_latest` — annotate.html still has `href="/judge"` ✓
- `test_get_judge_serves_judge_page` — judge.html still has `href="/"` ✓

- [ ] **Step 4: Commit**

```bash
git add eval/templates/annotate.html eval/templates/judge.html
git commit -m "feat: extend FAB to 3 tabs (Collect / 标注 / Judge) across all pages"
```

---

## Self-Review

**Spec coverage:**

| Spec requirement | Task |
|-----------------|------|
| `/collect` 独立页面，`GET /collect` 路由 | Task 2 Step 4, Task 3 |
| `POST /api/collect` 后台线程，主线程持锁设 running | Task 2 Step 3–4 |
| 写临时文件 + 原子 `replace()` | Task 2 Step 3 `_run_collector` |
| all-fail 时不替换旧文件 | Task 2 Step 3（`succeeded == 0` 分支） |
| `GET /api/collect/status` | Task 2 Step 4 |
| `GET /api/collect/info` | Task 2 Step 4 |
| 并发 409 保护 | Task 2 Step 4 `post_collect` |
| `collector_fn` 可注入 | Task 2 Step 2 |
| 5 种 UI 状态（idle/running/success/warning/error） | Task 3 Step 1 `renderState()` |
| 页面初始化先 `GET /api/collect/status` | Task 3 Step 1 `init()` |
| 采集完成后刷新侧边栏统计 | Task 3 Step 1 `startPolling` → `refreshInfo()` |
| warning 显示 failed ID 列表 | Task 3 Step 1 `renderState` warning 分支 |
| FAB 三态扩展（Collect / 标注 / Judge） | Task 4 |

**Placeholder scan:** No TBD, TODO, or vague steps.

**Type consistency:**
- `summary["succeeded"]` / `summary["failed"]` — matches `collect_all()` return `{"succeeded": int, "failed": list[str]}` ✓
- `_cstate` keys used in `post_collect`, `get_collect_status`, `_run_collector` are identical ✓
- `_make_client()` signature matches updated `create_app()` ✓
- `fab-active` / `fab-link` CSS classes defined in `collect.html` and already present in `annotate.html` / `judge.html` ✓
