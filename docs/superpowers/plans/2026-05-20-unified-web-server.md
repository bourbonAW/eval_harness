# Unified Web Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge `eval/annotate_web.py` and `eval/judge_web.py` into a single `eval/web.py` Flask app with two pages (`/` and `/judge`) on one port.

**Architecture:** One `create_app()` factory holds all routes from both existing apps. The unified `/api/traces` response uses `human_annotation` (not `latest_annotation`) as the key name. Old files are deleted; no compatibility shims.

**Tech Stack:** Flask, pytest, Python 3.11+, uv

---

## File Map

| Action | File |
|--------|------|
| **Create** | `eval/web.py` |
| **Create** | `tests/test_web.py` |
| **Modify** | `eval/templates/annotate.html` |
| **Modify** | `eval/templates/judge.html` |
| **Modify** | `CLAUDE.md` |
| **Modify** | `README.md` |
| **Delete** | `eval/annotate_web.py` |
| **Delete** | `eval/judge_web.py` |
| **Delete** | `tests/test_annotate_web.py` |
| **Delete** | `tests/test_judge_web.py` |

---

## Task 1: Write `tests/test_web.py` (RED)

Merge both existing test files into one. Update:
- import from `eval.web` (not `eval.annotate_web` / `eval.judge_web`)
- all `latest_annotation` key references → `human_annotation`
- mock patch paths: `eval.judge_web.run_all_judges` → `eval.web.run_all_judges`
- unified `client` fixture includes `judge_results_path` + `annotator`
- 5 new regression test cases

**Files:**
- Create: `tests/test_web.py`

- [ ] **Step 1: Write `tests/test_web.py`**

