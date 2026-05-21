import json
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from eval.judges import DimensionResult, EvalResult
from eval.web import create_app


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

SAMPLE_ANNOTATION = {
    "id": "q_001",
    "question_id": "q_001",
    "question": "最低要求是多少？",
    "complete_question": "最低要求是多少？",
    "conversation_history": [],
    "actual_answer": "最低为500万元。",
    "doc_context": "",
    "faq_context": "",
    "references": [],
    "ref_num": 0,
    "expected_answer": "500万元。",
    "label": "pass",
    "critique": "正确回答",
    "failure_category": None,
    "annotated_by": "tester",
    "annotated_at": "2026-05-20T10:00:00+00:00",
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
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _read_dataset(data_dir: Path) -> list[dict]:
    return _read_jsonl(data_dir / "dataset.jsonl")


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
        annotator="tester",
    )
    app.testing = True
    return app.test_client()


def _make_client(data_dir, collector_fn=None):
    app = create_app(
        traces_path=data_dir / "traces.jsonl",
        questions_path=data_dir / "questions.jsonl",
        dataset_path=data_dir / "dataset.jsonl",
        judge_results_path=data_dir / "judge_results.jsonl",
        annotator="tester",
        collector_fn=collector_fn,
    )
    app.testing = True
    return app.test_client()


