import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from eval.collectors.workflow_collector import WorkflowCollector, collect_all
from eval.schema import Question

SAMPLE_Q: Question = {
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
}

MOCK_RESPONSE = {
    "answer": "根据政策，最低为500万元。",
    "retrieved_chunks": ["原文：设备总额不低于500万元"],
    "citations": ["https://policy.example.com"],
}


def _make_collector() -> WorkflowCollector:
    return WorkflowCollector(
        base_url="http://fake-api",
        api_key="fake-key",
        answer_field="answer",
        chunks_field="retrieved_chunks",
        citations_field="citations",
    )


def test_collect_returns_trace():
    collector = _make_collector()
    with patch.object(collector, "_call_api", return_value=MOCK_RESPONSE):
        trace = collector.collect(SAMPLE_Q)
    assert trace["question_id"] == "q_001"
    assert trace["actual_answer"] == "根据政策，最低为500万元。"
    assert trace["retrieved_chunks"] == ["原文：设备总额不低于500万元"]
    assert trace["citations"] == ["https://policy.example.com"]


def test_collect_passes_history_to_api():
    q_with_history: Question = {
        **SAMPLE_Q,
        "conversation_history": [
            {"role": "user", "content": "支持哪些企业？"},
            {"role": "assistant", "content": "已认定的省级技术中心。"},
        ],
    }
    collector = _make_collector()
    with patch.object(collector, "_call_api", return_value=MOCK_RESPONSE) as mock_call:
        collector.collect(q_with_history)
    _, kwargs = mock_call.call_args
    assert len(kwargs["conversation_history"]) == 2


def test_missing_chunks_field_returns_empty_list():
    collector = WorkflowCollector(
        base_url="http://fake-api",
        api_key="fake-key",
        answer_field="answer",
        chunks_field="nonexistent",
        citations_field="citations",
    )
    with patch.object(collector, "_call_api", return_value={"answer": "回答", "citations": []}):
        trace = collector.collect(SAMPLE_Q)
    assert trace["retrieved_chunks"] == []


def test_trace_id_matches_question_id():
    collector = _make_collector()
    with patch.object(collector, "_call_api", return_value=MOCK_RESPONSE):
        t = collector.collect(SAMPLE_Q)
    assert t["id"] == "q_001"


def test_null_chunks_field_returns_empty_list():
    collector = _make_collector()
    response_with_null = {"answer": "回答", "retrieved_chunks": None, "citations": None}
    with patch.object(collector, "_call_api", return_value=response_with_null):
        trace = collector.collect(SAMPLE_Q)
    assert trace["retrieved_chunks"] == []
    assert trace["citations"] == []


def test_call_api_sends_correct_request():
    """End-to-end shape check on the actual HTTP path: URL, bearer header,
    payload, timeout tuple, and raise_for_status all wired correctly."""
    collector = WorkflowCollector(
        base_url="http://fake-api/v1/",  # trailing slash — should be stripped
        api_key="secret-token",
        answer_field="answer",
        chunks_field="retrieved_chunks",
        citations_field="citations",
        connect_timeout=5,
        read_timeout=90,
    )
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"answer": "ok", "retrieved_chunks": [], "citations": []}
    fake_resp.raise_for_status = MagicMock()

    with patch("eval.collectors.workflow_collector.requests.post", return_value=fake_resp) as mock_post:
        collector._call_api(query="Q", conversation_history=[])

    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert args[0] == "http://fake-api/v1/workflows/run"
    assert kwargs["headers"]["Authorization"] == "Bearer secret-token"
    assert kwargs["headers"]["Content-Type"] == "application/json"
    assert kwargs["json"]["query"] == "Q"
    assert kwargs["timeout"] == (5, 90)
    fake_resp.raise_for_status.assert_called_once()


def test_call_api_raises_on_http_error():
    collector = _make_collector()
    fake_resp = MagicMock()
    fake_resp.raise_for_status.side_effect = requests.HTTPError("500 Server Error")

    with patch("eval.collectors.workflow_collector.requests.post", return_value=fake_resp):
        with pytest.raises(requests.HTTPError):
            collector._call_api(query="Q", conversation_history=[])


def test_collect_all_overwrites_output(tmp_path: Path):
    """Re-running collect_all should produce a fresh traces file, not append
    to the previous run."""
    q_path = tmp_path / "questions.jsonl"
    q_path.write_text(
        json.dumps({**SAMPLE_Q, "id": "q_001"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    out_path = tmp_path / "traces.jsonl"
    out_path.write_text("STALE PRIOR CONTENT\n", encoding="utf-8")

    collector = _make_collector()
    with patch.object(collector, "_call_api", return_value=MOCK_RESPONSE):
        result = collect_all(q_path, out_path, collector=collector)

    lines = out_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    assert "STALE" not in out_path.read_text(encoding="utf-8")
    assert result["succeeded"] == 1
    assert result["failed"] == []


def test_collect_all_continues_on_per_question_failure(tmp_path: Path):
    """One failing question must not abort the batch — its ID is recorded
    and remaining questions still get traces."""
    q_path = tmp_path / "questions.jsonl"
    q_path.write_text(
        json.dumps({**SAMPLE_Q, "id": "q_001"}, ensure_ascii=False) + "\n"
        + json.dumps({**SAMPLE_Q, "id": "q_002"}, ensure_ascii=False) + "\n"
        + json.dumps({**SAMPLE_Q, "id": "q_003"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    out_path = tmp_path / "traces.jsonl"

    collector = _make_collector()
    call_results = [MOCK_RESPONSE, requests.HTTPError("502 Bad Gateway"), MOCK_RESPONSE]

    def side_effect(**kwargs):
        result = call_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    with patch.object(collector, "_call_api", side_effect=side_effect):
        result = collect_all(q_path, out_path, collector=collector)

    assert result["succeeded"] == 2
    assert result["failed"] == ["q_002"]
    written = [json.loads(line) for line in out_path.read_text(encoding="utf-8").strip().split("\n")]
    assert [t["id"] for t in written] == ["q_001", "q_003"]
