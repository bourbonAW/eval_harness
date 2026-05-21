import json
from pathlib import Path
from unittest.mock import patch

import pytest

from eval.judges import DimensionResult, EvalResult
from eval.web import create_app


SAMPLE_TRACE = {
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
}


def _write_jsonl(path: Path, items: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in items) + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def rubric_client(tmp_path: Path):
    app = create_app(
        traces_path=tmp_path / "traces.jsonl",
        questions_path=tmp_path / "questions.jsonl",
        dataset_path=tmp_path / "dataset.jsonl",
        judge_results_path=tmp_path / "judge_results.jsonl",
        rubric_path=tmp_path / "rubric.json",
    )
    app.testing = True
    return app.test_client(), tmp_path


def _make_rubric_client(tmp_path: Path):
    app = create_app(
        traces_path=tmp_path / "traces.jsonl",
        questions_path=tmp_path / "questions.jsonl",
        dataset_path=tmp_path / "dataset.jsonl",
        judge_results_path=tmp_path / "judge_results.jsonl",
        rubric_path=tmp_path / "rubric.json",
    )
    app.testing = True
    return app.test_client()


def test_get_rubric_returns_defaults(rubric_client):
    client, _ = rubric_client

    resp = client.get("/api/rubric/answer_relevance")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["dimension"] == "answer_relevance"
    assert "system_prompt" in body
    assert "few_shot" in body


def test_get_rubric_invalid_dimension_returns_400(rubric_client):
    client, _ = rubric_client

    resp = client.get("/api/rubric/invalid_dim")

    assert resp.status_code == 400


def test_put_rubric_saves_and_reloads(rubric_client):
    client, _ = rubric_client
    payload = {
        "system_prompt": "new prompt",
        "few_shot": [
            {
                "question": "q",
                "answer": "a",
                "verdict": "Pass",
                "critique": "c",
                "evidence": [],
            }
        ],
    }

    put_resp = client.put("/api/rubric/answer_relevance", json=payload)
    get_resp = client.get("/api/rubric/answer_relevance")

    assert put_resp.status_code == 200
    assert get_resp.get_json()["system_prompt"] == "new prompt"


def test_put_rubric_preserves_other_dimension(rubric_client):
    client, tmp_path = rubric_client
    rubric_path = tmp_path / "rubric.json"
    rubric_path.write_text(
        json.dumps(
            {
                "faithfulness": {
                    "system_prompt": "faith prompt",
                    "few_shot": [{"doc_context": "", "faq_context": "", "answer": "a", "verdict": "Fail"}],
                }
            }
        ),
        encoding="utf-8",
    )

    resp = client.put("/api/rubric/answer_relevance", json={"system_prompt": "p", "few_shot": []})

    assert resp.status_code == 200
    saved = json.loads(rubric_path.read_text(encoding="utf-8"))
    assert saved["faithfulness"]["system_prompt"] == "faith prompt"
    assert saved["answer_relevance"]["system_prompt"] == "p"


def test_put_rubric_atomic_write_no_tmp_leftover(rubric_client):
    client, tmp_path = rubric_client

    resp = client.put("/api/rubric/answer_relevance", json={"system_prompt": "p", "few_shot": []})

    assert resp.status_code == 200
    assert not (tmp_path / "rubric.tmp").exists()


def test_put_rubric_empty_prompt_returns_400(rubric_client):
    client, _ = rubric_client

    resp = client.put("/api/rubric/answer_relevance", json={"system_prompt": "", "few_shot": []})

    assert resp.status_code == 400
    assert "system_prompt" in resp.get_json()["error"]


def test_put_rubric_invalid_verdict_returns_400(rubric_client):
    client, _ = rubric_client

    resp = client.put(
        "/api/rubric/answer_relevance",
        json={
            "system_prompt": "p",
            "few_shot": [{"question": "q", "answer": "a", "verdict": "fail", "critique": "c", "evidence": []}],
        },
    )

    assert resp.status_code == 400


def test_put_rubric_answer_relevance_validates_question_field(rubric_client):
    client, _ = rubric_client

    resp = client.put(
        "/api/rubric/answer_relevance",
        json={
            "system_prompt": "p",
            "few_shot": [{"question": "", "answer": "a", "verdict": "Pass", "critique": "c", "evidence": []}],
        },
    )

    assert resp.status_code == 400


