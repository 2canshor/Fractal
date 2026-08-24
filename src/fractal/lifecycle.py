"""Deterministic Project lifecycle transitions and human authority gates."""

from __future__ import annotations

import copy
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from fractal.models import Change, WriteResult, utc_now
from fractal.storage import AuthorityError, ProjectStore, value_sha256


class LifecycleError(RuntimeError):
    """Raised when a lifecycle precondition is not satisfied."""


PROJECT_REVIEW_DIMENSIONS = (
    "direction",
    "goal",
    "success_criteria",
    "priorities",
    "plan",
    "progress_and_evidence",
    "risks_and_deviations",
    "resources_and_deadline",
    "remaining_work",
)

PROJECT_RESOURCE_DIMENSIONS = {"time", "attention"}
PLAN_RESOURCE_STATES = {"provided", "unknown-at-plan-time", "not-applicable"}


def validate_plan_resources(resources: list[dict[str, Any]]) -> None:
    """Validate the minimum plan-time resource truth without inventing estimates."""
    if not isinstance(resources, list):
        raise LifecycleError("Project Plan requires resource state records")
    dimensions = [item.get("dimension") for item in resources if isinstance(item, dict)]
    if len(dimensions) != len(set(dimensions)):
        raise LifecycleError("Project Plan resource dimensions must be unique")
    missing = sorted(PROJECT_RESOURCE_DIMENSIONS.difference(dimensions))
    if missing:
        raise LifecycleError(f"Project Plan resource state is missing: {missing}")
    for item in resources:
        if not isinstance(item, dict):
            raise LifecycleError("Project Plan resource states must be typed records")
        state = item.get("plan_state")
        if state not in PLAN_RESOURCE_STATES:
            raise LifecycleError("Project Plan resource state is invalid")
        if not str(item.get("reason", "")).strip():
            raise LifecycleError("Project Plan resource state requires a reason")
        estimate = item.get("estimate")
        unit = item.get("unit")
        if state == "provided":
            if not isinstance(estimate, int | float) or estimate < 0:
                raise LifecycleError("Provided resource state requires a non-negative estimate")
            if not isinstance(unit, str) or not unit.strip():
                raise LifecycleError("Provided resource state requires a unit")
        elif estimate is not None or unit is not None:
            raise LifecycleError(
                "Unknown or not-applicable resource state keeps estimate and unit null"
            )


def _validate_project_review_resources(resources: list[dict[str, Any]]) -> None:
    if not isinstance(resources, list):
        raise LifecycleError("Perspective requires planned-versus-actual resource records")
    dimensions = {item.get("dimension") for item in resources if isinstance(item, dict)}
    if not dimensions >= PROJECT_RESOURCE_DIMENSIONS:
        missing = sorted(PROJECT_RESOURCE_DIMENSIONS.difference(dimensions))
        raise LifecycleError(f"Perspective resource comparison is missing: {missing}")
    for item in resources:
        if not isinstance(item, dict):
            raise LifecycleError("Perspective resource entries must be typed records")
        if item.get("status") not in {"within-plan", "over-plan", "under-plan", "unknown"}:
            raise LifecycleError("Perspective resource entry requires a comparison status")
        if not str(item.get("unit", "")).strip() or not str(item.get("reason", "")).strip():
            raise LifecycleError("Perspective resource entry requires unit and reason")
        planned = item.get("planned")
        actual = item.get("actual")
        if item["status"] == "unknown":
            if planned is not None or actual is not None:
                raise LifecycleError("Unknown resource comparison keeps planned and actual null")
        elif not isinstance(planned, int | float) or not isinstance(actual, int | float):
            raise LifecycleError("Known resource comparison requires numeric planned and actual")


def _validate_neglected_areas(areas: list[dict[str, Any]]) -> None:
    if not isinstance(areas, list):
        raise LifecycleError("Perspective requires a neglected-area assessment")
    for item in areas:
        if not isinstance(item, dict) or not str(item.get("area", "")).strip():
            raise LifecycleError("Neglected-area records require an area")
        if item.get("status") not in {"healthy", "watch", "neglected"}:
            raise LifecycleError("Neglected-area record requires a status")
        if not isinstance(item.get("evidence_ids"), list):
            raise LifecycleError("Neglected-area record requires an evidence list")