```python
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from eval.web import create_app
from eval.judges import DimensionResult, EvalResult

# ── Shared test data ─────────────────────────────────────

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

SAMPLE_ANNOTATION = {
    "id": "q_001",
    "question_id": "q_001",
    "question": "最低要求是多少？",
    "complete_question": "最低要求是多少？",
    "conversation_history": [],
    "actual_answer": "最低为500万元。",
    "doc_context": "",
    "faq_context": "",
    "references": [],
    "ref_num": 0,
    "expected_answer": "500万元。",
    "label": "pass",
    "critique": "正确回答",
    "failure_category": None,
    "annotated_by": "tester",
    "annotated_at": "2026-05-20T10:00:00+00:00",
}

SAMPLE_JUDGE_RESULT = {
    "trace_id": "q_001",
    "label": "pass",
    "dimensions": [
        {
            "dimension": "answer_relevance",
            "label": "pass",
            "critique": "直接回答了问题",
            "evidence": ["最低为500万元"],
            "model": "mimo-v2.5-pro",
        }
    ],
    "judged_at": "2026-05-20T10:05:00+00:00",
}


def _write_jsonl(path: Path, items: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(it, ensure_ascii=False) for it in items) + "\n",
        encoding="utf-8",
    )


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _read_dataset(data_dir: Path) -> list[dict]:
    return _read_jsonl(data_dir / "dataset.jsonl")


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
        judge_results_path=data_dir / "judge_results.jsonl",
        annotator="tester",
    )
    app.testing = True
    return app.test_client()


# ── New unified contract tests ────────────────────────────


def test_get_traces_returns_human_annotation_key(client):
    body = client.get("/api/traces").get_json()
    assert "human_annotation" in body[0]
    assert "latest_annotation" not in body[0]


def test_get_traces_human_annotation_null_when_missing(client):
    body = client.get("/api/traces").get_json()
    for entry in body:
        assert entry["human_annotation"] is None


def test_get_traces_judge_result_null_when_missing(client):
    body = client.get("/api/traces").get_json()
    for entry in body:
        assert entry["judge_result"] is None


def test_get_root_serves_annotate_page(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.mimetype == "text/html"
    assert "hallucination" in resp.get_data(as_text=True)


def test_get_judge_serves_judge_page(client):
    resp = client.get("/judge")
    assert resp.status_code == 200
    assert resp.mimetype == "text/html"
    html = resp.get_data(as_text=True)
    assert "Eval · Stage 4/5 Judge" in html
    assert "运行 Judge" in html
    assert 'href="/"' in html


def test_get_root_html_uses_human_annotation_not_latest(client):
    html = client.get("/").get_data(as_text=True)
    assert "human_annotation" in html
    assert "latest_annotation" not in html
    assert 'href="/judge"' in html


# ── Migrated annotate tests ───────────────────────────────


def test_get_traces_returns_all_with_status(client):
    resp = client.get("/api/traces")
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body) == 2
    ids = [entry["trace"]["id"] for entry in body]
    assert ids == ["q_001", "q_002"]
    for entry in body:
        assert entry["human_annotation"] is None


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
    assert by_id["q_001"]["human_annotation"]["label"] == "pass"
    assert by_id["q_002"]["human_annotation"] is None


def test_post_pass_appends_dataset(client, data_dir):
    resp = client.post(
        "/api/annotate",
        json={
            "trace_id": "q_001",
            "label": "pass",
            "critique": "",
            "failure_category": None,
        },
    )
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
    resp = client.post(
        "/api/annotate",
        json={
            "trace_id": "q_001",
            "label": "fail",
            "critique": "   ",
            "failure_category": "hallucination",
        },
    )
    assert resp.status_code == 400
    assert "critique" in resp.get_json()["error"]
    assert _read_dataset(data_dir) == []


def test_post_fail_without_category_returns_400(client, data_dir):
    resp = client.post(
        "/api/annotate",
        json={
            "trace_id": "q_001",
            "label": "fail",
            "critique": "答非所问",
            "failure_category": None,
        },
    )
    assert resp.status_code == 400
    assert "failure_category" in resp.get_json()["error"]
    assert _read_dataset(data_dir) == []


def test_post_skip_allows_empty_critique(client, data_dir):
    resp = client.post(
        "/api/annotate",
        json={
            "trace_id": "q_001",
            "label": "skip",
            "critique": "",
            "failure_category": None,
        },
    )
    assert resp.status_code == 200
    assert _read_dataset(data_dir)[0]["label"] == "skip"


def test_post_unknown_trace_id_returns_404(client, data_dir):
    resp = client.post(
        "/api/annotate",
        json={
            "trace_id": "q_999",
            "label": "pass",
            "critique": "",
            "failure_category": None,
        },
    )
    assert resp.status_code == 404
    assert _read_dataset(data_dir) == []


def test_post_invalid_label_returns_400(client, data_dir):
    resp = client.post(
        "/api/annotate",
        json={
            "trace_id": "q_001",
            "label": "maybe",
            "critique": "",
            "failure_category": None,
        },
    )
    assert resp.status_code == 400
    assert _read_dataset(data_dir) == []


def test_post_overwrites_via_append(client, data_dir):
    client.post(
        "/api/annotate",
        json={
            "trace_id": "q_001",
            "label": "fail",
            "critique": "缺少引用",
            "failure_category": "citation_error",
        },
    )
    client.post(
        "/api/annotate",
        json={
            "trace_id": "q_001",
            "label": "pass",
            "critique": "",
            "failure_category": None,
        },
    )
    rows = _read_dataset(data_dir)
    assert len(rows) == 2
    assert rows[-1]["label"] == "pass"

    body = client.get("/api/traces").get_json()
    by_id = {entry["trace"]["id"]: entry for entry in body}
    assert by_id["q_001"]["human_annotation"]["label"] == "pass"


def test_annotated_at_is_server_timestamp(client, data_dir):
    resp = client.post(
        "/api/annotate",
        json={
            "trace_id": "q_001",
            "label": "pass",
            "critique": "",
            "failure_category": None,
            "annotated_at": "1999-01-01T00:00:00+00:00",
        },
    )
    assert resp.status_code == 200
    ts = resp.get_json()["annotation"]["annotated_at"]
    assert ts.startswith("20")
    assert "1999" not in ts


def test_get_root_returns_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.mimetype == "text/html"
    assert "hallucination" in resp.get_data(as_text=True)


def test_post_pass_preserves_optional_note(client, data_dir):
    resp = client.post(
        "/api/annotate",
        json={
            "trace_id": "q_001",
            "label": "pass",
            "critique": "正确引用了第3条政策条文\n完整覆盖了申请要求",
            "failure_category": None,
        },
    )
    assert resp.status_code == 200
    rows = _read_dataset(data_dir)
    assert rows[0]["critique"] == "正确引用了第3条政策条文\n完整覆盖了申请要求"


def test_post_skip_preserves_optional_note(client, data_dir):
    resp = client.post(
        "/api/annotate",
        json={
            "trace_id": "q_001",
            "label": "skip",
            "critique": "问题本身不明确，无法判断好坏",
            "failure_category": None,
        },
    )
    assert resp.status_code == 200
    rows = _read_dataset(data_dir)
    assert rows[0]["critique"] == "问题本身不明确，无法判断好坏"


# ── Migrated judge tests ──────────────────────────────────


def test_get_traces_judge_result_is_none_when_not_run(client):
    body = client.get("/api/traces").get_json()
    assert body[0]["judge_result"] is None


def test_get_traces_includes_judge_result_when_cached(client, data_dir):
    _write_jsonl(data_dir / "judge_results.jsonl", [SAMPLE_JUDGE_RESULT])
    body = client.get("/api/traces").get_json()
    jr = body[0]["judge_result"]
    assert jr is not None
    assert jr["label"] == "pass"
    assert jr["dimensions"][0]["dimension"] == "answer_relevance"


def test_get_traces_includes_human_annotation_when_present(client, data_dir):
    _write_jsonl(data_dir / "dataset.jsonl", [SAMPLE_ANNOTATION])
    body = client.get("/api/traces").get_json()
    assert body[0]["human_annotation"]["label"] == "pass"


def test_get_traces_human_annotation_is_none_when_absent(client):
    body = client.get("/api/traces").get_json()
    assert body[0]["human_annotation"] is None


def _fake_judge(trace, *, model="mimo-v2.5-pro"):
    return EvalResult(
        trace_id=trace["id"],
        dimensions=[
            DimensionResult(
                dimension="answer_relevance",
                label="pass",
                critique="直接回答了问题",
                evidence=["最低为500万元"],
                model=model,
            )
        ],
    )


def test_post_judge_saves_result_and_returns_eval_result(client, data_dir):
    with patch("eval.web.run_all_judges", side_effect=_fake_judge):
        resp = client.post("/api/judge", json={"trace_id": "q_001"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["label"] == "pass"
    assert body["dimensions"][0]["dimension"] == "answer_relevance"

    rows = _read_jsonl(data_dir / "judge_results.jsonl")
    assert len(rows) == 1
    assert rows[0]["trace_id"] == "q_001"
    assert rows[0]["label"] == "pass"
    assert "judged_at" in rows[0]


def test_post_judge_unknown_trace_returns_404(client, data_dir):
    with patch("eval.web.run_all_judges", side_effect=_fake_judge):
        resp = client.post("/api/judge", json={"trace_id": "q_999"})
    assert resp.status_code == 404
    assert _read_jsonl(data_dir / "judge_results.jsonl") == []


def test_post_judge_respects_model_param(client, data_dir):
    captured = {}

    def fake_with_model(trace, *, model="mimo-v2.5-pro"):
        captured["model"] = model
        return _fake_judge(trace, model=model)

    with patch("eval.web.run_all_judges", side_effect=fake_with_model):
        client.post("/api/judge", json={"trace_id": "q_001", "model": "mimo-v2-omni"})
    assert captured["model"] == "mimo-v2-omni"
```

