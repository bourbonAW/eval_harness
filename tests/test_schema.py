from eval.schema import AnnotatedSample, Question, Trace


def test_question_fields():
    q: Question = {
        "id": "q_001",
        "question": "支持哪些企业？",
        "expected_answer": "已认定为省级企业技术中心的企业。",
        "source_policy_url": "https://example.com/policy",
        "source_doc_url": "https://example.com/doc",
        "source_doc_name": "申报指南.docx",
        "is_multi_intent": False,
        "knowledge_type": "文档",
        "is_prohibited": False,
        "conversation_history": [],
        "notes": "",
    }
    assert q["id"] == "q_001"
    assert q["knowledge_type"] == "文档"
    assert q["conversation_history"] == []


def test_trace_fields():
    t: Trace = {
        "id": "trace_001",
        "question_id": "q_001",
        "question": "支持哪些企业？",
        "conversation_history": [],
        "actual_answer": "已认定的省级企业技术中心。",
        "retrieved_chunks": ["原文：不低于500万元"],
        "citations": ["https://example.com/policy"],
    }
    assert t["retrieved_chunks"] == ["原文：不低于500万元"]


def test_annotated_sample_fields():
    s: AnnotatedSample = {
        "id": "trace_001",
        "question_id": "q_001",
        "question": "支持哪些企业？",
        "conversation_history": [],
        "actual_answer": "已认定的省级企业技术中心。",
        "retrieved_chunks": [],
        "citations": [],
        "expected_answer": "已认定为省级企业技术中心的企业。",
        "label": "pass",
        "critique": "",
        "failure_category": None,
        "annotated_by": "tester",
        "annotated_at": "2026-05-18T10:00:00+00:00",
    }
    assert s["label"] == "pass"
    assert s["failure_category"] is None
