"""Stage 4/5 judge web UI — run LLM judges and compare with human annotations."""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from eval.annotate import load_jsonl, load_latest_annotations
from eval.judges import run_all_judges


def load_latest_judge_results(path: Path) -> dict:
    """Last-wins deduplication by trace_id."""
    latest: dict = {}
    for row in load_jsonl(path):
        latest[row["trace_id"]] = row
    return latest


def save_judge_result(result: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")


def create_app(
    *,
    traces_path: Path,
    questions_path: Path,
    dataset_path: Path,
    judge_results_path: Path,
) -> Flask:
    app = Flask(__name__, template_folder="templates")
    app.config["TRACES_PATH"] = Path(traces_path)
    app.config["QUESTIONS_PATH"] = Path(questions_path)
    app.config["DATASET_PATH"] = Path(dataset_path)
    app.config["JUDGE_RESULTS_PATH"] = Path(judge_results_path)

    @app.get("/api/traces")
    def get_traces():
        traces = load_jsonl(app.config["TRACES_PATH"])
        questions_by_id = {q["id"]: q for q in load_jsonl(app.config["QUESTIONS_PATH"])}
        human_annotations = load_latest_annotations(app.config["DATASET_PATH"])
        judge_results = load_latest_judge_results(app.config["JUDGE_RESULTS_PATH"])
        result = []
        for t in traces:
            q = questions_by_id.get(t["question_id"], {})
            result.append({
                "trace": t,
                "expected_answer": q.get("expected_answer", ""),
                "human_annotation": human_annotations.get(t["id"]),
                "judge_result": judge_results.get(t["id"]),
            })
        return jsonify(result)

    @app.post("/api/judge")
    def post_judge():
        body = request.get_json(silent=True) or {}
        trace_id = body.get("trace_id")
        model = body.get("model", "mimo-v2.5-pro")

        traces = load_jsonl(app.config["TRACES_PATH"])
        trace = next((t for t in traces if t["id"] == trace_id), None)
        if trace is None:
            return jsonify({"error": f"trace_id {trace_id} 不存在"}), 404

        eval_result = run_all_judges(trace, model=model)

        row = {
            "trace_id": eval_result.trace_id,
            "label": eval_result.label,
            "dimensions": [asdict(d) for d in eval_result.dimensions],
            "judged_at": datetime.now(timezone.utc).isoformat(),
        }
        save_judge_result(row, app.config["JUDGE_RESULTS_PATH"])
        return jsonify(row)

    @app.get("/")
    def index():
        return render_template("judge.html")

    return app


def _main() -> None:
    parser = argparse.ArgumentParser(description="Judge 结果查看工具")
    parser.add_argument("--port", type=int, default=5001)
    args = parser.parse_args()

    app = create_app(
        traces_path=Path("data/traces.jsonl"),
        questions_path=Path("data/questions.jsonl"),
        dataset_path=Path("data/dataset.jsonl"),
        judge_results_path=Path("data/judge_results.jsonl"),
    )
    print(f"Judge 工具运行在 http://127.0.0.1:{args.port}")
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    _main()
