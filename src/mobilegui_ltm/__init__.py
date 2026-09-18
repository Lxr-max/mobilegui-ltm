"""Pluggable long-term memory SDK for mobile GUI agents."""

from mobilegui_ltm.api import MemoryStore, create_store
from mobilegui_ltm.blocks import DEFAULT_BLOCKS, MemoryBlock
from mobilegui_ltm.encode.anchors import CausalAnchorEncoder
from mobilegui_ltm.encode.shortcuts import ShortcutEncoder
from mobilegui_ltm.encode.traj_summarizer import TrajectorySummarizer
from mobilegui_ltm.inject import PromptInjector, format_shortcut_for_worker
from mobilegui_ltm.integrity import IntegrityGuard, content_hash, tamper
from mobilegui_ltm.localrag import AppFact, CatalogLocalRAG, NullLocalRAG
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
    Stability,
    Trajectory,
    TrajectoryStep,
)
from mobilegui_ltm.store.json import JsonFileBackend

__version__ = "0.4.0"

__all__ = [
    "AppFact",
    "AttemptOutcome",
    "AtomicAction",
    "BM25Retriever",
    "CatalogLocalRAG",
    "CausalAnchorEncoder",
    "DEFAULT_BLOCKS",
    "EmbeddingRetriever",
    "EvalTask",
    "Evidence",
    "FakeEmbedder",
    "HashingEmbedder",
    "HybridRetriever",
    "InjectionTarget",
    "IntegrityGuard",
    "JsonFileBackend",
    "KeywordRetriever",
    "MATRIX_MODES",
    "MemoryBlock",
    "MemoryKind",
    "MemoryProfile",
    "MemoryRecord",
    "MemoryStore",
    "NullLocalRAG",
    "OutcomeStatus",
    "PromptInjector",
    "RecordStatus",
    "SessionBundle",
    "ShortcutEncoder",
    "ShortcutSpec",
    "Stability",
    "Trajectory",
    "TrajectoryStep",
    "TrajectorySummarizer",
    "content_hash",
    "create_store",
    "format_shortcut_for_worker",
    "resolve_profile",
    "tamper",
    "__version__",
]
