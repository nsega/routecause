"""Shared types for the evidence collectors."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..schema import Evidence, EvidenceSource, FaultCategory


class Signal(BaseModel):
    """A deterministic candidate classification derived from one source."""

    category: FaultCategory
    strength: float  # 0..1 deterministic confidence
    rationale: str
    evidence: list[Evidence] = Field(default_factory=list)


class CollectorResult(BaseModel):
    collector: str  # "E1" | "E2" | "E3"
    source: EvidenceSource
    summary: str = ""
    observations: list[Evidence] = Field(default_factory=list)
    signals: list[Signal] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    def top_signal(self) -> Signal | None:
        return max(self.signals, key=lambda s: s.strength, default=None)
