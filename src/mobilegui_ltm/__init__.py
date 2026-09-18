"""Pluggable long-term memory SDK for mobile GUI agents."""

from mobilegui_ltm.api import MemoryStore, create_store
from mobilegui_ltm.encode.anchors import CausalAnchorEncoder
from mobilegui_ltm.encode.shortcuts import ShortcutEncoder
from mobilegui_ltm.encode.traj_summarizer import TrajectorySummarizer
from mobilegui_ltm.inject import PromptInjector, format_shortcut_for_worker
from mobilegui_ltm.profiles import MATRIX_MODES, MemoryProfile, resolve_profile
from mobilegui_ltm.retrieve.embed import EmbeddingRetriever, HybridRetriever
from mobilegui_ltm.retrieve.embedder import FakeEmbedder, HashingEmbedder
from mobilegui_ltm.retrieve.keyword import BM25Retriever, KeywordRetriever
from mobilegui_ltm.schema import (
    AttemptOutcome,
    AtomicAction,
    EvalTask,
    Evidence,
    InjectionTarget,
    MemoryKind,
    MemoryRecord,
    OutcomeStatus,
    RecordStatus,
    SessionBundle,
    ShortcutSpec,
    Trajectory,
    TrajectoryStep,
)
from mobilegui_ltm.store.json import JsonFileBackend

__version__ = "0.3.0"

__all__ = [
    "AttemptOutcome",
    "AtomicAction",
    "BM25Retriever",
    "CausalAnchorEncoder",
    "EmbeddingRetriever",
    "EvalTask",
    "Evidence",
    "FakeEmbedder",
    "HashingEmbedder",
    "HybridRetriever",
    "InjectionTarget",
    "JsonFileBackend",
    "KeywordRetriever",
    "MATRIX_MODES",
    "MemoryKind",
    "MemoryProfile",
    "MemoryRecord",
    "MemoryStore",
    "OutcomeStatus",
    "PromptInjector",
    "RecordStatus",
    "SessionBundle",
    "ShortcutEncoder",
    "ShortcutSpec",
    "Trajectory",
    "TrajectoryStep",
    "TrajectorySummarizer",
    "create_store",
    "format_shortcut_for_worker",
    "resolve_profile",
    "__version__",
]
