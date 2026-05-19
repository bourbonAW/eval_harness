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

# What _call_api returns after parsing the SSE stream.
MOCK_PARSED = {
    "answer": "根据政策，最低为500万元。",
    "complete_question": "广州市企业申报专精特新最低要求是多少？",
    "doc_str": "1. [政策文件](https://policy.example.com)\n原文：设备总额不低于500万元",
    "faq_str": "question: 最低要求\nanswer: 500万元。",
    "ref_str": json.dumps([{"id": 1, "name": "[1] 政策文件", "url": "https://policy.example.com"}]),
    "ref_num": 1,
}


def _make_collector() -> WorkflowCollector:
    return WorkflowCollector(
        base_url="http://fake-api",
        api_key="fake-key",
        session_id="test-session",
        channel_id="1",
        answer_field="answer",
        complete_question_field="complete_question",
        doc_str_field="doc_str",
        faq_str_field="faq_str",
        ref_str_field="ref_str",
        ref_num_field="ref_num",
    )


def _make_sse_response(text_parts: list[str], think_json: dict | None = None) -> MagicMock:
    """Build a mock streaming response whose iter_lines() yields SSE events."""
    lines = []

    if think_json:
        think_text = f"<THINK>{json.dumps(think_json, ensure_ascii=False)}</THINK>"
        event = {"jsonrpc": "2.0", "result": {"parts": [{"kind": "text", "text": think_text}]}}
        lines.append(f"data: {json.dumps(event)}".encode())

    for text in text_parts:
        event = {"jsonrpc": "2.0", "result": {"parts": [{"kind": "text", "text": text}]}}
        lines.append(f"data: {json.dumps(event)}".encode())

    # terminal event with finish_reason
    final_event = {"jsonrpc": "2.0", "result": {"parts": [{"kind": "text", "text": ""}], "metadata": {"finish_reason": "stop"}}}
    lines.append(f"data: {json.dumps(final_event)}".encode())

    mock_resp = MagicMock()
    mock_resp.iter_lines.return_value = iter(lines)
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


# ---------------------------------------------------------------------------
# collect() — mocks _call_api directly; not affected by streaming changes
# ---------------------------------------------------------------------------

def test_collect_returns_trace():
    collector = _make_collector()
    with patch.object(collector, "_call_api", return_value=MOCK_PARSED):
        trace = collector.collect(SAMPLE_Q)
    assert trace["question_id"] == "q_001"
    assert trace["actual_answer"] == "根据政策，最低为500万元。"
    assert trace["complete_question"] == "广州市企业申报专精特新最低要求是多少？"
    assert trace["doc_context"] == "1. [政策文件](https://policy.example.com)\n原文：设备总额不低于500万元"
    assert trace["faq_context"] == "question: 最低要求\nanswer: 500万元。"
    assert trace["references"] == [{"doc_id": 1, "name": "[1] 政策文件", "url": "https://policy.example.com"}]
    assert trace["ref_num"] == 1


def test_collect_passes_history_to_api():
    q_with_history: Question = {
        **SAMPLE_Q,
        "conversation_history": [
            {"role": "user", "content": "支持哪些企业？"},
            {"role": "assistant", "content": "已认定的省级技术中心。"},
        ],
    }
    collector = _make_collector()
    with patch.object(collector, "_call_api", return_value=MOCK_PARSED) as mock_call:
        collector.collect(q_with_history)
    _, kwargs = mock_call.call_args
    assert len(kwargs["conversation_history"]) == 2


def test_missing_doc_str_returns_empty_string():
    collector = WorkflowCollector(
        base_url="http://fake-api",
        api_key="fake-key",
        session_id="test-session",
        channel_id="1",
        answer_field="answer",
        complete_question_field="complete_question",
        doc_str_field="nonexistent_doc",
        faq_str_field="faq_str",
        ref_str_field="ref_str",
        ref_num_field="ref_num",
    )
    with patch.object(collector, "_call_api", return_value=MOCK_PARSED):
        trace = collector.collect(SAMPLE_Q)
    assert trace["doc_context"] == ""


def test_trace_id_matches_question_id():
    collector = _make_collector()
    with patch.object(collector, "_call_api", return_value=MOCK_PARSED):
        t = collector.collect(SAMPLE_Q)
    assert t["id"] == "q_001"


