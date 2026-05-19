from typing import List, Literal, Optional, TypedDict

FailureCategory = Literal[
    "hallucination",
    "context_miss",
    "refusal_fail",
    "citation_error",
    "incomplete",
    "off_topic",
    "other",
]


class ConversationTurn(TypedDict):
    role: Literal["user", "assistant"]
    content: str


class RetrievedDoc(TypedDict):
    doc_id: str | int  # int for doc chunks, str for FAQ chunks
    name: str          # e.g. "[1] 2026年专精特新..."
    url: str           # source URL for citation correctness checks


class Question(TypedDict):
    id: str
    question: str
    expected_answer: str  # annotation reference only; not used in judge computation
    source_policy_url: str
    source_doc_url: str
    source_doc_name: str
    is_multi_intent: bool
    knowledge_type: Literal["文档", "结合上下文"]
    is_prohibited: bool
    conversation_history: List[ConversationTurn]
    notes: str


class Trace(TypedDict):
    id: str
    question_id: str
    question: str
    complete_question: str          # expanded query actually sent to the retriever
    conversation_history: List[ConversationTurn]
    actual_answer: str
    doc_context: str                # doc_str: retrieved document chunks concatenated
    faq_context: str                # faq_str: retrieved FAQ chunks concatenated
    references: List[RetrievedDoc]  # parsed from ref_str; used for citation checks
    ref_num: int


class AnnotatedSample(TypedDict):
    id: str
    question_id: str
    question: str
    complete_question: str
    conversation_history: List[ConversationTurn]
    actual_answer: str
    doc_context: str
    faq_context: str
    references: List[RetrievedDoc]
    ref_num: int
    expected_answer: str
    label: Literal["pass", "fail", "skip"]
    critique: str                        # required when label == "fail"
    failure_category: Optional[FailureCategory]  # required when label == "fail"
    annotated_by: str
    annotated_at: str                    # ISO 8601
