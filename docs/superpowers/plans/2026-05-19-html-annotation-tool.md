# HTML Annotation Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the terminal-based annotation CLI with a Flask + single-HTML web UI for Stage 2 eval labeling, while keeping the CLI in place and reusing the same `data/*.jsonl` pipeline.

**Architecture:** Single-process local Flask app (127.0.0.1, no auth). Backend exposes `GET /api/traces` and `POST /api/annotate`; frontend is one HTML file with HTML+CSS+native JS (no build chain). `dataset.jsonl` is append-only — same `id` may appear multiple times, and the loader returns the last version per id. Spec: `docs/superpowers/specs/2026-05-19-html-annotation-tool-design.md`.

**Tech Stack:** Python 3.11+, `uv`, Flask 3.x, existing rich/pytest. No frontend tooling.

---

## File Structure

```
eval/
├── annotate.py            # Modify: add load_latest_annotations()
├── annotate_web.py        # Create: Flask app factory + __main__ entry
└── templates/
    └── annotate.html      # Create: single HTML file (HTML + CSS + native JS)
tests/
├── test_annotate.py       # Modify: add tests for load_latest_annotations
└── test_annotate_web.py   # Create: route tests via app.test_client()
pyproject.toml             # Modify: add flask>=3.0
```

Each file's responsibility:
- `eval/annotate.py` — JSONL helpers (`load_jsonl`, `save_annotation`, `load_latest_annotations`), category dict, CLI entry. Shared between CLI and web.
- `eval/annotate_web.py` — Flask `create_app()` factory + `if __name__ == "__main__":` entry with argparse. No business logic that the CLI doesn't also need.
- `eval/templates/annotate.html` — UI (list strip + detail panel + form), all client logic inline.
- `tests/test_annotate.py` — unit tests for helpers.
- `tests/test_annotate_web.py` — HTTP route tests using `app.test_client()` and `tmp_path` for data file isolation.

---

### Task 1: Add Flask Dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add `flask>=3.0` to dependencies**

Edit `pyproject.toml` so the `dependencies` list contains an additional line for Flask. After the change the section reads:

```toml
[project]
name = "customer-service-chat-bot-eval"
version = "0.1.0"
description = "Stage 2 eval dataset pipeline for a customer service chatbot."
readme = "eval-flywheel-plan.md"
requires-python = ">=3.11"
dependencies = [
    "openpyxl>=3.1.0",
    "requests>=2.31.0",
    "rich>=13.7.0",
    "python-dotenv>=1.0.0",
    "pytest>=8.0.0",
    "flask>=3.0.0",
]

[tool.pytest.ini_options]
pythonpath = ["."]
```

- [ ] **Step 2: Sync dependencies**

```bash
uv sync
```

Expected: `uv.lock` updated, install completes without errors.

- [ ] **Step 3: Verify import**

```bash
uv run python -c "import flask; print(flask.__version__)"
```

