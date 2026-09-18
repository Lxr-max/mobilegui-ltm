"""Integrity / poisoning research hooks (hash + optional HMAC).

These are experiment rails, not a cryptography product. Enable them when
measuring retrieve-time tamper detection; leave them off for the default SDK.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Sequence

from mobilegui_ltm.schema import MemoryRecord

HMAC_ENV = "MOBILEGUI_LTM_HMAC_KEY"

_SKIP_META = {
    "embedder",
    "integrity",
    "content_hash",
    "signature",
    "signer",
    "quarantined",
    "quarantine_reason",
    "quarantined_at",
}


def canonical_payload(record: MemoryRecord) -> bytes:
    """Stable bytes for hashing. Embeddings and signatures are excluded."""
    meta = {
        key: value
        for key, value in (record.metadata or {}).items()
        if key not in _SKIP_META
    }
    payload = {
        "kind": record.kind.value if hasattr(record.kind, "value") else str(record.kind),
        "content": record.content,
        "task_id": record.task_id,
        "agent_id": record.agent_id,
        "logical_key": record.logical_key,
        "block": record.block,
        "screen": record.screen,
        "app_ids": list(record.app_ids),
        "tags": list(record.tags),
        "depends_on": list(record.depends_on),
        "metadata": meta,
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode(
        "utf-8"
    )


def content_hash(record: MemoryRecord) -> str:
    return hashlib.sha256(canonical_payload(record)).hexdigest()


def _coerce_key(key: str | bytes | None) -> bytes | None:
    if key is None:
        env = os.environ.get(HMAC_ENV)
        key = env if env else None
    if key is None or key == "":
        return None
    return key if isinstance(key, bytes) else str(key).encode("utf-8")


def hmac_sign(digest: str, key: str | bytes) -> str:
    raw = key if isinstance(key, bytes) else str(key).encode("utf-8")
    return hmac.new(raw, digest.encode("utf-8"), hashlib.sha256).hexdigest()


class IntegrityGuard:
    """Stamp records at write time; verify at retrieve time."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        hmac_key: str | bytes | None = None,
        verify_on_retrieve: bool = True,
        drop_unverified: bool = True,
    ) -> None:
        self.enabled = enabled
        self.hmac_key = _coerce_key(hmac_key)
        self.verify_on_retrieve = verify_on_retrieve
        self.drop_unverified = drop_unverified

    @property
    def signer(self) -> str:
        return "hmac-sha256" if self.hmac_key else "sha256"

    def stamp(self, record: MemoryRecord) -> MemoryRecord:
        if not self.enabled:
            return record
        digest = content_hash(record)
        signature = hmac_sign(digest, self.hmac_key) if self.hmac_key else None
        return record.model_copy(
            update={
                "content_hash": digest,
                "signature": signature,
                "signer": self.signer,
            }
        )

    def stamp_many(self, records: Sequence[MemoryRecord]) -> list[MemoryRecord]:
        return [self.stamp(record) for record in records]

    def verify(self, record: MemoryRecord) -> bool:
        if not self.enabled:
            return True
        if not record.content_hash:
            return False
        if content_hash(record) != record.content_hash:
            return False
        if self.hmac_key:
            expected = hmac_sign(record.content_hash, self.hmac_key)
            got = record.signature or ""
            return hmac.compare_digest(expected, got)
        return True

    def check(self, records: Sequence[MemoryRecord]) -> tuple[list[MemoryRecord], list[MemoryRecord]]:
        """Split (verified, tampered_or_unsigned)."""
        ok: list[MemoryRecord] = []
        bad: list[MemoryRecord] = []
        for record in records:
            if self.verify(record):
                ok.append(record)
            else:
                flagged = record.model_copy(
                    update={
                        "metadata": {**record.metadata, "integrity": "tampered"}
                    }
                )
                bad.append(flagged)
        return ok, bad


def tamper(record: MemoryRecord, *, content: str | None = None) -> MemoryRecord:
    """Corrupt content without refreshing the hash (adversarial / eval fixture)."""
    poisoned = content if content is not None else f"{record.content} [TAMPERED]"
    return record.model_copy(update={"content": poisoned})


def resolve_integrity(
    value: IntegrityGuard | bool | None,
    *,
    hmac_key: str | bytes | None = None,
    verify_on_retrieve: bool = True,
    drop_unverified: bool = True,
) -> IntegrityGuard | None:
    if value is None or value is False:
        if hmac_key or os.environ.get(HMAC_ENV):
            return IntegrityGuard(
                enabled=True,
                hmac_key=hmac_key,
                verify_on_retrieve=verify_on_retrieve,
                drop_unverified=drop_unverified,
            )
        return None
    if value is True:
        return IntegrityGuard(
            enabled=True,
            hmac_key=hmac_key,
            verify_on_retrieve=verify_on_retrieve,
            drop_unverified=drop_unverified,
        )
    return value
