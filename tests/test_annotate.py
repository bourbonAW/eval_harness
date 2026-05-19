import json

from eval.annotate import (
    annotate_interactive,
    load_annotated_ids,
    load_jsonl,
    load_latest_annotations,
    needs_annotation,
    save_annotation,
)
from eval.schema import AnnotatedSample, Trace

SAMPLE_TRACE: Trace = {
    "id": "trace_001",
    "question_id": "q_001",
    "question": "最低要求是多少？",
    "complete_question": "广州市申报专精特新最低要求是多少？",
    "conversation_history": [],
    "actual_answer": "根据政策，最低为500万元。",
    "doc_context": "1. [政策文件](https://policy.example.com)\n原文：不低于500万元",
    "faq_context": "",
    "references": [{"doc_id": 1, "name": "[1] 政策文件", "url": "https://policy.example.com"}],
    "ref_num": 1,
}

_SAMPLE_ANNOTATED: AnnotatedSample = {
    **SAMPLE_TRACE,
    "expected_answer": "500万元。",
    "label": "pass",
    "critique": "",
    "failure_category": None,
    "annotated_by": "tester",
    "annotated_at": "2026-05-18T10:00:00+00:00",
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
    dataset.write_text(json.dumps(_SAMPLE_ANNOTATED, ensure_ascii=False) + "\n", encoding="utf-8")
    assert needs_annotation("trace_001", dataset) is False


def test_save_annotation_appends(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    save_annotation(_SAMPLE_ANNOTATED, dataset)
    save_annotation(_SAMPLE_ANNOTATED, dataset)
    lines = dataset.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2


def test_save_annotation_does_not_enforce_critique(tmp_path):
    """save_annotation is a pure write — enforcement lives in the CLI loop."""
    dataset = tmp_path / "dataset.jsonl"
    sample: AnnotatedSample = {
        **_SAMPLE_ANNOTATED,
        "label": "fail",
        "critique": "",
        "failure_category": None,
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
    assert saved[0]["failure_category"] is None
    assert saved[0]["annotated_by"] == "tester"
    assert saved[0]["expected_answer"] == "500万元。"


def test_annotate_interactive_skip_path_writes_nothing(tmp_path, monkeypatch):
    traces_path, questions_path, dataset_path = _write_traces_and_questions(tmp_path)
    monkeypatch.setattr("eval.annotate.Prompt.ask", lambda *a, **kw: "skip")

    annotate_interactive(traces_path, questions_path, dataset_path, annotator_name="tester")

    assert not dataset_path.exists() or dataset_path.read_text(encoding="utf-8") == ""


def test_annotate_interactive_fail_requires_non_empty_critique(tmp_path, monkeypatch):
    """Fail with empty critique re-prompts; first valid critique+category is saved."""
    traces_path, questions_path, dataset_path = _write_traces_and_questions(tmp_path)

    # Sequence: label=fail, critique="" (rejected), label=fail, critique="real", category=incomplete
    responses = iter(["fail", "", "fail", "回答漏掉了关键约束", "incomplete"])
    monkeypatch.setattr("eval.annotate.Prompt.ask", lambda *a, **kw: next(responses))

    annotate_interactive(traces_path, questions_path, dataset_path, annotator_name="tester")

    saved = [json.loads(line) for line in dataset_path.read_text(encoding="utf-8").strip().split("\n")]
    assert len(saved) == 1
    assert saved[0]["label"] == "fail"
    assert saved[0]["critique"] == "回答漏掉了关键约束"
    assert saved[0]["failure_category"] == "incomplete"


def test_annotate_interactive_fail_saves_category(tmp_path, monkeypatch):
    """Fail label saves both critique and failure_category."""
    traces_path, questions_path, dataset_path = _write_traces_and_questions(tmp_path)

    responses = iter(["fail", "答案数字与context不符", "hallucination"])
    monkeypatch.setattr("eval.annotate.Prompt.ask", lambda *a, **kw: next(responses))

    annotate_interactive(traces_path, questions_path, dataset_path, annotator_name="tester")

    saved = json.loads(dataset_path.read_text(encoding="utf-8").strip())
    assert saved["failure_category"] == "hallucination"
    assert saved["critique"] == "答案数字与context不符"


def test_annotate_interactive_skips_already_annotated(tmp_path, monkeypatch):
    traces_path, questions_path, dataset_path = _write_traces_and_questions(tmp_path)
    dataset_path.write_text(json.dumps(_SAMPLE_ANNOTATED, ensure_ascii=False) + "\n", encoding="utf-8")

    def should_not_be_called(*a, **kw):
        raise AssertionError("Prompt.ask should not be called for already-annotated traces")

    monkeypatch.setattr("eval.annotate.Prompt.ask", should_not_be_called)
    annotate_interactive(traces_path, questions_path, dataset_path, annotator_name="tester")

    lines = dataset_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1  # unchanged


def test_load_latest_annotations_picks_last_version(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    base: AnnotatedSample = {
        **SAMPLE_TRACE,
        "complete_question": SAMPLE_TRACE["question"],
        "doc_context": "",
        "faq_context": "",
        "references": [],
        "ref_num": 0,
        "expected_answer": "500万元。",
        "label": "fail",
        "critique": "缺少引用",
        "failure_category": "citation_error",
        "annotated_by": "tester",
        "annotated_at": "2026-05-18T10:00:00+00:00",
    }
    revised: AnnotatedSample = {
        **base,
        "label": "pass",
        "critique": "",
        "failure_category": None,
        "annotated_at": "2026-05-18T11:00:00+00:00",
    }
    save_annotation(base, dataset)
    save_annotation(revised, dataset)

    latest = load_latest_annotations(dataset)
    assert set(latest.keys()) == {SAMPLE_TRACE["id"]}
    assert latest[SAMPLE_TRACE["id"]]["label"] == "pass"
    assert latest[SAMPLE_TRACE["id"]]["annotated_at"] == "2026-05-18T11:00:00+00:00"


def test_load_latest_annotations_empty_for_missing_file(tmp_path):
    assert load_latest_annotations(tmp_path / "no.jsonl") == {}