def _wait_collect_status(client, expected: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    last_body = None
    while time.monotonic() < deadline:
        last_body = client.get("/api/collect/status").get_json()
        if last_body["status"] == expected:
            return last_body
        time.sleep(0.01)
    raise AssertionError(f"collect status did not become {expected!r}; last status was {last_body!r}")


@pytest.fixture
def mock_collector_success():
    ready = threading.Event()

    def _collect(questions_path, output_path):
        _write_jsonl(output_path, SAMPLE_TRACES)
        ready.set()
        return {"succeeded": 2, "failed": []}

    _collect.ready = ready
    return _collect


@pytest.fixture
def mock_collector_warning():
    ready = threading.Event()

    def _collect(questions_path, output_path):
        _write_jsonl(output_path, SAMPLE_TRACES[:1])
        ready.set()
        return {"succeeded": 1, "failed": ["q_002"]}

    _collect.ready = ready
    return _collect


@pytest.fixture
def mock_collector_error():
    ready = threading.Event()

    def _collect(questions_path, output_path):
        ready.set()
        return {"succeeded": 0, "failed": ["q_001", "q_002"]}

    _collect.ready = ready
    return _collect


def test_get_traces_returns_human_annotation_key(client):
    body = client.get("/api/traces").get_json()
    assert "human_annotation" in body[0]
    assert "latest_annotation" not in body[0]


def test_get_traces_human_annotation_null_when_missing(client):
    body = client.get("/api/traces").get_json()
    for entry in body:
        assert entry["human_annotation"] is None


def test_get_traces_judge_result_null_when_missing(client):
    body = client.get("/api/traces").get_json()
    for entry in body:
        assert entry["judge_result"] is None


def test_get_root_serves_annotate_page(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.mimetype == "text/html"
    assert "hallucination" in resp.get_data(as_text=True)


def test_get_judge_serves_judge_page(client):
    resp = client.get("/judge")
    assert resp.status_code == 200
    assert resp.mimetype == "text/html"
    html = resp.get_data(as_text=True)
    assert "Eval · Stage 4/5 Judge" in html
    assert "运行 Judge" in html
    assert 'href="/"' in html


def test_get_judge_page_includes_rubric_editor_shell(client):
    html = client.get("/judge").get_data(as_text=True)
    assert "编辑 Rubric" in html
    assert 'id="rubricOverlay"' in html
    assert "/api/rubric/" in html
    assert "openAddFromTraces" in html


def test_get_root_html_uses_human_annotation_not_latest(client):
    html = client.get("/").get_data(as_text=True)
    assert "human_annotation" in html
    assert "latest_annotation" not in html
    assert 'href="/judge"' in html


def test_get_traces_returns_all_with_status(client):
    resp = client.get("/api/traces")
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body) == 2
    ids = [entry["trace"]["id"] for entry in body]
    assert ids == ["q_001", "q_002"]
    for entry in body:
        assert entry["human_annotation"] is None


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
        json.dumps(sample, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    body = client.get("/api/traces").get_json()
    by_id = {entry["trace"]["id"]: entry for entry in body}
    assert by_id["q_001"]["human_annotation"]["label"] == "pass"
    assert by_id["q_002"]["human_annotation"] is None


def test_post_pass_appends_dataset(client, data_dir):
    resp = client.post(
        "/api/annotate",
        json={
            "trace_id": "q_001",
            "label": "pass",
            "critique": "",
            "failure_category": None,
        },
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["annotation"]["label"] == "pass"
    assert body["annotation"]["annotated_by"] == "tester"
    assert body["annotation"]["expected_answer"] == "500万元。"

    rows = _read_dataset(data_dir)
    assert len(rows) == 1
    assert rows[0]["id"] == "q_001"
    assert rows[0]["label"] == "pass"


def test_post_fail_without_critique_returns_400(client, data_dir):
    resp = client.post(
        "/api/annotate",
        json={
            "trace_id": "q_001",
            "label": "fail",
            "critique": "   ",
            "failure_category": "hallucination",
        },
    )
    assert resp.status_code == 400
    assert "critique" in resp.get_json()["error"]
    assert _read_dataset(data_dir) == []


def test_post_fail_without_category_returns_400(client, data_dir):
    resp = client.post(
        "/api/annotate",
        json={
            "trace_id": "q_001",
            "label": "fail",
            "critique": "答非所问",
            "failure_category": None,
        },
    )
    assert resp.status_code == 400
    assert "failure_category" in resp.get_json()["error"]
    assert _read_dataset(data_dir) == []


def test_post_skip_allows_empty_critique(client, data_dir):
    resp = client.post(
        "/api/annotate",
        json={
            "trace_id": "q_001",
            "label": "skip",
            "critique": "",
            "failure_category": None,
        },
    )
    assert resp.status_code == 200
    assert _read_dataset(data_dir)[0]["label"] == "skip"


def test_post_unknown_trace_id_returns_404(client, data_dir):
    resp = client.post(
        "/api/annotate",
        json={
            "trace_id": "q_999",
            "label": "pass",
            "critique": "",
            "failure_category": None,
        },
    )
    assert resp.status_code == 404
    assert _read_dataset(data_dir) == []


def test_post_invalid_label_returns_400(client, data_dir):
    resp = client.post(
        "/api/annotate",
        json={
            "trace_id": "q_001",
            "label": "maybe",
            "critique": "",
            "failure_category": None,
        },
    )
    assert resp.status_code == 400
    assert _read_dataset(data_dir) == []


def test_post_overwrites_via_append(client, data_dir):
    client.post(
        "/api/annotate",
        json={
            "trace_id": "q_001",
            "label": "fail",
            "critique": "缺少引用",
            "failure_category": "citation_error",
        },
    )
    client.post(
        "/api/annotate",
        json={
            "trace_id": "q_001",
            "label": "pass",
            "critique": "",
            "failure_category": None,
        },
    )
    rows = _read_dataset(data_dir)
    assert len(rows) == 2
    assert rows[-1]["label"] == "pass"

    body = client.get("/api/traces").get_json()
    by_id = {entry["trace"]["id"]: entry for entry in body}
    assert by_id["q_001"]["human_annotation"]["label"] == "pass"


def test_annotated_at_is_server_timestamp(client, data_dir):
    resp = client.post(
        "/api/annotate",
        json={
            "trace_id": "q_001",
            "label": "pass",
            "critique": "",
            "failure_category": None,
            "annotated_at": "1999-01-01T00:00:00+00:00",
        },
    )
    assert resp.status_code == 200
    ts = resp.get_json()["annotation"]["annotated_at"]
    assert ts.startswith("20")
    assert "1999" not in ts


def test_post_pass_preserves_optional_note(client, data_dir):
    resp = client.post(
        "/api/annotate",
        json={
            "trace_id": "q_001",
            "label": "pass",
            "critique": "正确引用了第3条政策条文\n完整覆盖了申请要求",
            "failure_category": None,
        },
    )
    assert resp.status_code == 200
    rows = _read_dataset(data_dir)
    assert rows[0]["critique"] == "正确引用了第3条政策条文\n完整覆盖了申请要求"


def test_post_skip_preserves_optional_note(client, data_dir):
    resp = client.post(
        "/api/annotate",
        json={
            "trace_id": "q_001",
            "label": "skip",
            "critique": "问题本身不明确，无法判断好坏",
            "failure_category": None,
        },
    )
    assert resp.status_code == 200
    rows = _read_dataset(data_dir)
    assert rows[0]["critique"] == "问题本身不明确，无法判断好坏"


def test_get_traces_judge_result_is_none_when_not_run(client):
    body = client.get("/api/traces").get_json()
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


def _fake_judge(trace, *, model="mimo-v2.5-pro", rubric_path=None):
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
    with patch("eval.web.run_all_judges", side_effect=_fake_judge):
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


def test_judge_page_uses_env_default_model(data_dir, monkeypatch):
    monkeypatch.setenv("JUDGE_MODEL", "env-judge-model")
    c = _make_client(data_dir)

    html = c.get("/judge").get_data(as_text=True)

    assert '<option value="env-judge-model" selected>env-judge-model</option>' in html


def test_post_judge_uses_env_default_model_when_missing(data_dir, monkeypatch):
    monkeypatch.setenv("JUDGE_MODEL", "env-judge-model")
    c = _make_client(data_dir)
    captured = {}

    def fake_with_model(trace, *, model="mimo-v2.5-pro", rubric_path=None):
        captured["model"] = model
        return _fake_judge(trace, model=model)

    with patch("eval.web.run_all_judges", side_effect=fake_with_model):
        c.post("/api/judge", json={"trace_id": "q_001"})

    assert captured["model"] == "env-judge-model"


def test_post_judge_unknown_trace_returns_404(client, data_dir):
    with patch("eval.web.run_all_judges", side_effect=_fake_judge):
        resp = client.post("/api/judge", json={"trace_id": "q_999"})
    assert resp.status_code == 404
    assert _read_jsonl(data_dir / "judge_results.jsonl") == []


def test_post_judge_respects_model_param(client):
    captured = {}

    def fake_with_model(trace, *, model="mimo-v2.5-pro", rubric_path=None):
        captured["model"] = model
        return _fake_judge(trace, model=model)

    with patch("eval.web.run_all_judges", side_effect=fake_with_model):
        client.post("/api/judge", json={"trace_id": "q_001", "model": "mimo-v2-omni"})
    assert captured["model"] == "mimo-v2-omni"


# ── Collect routes ────────────────────────────────────────


def test_get_collect_status_initial_idle(data_dir):
    c = _make_client(data_dir)
    body = c.get("/api/collect/status").get_json()
    assert body["status"] == "idle"
    assert body["succeeded"] == 0
    assert body["failed"] == []


def test_get_collect_info_returns_counts_and_paths(data_dir):
    c = _make_client(data_dir)
    body = c.get("/api/collect/info").get_json()
    assert body["question_count"] == 2
    assert body["trace_count"] == 2
    assert body["questions_path"].endswith("questions.jsonl")
    assert body["traces_path"].endswith("traces.jsonl")


def test_get_collect_page_returns_html(data_dir):
    c = _make_client(data_dir)
    resp = c.get("/collect")
    assert resp.status_code == 200
    assert resp.mimetype == "text/html"
    html = resp.get_data(as_text=True)
    assert "Collect" in html
    assert 'href="/"' in html
    assert 'href="/judge"' in html
    assert "s.failed" in html


def test_post_collect_returns_started(data_dir, mock_collector_success):
    c = _make_client(data_dir, mock_collector_success)
    resp = c.post("/api/collect")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "started"
    assert mock_collector_success.ready.wait(timeout=5)


def test_post_collect_returns_409_when_running(data_dir):
    released = threading.Event()

    def _slow_collect(questions_path, output_path):
        released.wait(timeout=10)
        return {"succeeded": 0, "failed": []}

    c = _make_client(data_dir, _slow_collect)
    c.post("/api/collect")
    resp2 = c.post("/api/collect")
    assert resp2.status_code == 409
    released.set()


def test_post_collect_success_state(data_dir, mock_collector_success):
    c = _make_client(data_dir, mock_collector_success)
    c.post("/api/collect")

    body = _wait_collect_status(c, "success")
    assert body["succeeded"] == 2
    assert body["failed"] == []
    assert _read_jsonl(data_dir / "traces.jsonl") == SAMPLE_TRACES
    assert not (data_dir / "traces.tmp").exists()


def test_post_collect_warning_state(data_dir, mock_collector_warning):
    c = _make_client(data_dir, mock_collector_warning)
    c.post("/api/collect")

    body = _wait_collect_status(c, "warning")
    assert body["succeeded"] == 1
    assert body["failed"] == ["q_002"]
    assert _read_jsonl(data_dir / "traces.jsonl") == SAMPLE_TRACES[:1]


def test_post_collect_error_preserves_old_traces(data_dir, mock_collector_error):
    original = json.dumps({"id": "q_old"}, ensure_ascii=False) + "\n"
    (data_dir / "traces.jsonl").write_text(original, encoding="utf-8")

    c = _make_client(data_dir, mock_collector_error)
    c.post("/api/collect")

    body = _wait_collect_status(c, "error")
    assert body["succeeded"] == 0
    assert body["failed"] == ["q_001", "q_002"]
    assert (data_dir / "traces.jsonl").read_text(encoding="utf-8") == original
    assert not (data_dir / "traces.tmp").exists()


def test_collect_info_updates_after_collection(data_dir, mock_collector_warning):
    c = _make_client(data_dir, mock_collector_warning)
    c.post("/api/collect")
    _wait_collect_status(c, "warning")

    body = c.get("/api/collect/info").get_json()
    assert body["trace_count"] == 1


def test_get_traces_still_works_after_collection(data_dir, mock_collector_success):
    c = _make_client(data_dir, mock_collector_success)
    c.post("/api/collect")
    _wait_collect_status(c, "success")

    resp = c.get("/api/traces")
    assert resp.status_code == 200
    body = resp.get_json()
    assert [entry["trace"]["id"] for entry in body] == ["q_001", "q_002"]


# ── Questions CRUD routes ─────────────────────────────────


def test_get_questions_returns_list(data_dir):
    c = _make_client(data_dir)
    body = c.get("/api/questions").get_json()
    assert isinstance(body, list)
    assert len(body) == 2
    assert body[0]["id"] == "q_001"
    assert "question" in body[0]
    assert "expected_answer" in body[0]


def test_get_questions_empty_when_no_file(tmp_path):
    app = create_app(
        traces_path=tmp_path / "traces.jsonl",
        questions_path=tmp_path / "questions.jsonl",
        dataset_path=tmp_path / "dataset.jsonl",
        judge_results_path=tmp_path / "judge_results.jsonl",
        annotator="tester",
    )
    app.testing = True
    body = app.test_client().get("/api/questions").get_json()
    assert body == []


def test_post_question_creates_with_auto_id(data_dir):
    c = _make_client(data_dir)
    resp = c.post("/api/questions", json={"question": "新问题？", "expected_answer": "新回答"})
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["id"] == "q_003"
    assert body["question"] == "新问题？"
    assert body["expected_answer"] == "新回答"
    assert body["knowledge_type"] == "文档"
    assert body["conversation_history"] == []
    assert len(c.get("/api/questions").get_json()) == 3


def test_post_question_validates_required_fields(data_dir):
    c = _make_client(data_dir)
    assert c.post("/api/questions", json={"question": "", "expected_answer": "x"}).status_code == 400
    assert c.post("/api/questions", json={"question": "x", "expected_answer": "  "}).status_code == 400
    assert c.post("/api/questions", json={"question": "x"}).status_code == 400


def test_put_question_updates_fields(data_dir):
    c = _make_client(data_dir)
    resp = c.put("/api/questions/q_001", json={"question": "修改后问题", "expected_answer": "修改后回答"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["question"] == "修改后问题"
    assert body["expected_answer"] == "修改后回答"
    assert body["knowledge_type"] == SAMPLE_QUESTIONS[0]["knowledge_type"]
    assert body["is_prohibited"] == SAMPLE_QUESTIONS[0]["is_prohibited"]


def test_put_question_unknown_id_returns_404(data_dir):
    c = _make_client(data_dir)
    resp = c.put("/api/questions/q_999", json={"question": "x", "expected_answer": "y"})
    assert resp.status_code == 404


def test_delete_question_removes_entry(data_dir):
    c = _make_client(data_dir)
    resp = c.delete("/api/questions/q_001")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    remaining = c.get("/api/questions").get_json()
    assert len(remaining) == 1
    assert remaining[0]["id"] == "q_002"


def test_delete_question_unknown_id_returns_404(data_dir):
    c = _make_client(data_dir)
    assert c.delete("/api/questions/q_999").status_code == 404


def test_save_questions_no_tmp_after_success(tmp_path):
    from eval.web import _save_questions
    questions = [{"id": "q_001", "question": "q", "expected_answer": "a"}]
    path = tmp_path / "questions.jsonl"
    _save_questions(questions, path)
    assert path.exists()
    assert not (tmp_path / "questions.tmp").exists()
    assert _read_jsonl(path) == questions