- [ ] **Step 2: Run tests to verify RED**

```bash
uv run pytest tests/test_web.py -v
```

Expected: `ModuleNotFoundError: No module named 'eval.web'` — all tests fail to collect.

- [ ] **Step 3: Commit RED**

```bash
git add tests/test_web.py
git commit -m "test: add tests/test_web.py (RED — eval.web not yet implemented)"
```

---

## Task 2: Implement `eval/web.py` (GREEN)

Single `create_app()` factory combining all routes. `annotator` defaults to `"unknown"` so the judge page doesn't need to pass it.

**Files:**
- Create: `eval/web.py`

- [ ] **Step 1: Write `eval/web.py`**

```python
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from eval.annotate import _CATEGORIES, _CATEGORY_LABELS, load_jsonl, load_latest_annotations, save_annotation
from eval.judges import run_all_judges


def load_latest_judge_results(path: Path) -> dict:
    """Last-wins deduplication by trace_id."""
    latest: dict = {}
    for row in load_jsonl(path):
        latest[row["trace_id"]] = row
    return latest


def save_judge_result(result: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")


def create_app(
    *,
    traces_path: Path,
    questions_path: Path,
    dataset_path: Path,
    judge_results_path: Path,
    annotator: str = "unknown",
) -> Flask:
    app = Flask(__name__, template_folder="templates")
    app.config["TRACES_PATH"] = Path(traces_path)
    app.config["QUESTIONS_PATH"] = Path(questions_path)
    app.config["DATASET_PATH"] = Path(dataset_path)
    app.config["JUDGE_RESULTS_PATH"] = Path(judge_results_path)
    app.config["ANNOTATOR"] = annotator

    @app.get("/")
    def index_annotate():
        return render_template("annotate.html", categories=_CATEGORY_LABELS)

    @app.get("/judge")
    def index_judge():
        return render_template("judge.html")

    @app.get("/api/traces")
    def get_traces():
        traces = load_jsonl(app.config["TRACES_PATH"])
        questions_by_id = {q["id"]: q for q in load_jsonl(app.config["QUESTIONS_PATH"])}
        human_annotations = load_latest_annotations(app.config["DATASET_PATH"])
        judge_results = load_latest_judge_results(app.config["JUDGE_RESULTS_PATH"])
        result = []
        for t in traces:
            q = questions_by_id.get(t["question_id"], {})
            result.append({
                "trace": t,
                "expected_answer": q.get("expected_answer", ""),
                "human_annotation": human_annotations.get(t["id"]),
                "judge_result": judge_results.get(t["id"]),
            })
        return jsonify(result)

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

    @app.post("/api/judge")
    def post_judge():
        body = request.get_json(silent=True) or {}
        trace_id = body.get("trace_id")
        model = body.get("model", "mimo-v2.5-pro")

        traces = load_jsonl(app.config["TRACES_PATH"])
        trace = next((t for t in traces if t["id"] == trace_id), None)
        if trace is None:
            return jsonify({"error": f"trace_id {trace_id} 不存在"}), 404

        eval_result = run_all_judges(trace, model=model)

        row = {
            "trace_id": eval_result.trace_id,
            "label": eval_result.label,
            "dimensions": [asdict(d) for d in eval_result.dimensions],
            "judged_at": datetime.now(timezone.utc).isoformat(),
        }
        save_judge_result(row, app.config["JUDGE_RESULTS_PATH"])
        return jsonify(row)

    return app


def _main() -> None:
    parser = argparse.ArgumentParser(description="Eval Web UI")
    parser.add_argument("--annotator", default="unknown", help="标注者名字（写入 dataset.jsonl）")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--traces", default="data/traces.jsonl")
    parser.add_argument("--questions", default="data/questions.jsonl")
    parser.add_argument("--dataset", default="data/dataset.jsonl")
    parser.add_argument("--judge-results", default="data/judge_results.jsonl", dest="judge_results")
    args = parser.parse_args()

    app = create_app(
        traces_path=Path(args.traces),
        questions_path=Path(args.questions),
        dataset_path=Path(args.dataset),
        judge_results_path=Path(args.judge_results),
        annotator=args.annotator,
    )
    print(f"Annotate UI: http://127.0.0.1:{args.port}/")
    print(f"Judge UI:    http://127.0.0.1:{args.port}/judge")
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    _main()
```

