import json

from eval.annotate import load_jsonl, needs_annotation, save_annotation
from eval.schema import AnnotatedSample, Trace

SAMPLE_TRACE: Trace = {
    "id": "trace_001",
    "question_id": "q_001",
    "question": "最低要求是多少？",
    "conversation_history": [],
    "actual_answer": "根据政策，最低为500万元。",
    "retrieved_chunks": ["原文：不低于500万元"],
    "citations": ["https://policy.example.com"],
}


def test_load_jsonl_reads_all_lines(tmp_path):
    f = tmp_path / "test.jsonl"
    f.write_text(
        json.dumps({"id": "a"}, ensure_ascii=False)
        + "\n"
        + json.dumps({"id": "b"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    items = load_jsonl(f)
    assert len(items) == 2
    assert items[0]["id"] == "a"


def test_load_jsonl_returns_empty_for_missing_file(tmp_path):
    items = load_jsonl(tmp_path / "nonexistent.jsonl")
    assert items == []


def test_needs_annotation_true_when_dataset_missing(tmp_path):
    assert needs_annotation("trace_001", tmp_path / "dataset.jsonl") is True


def test_needs_annotation_false_when_already_annotated(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    sample: AnnotatedSample = {
        **SAMPLE_TRACE,
        "expected_answer": "500万元。",
        "label": "pass",
        "critique": "",
        "failure_category": None,
        "annotated_by": "tester",
        "annotated_at": "2026-05-18T10:00:00+00:00",
    }
    dataset.write_text(json.dumps(sample, ensure_ascii=False) + "\n", encoding="utf-8")
    assert needs_annotation("trace_001", dataset) is False


def test_save_annotation_appends(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    sample: AnnotatedSample = {
        **SAMPLE_TRACE,
        "expected_answer": "500万元。",
        "label": "fail",
        "critique": "回答正确但缺少来源引用。",
        "failure_category": None,
        "annotated_by": "tester",
        "annotated_at": "2026-05-18T10:00:00+00:00",
    }
    save_annotation(sample, dataset)
    save_annotation(sample, dataset)
    lines = dataset.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2


def test_save_annotation_fail_requires_critique(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    sample: AnnotatedSample = {
        **SAMPLE_TRACE,
        "expected_answer": "",
        "label": "fail",
        "critique": "",
        "failure_category": None,
        "annotated_by": "tester",
        "annotated_at": "2026-05-18T10:00:00+00:00",
    }
    save_annotation(sample, dataset)
    saved = json.loads(dataset.read_text(encoding="utf-8"))
    assert saved["label"] == "fail"
    assert saved["critique"] == ""


def test_load_jsonl_skips_malformed_lines(tmp_path):
    f = tmp_path / "test.jsonl"
    f.write_text(
        json.dumps({"id": "a"}, ensure_ascii=False)
        + "\n"
        + "{not valid json\n"
        + json.dumps({"id": "b"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    items = load_jsonl(f)
    assert len(items) == 2
    assert items[0]["id"] == "a"
    assert items[1]["id"] == "b"