def test_put_rubric_faithfulness_allows_examples_without_question(rubric_client):
    client, _ = rubric_client

    resp = client.put(
        "/api/rubric/faithfulness",
        json={
            "system_prompt": "p",
            "few_shot": [
                {
                    "doc_context": "",
                    "faq_context": "faq",
                    "answer": "a",
                    "verdict": "Fail",
                    "critique": "c",
                    "evidence": [],
                }
            ],
        },
    )

    assert resp.status_code == 200


def test_post_judge_passes_rubric_path(rubric_client):
    client, tmp_path = rubric_client
    _write_jsonl(tmp_path / "traces.jsonl", [SAMPLE_TRACE])
    captured = {}

    def fake_judge(trace, *, model="mimo-v2.5-pro", rubric_path=None):
        captured["rubric_path"] = rubric_path
        return EvalResult(
            trace_id=trace["id"],
            dimensions=[
                DimensionResult(
                    dimension="answer_relevance",
                    label="pass",
                    critique="ok",
                    evidence=[],
                    model=model,
                )
            ],
        )

    with patch("eval.web.run_all_judges", side_effect=fake_judge):
        resp = client.post("/api/judge", json={"trace_id": "q_001"})

    assert resp.status_code == 200
    assert captured["rubric_path"] == tmp_path / "rubric.json"


def test_post_rubric_suggest_no_disagreements_returns_empty(rubric_client):
    client, tmp_path = rubric_client
    _write_jsonl(tmp_path / "traces.jsonl", [SAMPLE_TRACE])

    with patch("eval.judges._call_llm") as call_llm:
        resp = client.post("/api/rubric/answer_relevance/suggest")

    assert resp.status_code == 200
    assert resp.get_json() == {"fp_fn_count": 0, "stale_warning": False, "suggestions": []}
    call_llm.assert_not_called()


def test_post_rubric_suggest_invalid_dimension_returns_400(rubric_client):
    client, _ = rubric_client

    resp = client.post("/api/rubric/bad/suggest")

    assert resp.status_code == 400


