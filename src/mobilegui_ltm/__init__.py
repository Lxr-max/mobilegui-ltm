"""Pluggable long-term memory SDK for mobile GUI agents."""

from mobilegui_ltm.api import MemoryStore, create_store
from mobilegui_ltm.encode.traj_summarizer import ShortcutEncoder, TrajectorySummarizer
from mobilegui_ltm.inject import PromptInjector
from mobilegui_ltm.retrieve.embed import EmbeddingRetriever
from mobilegui_ltm.retrieve.keyword import BM25Retriever, KeywordRetriever
from mobilegui_ltm.schema import (
    AttemptOutcome,
    EvalTask,
    InjectionTarget,
    MemoryKind,
    MemoryRecord,
    OutcomeStatus,
    SessionBundle,
    Trajectory,
    TrajectoryStep,
)
from mobilegui_ltm.store.json import JsonFileBackend

__version__ = "0.1.0"

__all__ = [
    "AttemptOutcome",
    "BM25Retriever",
    "EmbeddingRetriever",
    "EvalTask",
    "InjectionTarget",
    "JsonFileBackend",
    "KeywordRetriever",
    "MemoryKind",
    "MemoryRecord",
    "MemoryStore",
    "OutcomeStatus",
    "PromptInjector",
    "SessionBundle",
    "ShortcutEncoder",
    "Trajectory",
    "TrajectoryStep",
    "TrajectorySummarizer",
    "create_store",
    "__version__",
]
