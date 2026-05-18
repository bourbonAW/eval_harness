import json
import os
import sys
from pathlib import Path
from typing import List, Optional

import requests

from eval.schema import ConversationTurn, Question, Trace


class WorkflowCollector:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        answer_field: str,
        chunks_field: str,
        citations_field: str,
        connect_timeout: int = 10,
        read_timeout: int = 120,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.answer_field = answer_field
        self.chunks_field = chunks_field
        self.citations_field = citations_field
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout

    @classmethod
    def from_env(cls) -> "WorkflowCollector":
        return cls(
            base_url=os.environ["WORKFLOW_API_BASE_URL"],
            api_key=os.environ["WORKFLOW_API_KEY"],
            answer_field=os.getenv("WORKFLOW_ANSWER_FIELD", "answer"),
            chunks_field=os.getenv("WORKFLOW_CHUNKS_FIELD", "retrieved_chunks"),
            citations_field=os.getenv("WORKFLOW_CITATIONS_FIELD", "citations"),
            connect_timeout=int(os.getenv("WORKFLOW_CONNECT_TIMEOUT", "10")),
            read_timeout=int(os.getenv("WORKFLOW_READ_TIMEOUT", "120")),
        )

    def collect(self, question: Question) -> Trace:
        """Collect a trace for one question.

        trace.id is set to question.id (deterministic across runs). This means
        re-running the collector for the same questions will produce traces with
        identical IDs — and annotate.needs_annotation will then skip them as
        already-annotated. To re-annotate after a workflow change, either remove
        the corresponding rows from dataset.jsonl first or write traces to a
        separate file path.
        """
        raw = self._call_api(
            query=question["question"],
            conversation_history=question["conversation_history"],
        )
        return {
            "id": question["id"],
            "question_id": question["id"],
            "question": question["question"],
            "conversation_history": question["conversation_history"],
            "actual_answer": str(raw.get(self.answer_field) or ""),
            "retrieved_chunks": list(raw.get(self.chunks_field) or []),
            "citations": list(raw.get(self.citations_field) or []),
        }

    def _call_api(self, query: str, conversation_history: List[ConversationTurn]) -> dict:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "query": query,
            "conversation_history": conversation_history,
            "response_mode": "blocking",
        }
        resp = requests.post(
            f"{self.base_url}/workflows/run",
            headers=headers,
            json=payload,
            timeout=(self.connect_timeout, self.read_timeout),
        )
        resp.raise_for_status()
        return resp.json()


def collect_all(
    questions_path: Path,
    output_path: Path,
    collector: Optional[WorkflowCollector] = None,
) -> dict:
    """Collect traces for every question in questions_path.

    Overwrites output_path (write-once-per-run). Per-question failures are caught,
    logged, and recorded — they do not abort the batch. Returns a summary dict
    with success/failure counts.
    """
    if collector is None:
        from dotenv import load_dotenv

        load_dotenv()
        collector = WorkflowCollector.from_env()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    succeeded = 0
    failed: List[str] = []

    with open(questions_path, encoding="utf-8") as f_in, open(
        output_path, "w", encoding="utf-8"
    ) as f_out:
        for line in f_in:
            q: Question = json.loads(line)
            try:
                trace = collector.collect(q)
            except Exception as e:
                failed.append(q["id"])
                print(f"  [FAIL  {q['id']}] {type(e).__name__}: {e}", file=sys.stderr)
                continue
            f_out.write(json.dumps(trace, ensure_ascii=False) + "\n")
            succeeded += 1
            print(f"  [OK    {trace['id']}] {q['question'][:40]}...")

    print(f"\nDone: {succeeded} succeeded, {len(failed)} failed")
    if failed:
        print(f"Failed question IDs: {', '.join(failed)}", file=sys.stderr)
    return {"succeeded": succeeded, "failed": failed}


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: uv run python -m eval.collectors.workflow_collector <questions.jsonl> <traces.jsonl>")
        sys.exit(1)
    collect_all(Path(sys.argv[1]), Path(sys.argv[2]))
