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
        "complete_question": "广州市支持哪些企业申报专精特新？",
        "conversation_history": [],
        "actual_answer": "已认定的省级企业技术中心。",
        "doc_context": "1. [政策文件](https://example.com)\n原文内容...",
        "faq_context": "question: 支持哪些企业？\nanswer: 省级企业技术中心。",
        "references": [{"doc_id": 1, "name": "[1] 政策文件", "url": "https://example.com"}],
        "ref_num": 1,
    }
    assert t["complete_question"] == "广州市支持哪些企业申报专精特新？"
    assert t["references"][0]["url"] == "https://example.com"
    assert t["ref_num"] == 1


def test_annotated_sample_fields():
    s: AnnotatedSample = {
        "id": "trace_001",
        "question_id": "q_001",
        "question": "支持哪些企业？",
        "complete_question": "广州市支持哪些企业申报专精特新？",
        "conversation_history": [],
        "actual_answer": "已认定的省级企业技术中心。",
        "doc_context": "",
        "faq_context": "",
        "references": [],
        "ref_num": 0,
        "expected_answer": "已认定为省级企业技术中心的企业。",
        "label": "pass",
        "critique": "",
        "failure_category": None,
        "annotated_by": "tester",
        "annotated_at": "2026-05-18T10:00:00+00:00",
    }
    assert s["label"] == "pass"
    assert s["failure_category"] is None


def test_annotated_sample_fail_fields():
    s: AnnotatedSample = {
        "id": "trace_002",
        "question_id": "q_002",
        "question": "申报条件是什么？",
        "complete_question": "申报条件是什么？",
        "conversation_history": [],
        "actual_answer": "需要500万元以上资产。",
        "doc_context": "原文：研发费用不低于100万元",
        "faq_context": "",
        "references": [],
        "ref_num": 1,
        "expected_answer": "需要研发费用不低于100万元。",
        "label": "fail",
        "critique": "答案数字错误，context 显示是研发费用门槛而非资产。",
        "failure_category": "hallucination",
        "annotated_by": "tester",
        "annotated_at": "2026-05-18T10:00:00+00:00",
    }
    assert s["label"] == "fail"
    assert s["failure_category"] == "hallucination"
    assert s["critique"] != ""
