import json
import sys
from pathlib import Path
from typing import List, Optional

import openpyxl

from eval.schema import ConversationTurn, Question

_COL = {
    "question": 0,
    "expected_answer": 1,
    "source_policy_url": 2,
    "source_doc_url": 3,
    "source_doc_name": 4,
    "is_multi_intent": 5,
    "knowledge_type": 6,
    "is_prohibited": 7,
    "notes": 8,
}

_MULTI_INTENT_MAP = {"单意图": False, "多意图": True, "": False}
_PROHIBITED_MAP = {"正常": False, "违禁": True, "": False}
_KNOWLEDGE_TYPES = {"文档", "结合上下文"}


def _cell(row: tuple, key: str) -> str:
    val = row[_COL[key]]
    return str(val).strip() if val is not None else ""


def _map_enum(value: str, mapping: dict, field: str, excel_row: int) -> bool:
    if value not in mapping:
        raise ValueError(
            f"Excel row {excel_row}: unknown '{field}' value {value!r}. "
            f"Expected one of {sorted(k for k in mapping if k)}."
        )
    return mapping[value]


def import_questions(excel_path: Path, sheet_name: Optional[str] = None) -> List[Question]:
    wb = openpyxl.load_workbook(excel_path)
    ws = wb[sheet_name] if sheet_name else wb.worksheets[0]
    rows = list(ws.iter_rows(min_row=2, values_only=True))

    questions: List[Question] = []
    thread: List[Question] = []
    valid_idx = 0

    for row_idx, row in enumerate(rows, start=2):  # min_row=2 → first data row is Excel row 2
        if not row[_COL["question"]]:
            continue

        valid_idx += 1
        knowledge_type = _cell(row, "knowledge_type")
        if knowledge_type not in _KNOWLEDGE_TYPES:
            raise ValueError(
                f"Excel row {row_idx}: unknown 'knowledge_type' value {knowledge_type!r}. "
                f"Expected one of {sorted(_KNOWLEDGE_TYPES)}."
            )

        if knowledge_type != "结合上下文":
            thread = []

        history: List[ConversationTurn] = []
        for prev in thread:
            history.append({"role": "user", "content": prev["question"]})
            history.append({"role": "assistant", "content": prev["expected_answer"]})

        q: Question = {
            "id": f"q_{valid_idx:03d}",
            "question": _cell(row, "question"),
            "expected_answer": _cell(row, "expected_answer"),
            "source_policy_url": _cell(row, "source_policy_url"),
            "source_doc_url": _cell(row, "source_doc_url"),
            "source_doc_name": _cell(row, "source_doc_name"),
            "is_multi_intent": _map_enum(
                _cell(row, "is_multi_intent"), _MULTI_INTENT_MAP, "is_multi_intent", row_idx
            ),
            "knowledge_type": knowledge_type,
            "is_prohibited": _map_enum(
                _cell(row, "is_prohibited"), _PROHIBITED_MAP, "is_prohibited", row_idx
            ),
            "conversation_history": history,
            "notes": _cell(row, "notes"),
        }

        thread.append(q)
        questions.append(q)

    return questions


def save_questions(questions: List[Question], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for q in questions:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: uv run python -m eval.importers.excel_importer <input.xlsx> <output.jsonl>")
        sys.exit(1)
    qs = import_questions(Path(sys.argv[1]))
    save_questions(qs, Path(sys.argv[2]))
    print(f"Imported {len(qs)} questions -> {sys.argv[2]}")