- [ ] **Step 2: Run tests to verify GREEN**

```bash
uv run pytest tests/test_web.py -v
```

Expected: all tests pass.

- [ ] **Step 3: Commit GREEN**

```bash
git add eval/web.py
git commit -m "feat: add eval/web.py — unified annotate + judge server"
```

---

## Task 3: Update Templates

Two changes in `annotate.html`: rename `latest_annotation` → `human_annotation` in JS (6 occurrences), add nav link. One change in `judge.html`: add nav link.

**Files:**
- Modify: `eval/templates/annotate.html`
- Modify: `eval/templates/judge.html`

- [ ] **Step 1: Rename `latest_annotation` → `human_annotation` in `annotate.html`**

There are 6 occurrences. Apply all of them:

| Old | New |
|-----|-----|
| `t.latest_annotation === null` (in `init`) | `t.human_annotation === null` |
| `const ann = entry.latest_annotation;` (in `renderStrip`) | `const ann = entry.human_annotation;` |
| `const ann = entry.latest_annotation;` (in `renderForm`) | `const ann = entry.human_annotation;` |
| `entry.latest_annotation = body.annotation;` (in `save`) | `entry.human_annotation = body.annotation;` |
| `t.latest_annotation === null` (first occurrence in `save`) | `t.human_annotation === null` |
| `t.latest_annotation === null` (second occurrence in `save`) | `t.human_annotation === null` |

Find and replace all: in `eval/templates/annotate.html`, replace every instance of `latest_annotation` with `human_annotation`.

- [ ] **Step 2: Add nav link to `annotate.html` header**

In `eval/templates/annotate.html`, find:
```html
      <div class="header">
        <span class="header-label">Eval · Stage 2</span>
        <span class="progress-badge" id="progress"></span>
      </div>
```

Replace with:
```html
      <div class="header">
        <span class="header-label">Eval · Stage 2</span>
        <span class="progress-badge" id="progress"></span>
        <a href="/judge" style="margin-left:auto;font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--text-3);text-decoration:none;">Judge UI →</a>
      </div>
```

- [ ] **Step 3: Add nav link to `judge.html` header**

In `eval/templates/judge.html`, find:
```html
    <div class="header">
      <span class="header-label">Eval · Stage 4/5 Judge</span>
    </div>
```