def test_null_fields_return_safe_defaults():
    collector = _make_collector()
    response_with_nulls = {
        "answer": "回答",
        "complete_question": None,
        "doc_str": None,
        "faq_str": None,
        "ref_str": None,
        "ref_num": None,
    }
    with patch.object(collector, "_call_api", return_value=response_with_nulls):
        trace = collector.collect(SAMPLE_Q)
    assert trace["complete_question"] == SAMPLE_Q["question"]
    assert trace["doc_context"] == ""
    assert trace["faq_context"] == ""
    assert trace["references"] == []
    assert trace["ref_num"] == 0


def test_complete_question_falls_back_to_question_when_missing():
    collector = WorkflowCollector(
        base_url="http://fake-api",
        api_key="fake-key",
        session_id="test-session",
        channel_id="1",
        answer_field="answer",
        complete_question_field="nonexistent_field",
        doc_str_field="doc_str",
        faq_str_field="faq_str",
        ref_str_field="ref_str",
        ref_num_field="ref_num",
    )
    with patch.object(collector, "_call_api", return_value=MOCK_PARSED):
        trace = collector.collect(SAMPLE_Q)
    assert trace["complete_question"] == SAMPLE_Q["question"]


# ---------------------------------------------------------------------------
# _parse_references
# ---------------------------------------------------------------------------

def test_parse_references_handles_json_string():
    collector = _make_collector()
    ref_str = json.dumps([{"id": 42, "name": "[1] 政策", "url": "https://example.com"}])
    refs = collector._parse_references(ref_str)
    assert refs == [{"doc_id": 42, "name": "[1] 政策", "url": "https://example.com"}]


def test_parse_references_handles_list_directly():
    collector = _make_collector()
    refs = collector._parse_references([{"id": "faq_01", "name": "FAQ", "url": "https://faq.com"}])
    assert refs == [{"doc_id": "faq_01", "name": "FAQ", "url": "https://faq.com"}]


def test_parse_references_returns_empty_on_malformed():
    collector = _make_collector()
    assert collector._parse_references("{not valid json") == []
    assert collector._parse_references(None) == []
    assert collector._parse_references("") == []


# ---------------------------------------------------------------------------
# _parse_response_text — unit tests for the SSE text parser
# ---------------------------------------------------------------------------

def test_parse_response_text_extracts_clean_answer():
    collector = _make_collector()
    text = (
        '<THINK>{"label":"正在检索","icon":"loading","display":"override"}</THINK>'
        "根据政策，最低为500万元。"
    )
    result = collector._parse_response_text(text)
    assert result["answer"] == "根据政策，最低为500万元。"


def test_parse_response_text_extracts_references_from_retrieval_done():
    collector = _make_collector()
    links = [{"id": 1, "name": "[1] 政策文件", "url": "https://example.com"}]
    think_json = {"label": "检索到1个文档", "icon": "done", "display": "fixed", "links": links}
    text = f"<THINK>{json.dumps(think_json)}</THINK>回答内容。"
    result = collector._parse_response_text(text)
    assert result["ref_str"] == json.dumps(links, ensure_ascii=False)
    assert result["ref_num"] == 1


def test_parse_response_text_ignores_loading_think_for_references():
    """A THINK block without icon==done must not be treated as references."""
    collector = _make_collector()
    text = '<THINK>{"label":"正在分析","icon":"loading"}</THINK>回答。'
    result = collector._parse_response_text(text)
    assert result["ref_str"] == ""
    assert result["ref_num"] == 0


def test_parse_response_text_handles_future_context_fields():
    """Workflow will eventually add doc_str/faq_str in a THINK block."""
    collector = _make_collector()
    context_block = {
        "doc_str": "1. [政策文件](https://example.com)\n原文内容",
        "faq_str": "question: Q\nanswer: A",
        "complete_question": "改写后的问题",
    }
    text = f"<THINK>{json.dumps(context_block, ensure_ascii=False)}</THINK>回答。"
    result = collector._parse_response_text(text)
    assert result["doc_str"] == "1. [政策文件](https://example.com)\n原文内容"
    assert result["faq_str"] == "question: Q\nanswer: A"
    assert result["complete_question"] == "改写后的问题"


