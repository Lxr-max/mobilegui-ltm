"""Optional LocalRAG plugin: installed-app / package-catalog priors.

Default is OFF. Nothing in this module talks to a device or ADB; adapters
register catalog rows explicitly (or tests pass a fake catalog).
"""

from __future__ import annotations

from typing import Any, Protocol, Sequence

from pydantic import BaseModel, Field

from mobilegui_ltm.schema import MemoryKind, MemoryRecord


class AppFact(BaseModel):
    """One installed-app / package prior (not an episode memory)."""

    app_id: str
    name: str = ""
    summary: str = ""
    capabilities: list[str] = Field(default_factory=list)
    package: str | None = None
    extras: dict[str, Any] = Field(default_factory=dict)

    def as_content(self) -> str:
        title = self.name or self.app_id
        caps = ", ".join(self.capabilities)
        bits = [f"App prior: {title} ({self.app_id})"]
        if self.summary:
            bits.append(self.summary)
        if caps:
            bits.append(f"capabilities: {caps}")
        if self.package and self.package != self.app_id:
            bits.append(f"package: {self.package}")
        return ". ".join(bits)


class LocalRAG(Protocol):
    """Pluggable catalog lookup. Default implementations need no device."""

    enabled: bool

    def lookup(
        self,
        query: str,
        *,
        app_ids: Sequence[str] | None = None,
        k: int = 5,
    ) -> list[AppFact]: ...

    def register_app(
        self,
        app_id: str,
        *,
        name: str = "",
        summary: str = "",
        capabilities: Sequence[str] | None = None,
        package: str | None = None,
        extras: dict[str, Any] | None = None,
    ) -> AppFact: ...


class NullLocalRAG:
    """Stub: always empty. Used when LocalRAG is off."""

    enabled = False

    def lookup(
        self,
        query: str,
        *,
        app_ids: Sequence[str] | None = None,
        k: int = 5,
    ) -> list[AppFact]:
        return []

    def register_app(
        self,
        app_id: str,
        *,
        name: str = "",
        summary: str = "",
        capabilities: Sequence[str] | None = None,
        package: str | None = None,
        extras: dict[str, Any] | None = None,
    ) -> AppFact:
        return AppFact(
            app_id=app_id,
            name=name,
            summary=summary,
            capabilities=list(capabilities or []),
            package=package,
            extras=dict(extras or {}),
        )


class CatalogLocalRAG:
    """In-memory app catalog. Offline; no ADB / package-manager calls."""

    enabled = True

    def __init__(self, apps: Sequence[AppFact | dict[str, Any]] | None = None) -> None:
        self._apps: dict[str, AppFact] = {}
        for item in apps or []:
            fact = item if isinstance(item, AppFact) else AppFact.model_validate(item)
            self._apps[fact.app_id] = fact

    def register_app(
        self,
        app_id: str,
        *,
        name: str = "",
        summary: str = "",
        capabilities: Sequence[str] | None = None,
        package: str | None = None,
        extras: dict[str, Any] | None = None,
    ) -> AppFact:
        previous = self._apps.get(app_id)
        fact = AppFact(
            app_id=app_id,
            name=name or (previous.name if previous else ""),
            summary=summary or (previous.summary if previous else ""),
            capabilities=list(capabilities or (previous.capabilities if previous else [])),
            package=package or (previous.package if previous else None),
            extras={**(previous.extras if previous else {}), **(extras or {})},
        )
        self._apps[app_id] = fact
        return fact

    def lookup(
        self,
        query: str,
        *,
        app_ids: Sequence[str] | None = None,
        k: int = 5,
    ) -> list[AppFact]:
        if k <= 0:
            return []
        wanted = set(app_ids) if app_ids else None
        needle = (query or "").lower()
        tokens = [tok for tok in needle.replace(",", " ").split() if tok]
        scored: list[tuple[int, AppFact]] = []
        for fact in self._apps.values():
            if wanted is not None and fact.app_id not in wanted:
                continue
            blob = " ".join(
                [
                    fact.app_id,
                    fact.name,
                    fact.summary,
                    " ".join(fact.capabilities),
                    fact.package or "",
                ]
            ).lower()
            score = 1 if not tokens else sum(1 for tok in tokens if tok in blob)
            if not tokens or score:
                scored.append((score, fact))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [fact for _, fact in scored[:k]]


def facts_to_records(
    facts: Sequence[AppFact],
    *,
    agent_id: str,
    task_id: str = "",
) -> list[MemoryRecord]:
    """Ephemeral APP_PRIOR records for retrieve blending (not persisted)."""
    records: list[MemoryRecord] = []
    for fact in facts:
        records.append(
            MemoryRecord(
                kind=MemoryKind.APP_PRIOR,
                content=fact.as_content(),
                task_id=task_id,
                attempt_k=0,
                agent_id=agent_id,
                app_ids=[fact.app_id],
                tags=["local_rag", "app_prior"],
                block="app_priors",
                metadata={"app_fact": fact.model_dump(mode="json"), "ephemeral": True},
                logical_key=f"app:{fact.app_id}",
            )
        )
    return records


def resolve_local_rag(
    value: LocalRAG | bool | Sequence[AppFact | dict[str, Any]] | None,
) -> LocalRAG:
    if value is None or value is False:
        return NullLocalRAG()
    if value is True:
        return CatalogLocalRAG()
    if isinstance(value, (list, tuple)):
        return CatalogLocalRAG(value)
    return value
