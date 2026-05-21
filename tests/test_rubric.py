import json
from pathlib import Path

from eval import judges


def test_load_rubric_file_missing_returns_hardcoded(tmp_path: Path):
    rubric = judges.load_rubric("answer_relevance", tmp_path / "missing.json")

    assert rubric["system_prompt"] == judges._SYSTEM_ANSWER_RELEVANCE
    assert len(rubric["few_shot"]) == len(judges._FEW_SHOT_ANSWER_RELEVANCE)
    assert rubric["few_shot"][0]["question"] == judges._FEW_SHOT_ANSWER_RELEVANCE[0][0]


def test_load_rubric_file_present_overrides_hardcoded(tmp_path: Path):
    path = tmp_path / "rubric.json"
    path.write_text(
        json.dumps(
            {
                "answer_relevance": {
                    "system_prompt": "custom prompt",
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
            }
        ),
        encoding="utf-8",
    )

    rubric = judges.load_rubric("answer_relevance", path)

    assert rubric["system_prompt"] == "custom prompt"
    assert rubric["few_shot"][0]["question"] == "q"


def test_load_rubric_corrupt_file_returns_hardcoded(tmp_path: Path):
    path = tmp_path / "rubric.json"
    path.write_text("NOT JSON", encoding="utf-8")

    rubric = judges.load_rubric("answer_relevance", path)

    assert rubric["system_prompt"] == judges._SYSTEM_ANSWER_RELEVANCE