def test_parse_response_text_strips_addition_blocks():
    collector = _make_collector()
    text = '回答内容。<ADDITION>{"display":"fixed","links":[]}</ADDITION>'
    result = collector._parse_response_text(text)
    assert result["answer"] == "回答内容。"


def test_parse_response_text_handles_lowercase_think_tags():
    """User plans to use lowercase <think> for future context blocks."""
    collector = _make_collector()
    context_block = {"doc_str": "文档内容", "faq_str": ""}
    text = f"<think>{json.dumps(context_block)}</think>回答。"
    result = collector._parse_response_text(text)
    assert result["doc_str"] == "文档内容"
    assert result["answer"] == "回答。"


def test_parse_response_text_raw_context_tags():
    """New tag format: <DOC_CONTEXT>, <FAQ_CONTEXT>, <COMPLETE_QUERY>."""
    collector = _make_collector()
    text = (
        "回答内容。"
        "<DOC_CONTEXT>\n1. [政策文件](https://example.com)\n原文内容\n</DOC_CONTEXT>"
        "<FAQ_CONTEXT>\nquestion: Q\nanswer: A\n</FAQ_CONTEXT>"
        "<COMPLETE_QUERY>\n完整问题\n</COMPLETE_QUERY>"
    )
    result = collector._parse_response_text(text)
    assert result["answer"] == "回答内容。"
    assert result["doc_str"] == "1. [政策文件](https://example.com)\n原文内容"
    assert result["faq_str"] == "question: Q\nanswer: A"
    assert result["complete_question"] == "完整问题"


def test_parse_response_text_raw_tags_with_quotes_and_newlines():
    """Raw tags must survive ASCII quotes and newlines without escaping."""
    collector = _make_collector()
    text = (
        '答案。'
        '<DOC_CONTEXT>\n内容含"引号"和换行\n</DOC_CONTEXT>'
        '<FAQ_CONTEXT>\n包含"小巨人"企业\n</FAQ_CONTEXT>'
        '<COMPLETE_QUERY>\n查询\n</COMPLETE_QUERY>'
    )
    result = collector._parse_response_text(text)
    assert result["answer"] == "答案。"
    assert '"引号"' in result["doc_str"]
    assert '"小巨人"' in result["faq_str"]


def test_parse_response_text_malformed_think_json_is_skipped():
    collector = _make_collector()
    text = "<THINK>{broken json</THINK>回答。"
    result = collector._parse_response_text(text)
    assert result["answer"] == "回答。"
    assert result["ref_str"] == ""


# ---------------------------------------------------------------------------
# _accumulate_sse — unit tests for the SSE stream reader
# ---------------------------------------------------------------------------

def test_accumulate_sse_concatenates_text_parts():
    collector = _make_collector()
    mock_resp = _make_sse_response(["政策", "收益"])
    result = collector._accumulate_sse(mock_resp)
    assert "政策收益" in result


def test_accumulate_sse_skips_ping_lines():
    collector = _make_collector()
    answer_event = json.dumps({
        "jsonrpc": "2.0",
        "result": {"parts": [{"kind": "text", "text": "回答"}]},
    })
    lines = [
        b": ping - 2026-05-19 10:00:00+00:00",
        f"data: {answer_event}".encode("utf-8"),
        b": ping - 2026-05-19 10:00:15+00:00",
    ]
    mock_resp = MagicMock()
    mock_resp.iter_lines.return_value = iter(lines)
    result = collector._accumulate_sse(mock_resp)
    assert result == "回答"


def test_accumulate_sse_skips_malformed_json():
    collector = _make_collector()
    ok_event = json.dumps({
        "jsonrpc": "2.0",
        "result": {"parts": [{"kind": "text", "text": "OK"}]},
    })
    lines = [
        b"data: {broken",
        f"data: {ok_event}".encode("utf-8"),
    ]
    mock_resp = MagicMock()
    mock_resp.iter_lines.return_value = iter(lines)
    result = collector._accumulate_sse(mock_resp)
    assert result == "OK"


# ---------------------------------------------------------------------------
# _call_api — end-to-end through SSE stream
# ---------------------------------------------------------------------------

