from unittest.mock import patch

from eval.collectors.workflow_collector import WorkflowCollector
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
