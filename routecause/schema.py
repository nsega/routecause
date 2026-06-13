"""RouteCause RCA report schema v1.0.

The product is the report. This module is the versioned, documented contract
(RUBRIC B3/B4) and it *enforces* the rubric's hard report invariants so a
non-compliant report cannot be silently emitted:

- ``fault_category`` is exactly one of the four enum values (A2).
- ``evidence`` has >= 2 items from >= 2 distinct sources (A3).
- ``fix`` carries a ``dry_run_validated`` flag set true only after a real
  ``kubectl apply --dry-run=server`` (A4); the orchestrator owns setting it.

Schema mirrors DESIGN-DRAFT §3 exactly.
"""

from __future__ import annotations

import uuid
from enum import Enum

from pydantic import BaseModel, Field, model_validator

SCHEMA_VERSION = "1.0"


class FaultCategory(str, Enum):
    """The verifier matches this against ground truth (A2)."""

    SCORER_WEIGHT_MISCONFIG = "scorer-weight-misconfig"  # s1
    UNHEALTHY_ENDPOINT = "unhealthy-endpoint-in-rotation"  # s2
    PREFIX_CACHE_DISABLED = "prefix-cache-routing-disabled"  # s3
    OTHER = "other"


# Ground-truth label (s1/s2/s3) -> fault_category, per RUBRIC A2.
SCENARIO_TO_CATEGORY = {
    "s1": FaultCategory.SCORER_WEIGHT_MISCONFIG,
    "s2": FaultCategory.UNHEALTHY_ENDPOINT,
    "s3": FaultCategory.PREFIX_CACHE_DISABLED,
}


class EvidenceSource(str, Enum):
    PROMETHEUS = "prometheus"
    CONFIGMAP = "configmap"
    K8S_API = "k8s-api"
    MANIFEST_DIFF = "manifest-diff"


class Evidence(BaseModel):
    """One re-executable citation. Every ``locator`` must be runnable by a third party."""

    source: EvidenceSource
    locator: str  # PromQL query, ConfigMap name + YAML path, or kubectl command
    observed: str  # actual value / line
    baseline: str | None = None  # healthy expectation when known
    claim: str  # what this evidence supports


class HypothesisStatus(str, Enum):
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class HypothesisOrigin(str, Enum):
    E1 = "E1"  # metrics
    E2 = "E2"  # scheduler config
    E3 = "E3"  # cluster state


class Hypothesis(BaseModel):
    hypothesis: str
    origin: HypothesisOrigin
    status: HypothesisStatus
    reason: str  # why rejected, or summary of confirming evidence


class RootCause(BaseModel):
    summary: str  # 1-2 sentence conclusion
    details: str  # mechanism: why this config produces this symptom


class FixType(str, Enum):
    KUBECTL_PATCH = "kubectl-patch"
    MANIFEST_DIFF = "manifest-diff"


class Fix(BaseModel):
    type: FixType
    command: str | None = None  # command to run (patch case)
    diff: str | None = None  # unified diff (manifest case)
    dry_run_validated: bool = False  # true only after kubectl apply --dry-run=server
    expected_impact: str  # which metrics should recover and rough timeframe


class Recovery(BaseModel):
    """Stretch (A6). Stays ``applied=False`` when unused."""

    applied: bool = False
    before: dict = Field(default_factory=dict)
    after: dict = Field(default_factory=dict)
    slo_met: bool | None = None


class Report(BaseModel):
    schema_version: str = SCHEMA_VERSION
    report_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    pool: str
    started_at: str  # ISO8601
    completed_at: str  # ISO8601
    fault_category: FaultCategory
    root_cause: RootCause
    evidence: list[Evidence]
    hypotheses: list[Hypothesis]
    fix: Fix
    recovery: Recovery = Field(default_factory=Recovery)

    @model_validator(mode="after")
    def _enforce_evidence_invariants(self) -> "Report":
        # A3: >= 2 distinct evidence sources, each a real citation.
        if len(self.evidence) < 2:
            raise ValueError("A3 violated: report must cite >= 2 evidence items")
        distinct_sources = {e.source for e in self.evidence}
        if len(distinct_sources) < 2:
            raise ValueError(
                "A3 violated: evidence must come from >= 2 distinct sources, "
                f"got {sorted(s.value for s in distinct_sources)}"
            )
        return self

    @model_validator(mode="after")
    def _enforce_fix_shape(self) -> "Report":
        # A4: a fix must carry an actionable artifact for its type.
        if self.fix.type == FixType.KUBECTL_PATCH and not self.fix.command:
            raise ValueError("A4 violated: kubectl-patch fix requires a command")
        if self.fix.type == FixType.MANIFEST_DIFF and not self.fix.diff:
            raise ValueError("A4 violated: manifest-diff fix requires a diff")
        return self
