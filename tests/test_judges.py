import json
from pathlib import Path

import pytest

from eval.judges import (
    DimensionResult,
    EvalResult,
    _parse_judge_response,
    judge_answer_relevance,
    judge_faithfulness,
)


def _load_trace(tid: str) -> dict:
    return {
        t["id"]: t
        for t in [
            json.loads(line)
            for line in Path("data/traces.jsonl").read_text().splitlines()
            if line.strip()
        ]
    }[tid]


# ── Round 1: Schema ───────────────────────────────────────────────────────────


def test_eval_result_label_fail_if_any_dimension_fails():
    dims = [
        DimensionResult(dimension="answer_relevance", label="pass", critique="ok", evidence=[], model="m"),
        DimensionResult(dimension="faithfulness", label="fail", critique="bad", evidence=[], model="m"),
    ]
    result = EvalResult(trace_id="q_001", dimensions=dims)
    assert result.label == "fail"


def test_eval_result_label_pass_if_all_dimensions_pass():
    dims = [
        DimensionResult(dimension="answer_relevance", label="pass", critique="ok", evidence=[], model="m"),
        DimensionResult(dimension="faithfulness", label="pass", critique="ok", evidence=[], model="m"),
    ]
    result = EvalResult(trace_id="q_001", dimensions=dims)
    assert result.label == "pass"


# ── Round 2: JSON parsing ─────────────────────────────────────────────────────


def test_parse_judge_json_valid():
    raw = '{"verdict":"Fail","critique":"没有正面回答用户提问","evidence":["只有政策总结，缺少直接答案"]}'
    result = _parse_judge_response(raw, dimension="answer_relevance", model="claude-haiku")
    assert result.label == "fail"
    assert result.critique == "没有正面回答用户提问"
    assert result.evidence == ["只有政策总结，缺少直接答案"]
    assert result.dimension == "answer_relevance"
    assert result.model == "claude-haiku"


def test_parse_judge_json_case_insensitive_verdict():
    raw = '{"verdict":"pass","critique":"直接回答了问题","evidence":[]}'
    result = _parse_judge_response(raw, dimension="answer_relevance", model="claude-haiku")
    assert result.label == "pass"


def test_parse_judge_json_missing_evidence_defaults_to_empty():
    raw = '{"verdict":"Pass","critique":"回答完整"}'
    result = _parse_judge_response(raw, dimension="answer_relevance", model="claude-haiku")
    assert result.evidence == []


def test_parse_judge_json_returns_dimension_result():
    raw = '{"verdict":"Pass","critique":"ok","evidence":[]}'
    result = _parse_judge_response(raw, dimension="answer_relevance", model="m")
    assert isinstance(result, DimensionResult)


# ── Round 3: Integration (real API) ──────────────────────────────────────────


@pytest.mark.integration
def test_judge_answer_relevance_fails_q001():
    """q_001: bot gave boilerplate policy summary, never answered 'which enterprises'."""
    result = judge_answer_relevance(_load_trace("q_001"))
    assert result.label == "fail", (
        f"Expected fail, got {result.label!r}.\n"
        f"Critique: {result.dimensions[0].critique}"
    )


@pytest.mark.integration
def test_judge_answer_relevance_passes_q003():
    """q_003: bot summarized policy AND gave direct answer (30% / 500万元)."""
    result = judge_answer_relevance(_load_trace("q_003"))
    assert result.label == "pass", (
        f"Expected pass, got {result.label!r}.\n"
        f"Critique: {result.dimensions[0].critique}"
    )


@pytest.mark.integration
def test_judge_result_structure():
    result = judge_answer_relevance(_load_trace("q_001"))
    assert isinstance(result, EvalResult)
    assert result.trace_id == "q_001"
    assert len(result.dimensions) == 1
    dim = result.dimensions[0]
    assert dim.dimension == "answer_relevance"
    assert len(dim.critique) > 0
    assert isinstance(dim.evidence, list)
    assert dim.model != ""


# ── Round 4: faithfulness (A|C) integration ──────────────────────────────────


@pytest.mark.integration
def test_judge_faithfulness_fails_q009():
    """q_009: empty context + vague '根据相关政策' attribution → fail."""
    result = judge_faithfulness(_load_trace("q_009"))
    assert result.label == "fail", (
        f"Expected fail, got {result.label!r}.\n"
        f"Critique: {result.dimensions[0].critique}"
    )


@pytest.mark.integration
def test_judge_faithfulness_passes_q005():
    """q_005: answer's '500万元' claim directly matches faq_context → pass."""
    result = judge_faithfulness(_load_trace("q_005"))
    assert result.label == "pass", (
        f"Expected pass, got {result.label!r}.\n"
        f"Critique: {result.dimensions[0].critique}"
    )


@pytest.mark.integration
def test_judge_faithfulness_result_structure():
    result = judge_faithfulness(_load_trace("q_009"))
    assert isinstance(result, EvalResult)
    assert result.trace_id == "q_009"
    dim = result.dimensions[0]
    assert dim.dimension == "faithfulness"
    assert dim.model != ""
    assert len(dim.critique) > 0
    assert isinstance(dim.evidence, list)
