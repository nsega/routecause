"""Schema-contract tests (RUBRIC B3/B4): the report schema is the product's contract."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from routecause.schema import (
    SCENARIO_TO_CATEGORY,
    SCHEMA_VERSION,
    Evidence,
    EvidenceSource,
    FaultCategory,
    Fix,
    FixType,
    Hypothesis,
    HypothesisOrigin,
    HypothesisStatus,
    Report,
    RootCause,
)


def _two_source_evidence() -> list[Evidence]:
    return [
        Evidence(
            source=EvidenceSource.PROMETHEUS,
            locator="sum by (backend) (vllm:num_requests_waiting)",
            observed="b0=88, b1=0, b2=0",
            baseline="~0 on every backend",
            claim="queue piled onto a single backend",
        ),
        Evidence(
            source=EvidenceSource.CONFIGMAP,
            locator="epp-config:config.yaml schedulingProfiles[0].plugins[queue-scorer].weight",
            observed="-2",
            baseline="2 (positive)",
            claim="negative queue-scorer weight inverts routing onto one backend",
        ),
    ]


def _valid_report(**overrides) -> Report:
    base = dict(
        pool="default",
        started_at="2026-06-13T10:40:00Z",
        completed_at="2026-06-13T10:43:00Z",
        fault_category=FaultCategory.SCORER_WEIGHT_MISCONFIG,
        root_cause=RootCause(summary="negative queue-scorer weight", details="mechanism..."),
        evidence=_two_source_evidence(),
        hypotheses=[
            Hypothesis(
                hypothesis="queue-scorer weight is negative",
                origin=HypothesisOrigin.E2,
                status=HypothesisStatus.CONFIRMED,
                reason="config line shows weight: -2",
            )
        ],
        fix=Fix(
            type=FixType.KUBECTL_PATCH,
            command="kubectl patch configmap epp-config ...",
            dry_run_validated=True,
            expected_impact="queue rebalances across backends; P95 e2e back under 16s within ~3m",
        ),
    )
    base.update(overrides)
    return Report(**base)


def test_schema_version_is_1_0():
    assert SCHEMA_VERSION == "1.0"
    assert _valid_report().schema_version == "1.0"


def test_fault_category_enum_values_match_rubric():
    # A2: the four allowed enum values, exactly.
    assert {c.value for c in FaultCategory} == {
        "scorer-weight-misconfig",
        "unhealthy-endpoint-in-rotation",
        "prefix-cache-routing-disabled",
        "other",
    }


def test_scenario_to_category_mapping():
    assert SCENARIO_TO_CATEGORY["s1"] == FaultCategory.SCORER_WEIGHT_MISCONFIG
    assert SCENARIO_TO_CATEGORY["s2"] == FaultCategory.UNHEALTHY_ENDPOINT
    assert SCENARIO_TO_CATEGORY["s3"] == FaultCategory.PREFIX_CACHE_DISABLED


def test_valid_report_roundtrips_json():
    rep = _valid_report()
    blob = rep.model_dump_json()
    again = Report.model_validate_json(blob)
    assert again.fault_category == rep.fault_category
    assert json.loads(blob)["evidence"][0]["source"] == "prometheus"


def test_a3_requires_two_evidence_items():
    with pytest.raises(ValidationError, match="A3"):
        _valid_report(evidence=[_two_source_evidence()[0]])


def test_a3_requires_two_distinct_sources():
    same_source = [
        Evidence(
            source=EvidenceSource.PROMETHEUS, locator="q1", observed="x", claim="c1"
        ),
        Evidence(
            source=EvidenceSource.PROMETHEUS, locator="q2", observed="y", claim="c2"
        ),
    ]
    with pytest.raises(ValidationError, match="distinct sources"):
        _valid_report(evidence=same_source)


def test_a4_patch_fix_requires_command():
    with pytest.raises(ValidationError, match="A4"):
        _valid_report(
            fix=Fix(
                type=FixType.KUBECTL_PATCH,
                command=None,
                expected_impact="...",
            )
        )


def test_a4_manifest_fix_requires_diff():
    with pytest.raises(ValidationError, match="A4"):
        _valid_report(
            fix=Fix(type=FixType.MANIFEST_DIFF, diff=None, expected_impact="...")
        )


def test_recovery_defaults_to_unused():
    rep = _valid_report()
    assert rep.recovery.applied is False
    assert rep.recovery.slo_met is None
