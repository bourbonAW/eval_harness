import argparse
from pathlib import Path

from flask import Flask, jsonify

from eval.annotate import load_jsonl, load_latest_annotations


def create_app(
    *,
    traces_path: Path,
    questions_path: Path,
    dataset_path: Path,
    annotator: str,
) -> Flask:
    app = Flask(__name__, template_folder="templates")
    app.config["TRACES_PATH"] = Path(traces_path)
    app.config["QUESTIONS_PATH"] = Path(questions_path)
    app.config["DATASET_PATH"] = Path(dataset_path)
    app.config["ANNOTATOR"] = annotator

    @app.get("/api/traces")
    def get_traces():
        traces = load_jsonl(app.config["TRACES_PATH"])
        questions_by_id = {q["id"]: q for q in load_jsonl(app.config["QUESTIONS_PATH"])}
        latest = load_latest_annotations(app.config["DATASET_PATH"])
        result = []
        for t in traces:
            q = questions_by_id.get(t["question_id"], {})
            result.append(
                {
                    "trace": t,
                    "expected_answer": q.get("expected_answer", ""),
                    "latest_annotation": latest.get(t["id"]),
                }
            )
        return jsonify(result)

    return app
