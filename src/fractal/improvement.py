"""Operational landings for Curiosity, Fatigue, Greed, and safe trials."""

from __future__ import annotations

import fcntl
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class WorkSignature:
    """Compact comparison data captured once after a work unit completes."""

    work_id: str
    project_id: str
    work_type: str
    input_shape: str
    steps: tuple[str, ...]
    tools: tuple[str, ...]
    outcome_category: str
    purpose_class: Literal["ordinary", "testing", "verification", "safety"]
    elapsed_seconds: float | None
    token_usage: int | None
    completed_at: str
    thread_id: str | None = None
    turn_id: str | None = None
    tool_evidence: tuple[str, ...] = ()
    evidence_state: Literal["stop-captured", "turn-completed"] = "stop-captured"

    def to_dict(self) -> dict[str, Any]:
        if self.elapsed_seconds is not None and self.elapsed_seconds < 0:
            raise ValueError("elapsed_seconds cannot be negative")
        if self.token_usage is not None and self.token_usage < 0:
            raise ValueError("token_usage cannot be negative")
        value = asdict(self)
        value["steps"] = list(self.steps)
        value["tools"] = list(self.tools)
        value["tool_evidence"] = list(self.tool_evidence)
        return value


class WorkSignatureStore:
    """Idempotent local capture with one controlled completion enrichment."""

    def __init__(self, journal_path: Path) -> None:
        self.journal_path = Path(journal_path)

    def capture_completion(self, signature: WorkSignature) -> bool:
        """Capture one compact signature per stable work id."""
        value = signature.to_dict()
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.journal_path.with_suffix(self.journal_path.suffix + ".lock")
        with lock_path.open("a", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                existing = {item["work_id"]: item for item in self.read_all()}
                if signature.work_id in existing:
                    if existing[signature.work_id] != value:
                        raise ValueError("A Work Signature id already has different content")
                    return False
                with self.journal_path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                return True
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def read_all(self) -> list[dict[str, Any]]:
        """Read captured signatures without treating them as instructions."""
        if not self.journal_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.journal_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def enrich_completion(self, signature: WorkSignature) -> bool:
        """Replace a lightweight Stop record with final App Server evidence once."""
        value = signature.to_dict()
        if signature.evidence_state != "turn-completed":
            raise ValueError("Completion enrichment needs final turn evidence")
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.journal_path.with_suffix(self.journal_path.suffix + ".lock")
        with lock_path.open("a", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                records = self.read_all()
                matches = [
                    index
                    for index, item in enumerate(records)
                    if item["work_id"] == signature.work_id
                ]
                if not matches:
                    with self.journal_path.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
                        stream.flush()
                        os.fsync(stream.fileno())
                    return True
                if len(matches) != 1:
                    raise ValueError("Work Signature journal contains duplicate ids")
                index = matches[0]
                existing = records[index]
                if existing == value:
                    return False
                if existing.get("evidence_state", "stop-captured") != "stop-captured":
                    raise ValueError("A completed Work Signature cannot be changed")
                for key in ("work_id", "project_id", "work_type", "input_shape"):
                    if existing[key] != value[key]:
                        raise ValueError("Completion evidence does not match the Stop record")
                records[index] = value
                temporary = self.journal_path.with_suffix(self.journal_path.suffix + ".tmp")
                with temporary.open("w", encoding="utf-8") as stream:
                    for item in records:
                        stream.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(self.journal_path)
                return True
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


@dataclass(frozen=True, slots=True)
class SignatureComparison:
    """Deterministic-first comparison result."""

    relation: Literal["substantially-similar", "distinct", "semantic-comparison-required"]
    confidence: Literal["high", "medium", "unknown"]
    compared_fields: tuple[str, ...]


def compare_work_signatures(
    first: WorkSignature,
    second: WorkSignature,
) -> SignatureComparison:
    """Compare exact compact fields before considering an LLM fallback."""
    exact_fields = (
        "work_type",
        "input_shape",
        "steps",
        "tools",
        "outcome_category",
    )
    if all(getattr(first, field) == getattr(second, field) for field in exact_fields):
        return SignatureComparison("substantially-similar", "high", exact_fields)
    shared_shape = (
        first.work_type == second.work_type
        and first.input_shape == second.input_shape
        and first.outcome_category == second.outcome_category
    )
    if shared_shape:
        return SignatureComparison(
            "semantic-comparison-required",
            "unknown",
            ("work_type", "input_shape", "outcome_category", "steps", "tools"),
        )
    return SignatureComparison(
        "distinct",
        "high",
        ("work_type", "input_shape", "outcome_category"),
    )


@dataclass(frozen=True, slots=True)
class RepetitionRecognition:
    """Auditable application of the approved first, second, and third occurrence rules."""

    status: Literal[
        "first-occurrence",
        "possible-repetition",
        "investigation-required",
        "necessary-repetition",
        "semantic-comparison-required",
    ]
    occurrence_count: int
    confidence: str
    route: str
    evidence_work_ids: tuple[str, ...]


def recognise_repetition(
    history: list[WorkSignature],
    current: WorkSignature,
    *,
    high_avoidable_cost: bool = False,
) -> RepetitionRecognition:
    """Recognise repetition without treating necessary checks as waste."""
    similar = []
    uncertain = []
    for previous in history:
        result = compare_work_signatures(previous, current)
        if result.relation == "substantially-similar":
            similar.append(previous.work_id)
        elif result.relation == "semantic-comparison-required":
            uncertain.append(previous.work_id)
    count = len(similar) + 1
    evidence = (*similar, current.work_id)
    if current.purpose_class in {"testing", "verification", "safety"} and count >= 2:
        return RepetitionRecognition(
            "necessary-repetition",
            count,
            "high",
            "retain-required-check",
            evidence,
        )
    if count >= 3 or (count >= 2 and high_avoidable_cost):
        return RepetitionRecognition(
            "investigation-required",
            count,
            "high",
            "improvement-researcher",
            evidence,
        )
    if count == 2:
        return RepetitionRecognition(
            "possible-repetition",
            count,
            "high",
            "record-only",
            evidence,
        )
    if uncertain:
        return RepetitionRecognition(
            "semantic-comparison-required",
            1,
            "unknown",
            "bounded-semantic-comparison",
            (*uncertain, current.work_id),
        )
    return RepetitionRecognition(
        "first-occurrence",
        1,
        "high",
        "record-only",
        (current.work_id,),
    )


def semantic_comparison_payload(
    first: WorkSignature,
    second: WorkSignature,
) -> dict[str, Any]:
    """Expose only compact fields required by a bounded semantic fallback."""
    fields = ["work_id", "work_type", "input_shape", "steps", "tools", "outcome_category"]
    return {
        "first": {field: first.to_dict()[field] for field in fields},
        "second": {field: second.to_dict()[field] for field in fields},
        "permission": "read-only-comparison",
        "raw_work_included": False,
    }


def curiosity_routes(trigger: str) -> list[dict[str, Any]]:
    """Return the approved 60/20/20 bounded research routes."""
    if trigger not in {
        "improvement-investigation",
        "success-criteria-challenge-pre",
        "success-criteria-challenge-post",
    }:
        raise ValueError(f"Unsupported Curiosity trigger: {trigger}")
    current_focus = (
        "improve the current method"
        if trigger == "improvement-investigation"
        else "raise the current Goal, criteria, or architecture"
    )
    return [
        {
            "action_id": "improve-current-method",
            "effort_share": 60,
            "focus": current_focus,
            "isolation": "current-project-context",
        },
        {
            "action_id": "research-latest-findings",
            "effort_share": 20,
            "focus": "find dated developments in the same field",
            "isolation": "isolated-research-branch",
        },
        {
            "action_id": "explore-related-fields",
            "effort_share": 20,
            "focus": "find a transferable idea with an explicit field relationship",
            "isolation": "isolated-research-branch",
        },
    ]


@dataclass(frozen=True, slots=True)
class ResearchFinding:
    """One bounded Curiosity result with provenance and relevance evidence."""

    action_id: str
    summary: str
    source: str | None
    observed_at: str
    source_date: str | None = None
    related_field: str | None = None
    relationship: str | None = None

    def validate(self) -> None:
        valid_actions = {
            route["action_id"] for route in curiosity_routes("improvement-investigation")
        }
        if self.action_id not in valid_actions or not self.summary.strip():
            raise ValueError("Finding requires a valid action and summary")
        if self.action_id == "research-latest-findings" and (
            not self.source or not self.source_date
        ):
            raise ValueError("Latest findings require a source and source date")
        if self.action_id == "explore-related-fields" and (
            not self.related_field or not self.relationship
        ):
            raise ValueError("Related-field findings require a concrete relationship")


def combine_curiosity_findings(trigger: str, findings: list[ResearchFinding]) -> dict[str, Any]:
    """Combine research as candidate evidence without automatic adoption."""
    for finding in findings:
        finding.validate()
    return {
        "trigger": trigger,
        "allocation": curiosity_routes(trigger),
        "status": "candidate-findings" if findings else "no-finding",
        "findings": [asdict(item) for item in findings],
        "automatic_adoption": False,
        "next_route": (
            "project-review"
            if trigger == "improvement-investigation"
            else "main-agent-success-criteria-options"
        ),
    }


def build_improvement_investigation(
    recognition: RepetitionRecognition,
    *,
    project_id: str,
    method_summary: str,
    alternatives: list[dict[str, Any]],
    persistent_scope: bool,
    findings: list[ResearchFinding],
) -> dict[str, Any]:
    """Build candidate analysis for an isolated read-only Improvement Researcher."""
    if recognition.status != "investigation-required":
        raise ValueError("Improvement Investigation requires the approved repetition trigger")
    return {
        "record_type": "improvement_investigation",
        "project_id": project_id,
        "researcher_role": "improvement-researcher",
        "permissions": ["read-only"],
        "context": {
            "work_signature_ids": list(recognition.evidence_work_ids),
            "current_method": method_summary,
            "raw_history_included": False,
        },
        "alternatives": alternatives,
        "research": combine_curiosity_findings("improvement-investigation", findings),
        "record_status": "candidate-analysis",
        "automatic_change": False,
        "next_route": "system-review" if persistent_scope else "project-review",
    }


@dataclass(frozen=True, slots=True)
class ComponentShape:
    """Compact structural-repetition inventory entry."""

    component_id: str
    canonical_responsibility: str
    canonical_source_id: str
    platform: str
    layer: str
    content_sha256: str
    guardrail: bool
    high_risk_boundary: bool


def classify_structural_repetition(
    first: ComponentShape,
    second: ComponentShape,
) -> dict[str, Any]:
    """Classify similarity before any merge or removal proposal."""
    same_responsibility = first.canonical_responsibility == second.canonical_responsibility
    same_source = first.canonical_source_id == second.canonical_source_id
    if (
        same_source
        and first.platform == second.platform
        and first.content_sha256 != second.content_sha256
    ):
        classification = "drift"
    elif same_source and first.platform != second.platform:
        classification = "platform-projection"
    elif (
        same_responsibility
        and first.guardrail
        and second.guardrail
        and first.layer != second.layer
    ):
        classification = "independent-guardrail"
    elif (
        same_responsibility
        and first.high_risk_boundary
        and second.high_risk_boundary
        and first.layer != second.layer
    ):
        classification = "deliberate-high-risk-reinforcement"
    elif (
        same_responsibility
        and first.content_sha256 == second.content_sha256
        and first.platform == second.platform
        and first.layer == second.layer
    ):
        classification = "accidental-duplication"
    else:
        classification = "distinct"
    return {
        "classification": classification,
        "removal_candidate": classification in {"drift", "accidental-duplication"},
        "automatic_removal": False,
        "trial_required": classification in {"drift", "accidental-duplication"},
    }


@dataclass(frozen=True, slots=True)
class TrialBoundary:
    """All safety facts required before an autonomous bounded trial."""

    small: bool
    reversible: bool
    isolated: bool
    restore_verified: bool
    no_real_data_change: bool
    no_external_action: bool
    no_new_recipient: bool
    no_cost: bool
    no_goal_change: bool
    no_scope_expansion: bool
    no_persistent_change: bool
    same_representative_work: bool


def assess_trial_boundary(boundary: TrialBoundary) -> dict[str, Any]:
    """Allow a trial only when every approved safety condition is observed."""
    failed = [name for name, value in asdict(boundary).items() if not value]
    return {
        "allowed": not failed,
        "failed_conditions": failed,
        "next_route": "run-isolated-trial" if not failed else "request-primary-user-decision",
    }


@dataclass(frozen=True, slots=True)
class TrialMeasurement:
    """Comparable result for the same representative work."""

    outcome_equivalent: bool
    quality_score: float
    elapsed_seconds: float | None
    token_usage: int | None
    reliability_score: float | None
    new_risk: bool


def compare_trial_results(
    boundary: TrialBoundary,
    baseline: TrialMeasurement,
    candidate: TrialMeasurement,
) -> dict[str, Any]:
    """Apply quality-first comparison and never adopt a trial automatically."""
    boundary_result = assess_trial_boundary(boundary)
    if not boundary_result["allowed"]:
        return {
            "status": "approval-required-before-trial",
            "boundary": boundary_result,
            "persistent_adoption": False,
        }
    if (
        not candidate.outcome_equivalent
        or candidate.quality_score < baseline.quality_score
        or candidate.new_risk
    ):
        return {
            "status": "no-adoption-quality-first",
            "boundary": boundary_result,
            "persistent_adoption": False,
        }
    time_improved = (
        candidate.elapsed_seconds is not None
        and baseline.elapsed_seconds is not None
        and candidate.elapsed_seconds < baseline.elapsed_seconds
    )
    tokens_improved = (
        candidate.token_usage is not None
        and baseline.token_usage is not None
        and candidate.token_usage < baseline.token_usage
    )
    reliability_improved = (
        candidate.reliability_score is not None
        and baseline.reliability_score is not None
        and candidate.reliability_score > baseline.reliability_score
    )
    improved = time_improved or tokens_improved or reliability_improved
    return {
        "status": "candidate-for-review" if improved else "no-adoption-no-improvement",
        "boundary": boundary_result,
        "persistent_adoption": False,
        "same_work_comparison": True,
    }


@dataclass(frozen=True, slots=True)
class VerifiedOutcome:
    """One canonical Project outcome eligible for a derived baseline."""

    project_id: str
    comparable_key: str
    project_status: str
    independently_verified: bool
    representative: bool
    metrics: dict[str, dict[str, Any]]


def build_performance_baseline(
    outcomes: list[VerifiedOutcome],
    *,
    comparable_key: str,
    requested_dimensions: list[str],
) -> dict[str, Any]:
    """Derive best verified values without inventing missing benchmarks."""
    eligible = [
        item
        for item in outcomes
        if item.comparable_key == comparable_key
        and item.project_status == "completed"
        and item.independently_verified
        and item.representative
    ]
    metrics: dict[str, dict[str, Any]] = {}
    for dimension in requested_dimensions:
        observations = [
            (item.project_id, item.metrics[dimension])
            for item in eligible
            if dimension in item.metrics and item.metrics[dimension].get("value") is not None
        ]
        if not observations:
            continue
        directions = {value["direction"] for _, value in observations}
        if len(directions) != 1:
            raise ValueError(f"Conflicting metric directions: {dimension}")
        direction = directions.pop()
        chooser = min if direction == "minimize" else max
        best_value = chooser(value["value"] for _, value in observations)
        provenance = [
            project_id
            for project_id, value in observations
            if value["value"] == best_value
        ]
        sample = observations[0][1]
        metrics[dimension] = {
            "value": best_value,
            "direction": direction,
            "unit": sample["unit"],
            "provenance_project_ids": provenance,
        }
    return {
        "record_type": "performance-baseline",
        "comparable_key": comparable_key,
        "eligible_project_ids": [item.project_id for item in eligible],
        "metrics": metrics,
        "unknown_dimensions": [item for item in requested_dimensions if item not in metrics],
        "derived": True,
    }


def challenge_candidate_criteria(
    candidate: dict[str, dict[str, Any]],
    baseline: dict[str, Any],
    *,
    architecture_options: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Propose evidence-supported stronger options without changing approved criteria."""
    options = []
    for dimension, candidate_metric in candidate.items():
        baseline_metric = baseline["metrics"].get(dimension)
        if baseline_metric is None:
            continue
        direction = candidate_metric["direction"]
        baseline_value = baseline_metric["value"]
        candidate_value = candidate_metric["threshold"]
        stronger = (
            baseline_value < candidate_value
            if direction == "minimize"
            else baseline_value > candidate_value
        )
        if stronger:
            options.append(
                {
                    "kind": "evidence-supported-ambitious-threshold",
                    "dimension": dimension,
                    "original_threshold": candidate_value,
                    "suggested_threshold": baseline_value,
                    "direction": direction,
                    "unit": baseline_metric["unit"],
                    "evidence_project_ids": baseline_metric["provenance_project_ids"],
                }
            )
    architecture = [
        option
        for option in architecture_options or []
        if option.get("evidence_ids") and option.get("summary")
    ]
    return {
        "original_candidate": candidate,
        "ambitious_options": options,
        "architecture_options": architecture,
        "unknown_dimensions": baseline["unknown_dimensions"],
        "fabricated_targets": False,
        "automatic_approval": False,
        "mid_project_goal_change": False,
        "human_decision_options": [
            "keep-original",
            "approve-stronger",
            "record-excess-result",
            "create-future-candidate",
        ],
    }


def challenge_trigger_allowed(
    *,
    trigger: str,
    project_status: str,
    original_criteria_achieved: bool,
) -> bool:
    """Keep pre-work and post-work triggers out of ordinary mid-Project work."""
    if trigger == "pre_work":
        return project_status == "planning"
    if trigger == "post_work":
        return project_status in {"in_progress", "awaiting_completion"} and (
            original_criteria_achieved
        )
    raise ValueError(f"Unsupported challenge trigger: {trigger}")