Expected: prints a version `3.x.x`.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add flask>=3.0 dependency for HTML annotation tool"
```

---

### Task 2: `load_latest_annotations` Helper (TDD)

**Files:**
- Modify: `eval/annotate.py` (add one function)
- Modify: `tests/test_annotate.py` (add two tests)

**Why:** Append-only `dataset.jsonl` may contain multiple records per `id` (when a user revises). Both web and any future tooling need a single map of `id → latest sample`.

- [ ] **Step 1: Write failing tests**

Open `tests/test_annotate.py` and add these tests at the bottom of the file (keep existing tests untouched). Also add the new import.

At the top, change the import line:

```python
from eval.annotate import load_jsonl, needs_annotation, save_annotation, load_latest_annotations
```

At the bottom, append:

```python
def test_load_latest_annotations_picks_last_version(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    base: AnnotatedSample = {
        **SAMPLE_TRACE,
        "complete_question": SAMPLE_TRACE["question"],
        "doc_context": "",
        "faq_context": "",
        "references": [],
        "ref_num": 0,
        "expected_answer": "500万元。",
        "label": "fail",
        "critique": "缺少引用",
        "failure_category": "citation_error",
        "annotated_by": "tester",
        "annotated_at": "2026-05-18T10:00:00+00:00",
    }
    revised: AnnotatedSample = {
        **base,
        "label": "pass",
        "critique": "",
        "failure_category": None,
        "annotated_at": "2026-05-18T11:00:00+00:00",
    }
    save_annotation(base, dataset)
    save_annotation(revised, dataset)

    latest = load_latest_annotations(dataset)
    assert set(latest.keys()) == {SAMPLE_TRACE["id"]}
    assert latest[SAMPLE_TRACE["id"]]["label"] == "pass"
    assert latest[SAMPLE_TRACE["id"]]["annotated_at"] == "2026-05-18T11:00:00+00:00"


def test_load_latest_annotations_empty_for_missing_file(tmp_path):
    assert load_latest_annotations(tmp_path / "no.jsonl") == {}
```

Note: `SAMPLE_TRACE` already exists in `test_annotate.py` from the existing tests — reuse it. The new fields (`complete_question`, `doc_context`, `faq_context`, `references`, `ref_num`) are required by the current `AnnotatedSample` TypedDict; include them so type-checkers are happy.

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_annotate.py -v
```

Expected: `ImportError: cannot import name 'load_latest_annotations'` (collection error fails all tests).

- [ ] **Step 3: Implement the function**

Edit `eval/annotate.py`. After the existing `load_annotated_ids` function and before `needs_annotation`, add:

```python
def load_latest_annotations(dataset_path: Path) -> dict[str, AnnotatedSample]:
    # dataset.jsonl is append-only — last occurrence per id wins.
    latest: dict[str, AnnotatedSample] = {}
    for sample in load_jsonl(dataset_path):
        latest[sample["id"]] = sample
    return latest
```

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run pytest tests/test_annotate.py -v
```

Expected: all tests in `test_annotate.py` pass (the 7 existing + 2 new = 9).

- [ ] **Step 5: Commit**

```bash
git add eval/annotate.py tests/test_annotate.py
git commit -m "feat: load_latest_annotations() — last-wins reader for append-only dataset"
```

---

### Task 3: Flask App Factory + `GET /api/traces` (TDD)

**Files:**
- Create: `eval/annotate_web.py`
- Create: `tests/test_annotate_web.py`

**Contract:** `create_app(traces_path, questions_path, dataset_path, annotator) -> Flask`. Tests construct the app with a `tmp_path`-backed data dir, exercise `app.test_client()`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_annotate_web.py`:

```python
import json
from pathlib import Path

import pytest

from eval.annotate_web import create_app

SAMPLE_QUESTIONS = [
    {
        "id": "q_001",
        "question": "最低要求是多少？",
        "expected_answer": "500万元。",
        "source_policy_url": "https://policy.example.com",
        "source_doc_url": "https://doc.example.com",
        "source_doc_name": "指南.docx",
        "is_multi_intent": False,
        "knowledge_type": "文档",
        "is_prohibited": False,
        "conversation_history": [],
        "notes": "",
    },
    {
        "id": "q_002",
        "question": "知识产权要求？",
        "expected_answer": "至少3件发明专利。",
        "source_policy_url": "https://policy.example.com",
        "source_doc_url": "https://doc.example.com",
        "source_doc_name": "指南.docx",
        "is_multi_intent": False,
        "knowledge_type": "文档",
        "is_prohibited": False,
        "conversation_history": [],
        "notes": "",
    },
]

SAMPLE_TRACES = [
    {
        "id": "q_001",
        "question_id": "q_001",
        "question": "最低要求是多少？",
        "complete_question": "最低要求是多少？",
        "conversation_history": [],
        "actual_answer": "根据政策，最低为500万元。",
        "doc_context": "原文：不低于500万元",
        "faq_context": "",
        "references": [{"doc_id": 1, "name": "指南", "url": "https://doc.example.com"}],
        "ref_num": 1,
    },
    {
        "id": "q_002",
        "question_id": "q_002",
        "question": "知识产权要求？",
        "complete_question": "知识产权要求？",
        "conversation_history": [],
        "actual_answer": "至少3件发明专利。",
        "doc_context": "",
        "faq_context": "FAQ: 关于专利数量",
        "references": [],
        "ref_num": 0,
    },
]


def _write_jsonl(path: Path, items: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(it, ensure_ascii=False) for it in items) + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    _write_jsonl(tmp_path / "questions.jsonl", SAMPLE_QUESTIONS)
    _write_jsonl(tmp_path / "traces.jsonl", SAMPLE_TRACES)
    return tmp_path


@pytest.fixture
def client(data_dir):
    app = create_app(
        traces_path=data_dir / "traces.jsonl",
        questions_path=data_dir / "questions.jsonl",
        dataset_path=data_dir / "dataset.jsonl",
        annotator="tester",
    )
    app.testing = True
    return app.test_client()


def test_get_traces_returns_all_with_status(client):
    resp = client.get("/api/traces")
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body) == 2
    ids = [entry["trace"]["id"] for entry in body]
    assert ids == ["q_001", "q_002"]
    for entry in body:
        assert entry["latest_annotation"] is None


def test_get_traces_merges_question_expected_answer(client):
    body = client.get("/api/traces").get_json()
    by_id = {entry["trace"]["id"]: entry for entry in body}
    assert by_id["q_001"]["expected_answer"] == "500万元。"
    assert by_id["q_002"]["expected_answer"] == "至少3件发明专利。"


def test_get_traces_reflects_existing_dataset(client, data_dir):
    sample = {
        "id": "q_001",
        "question_id": "q_001",
        "question": "最低要求是多少？",
        "complete_question": "最低要求是多少？",
        "conversation_history": [],
        "actual_answer": "根据政策，最低为500万元。",
        "doc_context": "原文：不低于500万元",
        "faq_context": "",
        "references": [],
        "ref_num": 1,
        "expected_answer": "500万元。",
        "label": "pass",
        "critique": "",
        "failure_category": None,
        "annotated_by": "tester",
        "annotated_at": "2026-05-18T10:00:00+00:00",
    }
    (data_dir / "dataset.jsonl").write_text(
        json.dumps(sample, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    body = client.get("/api/traces").get_json()
    by_id = {entry["trace"]["id"]: entry for entry in body}
    assert by_id["q_001"]["latest_annotation"]["label"] == "pass"
    assert by_id["q_002"]["latest_annotation"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_annotate_web.py -v
```

Expected: `ModuleNotFoundError: No module named 'eval.annotate_web'`.

- [ ] **Step 3: Implement the app factory + `GET /api/traces`**

Create `eval/annotate_web.py`:

```python
import argparse
from pathlib import Path

from flask import Flask, jsonify

from eval.annotate import load_jsonl, load_latest_annotations


def create_app(
    *,
    traces_path: Path,
    questions_path: Path,
    dataset_path: Path,
    annotator: str,
) -> Flask:
    app = Flask(__name__, template_folder="templates")
    app.config["TRACES_PATH"] = Path(traces_path)
    app.config["QUESTIONS_PATH"] = Path(questions_path)
    app.config["DATASET_PATH"] = Path(dataset_path)
    app.config["ANNOTATOR"] = annotator

    @app.get("/api/traces")
    def get_traces():
        traces = load_jsonl(app.config["TRACES_PATH"])
        questions_by_id = {q["id"]: q for q in load_jsonl(app.config["QUESTIONS_PATH"])}
        latest = load_latest_annotations(app.config["DATASET_PATH"])
        result = []
        for t in traces:
            q = questions_by_id.get(t["question_id"], {})
            result.append({
                "trace": t,
                "expected_answer": q.get("expected_answer", ""),
                "latest_annotation": latest.get(t["id"]),
            })
        return jsonify(result)

    return app
```

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run pytest tests/test_annotate_web.py -v
```

Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
mkdir -p eval/templates
git add eval/annotate_web.py tests/test_annotate_web.py
git commit -m "feat: Flask app factory + GET /api/traces"
```

---

### Task 4: `POST /api/annotate` with Validation (TDD)

**Files:**
- Modify: `eval/annotate_web.py` (add the POST route)
- Modify: `tests/test_annotate_web.py` (add 7 tests)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_annotate_web.py`:

```python
def _read_dataset(data_dir: Path) -> list[dict]:
    path = data_dir / "dataset.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_post_pass_appends_dataset(client, data_dir):
    resp = client.post("/api/annotate", json={
        "trace_id": "q_001",
        "label": "pass",
        "critique": "",
        "failure_category": None,
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["annotation"]["label"] == "pass"
    assert body["annotation"]["annotated_by"] == "tester"
    assert body["annotation"]["expected_answer"] == "500万元。"

    rows = _read_dataset(data_dir)
    assert len(rows) == 1
    assert rows[0]["id"] == "q_001"
    assert rows[0]["label"] == "pass"


def test_post_fail_without_critique_returns_400(client, data_dir):
    resp = client.post("/api/annotate", json={
        "trace_id": "q_001",
        "label": "fail",
        "critique": "   ",
        "failure_category": "hallucination",
    })
    assert resp.status_code == 400
    assert "critique" in resp.get_json()["error"]
    assert _read_dataset(data_dir) == []


def test_post_fail_without_category_returns_400(client, data_dir):
    resp = client.post("/api/annotate", json={
        "trace_id": "q_001",
        "label": "fail",
        "critique": "答非所问",
        "failure_category": None,
    })
    assert resp.status_code == 400
    assert "failure_category" in resp.get_json()["error"]
    assert _read_dataset(data_dir) == []


def test_post_skip_allows_empty_critique(client, data_dir):
    resp = client.post("/api/annotate", json={
        "trace_id": "q_001",
        "label": "skip",
        "critique": "",
        "failure_category": None,
    })
    assert resp.status_code == 200
    assert _read_dataset(data_dir)[0]["label"] == "skip"


def test_post_unknown_trace_id_returns_404(client, data_dir):
    resp = client.post("/api/annotate", json={
        "trace_id": "q_999",
        "label": "pass",
        "critique": "",
        "failure_category": None,
    })
    assert resp.status_code == 404
    assert _read_dataset(data_dir) == []


def test_post_invalid_label_returns_400(client, data_dir):
    resp = client.post("/api/annotate", json={
        "trace_id": "q_001",
        "label": "maybe",
        "critique": "",
        "failure_category": None,
    })
    assert resp.status_code == 400
    assert _read_dataset(data_dir) == []


def test_post_overwrites_via_append(client, data_dir):
    client.post("/api/annotate", json={
        "trace_id": "q_001",
        "label": "fail",
        "critique": "缺少引用",
        "failure_category": "citation_error",
    })
    client.post("/api/annotate", json={
        "trace_id": "q_001",
        "label": "pass",
        "critique": "",
        "failure_category": None,
    })
    rows = _read_dataset(data_dir)
    assert len(rows) == 2
    assert rows[-1]["label"] == "pass"

    body = client.get("/api/traces").get_json()
    by_id = {entry["trace"]["id"]: entry for entry in body}
    assert by_id["q_001"]["latest_annotation"]["label"] == "pass"


def test_annotated_at_is_server_timestamp(client, data_dir):
    resp = client.post("/api/annotate", json={
        "trace_id": "q_001",
        "label": "pass",
        "critique": "",
        "failure_category": None,
        "annotated_at": "1999-01-01T00:00:00+00:00",
    })
    assert resp.status_code == 200
    ts = resp.get_json()["annotation"]["annotated_at"]
    assert ts.startswith("20")  # server-generated, current year not 1999
    assert "1999" not in ts
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_annotate_web.py -v
```

Expected: the 8 new tests fail (the 3 from Task 3 still pass). Errors are `404 NOT FOUND` for the POST route.

- [ ] **Step 3: Implement the route**

Edit `eval/annotate_web.py`. Add these imports at the top:

```python
from datetime import datetime, timezone

from flask import request

from eval.annotate import _CATEGORIES, save_annotation
```

After the `get_traces` route inside `create_app`, add:

```python
    @app.post("/api/annotate")
    def post_annotate():
        body = request.get_json(silent=True) or {}
        trace_id = body.get("trace_id")
        label = body.get("label")
        critique = (body.get("critique") or "").strip()
        failure_category = body.get("failure_category")

        if label not in ("pass", "fail", "skip"):
            return jsonify({"error": "label 必须是 pass/fail/skip"}), 400

        traces = load_jsonl(app.config["TRACES_PATH"])
        trace = next((t for t in traces if t["id"] == trace_id), None)
        if trace is None:
            return jsonify({"error": f"trace_id {trace_id} 不存在"}), 404

        if label == "fail":
            if not critique:
                return jsonify({"error": "fail 必须填写 critique"}), 400
            if failure_category not in _CATEGORIES:
                return jsonify({"error": "fail 必须选择 failure_category"}), 400
        else:
            critique = ""
            failure_category = None

        questions_by_id = {q["id"]: q for q in load_jsonl(app.config["QUESTIONS_PATH"])}
        q = questions_by_id.get(trace["question_id"], {})

        sample = {
            **trace,
            "complete_question": trace.get("complete_question", trace["question"]),
            "doc_context": trace.get("doc_context", ""),
            "faq_context": trace.get("faq_context", ""),
            "references": trace.get("references", []),
            "ref_num": trace.get("ref_num", 0),
            "expected_answer": q.get("expected_answer", ""),
            "label": label,
            "critique": critique,
            "failure_category": failure_category,
            "annotated_by": app.config["ANNOTATOR"],
            "annotated_at": datetime.now(timezone.utc).isoformat(),
        }
        save_annotation(sample, app.config["DATASET_PATH"])
        return jsonify({"ok": True, "annotation": sample})
```

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run pytest tests/test_annotate_web.py -v
```

Expected: 11 tests pass.

- [ ] **Step 5: Commit**

```bash
git add eval/annotate_web.py tests/test_annotate_web.py
git commit -m "feat: POST /api/annotate with fail-critique/category validation"
```

---

### Task 5: `GET /` Serves the HTML Template (TDD)

**Files:**
- Modify: `eval/annotate_web.py` (add `/` route)
- Create: `eval/templates/annotate.html` (placeholder)
- Modify: `tests/test_annotate_web.py` (add 1 test)

We add the route and a minimal template now so the page is reachable; Task 6 fleshes out the full UI.

- [ ] **Step 1: Write failing test**

Append to `tests/test_annotate_web.py`:

```python
def test_get_root_returns_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.mimetype == "text/html"
    text = resp.get_data(as_text=True)
    # categories must be embedded via Jinja so frontend doesn't drift from backend
    assert "hallucination" in text
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_annotate_web.py::test_get_root_returns_html -v
```

Expected: `404 NOT FOUND`.

- [ ] **Step 3: Create the placeholder template**

Create `eval/templates/annotate.html`:

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>标注 - Eval Stage 2</title></head>
<body>
  <p>HTML 标注工具占位页 — Task 6 会填实际 UI。</p>
  <script>
    // Embedded by Jinja so frontend categories never drift from backend _CATEGORIES.
    const FAILURE_CATEGORIES = {{ categories | tojson }};
  </script>
</body>
</html>
```

- [ ] **Step 4: Add the route**

Edit `eval/annotate_web.py`. Add to imports:

```python
from flask import render_template

from eval.annotate import _CATEGORY_LABELS
```

Inside `create_app`, before the `return app` line, add:

```python
    @app.get("/")
    def index():
        return render_template("annotate.html", categories=_CATEGORY_LABELS)
```

- [ ] **Step 5: Run tests to verify pass**

```bash
uv run pytest tests/test_annotate_web.py -v
```

Expected: 12 tests pass.

- [ ] **Step 6: Commit**

```bash
git add eval/annotate_web.py eval/templates/annotate.html tests/test_annotate_web.py
git commit -m "feat: GET / serves annotate.html template (placeholder)"
```

---

### Task 6: Full HTML/CSS/JS UI

**Files:**
- Modify: `eval/templates/annotate.html` (replace placeholder with full UI)

No automated tests — manual smoke test only (per spec). The template covers: top progress + status strip; detail panel (question / complete_question / history / contexts / references / actual_answer / expected_answer); form (label buttons / critique / failure_category / save+nav); keyboard shortcuts; latest-annotation form refill; auto-jump to next unannotated after save.

- [ ] **Step 1: Replace the entire content of `eval/templates/annotate.html`**

Open `eval/templates/annotate.html` and replace its full content with:

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>标注 - Eval Stage 2</title>
  <style>
    * { box-sizing: border-box; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif; margin: 0 auto; padding: 20px; max-width: 1100px; color: #222; }
    h1 { font-size: 18px; margin: 0 0 8px; }
    .progress { color: #666; font-size: 14px; font-weight: normal; margin-left: 12px; }
    .strip { display: flex; gap: 6px; flex-wrap: wrap; margin: 16px 0; }
    .chip { padding: 6px 10px; border-radius: 4px; cursor: pointer; font-size: 13px; border: 2px solid transparent; user-select: none; }
    .chip[data-status="unannotated"] { background: #e5e5e5; color: #555; }
    .chip[data-status="pass"] { background: #d4f4dd; color: #1a6d2f; }
    .chip[data-status="fail"] { background: #fbd5d5; color: #8a1d1d; }
    .chip[data-status="skip"] { background: #fdebc8; color: #7a5a14; }
    .chip.current { border-color: #2563eb; }
    .panel { margin-bottom: 12px; border-radius: 4px; padding: 10px 14px; border-left: 4px solid; background: #fafafa; }
    .panel.question { border-left-color: #2563eb; }
    .panel.answer { border-left-color: #16a34a; background: #f0faf3; }
    .panel.expected { border-left-color: #999; background: #f5f5f5; color: #555; font-size: 14px; }
    .panel .body { white-space: pre-wrap; word-break: break-word; margin: 6px 0 0; font-size: 14px; }
    details { margin-bottom: 10px; }
    details summary { cursor: pointer; padding: 6px 0; color: #555; font-size: 13px; }
    details > div { padding: 8px 12px; border-left: 3px solid #eee; background: #fafafa; white-space: pre-wrap; word-break: break-word; font-size: 13px; }
    .refs { font-size: 13px; color: #555; margin: 8px 0; }
    .refs a { color: #2563eb; text-decoration: none; margin-right: 12px; }
    .form { margin-top: 16px; padding: 14px; background: #f9f9f9; border-radius: 6px; }
    .labels { display: flex; gap: 10px; margin-bottom: 12px; }
    .labels button { flex: 1; padding: 12px; font-size: 16px; border: 2px solid #ddd; background: white; cursor: pointer; border-radius: 4px; }
    .labels button.selected[data-label="pass"] { background: #16a34a; color: white; border-color: #16a34a; }
    .labels button.selected[data-label="fail"] { background: #dc2626; color: white; border-color: #dc2626; }
    .labels button.selected[data-label="skip"] { background: #eab308; color: white; border-color: #eab308; }
    .fail-section { display: none; margin-bottom: 12px; }
    .fail-section.shown { display: block; }
    .fail-section textarea { width: 100%; min-height: 60px; padding: 8px; font-family: inherit; font-size: 14px; border: 1px solid #ccc; border-radius: 4px; }
    .fail-section textarea.error { border-color: #dc2626; }
    .categories { margin-top: 8px; display: flex; gap: 8px; flex-wrap: wrap; }
    .categories label { font-size: 13px; padding: 4px 8px; cursor: pointer; }
    .actions { display: flex; gap: 8px; }
    .actions button { padding: 8px 14px; font-size: 14px; border: 1px solid #ddd; background: white; cursor: pointer; border-radius: 4px; }
    .actions button.primary { background: #2563eb; color: white; border-color: #2563eb; }
    .toast { position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%); padding: 10px 16px; border-radius: 4px; font-size: 14px; display: none; }
    .toast.show { display: block; }
    .toast.error { background: #fbd5d5; color: #8a1d1d; }
    .toast.success { background: #d4f4dd; color: #1a6d2f; }
    .hint { font-size: 12px; color: #888; margin-top: 8px; }
  </style>
</head>
<body>
  <h1>Eval 标注 <span class="progress" id="progress"></span></h1>
  <div class="strip" id="strip"></div>
  <div id="detail">加载中...</div>
  <div class="form" id="form" style="display:none">
    <div class="labels">
      <button data-label="pass" onclick="selectLabel('pass')">Pass (1)</button>
      <button data-label="fail" onclick="selectLabel('fail')">Fail (2)</button>
      <button data-label="skip" onclick="selectLabel('skip')">Skip (3)</button>
    </div>
    <div class="fail-section" id="failSection">
      <label>失败原因（一句话）：</label>
      <textarea id="critique" placeholder="为什么 fail？"></textarea>
      <div class="categories" id="categories"></div>
    </div>
    <div class="actions">
      <button class="primary" onclick="save()">保存 (Enter)</button>
      <button onclick="navigate(-1)">上一条 (←)</button>
      <button onclick="navigate(1)">下一条 (→)</button>
    </div>
    <div class="hint">快捷键：1=Pass 2=Fail 3=Skip ←/→ 切换条 Enter 保存（critique 框内 Ctrl+Enter 保存）</div>
  </div>
  <div class="toast" id="toast"></div>

<script>
// Categories are embedded by Jinja so the frontend never drifts from backend _CATEGORIES.
const FAILURE_CATEGORIES = {{ categories | tojson }};
let traces = [];
let currentIndex = 0;
let currentLabel = null;

async function init() {
  try {
    const resp = await fetch('/api/traces');
    if (!resp.ok) throw new Error('GET /api/traces ' + resp.status);
    traces = await resp.json();
  } catch (e) {
    document.getElementById('detail').textContent = '加载失败：' + e.message;
    return;
  }
  if (!traces.length) {
    document.getElementById('detail').textContent = '没有任何 trace';
    return;
  }
  renderCategories();
  const firstUnannotated = traces.findIndex(t => t.latest_annotation === null);
  currentIndex = firstUnannotated >= 0 ? firstUnannotated : 0;
  render();
}

function renderCategories() {
  const container = document.getElementById('categories');
  container.innerHTML = '';
  for (const [key, label] of Object.entries(FAILURE_CATEGORIES)) {
    const wrap = document.createElement('label');
    wrap.innerHTML = `<input type="radio" name="failure_category" value="${key}"> ${escapeHtml(label)}`;
    container.appendChild(wrap);
  }
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function render() {
  renderStrip();
  renderDetail();
  renderForm();
  document.getElementById('form').style.display = 'block';
}

function renderStrip() {
  const strip = document.getElementById('strip');
  strip.innerHTML = '';
  let done = 0;
  traces.forEach((entry, i) => {
    const ann = entry.latest_annotation;
    const status = ann ? ann.label : 'unannotated';
    if (ann) done++;
    const chip = document.createElement('div');
    chip.className = 'chip' + (i === currentIndex ? ' current' : '');
    chip.dataset.status = status;
    chip.textContent = entry.trace.id;
    chip.onclick = () => { currentIndex = i; render(); };
    strip.appendChild(chip);
  });
  document.getElementById('progress').textContent = `${done}/${traces.length} 已完成`;
}

function renderDetail() {
  const entry = traces[currentIndex];
  const t = entry.trace;
  let html = `<div class="panel question"><strong>问题</strong><div class="body">${escapeHtml(t.question)}</div></div>`;
  if (t.complete_question && t.complete_question !== t.question) {
    html += `<details><summary>检索 query</summary><div>${escapeHtml(t.complete_question)}</div></details>`;
  }
  if (t.conversation_history && t.conversation_history.length) {
    const histText = t.conversation_history
      .map(turn => `[${turn.role === 'user' ? '用户' : 'Bot'}] ${turn.content}`)
      .join('\n');
    html += `<details><summary>对话历史（${t.conversation_history.length} 条）</summary><div>${escapeHtml(histText)}</div></details>`;
  }
  if (t.doc_context) {
    html += `<details><summary>文档 Context (doc_str)</summary><div>${escapeHtml(t.doc_context)}</div></details>`;
  }
  if (t.faq_context) {
    html += `<details><summary>FAQ Context (faq_str)</summary><div>${escapeHtml(t.faq_context)}</div></details>`;
  }
  if (t.references && t.references.length) {
    const refs = t.references
      .map(r => `<a href="${escapeHtml(r.url)}" target="_blank" rel="noopener">[${escapeHtml(r.doc_id)}] ${escapeHtml(r.name)}</a>`)
      .join('');
    html += `<div class="refs">引用：${refs}</div>`;
  }
  html += `<div class="panel answer"><strong>Bot 实际回复</strong><div class="body">${escapeHtml(t.actual_answer)}</div></div>`;
  if (entry.expected_answer) {
    html += `<div class="panel expected"><strong>参考答案</strong>（仅供参考，不参与评分）<div class="body">${escapeHtml(entry.expected_answer)}</div></div>`;
  }
  document.getElementById('detail').innerHTML = html;
}

function renderForm() {
  const entry = traces[currentIndex];
  const ann = entry.latest_annotation;
  currentLabel = ann ? ann.label : null;
  document.querySelectorAll('.labels button').forEach(b => b.classList.remove('selected'));
  if (currentLabel) {
    document.querySelector(`.labels button[data-label="${currentLabel}"]`).classList.add('selected');
  }
  document.getElementById('critique').value = ann && ann.label === 'fail' ? ann.critique : '';
  document.getElementById('critique').classList.remove('error');
  document.querySelectorAll('input[name="failure_category"]').forEach(r => r.checked = false);
  if (ann && ann.label === 'fail' && ann.failure_category) {
    const el = document.querySelector(`input[name="failure_category"][value="${ann.failure_category}"]`);
    if (el) el.checked = true;
  }
  document.getElementById('failSection').classList.toggle('shown', currentLabel === 'fail');
}

function selectLabel(label) {
  currentLabel = label;
  document.querySelectorAll('.labels button').forEach(b => b.classList.remove('selected'));
  document.querySelector(`.labels button[data-label="${label}"]`).classList.add('selected');
  document.getElementById('failSection').classList.toggle('shown', label === 'fail');
  if (label === 'fail') {
    setTimeout(() => document.getElementById('critique').focus(), 0);
  }
}

function navigate(delta) {
  const next = currentIndex + delta;
  if (next < 0 || next >= traces.length) return;
  currentIndex = next;
  render();
}

async function save() {
  if (!currentLabel) {
    showToast('请先选择 Pass/Fail/Skip', 'error');
    return;
  }
  const critique = document.getElementById('critique').value.trim();
  const category = document.querySelector('input[name="failure_category"]:checked');
  if (currentLabel === 'fail') {
    if (!critique) {
      document.getElementById('critique').classList.add('error');
      document.getElementById('critique').focus();
      showToast('fail 必须填写 critique', 'error');
      return;
    }
    if (!category) {
      showToast('fail 必须选择失败类型', 'error');
      return;
    }
  }
  const entry = traces[currentIndex];
  try {
    const resp = await fetch('/api/annotate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        trace_id: entry.trace.id,
        label: currentLabel,
        critique: currentLabel === 'fail' ? critique : '',
        failure_category: currentLabel === 'fail' ? category.value : null,
      }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({error: 'HTTP ' + resp.status}));
      showToast('保存失败：' + (err.error || resp.status), 'error');
      return;
    }
    const body = await resp.json();
    entry.latest_annotation = body.annotation;
    showToast('已保存', 'success');
    renderStrip();
    const next = traces.findIndex((t, i) => i > currentIndex && t.latest_annotation === null);
    if (next >= 0) {
      currentIndex = next;
      render();
    } else {
      const before = traces.findIndex(t => t.latest_annotation === null);
      if (before >= 0) {
        currentIndex = before;
        render();
      } else {
        showToast('全部完成！', 'success');
      }
    }
  } catch (e) {
    showToast('保存失败：网络错误 ' + e.message, 'error');
  }
}

let toastTimer = null;
function showToast(msg, kind) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show ' + (kind || '');
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 2500);
}

document.addEventListener('keydown', e => {
  const tag = e.target.tagName;
  if (tag === 'TEXTAREA') {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      save();
    }
    return;
  }
  if (tag === 'INPUT') return;
  if (e.key === '1') selectLabel('pass');
  else if (e.key === '2') selectLabel('fail');
  else if (e.key === '3') selectLabel('skip');
  else if (e.key === 'ArrowLeft') navigate(-1);
  else if (e.key === 'ArrowRight') navigate(1);
  else if (e.key === 'Enter') { e.preventDefault(); save(); }
});

init();
</script>
</body>
</html>
```

- [ ] **Step 2: Verify backend tests still pass**

```bash
uv run pytest tests/ -v
```

Expected: all tests pass (`test_get_root_returns_html` is the only one touching the template; it only checks `hallucination` appears, which still does via `{{ categories | tojson }}`).

- [ ] **Step 3: Manual smoke test (skip if backend isn't reachable)**

Only runs if `data/traces.jsonl` + `data/questions.jsonl` exist. If they don't, skip — Task 7's `__main__` block will cover the launch path next.

```bash
# Sanity-check the template by rendering it via flask in a Python REPL
uv run python -c "
from pathlib import Path
from eval.annotate_web import create_app
app = create_app(
    traces_path=Path('data/traces.jsonl'),
    questions_path=Path('data/questions.jsonl'),
    dataset_path=Path('data/dataset.jsonl'),
    annotator='smoke',
)
client = app.test_client()
r = client.get('/')
assert r.status_code == 200
assert 'FAILURE_CATEGORIES' in r.get_data(as_text=True)
assert 'hallucination' in r.get_data(as_text=True)
print('template renders OK')
"
```

Expected: `template renders OK`.

- [ ] **Step 4: Commit**

```bash
git add eval/templates/annotate.html
git commit -m "feat: full HTML/CSS/JS annotation UI (strip + detail + form + keyboard)"
```

---

### Task 7: `__main__` Entry with Argparse

**Files:**
- Modify: `eval/annotate_web.py` (add `if __name__ == "__main__":` block)

- [ ] **Step 1: Append the entry block**

Edit `eval/annotate_web.py`. After the `create_app` function, append:

```python
def _main() -> None:
    parser = argparse.ArgumentParser(description="HTML 标注工具")
    parser.add_argument("--annotator", required=True, help="标注者名字（写入 dataset.jsonl 的 annotated_by 字段）")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()

    app = create_app(
        traces_path=Path("data/traces.jsonl"),
        questions_path=Path("data/questions.jsonl"),
        dataset_path=Path("data/dataset.jsonl"),
        annotator=args.annotator,
    )
    print(f"标注工具运行在 http://127.0.0.1:{args.port}  （标注者：{args.annotator}）")
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    _main()
```

- [ ] **Step 2: Verify argparse contract**

```bash
uv run python -m eval.annotate_web --help
```

Expected output contains `--annotator` (required) and `--port` (default 5000).

- [ ] **Step 3: Verify missing `--annotator` fails fast**

```bash
uv run python -m eval.annotate_web
```

Expected: argparse error mentioning `--annotator` is required; exit code != 0.

- [ ] **Step 4: Manual launch smoke test (optional, requires real data files)**

If `data/traces.jsonl` exists:

```bash
uv run python -m eval.annotate_web --annotator $(whoami) --port 5050 &
sleep 1
curl -s http://127.0.0.1:5050/api/traces | head -c 200
kill %1
```

Expected: the curl prints a JSON array starting with `[{"expected_answer":...`.

- [ ] **Step 5: Full test sweep**

```bash
uv run pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add eval/annotate_web.py
git commit -m "feat: __main__ entry — uv run python -m eval.annotate_web --annotator <name>"
```

---

## Self-Review

### Spec coverage

| Spec section | Covered by |
|---|---|
| 目标 / 非目标 / 关键决策 | All tasks (architectural choices baked in) |
| 架构（前端缓存 + 单 HTML + Flask） | Task 3, 6 |
| 文件结构 | Tasks 1–7 |
| 复用 `load_jsonl` / `save_annotation` / `_CATEGORY_LABELS` | Tasks 2, 3, 4, 5 |
| 新增 `load_latest_annotations` | Task 2 |
| `GET /` 渲染 annotate.html，categories 由 Jinja 注入 | Task 5 |
| `GET /api/traces`（返回结构 + expected_answer + latest_annotation） | Task 3 |
| `POST /api/annotate`（label 白名单 / fail 强制 critique+category / unknown trace 404） | Task 4 |
| annotator 来自启动参数；annotated_at 服务端打戳 | Task 4 |
| UI 布局（status 色 strip + 折叠 context + 三大按钮 + fail 区域） | Task 6 |
| 键盘快捷键（1/2/3 / ←→ / Enter / Ctrl+Enter） | Task 6 |
| 回看修改（renderForm 回填 latest_annotation） | Task 6 |
| 切换 trace 时表单回填 / 未标注则重置 | Task 6 (`renderForm`) |
| 数据流：保存后自动跳下一未标 + 进度更新 | Task 6 (`save` → next-search) |
| 错误处理（4xx + toast；写文件异常 500；malformed line 跳过） | Task 4 + Task 6 |
| 启动 & 配置（127.0.0.1, debug=False, host写死, port 默认5000） | Task 7 |
| 数据文件路径写死与 CLI 一致 | Task 7 (`_main` uses literal paths) |
| 依赖 flask>=3.0 | Task 1 |
| 测试策略（12 项） | Tasks 2 (2 项)、3 (3 项)、4 (8 项)、5 (1 项) = 14 项（多于 12） |
| HTML 不写自动化测试 | Task 6 仅手动 smoke test |

### Out-of-scope items（spec 明确不做，所以不在本计划中）

- 错误聚类 / Pass rate 看板（Stage 3）
- LLM judges（Stage 4）
- Langfuse（Stage 6）
- HTML E2E 自动化测试

### Placeholder & consistency 扫描

- 所有 step 含完整代码或具体命令。
- 类型 / 函数名前后一致：
  - `create_app(traces_path, questions_path, dataset_path, annotator)` — Task 3 定义，Task 4/5/7 复用。
  - `load_latest_annotations` — Task 2 定义，Task 3 import。
  - `_CATEGORY_LABELS` / `_CATEGORIES` — 复用 `eval/annotate.py` 现有定义。
- 没有 "TBD / TODO / fill in later"。