Replace with:
```html
    <div class="header">
      <span class="header-label">Eval · Stage 4/5 Judge</span>
      <a href="/" style="margin-left:auto;font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--text-3);text-decoration:none;">← 标注 UI</a>
    </div>
```

- [ ] **Step 4: Run template content tests to verify changes**

```bash
uv run pytest tests/test_web.py::test_get_root_html_uses_human_annotation_not_latest tests/test_web.py::test_get_judge_serves_judge_page -v
```

Expected: both pass. `test_get_root_html_uses_human_annotation_not_latest` verifies `annotate.html` JS has no `latest_annotation` and the nav link is present. `test_get_judge_serves_judge_page` verifies `/judge` renders the correct template with expected content.

- [ ] **Step 5: Commit**

```bash
git add eval/templates/annotate.html eval/templates/judge.html
git commit -m "feat: update templates — human_annotation key rename + nav links"
```

---

## Task 4: Delete Old Files + Update Docs

Delete the four old files, update commands in `CLAUDE.md` and `README.md`.

**Files:**
- Delete: `eval/annotate_web.py`
- Delete: `eval/judge_web.py`
- Delete: `tests/test_annotate_web.py`
- Delete: `tests/test_judge_web.py`
- Modify: `CLAUDE.md`
- Modify: `README.md`

- [ ] **Step 1: Delete old source + test files**

```bash
rm eval/annotate_web.py eval/judge_web.py
rm tests/test_annotate_web.py tests/test_judge_web.py
```

- [ ] **Step 2: Update `CLAUDE.md` commands section**

In `CLAUDE.md`, find the commands block that contains:
```
# 标注 UI（Stage 2）
uv run python -m eval.annotate_web --port 5000 --annotator <name>

# Judge UI（Stage 4/5）
uv run python -m eval.judge_web --port 5001
```

Replace with:
```
# 标注 & Judge UI（单一服务）
uv run python -m eval.web --port 5000 --annotator <name>
# 标注 UI: http://127.0.0.1:5000/
# Judge UI: http://127.0.0.1:5000/judge
```

- [ ] **Step 3: Update `README.md` Stage 2 command**

In `README.md`, find:
```
uv run python -m eval.annotate_web --port 5000 --annotator yourname
# 打开 http://127.0.0.1:5000
```

Replace with:
```
uv run python -m eval.web --port 5000 --annotator yourname
# 标注 UI: http://127.0.0.1:5000/
# Judge UI: http://127.0.0.1:5000/judge
```

- [ ] **Step 4: Update `README.md` Stage 4 command**

In `README.md`, find:
```
uv run python -m eval.judge_web --port 5001
# 打开 http://127.0.0.1:5001
```

Replace with:
```
uv run python -m eval.web --port 5000
# Judge UI: http://127.0.0.1:5000/judge
```

- [ ] **Step 5: Run full test suite to verify clean**

```bash
uv run pytest tests/ -m "not integration" -q
```

Expected: all pass, no import errors from deleted modules.

- [ ] **Step 6: Commit**

```bash
git add -u
git commit -m "feat: delete annotate_web/judge_web, update CLAUDE.md + README commands"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Covered by |
|-----------------|-----------|
| Single `create_app()` with all 5 routes | Task 2 |
| `--annotator` optional, defaults to `"unknown"` | Task 2, `_main()` |
| All CLI flags with defaults | Task 2, `_main()` |
| `human_annotation` key (not `latest_annotation`) | Task 2 `/api/traces` + Task 3 annotate.html |
| `human_annotation` and `judge_result` are `null` when absent | Task 2 (`.get()` returns `None` → JSON `null`) |
| Missing JSONL files treated as empty | Task 2 (inherits from `load_jsonl` + `load_latest_annotations`) |
| Nav link in `annotate.html` | Task 3 Step 2 |
| Nav link in `judge.html` | Task 3 Step 3 |
| Delete 4 old files | Task 4 Step 1 |
| `CLAUDE.md` command update | Task 4 Step 2 |
| `README.md` command update | Task 4 Steps 3-4 |
| 5 new regression tests | Task 1 (new unified contract tests section) |
| All existing tests preserved | Task 1 (migrated annotate + judge sections) |

**Placeholder scan:** None found. All steps have concrete code or exact commands.

**Type consistency:**
- `create_app()` signature is identical across Task 1 (imported in tests) and Task 2 (defined) — ✓
- `load_latest_judge_results` / `save_judge_result` defined in Task 2, not called from tests directly — ✓
- Mock patch target `eval.web.run_all_judges` matches the import in Task 2 — ✓
- `human_annotation` key used consistently in Task 1 tests and Task 2 implementation — ✓
