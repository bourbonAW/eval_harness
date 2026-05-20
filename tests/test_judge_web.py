import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from eval.judge_web import create_app
from eval.judges import DimensionResult, EvalResult

SAMPLE_QUESTIONS = [
    {
        "id": "q_001", "question": "最低要求是多少？", "expected_answer": "500万元。",
        "source_policy_url": "", "source_doc_url": "", "source_doc_name": "",
        "is_multi_intent": False, "knowledge_type": "文档", "is_prohibited": False,
        "conversation_history": [], "notes": "",
    },
]

SAMPLE_TRACES = [
    {
        "id": "q_001", "question_id": "q_001", "question": "最低要求是多少？",
        "complete_question": "最低要求是多少？", "conversation_history": [],
        "actual_answer": "最低为500万元。", "doc_context": "不低于500万元",
        "faq_context": "", "references": [], "ref_num": 0,
    },
]

SAMPLE_ANNOTATION = {
    "id": "q_001", "question_id": "q_001", "question": "最低要求是多少？",
    "complete_question": "最低要求是多少？", "conversation_history": [],
    "actual_answer": "最低为500万元。", "doc_context": "", "faq_context": "",
    "references": [], "ref_num": 0, "expected_answer": "500万元。",
    "label": "pass", "critique": "正确回答", "failure_category": None,
    "annotated_by": "tester", "annotated_at": "2026-05-20T10:00:00+00:00",
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
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


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
    )
    app.testing = True
    return app.test_client()


# ── GET /api/traces ───────────────────────────────────────────────────────────


def test_get_traces_judge_result_is_none_when_not_run(client):
    body = client.get("/api/traces").get_json()
    assert len(body) == 1
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


# ── POST /api/judge ───────────────────────────────────────────────────────────

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
    with patch("eval.judge_web.run_all_judges", side_effect=_fake_judge):
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
    with patch("eval.judge_web.run_all_judges", side_effect=_fake_judge):
        resp = client.post("/api/judge", json={"trace_id": "q_999"})
    assert resp.status_code == 404
    assert _read_jsonl(data_dir / "judge_results.jsonl") == []


def test_post_judge_respects_model_param(client, data_dir):
    captured = {}

    def fake_with_model(trace, *, model="mimo-v2.5-pro"):
        captured["model"] = model
        return _fake_judge(trace, model=model)

    with patch("eval.judge_web.run_all_judges", side_effect=fake_with_model):
        client.post("/api/judge", json={"trace_id": "q_001", "model": "mimo-v2-omni"})
    assert captured["model"] == "mimo-v2-omni"


# ── GET / ─────────────────────────────────────────────────────────────────────


def test_get_root_returns_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.mimetype == "text/html"