def test_post_rubric_suggest_calls_llm_with_full_rubric_and_disagreements(rubric_client):
    client, tmp_path = rubric_client
    _write_jsonl(tmp_path / "traces.jsonl", [SAMPLE_TRACE])
    _write_jsonl(
        tmp_path / "dataset.jsonl",
        [{**SAMPLE_TRACE, "label": "fail", "critique": "人工认为答非所问"}],
    )
    _write_jsonl(
        tmp_path / "judge_results.jsonl",
        [
            {
                "trace_id": "q_001",
                "label": "pass",
                "dimensions": [
                    {
                        "dimension": "answer_relevance",
                        "label": "pass",
                        "critique": "judge pass",
                        "evidence": [],
                        "model": "m",
                    }
                ],
                "judged_at": "2099-01-01T00:00:00+00:00",
            }
        ],
    )
    (tmp_path / "rubric.json").write_text(
        json.dumps(
            {
                "answer_relevance": {
                    "system_prompt": "custom prompt",
                    "few_shot": [
                        {
                            "question": "example question",
                            "answer": "example answer",
                            "verdict": "Fail",
                            "critique": "example critique",
                            "evidence": [],
                        }
                    ],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with patch(
        "eval.judges._call_llm",
        return_value=json.dumps({"suggestions": [{"type": "system_prompt", "description": "d", "proposed_full": "p"}]}),
    ) as call_llm:
        resp = client.post("/api/rubric/answer_relevance/suggest")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["fp_fn_count"] == 1
    assert body["suggestions"][0]["proposed_full"] == "p"
    llm_messages = call_llm.call_args.args[1]
    user_content = llm_messages[0]["content"]
    assert "custom prompt" in user_content
    assert "example question" in user_content
    assert "人工认为答非所问" in user_content


def test_post_rubric_suggest_accepts_fenced_json_response(rubric_client):
    client, tmp_path = rubric_client
    _write_jsonl(tmp_path / "traces.jsonl", [SAMPLE_TRACE])
    _write_jsonl(tmp_path / "dataset.jsonl", [{**SAMPLE_TRACE, "label": "fail", "critique": "bad"}])
    _write_jsonl(
        tmp_path / "judge_results.jsonl",
        [
            {
                "trace_id": "q_001",
                "label": "pass",
                "dimensions": [{"dimension": "answer_relevance", "label": "pass", "critique": "", "evidence": []}],
                "judged_at": "2099-01-01T00:00:00+00:00",
            }
        ],
    )

    raw = '```json\n{"suggestions": [{"type": "system_prompt", "description": "d", "proposed_full": "p"}]}\n```'
    with patch("eval.judges._call_llm", return_value=raw):
        resp = client.post("/api/rubric/answer_relevance/suggest")

    assert resp.status_code == 200
    assert resp.get_json()["suggestions"][0]["proposed_full"] == "p"


def test_post_rubric_suggest_uses_env_specific_model(tmp_path, monkeypatch):
    monkeypatch.setenv("JUDGE_MODEL", "shared-model")
    monkeypatch.setenv("RUBRIC_SUGGEST_MODEL", "suggest-model")
    client = _make_rubric_client(tmp_path)
    _write_jsonl(tmp_path / "traces.jsonl", [SAMPLE_TRACE])
    _write_jsonl(tmp_path / "dataset.jsonl", [{**SAMPLE_TRACE, "label": "fail", "critique": "bad"}])
    _write_jsonl(
        tmp_path / "judge_results.jsonl",
        [
            {
                "trace_id": "q_001",
                "label": "pass",
                "dimensions": [{"dimension": "answer_relevance", "label": "pass", "critique": "", "evidence": []}],
                "judged_at": "2099-01-01T00:00:00+00:00",
            }
        ],
    )

    with patch("eval.judges._call_llm", return_value=json.dumps({"suggestions": []})) as call_llm:
        resp = client.post("/api/rubric/answer_relevance/suggest")

    assert resp.status_code == 200
    assert call_llm.call_args.kwargs["model"] == "suggest-model"


def test_post_rubric_suggest_falls_back_to_judge_model_env(tmp_path, monkeypatch):
    monkeypatch.setenv("JUDGE_MODEL", "shared-model")
    monkeypatch.delenv("RUBRIC_SUGGEST_MODEL", raising=False)
    client = _make_rubric_client(tmp_path)
    _write_jsonl(tmp_path / "traces.jsonl", [SAMPLE_TRACE])
    _write_jsonl(tmp_path / "dataset.jsonl", [{**SAMPLE_TRACE, "label": "fail", "critique": "bad"}])
    _write_jsonl(
        tmp_path / "judge_results.jsonl",
        [
            {
                "trace_id": "q_001",
                "label": "pass",
                "dimensions": [{"dimension": "answer_relevance", "label": "pass", "critique": "", "evidence": []}],
                "judged_at": "2099-01-01T00:00:00+00:00",
            }
        ],
    )

    with patch("eval.judges._call_llm", return_value=json.dumps({"suggestions": []})) as call_llm:
        resp = client.post("/api/rubric/answer_relevance/suggest")

    assert resp.status_code == 200
    assert call_llm.call_args.kwargs["model"] == "shared-model"


def test_post_rubric_suggest_stale_warning_when_rubric_newer_than_results(rubric_client):
    client, tmp_path = rubric_client
    _write_jsonl(tmp_path / "traces.jsonl", [SAMPLE_TRACE])
    _write_jsonl(tmp_path / "dataset.jsonl", [{**SAMPLE_TRACE, "label": "fail", "critique": "bad"}])
    _write_jsonl(
        tmp_path / "judge_results.jsonl",
        [
            {
                "trace_id": "q_001",
                "label": "pass",
                "dimensions": [{"dimension": "answer_relevance", "label": "pass", "critique": "", "evidence": []}],
                "judged_at": "2000-01-01T00:00:00+00:00",
            }
        ],
    )
    (tmp_path / "rubric.json").write_text(
        json.dumps({"answer_relevance": {"system_prompt": "p", "few_shot": []}}),
        encoding="utf-8",
    )

    with patch("eval.judges._call_llm", return_value=json.dumps({"suggestions": []})):
        resp = client.post("/api/rubric/answer_relevance/suggest")

    assert resp.status_code == 200
    assert resp.get_json()["stale_warning"] is True


def test_post_rubric_suggest_malformed_llm_json_returns_500(rubric_client):
    client, tmp_path = rubric_client
    _write_jsonl(tmp_path / "traces.jsonl", [SAMPLE_TRACE])
    _write_jsonl(tmp_path / "dataset.jsonl", [{**SAMPLE_TRACE, "label": "fail", "critique": "bad"}])
    _write_jsonl(
        tmp_path / "judge_results.jsonl",
        [
            {
                "trace_id": "q_001",
                "label": "pass",
                "dimensions": [{"dimension": "answer_relevance", "label": "pass", "critique": "", "evidence": []}],
                "judged_at": "2099-01-01T00:00:00+00:00",
            }
        ],
    )

    with patch("eval.judges._call_llm", return_value="not json"):
        resp = client.post("/api/rubric/answer_relevance/suggest")

    assert resp.status_code == 500
    assert "LLM" in resp.get_json()["error"]