@dataclass(frozen=True, slots=True)
class DirectionSummary:
    """The four short summaries used for formal Project confirmation."""

    intended_outcome: str
    deliverable: str
    completion_standard: str
    exclusions: str

    def to_dict(self) -> dict[str, str]:
        value = {key: item.strip() for key, item in asdict(self).items()}
        if any(not item for item in value.values()):
            raise LifecycleError("All four Project Direction summaries are required")
        return value


@dataclass(frozen=True, slots=True)
class SuccessCriterion:
    """An observable completion condition with an explicit evidence contract."""

    id: str
    summary: str
    evidence_required: str

    def to_dict(self) -> dict[str, Any]:
        if not self.id or not self.summary.strip() or not self.evidence_required.strip():
            raise LifecycleError("Success Criteria require id, summary, and evidence type")
        return {
            "id": self.id,
            "summary": self.summary.strip(),
            "evidence_required": self.evidence_required.strip(),
            "achieved": False,
            "evidence_ids": [],
        }


class LifecycleController:
    """Apply typed lifecycle actions through the canonical conflict-safe store."""

    MATERIAL_DIMENSIONS = {
        "goal",
        "success_criteria",
        "priorities",
        "scope",
        "risk",
        "delivery",
    }

    def __init__(self, store: ProjectStore) -> None:
        self.store = store

    def confirm_direction(
        self,
        project_id: str,
        *,
        expected_revision: int,
        summary: DirectionSummary,
        actor: str,
        platform: str,
        human_action: bool,
        authority_source: str,
        material_change_reason: str | None = None,
    ) -> WriteResult:
        """Confirm or materially reconfirm the four-part Project Direction."""
        self._require_primary_user(actor=actor, human_action=human_action)
        record = self.store.read(project_id)
        current = record.lifecycle["direction"]
        summary_value = summary.to_dict()
        current_summary = {key: current[key] for key in summary_value}
        if current["status"] == "confirmed" and current_summary == summary_value:
            return WriteResult(applied=True, merged=False, revision=record.revision)
        if current["status"] == "confirmed" and not material_change_reason:
            raise LifecycleError("Material direction changes require a reconfirmation reason")
        now = utc_now()
        version = current["version"] + (current["status"] == "confirmed")
        confirmation = {
            "id": f"confirmation-{uuid.uuid4()}",
            "actor": actor,
            "confirmed_at": now,
            "summary_sha256": value_sha256(summary_value),
            "authority_source": authority_source,
        }
        candidate = {
            **summary_value,
            "status": "confirmed",
            "version": version,
            "confirmations": [*current["confirmations"], confirmation],
            "material_change_reason": material_change_reason,
        }
        return self.store.apply_changes(
            project_id,
            expected_revision=expected_revision,
            changes=[Change("set", "/lifecycle/direction", candidate, base_value=current)],
            actor=actor,
            platform=platform,
            authority_write=True,
            action="confirm-project-direction",
        )

    def approve_outcome(
        self,
        project_id: str,
        *,
        expected_revision: int,
        goal: str,
        criteria: list[SuccessCriterion],
        priorities: list[str],
        pre_work_challenge: dict[str, Any],
        actor: str,
        platform: str,
        human_action: bool,
    ) -> WriteResult:
        """Approve Goal, Success Criteria, and priorities as one human action."""
        self._require_primary_user(actor=actor, human_action=human_action)
        if not goal.strip() or not criteria:
            raise LifecycleError("An approved outcome needs a Goal and Success Criteria")
        if len(priorities) != len(set(priorities)):
            raise LifecycleError("Priorities cannot contain duplicates")
        record = self.store.read(project_id)
        now = utc_now()
        current_criteria = record.lifecycle["success_criteria"]
        version = current_criteria["version"]
        goal_value = {
            "statement": goal.strip(),
            "status": "approved",
            "version": record.lifecycle["goal"]["version"],
            "approved_at": now,
        }
        criteria_value = {
            "version": version,
            "status": "approved",
            "items": [item.to_dict() for item in criteria],
            "pre_work_challenge": copy.deepcopy(pre_work_challenge),
            "post_work_challenges": [],
        }
        return self.store.apply_changes(
            project_id,
            expected_revision=expected_revision,
            changes=[
                Change("set", "/lifecycle/goal", goal_value, record.lifecycle["goal"]),
                Change(
                    "set",
                    "/lifecycle/success_criteria",
                    criteria_value,
                    current_criteria,
                ),
                Change(
                    "set",
                    "/lifecycle/priorities",
                    list(priorities),
                    record.lifecycle["priorities"],
                ),
            ],
            actor=actor,
            platform=platform,
            authority_write=True,
            action="approve-project-outcome",
        )

    def record_review_point(
        self,
        project_id: str,
        *,
        expected_revision: int,
        trigger: str,
        review_kind: str | None = None,
        reason: str,
        evidence_ids: list[str],
        actor: str,
        platform: str,
    ) -> WriteResult:
        """Record a checkpoint that requires a whole-Project review."""
        if trigger not in {"checkpoint", "risk", "deviation", "failure", "human_request"}:
            raise LifecycleError(f"Unsupported Perspective trigger: {trigger}")
        inferred_kind = "milestone" if trigger == "checkpoint" else "exception"
        if review_kind is not None and review_kind != inferred_kind:
            raise LifecycleError(f"Trigger {trigger} requires a {inferred_kind} Perspective")
        point = {
            "id": f"review-point-{uuid.uuid4()}",
            "trigger": trigger,
            "review_kind": inferred_kind,
            "reason": reason,
            "status": "open",
            "recorded_at": utc_now(),
            "evidence_ids": evidence_ids,
        }
        return self.store.apply_changes(
            project_id,
            expected_revision=expected_revision,
            changes=[Change("append", "/lifecycle/review_points", point)],
            actor=actor,
            platform=platform,
            action="record-review-point",
        )

    def record_unknown(
        self,
        project_id: str,
        *,
        expected_revision: int,
        unknown_id: str,
        summary: str,
        status: str,
        material: bool,
        actor: str,
        platform: str,
    ) -> WriteResult:
        """Persist one bounded unknown and its honest current status."""
        unknown = {
            "id": unknown_id,
            "summary": summary,
            "status": status,
            "material": material,
            "recorded_at": utc_now(),
        }
        return self.store.apply_changes(
            project_id,
            expected_revision=expected_revision,
            changes=[Change("append", "/lifecycle/unknowns", unknown)],
            actor=actor,
            platform=platform,
            action="record-project-unknown",
        )

    def record_deviation(
        self,
        project_id: str,
        *,
        expected_revision: int,
        summary: str,
        dimensions: list[str],
        evidence_ids: list[str],
        actor: str,
        platform: str,
    ) -> WriteResult:
        """Record a deviation and open a Review Point only when it is material."""
        material = bool(self.MATERIAL_DIMENSIONS.intersection(dimensions))
        now = utc_now()
        deviation = {
            "id": f"deviation-{uuid.uuid4()}",
            "summary": summary,
            "dimensions": dimensions,
            "material": material,
            "recorded_at": now,
            "evidence_ids": evidence_ids,
        }
        changes = [Change("append", "/lifecycle/deviations", deviation)]
        if material:
            changes.append(
                Change(
                    "append",
                    "/lifecycle/review_points",
                    {
                        "id": f"review-point-{uuid.uuid4()}",
                        "trigger": "deviation",
                        "review_kind": "exception",
                        "reason": summary,
                        "status": "open",
                        "recorded_at": now,
                        "evidence_ids": evidence_ids,
                    },
                )
            )
        return self.store.apply_changes(
            project_id,
            expected_revision=expected_revision,
            changes=changes,
            actor=actor,
            platform=platform,
            action="record-material-deviation" if material else "record-minor-deviation",
        )

    def request_goal_change(
        self,
        project_id: str,
        *,
        expected_revision: int,
        proposed_goal: str,
        actor: str,
        platform: str,
    ) -> WriteResult:
        """Create a Request Decision instead of silently moving the Goal."""
        record = self.store.read(project_id)
        request = {
            "id": f"request-{uuid.uuid4()}",
            "kind": "request_decision",
            "status": "pending",
            "path": "/lifecycle/goal",
            "base_value": record.lifecycle["goal"],
            "current_value": record.lifecycle["goal"],
            "proposed_value": {
                "statement": proposed_goal,
                "status": "change_requested",
            },
            "created_at": utc_now(),
        }
        return self.store.apply_changes(
            project_id,
            expected_revision=expected_revision,
            changes=[Change("append", "/requests", request)],
            actor=actor,
            platform=platform,
            action="request-goal-change",
        )

    def record_project_review(
        self,
        project_id: str,
        *,
        expected_revision: int,
        conclusion: str,
        confidence: str,
        plan_delta: str,
        concern: str,
        whole_project_assessment: dict[str, str],
        planned_vs_actual_resources: list[dict[str, Any]],
        neglected_areas: list[dict[str, Any]],
        opportunity_cost: str,
        continuation_decision: dict[str, str],
        evidence_ids: list[str],
        actor: str,
        platform: str,
    ) -> WriteResult:
        """Review the whole Project snapshot and close its open Review Points."""
        record = self.store.read(project_id)
        lifecycle = record.lifecycle
        missing = [
            dimension
            for dimension in PROJECT_REVIEW_DIMENSIONS
            if not whole_project_assessment.get(dimension, "").strip()
        ]
        extra = sorted(set(whole_project_assessment).difference(PROJECT_REVIEW_DIMENSIONS))
        if missing or extra:
            raise LifecycleError(
                "Perspective must assess every whole-Project dimension; "
                f"missing={missing}, extra={extra}"
            )
        _validate_project_review_resources(planned_vs_actual_resources)
        _validate_neglected_areas(neglected_areas)
        if not opportunity_cost.strip():
            raise LifecycleError("Perspective requires an explicit opportunity cost")
        if (
            continuation_decision.get("decision")
            not in {
                "continue-as-planned",
                "continue-with-plan-update",
                "pause-and-request-decision",
            }
            or not continuation_decision.get("justification", "").strip()
        ):
            raise LifecycleError("Perspective requires a continuation decision and justification")
        points = copy.deepcopy(lifecycle["review_points"])
        open_points = [point for point in points if point["status"] == "open"]
        if not open_points:
            raise LifecycleError("Perspective requires an open Review Point")
        for point in points:
            if point["status"] == "open":
                point["status"] = "reviewed"
        review = {
            "record_version": 2,
            "id": f"review-{uuid.uuid4()}",
            "project_sha256": value_sha256(record.to_dict()),
            "review_point_ids": [point["id"] for point in open_points],
            "review_kinds": sorted(
                {
                    point.get(
                        "review_kind",
                        "milestone" if point["trigger"] == "checkpoint" else "exception",
                    )
                    for point in open_points
                }
            ),
            "whole_project_assessment": {
                dimension: whole_project_assessment[dimension].strip()
                for dimension in PROJECT_REVIEW_DIMENSIONS
            },
            "whole_project_scope_receipt": {
                "assessed_dimensions": list(PROJECT_REVIEW_DIMENSIONS),
                "project_snapshot_sha256": value_sha256(record.to_dict()),
                "local_trigger_review_point_ids": [point["id"] for point in open_points],
            },
            "planned_vs_actual_resources": copy.deepcopy(planned_vs_actual_resources),
            "neglected_areas": copy.deepcopy(neglected_areas),
            "opportunity_cost": opportunity_cost.strip(),
            "continuation_decision": copy.deepcopy(continuation_decision),
            "conclusion": conclusion,
            "confidence": confidence,
            "plan_delta": plan_delta,
            "recorded_at": utc_now(),
            "evidence_ids": evidence_ids,
        }
        concern_value = {"summary": concern, "evidence_ids": evidence_ids}
        return self.store.apply_changes(
            project_id,
            expected_revision=expected_revision,
            changes=[
                Change("append", "/lifecycle/reviews", review),
                Change("set", "/lifecycle/review_points", points, lifecycle["review_points"]),
                Change(
                    "set",
                    "/lifecycle/biggest_remaining_concern",
                    concern_value,
                    lifecycle["biggest_remaining_concern"],
                ),
            ],
            actor=actor,
            platform=platform,
            action="record-project-review",
        )

    def record_plan_update(
        self,
        project_id: str,
        *,
        expected_revision: int,
        plan: dict[str, Any],
        reason: str,
        material: bool,
        actor: str,
        platform: str,
        human_action: bool = False,
    ) -> WriteResult:
        """Version a Project Plan update and preserve before/after snapshots."""
        validate_plan_resources(plan.get("resources"))
        if material:
            self._require_primary_user(actor=actor, human_action=human_action)
        record = self.store.read(project_id)
        history = record.lifecycle["plan_history"]
        entry = {
            "id": f"plan-history-{uuid.uuid4()}",
            "version": len(history) + 1,
            "reason": reason,
            "material": material,
            "before_sha256": value_sha256(record.plan),
            "after_sha256": value_sha256(plan),
            "before_plan": copy.deepcopy(record.plan),
            "after_plan": copy.deepcopy(plan),
            "recorded_at": utc_now(),
            "authority": "primary-user" if material else "delegated-project-work",
        }
        return self.store.apply_changes(
            project_id,
            expected_revision=expected_revision,
            changes=[
                Change("set", "/plan", plan, record.plan),
                Change("append", "/lifecycle/plan_history", entry),
            ],
            actor=actor,
            platform=platform,
            authority_write=material,
            action="record-project-plan-update",
        )

    def record_criterion_achievement(
        self,
        project_id: str,
        *,
        expected_revision: int,
        criterion_id: str,
        evidence_ids: list[str],
        actor: str,
        platform: str,
    ) -> WriteResult:
        """Attach evidence to one approved criterion without changing its threshold."""
        if not evidence_ids:
            raise LifecycleError("Criterion achievement requires evidence")
        record = self.store.read(project_id)
        available_evidence = {item["id"] for item in record.evidence}
        if not set(evidence_ids).issubset(available_evidence):
            raise LifecycleError("Criterion evidence must exist in the canonical Project")
        criteria = record.lifecycle["success_criteria"]
        if criteria["status"] != "approved":
            raise LifecycleError("Only approved Success Criteria can be achieved")
        items = copy.deepcopy(criteria["items"])
        target = next((item for item in items if item["id"] == criterion_id), None)
        if target is None:
            raise LifecycleError(f"Unknown Success Criterion: {criterion_id}")
        target["achieved"] = True
        target["evidence_ids"] = list(dict.fromkeys([*target["evidence_ids"], *evidence_ids]))
        return self.store.apply_changes(
            project_id,
            expected_revision=expected_revision,
            changes=[Change("set", "/lifecycle/success_criteria/items", items, criteria["items"])],
            actor=actor,
            platform=platform,
            action="record-criterion-achievement",
        )

    def record_post_work_challenge(
        self,
        project_id: str,
        *,
        expected_revision: int,
        higher_target_summary: str,
        higher_target_status: str,
        evidence_ids: list[str],
        actor: str,
        platform: str,
    ) -> WriteResult:
        """Record one post-work challenge per approved criteria version."""
        record = self.store.read(project_id)
        criteria = record.lifecycle["success_criteria"]
        if criteria["status"] != "approved" or not criteria["items"]:
            raise LifecycleError("Post-work challenge requires approved Success Criteria")
        if not all(item["achieved"] for item in criteria["items"]):
            raise LifecycleError("Original Success Criteria must be achieved first")
        version = criteria["version"]
        if any(item["criteria_version"] == version for item in criteria["post_work_challenges"]):
            raise LifecycleError("Post-work challenge already recorded for this criteria version")
        challenge = {
            "id": f"challenge-{uuid.uuid4()}",
            "criteria_version": version,
            "trigger": "post_work",
            "status": "completed",
            "original_achievement_preserved": True,
            "higher_target": {
                "summary": higher_target_summary,
                "status": higher_target_status,
            },
            "recorded_at": utc_now(),
            "evidence_ids": evidence_ids,
        }
        return self.store.apply_changes(
            project_id,
            expected_revision=expected_revision,
            changes=[
                Change(
                    "append",
                    "/lifecycle/success_criteria/post_work_challenges",
                    challenge,
                )
            ],
            actor=actor,
            platform=platform,
            action="record-post-work-challenge",
        )

    def mark_awaiting_completion(
        self,
        project_id: str,
        *,
        expected_revision: int,
        actor: str,
        platform: str,
    ) -> WriteResult:
        """Enter Awaiting Completion only after evidence and challenge gates pass."""
        record = self.store.read(project_id)
        criteria = record.lifecycle["success_criteria"]
        version = criteria["version"]
        if criteria["status"] != "approved" or not criteria["items"]:
            raise LifecycleError("Awaiting Completion requires approved Success Criteria")
        if not all(item["achieved"] for item in criteria["items"]):
            raise LifecycleError("Awaiting Completion requires achieved Success Criteria")
        if not any(
            item["criteria_version"] == version for item in criteria["post_work_challenges"]
        ):
            raise LifecycleError("Awaiting Completion requires the post-work challenge")
        requested_at = utc_now()
        return self.store.apply_changes(
            project_id,
            expected_revision=expected_revision,
            changes=[
                Change("set", "/status", "awaiting_completion", record.status),
                Change(
                    "set",
                    "/completion/requested_at",
                    requested_at,
                    record.completion["requested_at"],
                ),
            ],
            actor=actor,
            platform=platform,
            action="mark-awaiting-completion",
        )

    def declare_project_completion(
        self,
        project_id: str,
        *,
        expected_revision: int,
        actor: str,
        platform: str,
        human_action: bool,
    ) -> WriteResult:
        """Let only the primary user declare Project Completion."""
        self._require_primary_user(actor=actor, human_action=human_action)
        record = self.store.read(project_id)
        if record.status != "awaiting_completion":
            raise LifecycleError("Project must be Awaiting Completion")
        now = utc_now()
        return self.store.apply_changes(
            project_id,
            expected_revision=expected_revision,
            changes=[
                Change("set", "/status", "completed", record.status),
                Change("set", "/completion/completed_at", now, None),
                Change("set", "/completion/completed_by", actor, None),
            ],
            actor=actor,
            platform=platform,
            authority_write=True,
            action="declare-project-completion",
        )

    def reopen_after_correction(
        self,
        project_id: str,
        *,
        expected_revision: int,
        reopen_phase: int,
        criterion_ids: list[str],
        reason: str,
        actor: str,
        platform: str,
        human_action: bool,
    ) -> WriteResult:
        """Reopen Awaiting Completion after a primary-user architecture correction."""
        self._require_primary_user(actor=actor, human_action=human_action)
        record = self.store.read(project_id)
        if record.status != "awaiting_completion":
            raise LifecycleError("Only an Awaiting Completion Project can be reopened")
        if reopen_phase < 0 or not reason.strip():
            raise LifecycleError("A reopen phase and correction reason are required")
        plan = copy.deepcopy(record.plan)
        matching_phase = False
        for item in plan["items"]:
            phase_label = item["id"].removeprefix("phase-")
            phase_number = phase_label.split("-", 1)[0]
            if not phase_number.isdigit():
                raise LifecycleError(f"Invalid phase plan item id: {item['id']}")
            phase = int(phase_number)
            if phase == reopen_phase:
                item["status"] = "in_progress"
                matching_phase = True
            elif phase > reopen_phase:
                item["status"] = "pending"
        if not matching_phase:
            raise LifecycleError(f"Unknown reopen phase: {reopen_phase}")
        plan["current_phase"] = reopen_phase
        plan["criteria_version"] += 1

        criteria = copy.deepcopy(record.lifecycle["success_criteria"])
        known_criteria = {item["id"] for item in criteria["items"]}
        unknown = sorted(set(criterion_ids).difference(known_criteria))
        if unknown:
            raise LifecycleError(f"Unknown Success Criteria: {', '.join(unknown)}")
        criteria["version"] += 1
        for item in criteria["items"]:
            if item["id"] in criterion_ids:
                item["achieved"] = False
                item["evidence_ids"] = []

        review_point = {
            "id": f"review-point-{uuid.uuid4()}",
            "trigger": "human_request",
            "review_kind": "exception",
            "reason": reason.strip(),
            "status": "open",
            "recorded_at": utc_now(),
            "evidence_ids": [],
        }
        return self.store.apply_changes(
            project_id,
            expected_revision=expected_revision,
            changes=[
                Change("set", "/status", "in_progress", record.status),
                Change(
                    "set",
                    "/completion/requested_at",
                    None,
                    record.completion["requested_at"],
                ),
                Change("set", "/plan", plan, record.plan),
                Change(
                    "set",
                    "/lifecycle/success_criteria",
                    criteria,
                    record.lifecycle["success_criteria"],
                ),
                Change("append", "/lifecycle/review_points", review_point),
            ],
            actor=actor,
            platform=platform,
            authority_write=True,
            action="reopen-after-primary-user-correction",
        )

    @staticmethod
    def _require_primary_user(*, actor: str, human_action: bool) -> None:
        if not human_action or actor != "primary-user":
            raise AuthorityError("This lifecycle action requires the primary user")
