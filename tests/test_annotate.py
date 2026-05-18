import json

import pytest

from eval.annotate import (
    annotate_interactive,
    load_annotated_ids,
    load_jsonl,
    needs_annotation,
    save_annotation,
)
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


def test_save_annotation_does_not_enforce_critique(tmp_path):
    """save_annotation is a pure write — enforcement of 'fail requires critique'
    lives in the CLI loop, not here. See test_annotate_interactive_* below."""
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


def test_load_annotated_ids(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(
        json.dumps({"id": "trace_001"}) + "\n" + json.dumps({"id": "trace_002"}) + "\n",
        encoding="utf-8",
    )
    ids = load_annotated_ids(dataset)
    assert ids == {"trace_001", "trace_002"}


def _write_traces_and_questions(tmp_path):
    traces_path = tmp_path / "traces.jsonl"
    questions_path = tmp_path / "questions.jsonl"
    dataset_path = tmp_path / "dataset.jsonl"
    traces_path.write_text(json.dumps(SAMPLE_TRACE, ensure_ascii=False) + "\n", encoding="utf-8")
    questions_path.write_text(
        json.dumps(
            {
                "id": "q_001",
                "question": "最低要求是多少？",
                "expected_answer": "500万元。",
                "source_policy_url": "u",
                "source_doc_url": "u",
                "source_doc_name": "n",
                "is_multi_intent": False,
                "knowledge_type": "文档",
                "is_prohibited": False,
                "conversation_history": [],
                "notes": "",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return traces_path, questions_path, dataset_path


def test_annotate_interactive_pass_path(tmp_path, monkeypatch):
    traces_path, questions_path, dataset_path = _write_traces_and_questions(tmp_path)
    monkeypatch.setattr("eval.annotate.Prompt.ask", lambda *a, **kw: "pass")

    annotate_interactive(traces_path, questions_path, dataset_path, annotator_name="tester")

    saved = [json.loads(line) for line in dataset_path.read_text(encoding="utf-8").strip().split("\n")]
    assert len(saved) == 1
    assert saved[0]["label"] == "pass"
    assert saved[0]["critique"] == ""
    assert saved[0]["annotated_by"] == "tester"
    assert saved[0]["expected_answer"] == "500万元。"


def test_annotate_interactive_skip_path_writes_nothing(tmp_path, monkeypatch):
    traces_path, questions_path, dataset_path = _write_traces_and_questions(tmp_path)
    monkeypatch.setattr("eval.annotate.Prompt.ask", lambda *a, **kw: "skip")

    annotate_interactive(traces_path, questions_path, dataset_path, annotator_name="tester")

    assert not dataset_path.exists() or dataset_path.read_text(encoding="utf-8") == ""


def test_annotate_interactive_fail_requires_non_empty_critique(tmp_path, monkeypatch):
    """Fail label with empty critique must re-prompt — first valid critique
    is what gets saved."""
    traces_path, questions_path, dataset_path = _write_traces_and_questions(tmp_path)

    # Sequence of Prompt.ask responses: label=fail, critique="", label=fail, critique="real reason"
    responses = iter(["fail", "", "fail", "回答漏掉了关键约束"])
    monkeypatch.setattr("eval.annotate.Prompt.ask", lambda *a, **kw: next(responses))

    annotate_interactive(traces_path, questions_path, dataset_path, annotator_name="tester")

    saved = [json.loads(line) for line in dataset_path.read_text(encoding="utf-8").strip().split("\n")]
    assert len(saved) == 1
    assert saved[0]["label"] == "fail"
    assert saved[0]["critique"] == "回答漏掉了关键约束"


def test_annotate_interactive_skips_already_annotated(tmp_path, monkeypatch):
    traces_path, questions_path, dataset_path = _write_traces_and_questions(tmp_path)
    pre_existing: AnnotatedSample = {
        **SAMPLE_TRACE,
        "expected_answer": "500万元。",
        "label": "pass",
        "critique": "",
        "failure_category": None,
        "annotated_by": "prev",
        "annotated_at": "2026-05-18T09:00:00+00:00",
    }
    dataset_path.write_text(json.dumps(pre_existing, ensure_ascii=False) + "\n", encoding="utf-8")

    def should_not_be_called(*a, **kw):
        raise AssertionError("Prompt.ask should not be called for already-annotated traces")

    monkeypatch.setattr("eval.annotate.Prompt.ask", should_not_be_called)
    annotate_interactive(traces_path, questions_path, dataset_path, annotator_name="tester")

    lines = dataset_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1  # unchanged
