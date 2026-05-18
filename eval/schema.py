from typing import List, Literal, Optional, TypedDict


class ConversationTurn(TypedDict):
    role: Literal["user", "assistant"]
    content: str


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
    conversation_history: List[ConversationTurn]
    actual_answer: str
    retrieved_chunks: List[str]
    citations: List[str]


class AnnotatedSample(TypedDict):
    id: str
    question_id: str
    question: str
    conversation_history: List[ConversationTurn]
    actual_answer: str
    retrieved_chunks: List[str]
    citations: List[str]
    expected_answer: str
    label: Literal["pass", "fail", "skip"]
    critique: str  # required when label == "fail"
    failure_category: Optional[str]
    annotated_by: str
    annotated_at: str  # ISO 8601
