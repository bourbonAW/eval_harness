import json
import os
import re
import sys
from pathlib import Path
from typing import List, Optional

import requests

from eval.schema import ConversationTurn, Question, RetrievedDoc, Trace


def _escape_json_strings(text: str) -> str:
    """Escape literal control characters inside JSON string values.

    Workflow may embed raw newlines/tabs in doc_str/faq_str values, which
    makes the JSON syntactically invalid. This walks the JSON character by
    character and escapes control chars only when inside a string literal.
    """
    out = []
    in_string = False
    escape_next = False
    _ctrl = {'\n': '\\n', '\r': '\\r', '\t': '\\t'}
    for ch in text:
        if escape_next:
            out.append(ch)
            escape_next = False
        elif ch == '\\' and in_string:
            out.append(ch)
            escape_next = True
        elif ch == '"':
            out.append(ch)
            in_string = not in_string
        elif in_string and ch in _ctrl:
            out.append(_ctrl[ch])
        else:
            out.append(ch)
    return ''.join(out)


class WorkflowCollector:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        session_id: str,
        channel_id: str,
        answer_field: str,
        complete_question_field: str,
        doc_str_field: str,
        faq_str_field: str,
        ref_str_field: str,
        ref_num_field: str,
        connect_timeout: int = 10,
        read_timeout: int = 120,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.session_id = session_id
        self.channel_id = channel_id
        self.answer_field = answer_field
        self.complete_question_field = complete_question_field
        self.doc_str_field = doc_str_field
        self.faq_str_field = faq_str_field
        self.ref_str_field = ref_str_field
        self.ref_num_field = ref_num_field
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout

    @classmethod
    def from_env(cls) -> "WorkflowCollector":
        return cls(
            base_url=os.environ["WORKFLOW_API_BASE_URL"],
            api_key=os.environ["WORKFLOW_API_KEY"],
            session_id=os.environ["WORKFLOW_SESSION_ID"],
            channel_id=os.getenv("WORKFLOW_CHANNEL_ID", "1"),
            answer_field=os.getenv("WORKFLOW_ANSWER_FIELD", "answer"),
            complete_question_field=os.getenv("WORKFLOW_COMPLETE_QUESTION_FIELD", "complete_question"),
            doc_str_field=os.getenv("WORKFLOW_DOC_STR_FIELD", "doc_str"),
            faq_str_field=os.getenv("WORKFLOW_FAQ_STR_FIELD", "faq_str"),
            ref_str_field=os.getenv("WORKFLOW_REF_STR_FIELD", "ref_str"),
            ref_num_field=os.getenv("WORKFLOW_REF_NUM_FIELD", "ref_num"),
            connect_timeout=int(os.getenv("WORKFLOW_CONNECT_TIMEOUT", "10")),
            read_timeout=int(os.getenv("WORKFLOW_READ_TIMEOUT", "120")),
        )

    def collect(self, question: Question) -> Trace:
        """Collect a trace for one question.

        trace.id is set to question.id (deterministic across runs). Re-running
        the collector for the same questions produces traces with identical IDs —
        annotate.needs_annotation will skip them as already-annotated. To
        re-collect after a workflow change, clear the relevant rows from
        dataset.jsonl first, or write traces to a new file path.
        """
        raw = self._call_api(
            query=question["question"],
            conversation_history=question["conversation_history"],
        )
        return {
            "id": question["id"],
            "question_id": question["id"],
            "question": question["question"],
            "complete_question": str(raw.get(self.complete_question_field) or question["question"]),
            "conversation_history": question["conversation_history"],
            "actual_answer": str(raw.get(self.answer_field) or ""),
            "doc_context": str(raw.get(self.doc_str_field) or ""),
            "faq_context": str(raw.get(self.faq_str_field) or ""),
            "references": self._parse_references(raw.get(self.ref_str_field)),
            "ref_num": int(raw.get(self.ref_num_field) or 0),
        }

    def _parse_references(self, ref_str) -> List[RetrievedDoc]:
        """Parse ref_str (JSON string or list) into a list of RetrievedDoc.

        Accepts either a JSON-encoded string (as returned by _parse_response_text)
        or a plain list (for test injection). Returns [] on any parse failure so
        a malformed field never aborts a collection run.
        """
        if not ref_str:
            return []
        try:
            items = json.loads(ref_str) if isinstance(ref_str, str) else ref_str
            return [
                {
                    "doc_id": item.get("id", ""),
                    "name": item.get("name", ""),
                    "url": item.get("url", ""),
                }
                for item in (items or [])
            ]
        except (json.JSONDecodeError, TypeError, AttributeError):
            return []

    def _call_api(self, query: str, conversation_history: List[ConversationTurn]) -> dict:
        headers = {
            "Authorization": self.api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "message": {
                "parts": [{"kind": "text", "text": query}]
            }
        }
        url = (
            f"{self.base_url}/consultant_platform/api/app/universal/chat"
            f"/1/{self.channel_id}/session/{self.session_id}/message_stream"
        )
        resp = requests.post(
            url,
            headers=headers,
            json=payload,
            stream=True,
            timeout=(self.connect_timeout, self.read_timeout),
        )
        resp.raise_for_status()
        full_text = self._accumulate_sse(resp)
        return self._parse_response_text(full_text)

    def _accumulate_sse(self, resp) -> str:
        """Read a JSON-RPC-over-SSE stream and concatenate all text parts.

        Each SSE event has the shape:
            data: {"jsonrpc":"2.0","result":{"parts":[{"kind":"text","text":"..."}],...}}
        Ping lines (`: ping - ...`) and blank lines are ignored.
        """
        parts: List[str] = []
        for raw_line in resp.iter_lines():
            if not raw_line:
                continue
            line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
            if line.startswith(": "):   # SSE comment / ping
                continue
            if not line.startswith("data: "):
                continue
            try:
                event = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            for part in event.get("result", {}).get("parts", []):
                if part.get("kind") == "text":
                    parts.append(part.get("text", ""))
        return "".join(parts)

    def _parse_response_text(self, full_text: str) -> dict:
        """Extract structured fields from the accumulated SSE text.

        The text may contain:
        - <THINK>{json}</THINK>: agent metadata. The retrieval-done event
          carries `links` (doc references).
        - <DOC_CONTEXT>...</DOC_CONTEXT>: raw retrieved document chunks.
        - <FAQ_CONTEXT>...</FAQ_CONTEXT>: raw retrieved FAQ chunks.
        - <COMPLETE_QUERY>...</COMPLETE_QUERY>: expanded query sent to retriever.
        - <ADDITION>{json}</ADDITION>: supplementary UI data. Ignored.
        - Everything else: the answer the user sees.
        """
        # --- extract raw-text context tags (no JSON, no escaping issues) ---
        def _extract(tag: str) -> str:
            m = re.search(rf"<{tag}>(.*?)</{tag}>", full_text, re.DOTALL | re.IGNORECASE)
            return m.group(1).strip() if m else ""

        doc_str = _extract("DOC_CONTEXT")
        faq_str = _extract("FAQ_CONTEXT")
        complete_question = _extract("COMPLETE_QUERY")

        # --- strip all special blocks to get the clean answer ---
        _STRIP = ["think", "addition", "doc_context", "faq_context", "complete_query"]
        answer = full_text
        for tag in _STRIP:
            answer = re.sub(rf"<{tag}>.*?</{tag}>", "", answer, flags=re.DOTALL | re.IGNORECASE)
        answer = answer.strip()

        # --- extract references from THINK blocks (still JSON, but small & safe) ---
        references: List[dict] = []
        for block in re.findall(r"<think>(.*?)</think>", full_text, re.DOTALL | re.IGNORECASE):
            try:
                data = json.loads(block.strip())
            except json.JSONDecodeError:
                try:
                    data = json.loads(_escape_json_strings(block.strip()))
                except json.JSONDecodeError:
                    continue
            if isinstance(data.get("links"), list) and data.get("icon") == "done":
                references = data["links"]
            # Fallback: honour old JSON-in-THINK format if new tags absent
            if not doc_str and "doc_str" in data:
                doc_str = str(data["doc_str"])
            if not faq_str and "faq_str" in data:
                faq_str = str(data["faq_str"])
            if not complete_question and "complete_question" in data:
                complete_question = str(data["complete_question"])

        return {
            self.answer_field: answer,
            self.complete_question_field: complete_question,
            self.doc_str_field: doc_str,
            self.faq_str_field: faq_str,
            self.ref_str_field: json.dumps(references, ensure_ascii=False) if references else "",
            self.ref_num_field: len(references),
        }


def collect_all(
    questions_path: Path,
    output_path: Path,
    collector: Optional[WorkflowCollector] = None,
) -> dict:
    """Collect traces for every question in questions_path.

    Overwrites output_path (write-once-per-run). Per-question failures are
    caught, logged, and recorded — they do not abort the batch. Returns a
    summary dict with success/failure counts.
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