def test_call_api_streams_and_parses_response():
    """_call_api must stream, parse SSE events, and return the structured dict."""
    collector = _make_collector()
    retrieval_think = {
        "label": "检索到1个文档", "icon": "done", "display": "fixed",
        "links": [{"id": 1, "name": "[1] 政策文件", "url": "https://policy.example.com"}],
    }
    mock_resp = _make_sse_response(["根据政策，最低为", "500万元。"], think_json=retrieval_think)

    with patch("eval.collectors.workflow_collector.requests.post", return_value=mock_resp) as mock_post:
        result = collector._call_api(query="最低要求是多少？", conversation_history=[])

    assert mock_post.call_args.kwargs["stream"] is True
    assert result["answer"] == "根据政策，最低为500万元。"
    assert result["ref_num"] == 1
    refs = json.loads(result["ref_str"])
    assert refs[0]["url"] == "https://policy.example.com"


def test_call_api_sends_correct_request():
    collector = WorkflowCollector(
        base_url="http://fake-api/v1/",  # trailing slash — should be stripped
        api_key="secret-token",
        session_id="sess-abc",
        channel_id="7",
        answer_field="answer",
        complete_question_field="complete_question",
        doc_str_field="doc_str",
        faq_str_field="faq_str",
        ref_str_field="ref_str",
        ref_num_field="ref_num",
        connect_timeout=5,
        read_timeout=90,
    )
    mock_resp = _make_sse_response(["ok"])

    with patch("eval.collectors.workflow_collector.requests.post", return_value=mock_resp) as mock_post:
        collector._call_api(query="Q", conversation_history=[])

    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    expected_url = (
        "http://fake-api/v1/consultant_platform/api/app/universal/chat"
        "/1/7/session/sess-abc/message_stream"
    )
    assert args[0] == expected_url
    assert kwargs["headers"]["Authorization"] == "secret-token"
    assert kwargs["headers"]["Content-Type"] == "application/json"
    assert kwargs["json"]["message"]["parts"][0]["text"] == "Q"
    assert kwargs["json"]["message"]["parts"][0]["kind"] == "text"
    assert kwargs["timeout"] == (5, 90)
    assert kwargs["stream"] is True
    mock_resp.raise_for_status.assert_called_once()


def test_call_api_raises_on_http_error():
    collector = _make_collector()
    fake_resp = MagicMock()
    fake_resp.raise_for_status.side_effect = requests.HTTPError("500 Server Error")

    with patch("eval.collectors.workflow_collector.requests.post", return_value=fake_resp):
        with pytest.raises(requests.HTTPError):
            collector._call_api(query="Q", conversation_history=[])


# ---------------------------------------------------------------------------
# collect_all
# ---------------------------------------------------------------------------

def test_collect_all_overwrites_output(tmp_path: Path):
    """Re-running collect_all should produce a fresh traces file, not append."""
    q_path = tmp_path / "questions.jsonl"
    q_path.write_text(
        json.dumps({**SAMPLE_Q, "id": "q_001"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    out_path = tmp_path / "traces.jsonl"
    out_path.write_text("STALE PRIOR CONTENT\n", encoding="utf-8")

    collector = _make_collector()
    with patch.object(collector, "_call_api", return_value=MOCK_PARSED):
        result = collect_all(q_path, out_path, collector=collector)

    lines = out_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    assert "STALE" not in out_path.read_text(encoding="utf-8")
    assert result["succeeded"] == 1
    assert result["failed"] == []


def test_collect_all_continues_on_per_question_failure(tmp_path: Path):
    """One failing question must not abort the batch."""
    q_path = tmp_path / "questions.jsonl"
    q_path.write_text(
        json.dumps({**SAMPLE_Q, "id": "q_001"}, ensure_ascii=False) + "\n"
        + json.dumps({**SAMPLE_Q, "id": "q_002"}, ensure_ascii=False) + "\n"
        + json.dumps({**SAMPLE_Q, "id": "q_003"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    out_path = tmp_path / "traces.jsonl"

    collector = _make_collector()
    call_results = [MOCK_PARSED, requests.HTTPError("502 Bad Gateway"), MOCK_PARSED]

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
