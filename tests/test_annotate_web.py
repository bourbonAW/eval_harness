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
