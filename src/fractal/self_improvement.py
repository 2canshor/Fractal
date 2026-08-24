"""Locally forked self-improvement review and candidate-method staging.

This module implements bounded methods adapted from Hermes Agent, Super Hermes,
Hermes Dojo, the Colleague Skill source, Hermes Workspace and selected Evey plugins. It has no
runtime dependency on any donor repository, service, model or plugin engine.
See THIRD_PARTY_NOTICES.md and ``learning-method-sources.json``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from fractal.blueprint import load_blueprint
from fractal.models import utc_now
from fractal.storage import value_sha256

RESEARCH_ROUTES = [
    ("improve-current-method", 60),
    ("research-latest-findings", 20),
    ("explore-related-fields", 20),
]
TRANSIENT_CATEGORIES = {"authentication", "infrastructure", "rate-limit", "transient"}
METHOD_CATEGORIES = {"correction", "method", "missing-parameter", "repetition"}
SUBTRACTION_FIRST_ACTIONS = (
    "delete",
    "shorten",
    "merge",
    "simplify",
    "reconfigure",
    "modify",
    "add",
    "no-change",
)


class SelfImprovementError(RuntimeError):
    """Raised when locally forked review or candidate evidence is invalid."""


def load_learning_method_sources() -> dict[str, Any]:
    """Load replaceable current donor bindings without contacting any upstream."""
    path = files("fractal.data").joinpath("learning-method-sources.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("record_type") != "learning-method-source-set":
        raise SelfImprovementError("Learning method source record type is invalid")
    if value.get("runtime_dependency_on_upstream") is not False:
        raise SelfImprovementError("Learning methods cannot depend on an upstream runtime")
    policy = value.get("selection_policy")
    if (
        not isinstance(policy, dict)
        or policy.get("donor_set_fixed") is not False
        or policy.get("select_from_current_need") is not True
        or policy.get("multiple_donors_per_method_allowed") is not True
        or policy.get("replacement_allowed") is not True
    ):
        raise SelfImprovementError("Learning method donor selection must remain replaceable")
    methods = value.get("methods")
    if not isinstance(methods, list) or not methods:
        raise SelfImprovementError("Forked improvement methods are missing")
    method_ids = [item.get("method_id") for item in methods]
    if len(method_ids) != len(set(method_ids)):
        raise SelfImprovementError("Forked improvement method ids must be unique")
    blueprint = load_blueprint()
    element_ids = {
        element["element_id"]
        for genre in blueprint["element_library"]["genres"]
        for element in genre["elements"]
    }
    flow_ids = {flow["flow_id"] for flow in blueprint["flows"]["entries"]}
    element_mapping_fields = {
        "activated_by",
        "implements",
        "serves_elements",
        "hands_off_to_element",
        "informs_elements",
        "informs_element",
        "validates_element",
    }
    flow_mapping_fields = {
        "serves_flows",
        "hands_off_to_flow",
        "informs_flows",
    }
    for method in methods:
        source_records = method.get("sources")
        if source_records is None:
            source_records = [method]
        if not isinstance(source_records, list) or not source_records:
            raise SelfImprovementError("Forked method requires at least one source")
        for source in source_records:
            if re.fullmatch(r"[a-f0-9]{40}", str(source.get("commit"))) is None:
                raise SelfImprovementError("Forked method requires an exact source commit")
            if source.get("licence") not in {"Apache-2.0", "MIT"}:
                raise SelfImprovementError("Forked method licence is invalid")
            if not str(source.get("donor_id", "")).strip() or not str(
                source.get("source_url", "")
            ).startswith("https://github.com/"):
                raise SelfImprovementError("Forked method source provenance is incomplete")
            if not source.get("source_paths"):
                raise SelfImprovementError("Forked method requires exact source paths")
        if not method.get("forked_behaviour") or not method.get("removed_behaviour"):
            raise SelfImprovementError("Forked method requires kept and removed boundaries")
        if method.get("source_binding") != "replaceable-current-source":
            raise SelfImprovementError("Learning method source binding must be replaceable")
        mapping = method.get("blueprint_mapping")
        if not isinstance(mapping, dict):
            raise SelfImprovementError("Forked method requires a Blueprint mapping")
        unknown_fields = set(mapping).difference(element_mapping_fields | flow_mapping_fields)
        if unknown_fields:
            raise SelfImprovementError(
                f"Forked method has ambiguous Blueprint mapping fields: {sorted(unknown_fields)}"
            )
        for field, target in mapping.items():
            targets = target if isinstance(target, list) else [target]
            allowed_ids = flow_ids if field in flow_mapping_fields else element_ids
            if not targets or not set(targets).issubset(allowed_ids):
                target_kind = "Flow" if field in flow_mapping_fields else "Element"
                raise SelfImprovementError(
                    f"Forked method references an unknown Blueprint {target_kind}"
                )
    return value


@dataclass(frozen=True, slots=True)
class ImprovementSignal:
    """One compact fact that a local improvement review may examine."""

    signal_id: str
    summary: str
    evidence_ids: tuple[str, ...]
    occurrences: int = 1
    error: str = ""
    signal_kind: str = "method"

    def to_dict(self) -> dict[str, Any]:
        if not self.signal_id.strip() or not self.summary.strip() or not self.evidence_ids:
            raise SelfImprovementError("Improvement signal requires id, summary and evidence")
        if self.occurrences < 1:
            raise SelfImprovementError("Improvement signal occurrences must be positive")
        category, fixable = classify_signal(self)
        return {
            "signal_id": self.signal_id,
            "summary": self.summary.strip(),
            "evidence_ids": list(dict.fromkeys(self.evidence_ids)),
            "occurrences": self.occurrences,
            "error": self.error.strip(),
            "signal_kind": self.signal_kind,
            "category": category,
            "method_fixable": fixable,
        }


def classify_signal(signal: ImprovementSignal) -> tuple[str, bool]:
    """Separate durable method weakness from temporary environment state."""
    text = f"{signal.signal_kind} {signal.summary} {signal.error}".lower()
    if any(
        phrase in text
        for phrase in (
            "authentication",
            "credential",
            "forbidden",
            "invalid key",
            "unauthorized",
        )
    ):
        return "authentication", False
    if any(
        phrase in text
        for phrase in (
            "connection refused",
            "dns",
            "network unavailable",
            "service unavailable",
            "unreachable",
        )
    ):
        return "infrastructure", False
    if any(phrase in text for phrase in ("429", "rate limit", "throttled")):
        return "rate-limit", False
    if any(
        phrase in text
        for phrase in (
            "command not found",
            "missing binary",
            "not installed",
            "fresh install",
        )
    ):
        return "transient", False
    if any(
        phrase in text
        for phrase in ("missing required", "field required", "is required")
    ):
        return "missing-parameter", True
    if signal.signal_kind in {"correction", "repetition"}:
        return signal.signal_kind, True
    return "method", True


class PostWorkLearning:
    """Review completed work locally and produce a staged method candidate."""

    def __init__(self, *, constraint_history: list[dict[str, Any]] | None = None) -> None:
        self.sources = load_learning_method_sources()
        self.constraint_history = list(constraint_history or [])

    def review(
        self,
        *,
        project: dict[str, Any],
        action_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Run deterministic background review with Curiosity's 60/20/20 routes."""
        project_id = str(project.get("project_id") or "").strip()
        if not project_id:
            raise SelfImprovementError("Improvement review requires a Project")
        current_method = action_payload.get("current_method")
        if not isinstance(current_method, dict):
            raise SelfImprovementError("Improvement review requires the current method")
        work_ids = [str(item) for item in action_payload.get("work_signature_ids", []) if item]
        if not work_ids:
            raise SelfImprovementError("Improvement review requires Work Signature evidence")

        signals = [
            ImprovementSignal(
                signal_id=f"signal-repetition-{value_sha256(work_ids)[:12]}",
                summary=(
                    "The same privacy-bounded request shape and Tool set recurred enough "
                    "times to investigate whether method reuse can reduce reconstruction."
                ),
                evidence_ids=tuple(work_ids),
                occurrences=len(work_ids),
                signal_kind="repetition",
            )
        ]
        for raw in action_payload.get("signals", []):
            if not isinstance(raw, dict):
                raise SelfImprovementError("Improvement review signals must be records")
            signals.append(
                ImprovementSignal(
                    signal_id=str(raw.get("signal_id") or f"signal-{uuid.uuid4()}"),
                    summary=str(raw.get("summary") or ""),
                    evidence_ids=tuple(raw.get("evidence_ids") or work_ids),
                    occurrences=int(raw.get("occurrences") or 1),
                    error=str(raw.get("error") or ""),
                    signal_kind=str(raw.get("signal_kind") or "method"),
                )
            )

        observations = []
        method_observation_ids = []
        for signal in signals:
            value = signal.to_dict()
            observation_id = f"observation-{value['signal_id']}"
            observations.append(
                {
                    "id": observation_id,
                    "summary": value["summary"],
                    "evidence_ids": value["evidence_ids"],
                    "uncertainty": (
                        "Recurrence proves avoidable review pressure, not that one specific "
                        "replacement will improve the whole Project."
                    ),
                    "kind": value["category"],
                    "method_fixable": value["method_fixable"],
                    "occurrences": value["occurrences"],
                }
            )
            if value["method_fixable"] and value["occurrences"] >= 2:
                method_observation_ids.append(observation_id)

        patterns = []
        if method_observation_ids:
            patterns.append(
                {
                    "id": f"pattern-reusable-method-{value_sha256(method_observation_ids)[:12]}",
                    "observation_ids": method_observation_ids,
                    "summary": (
                        "The same request shape is being completed repeatedly and may benefit "
                        "from one reusable method."
                    ),
                    "cause": (
                        "Whether repetition is necessary or avoidable remains for Perspective; "
                        "the current evidence is sufficient only to stage and test a candidate."
                    ),
                    "confidence": "high" if len(work_ids) >= 3 else "medium",
                }
            )

        prior_blind_spots = sorted(
            {
                str(item)
                for report in self.constraint_history[-5:]
                for item in report.get("blind_spots", [])
                if item
            }
        )
        method_name = _method_name(current_method)
        candidate_changes = []
        status = "no-change"
        if patterns:
            status = "candidate"
            candidate_changes.append(
                {
                    "id": f"change-{value_sha256([project_id, method_name, work_ids])[:12]}",
                    "action": "reconfigure",
                    "target": method_name,
                    "change": (
                        "Turn the repeated work shape into a local class-level method with "
                        "pre-flight checks, exact steps, verification, failure handling and a "
                        "blind-spot record."
                    ),
                    "reason": patterns[0]["summary"],
                    "expected_effect": (
                        "Future matching work can load one tested method instead of rebuilding "
                        "the same sequence."
                    ),
                    "test_plan": [
                        "validate portable method identity and complete sections",
                        "verify every source Work Signature remains linked",
                        "run the method artifact parser and integrity check",
                        "compare a later matching work result before promotion",
                    ],
                    "recovery": (
                        "Reject the staged candidate and keep the current method; no active "
                        "capability depends on this artifact."
                    ),
                    "blueprint_handoff": "map-implementations-to-blueprint",
                    "proposed_method": {
                        "name": method_name,
                        "trigger": str(current_method.get("input_shape") or "matching work"),
                        "steps": _method_steps(current_method),
                        "tools": list(current_method.get("tools") or []),
                        "preflight": [
                            "Confirm Perspective still supports doing this work now.",
                            "Separate method weakness from authentication, infrastructure and "
                            "temporary service state.",
                        ],
                        "verification": [
                            "Record the actual outcome and exact evidence.",
                            "Do not promote an unresolved attempt as a reliable method.",
                        ],
                        "blind_spots_to_check": prior_blind_spots
                        or ["time degradation", "authority boundary", "recovery"],
                    },
                }
            )

        research_routes = [
            {
                "action_id": "improve-current-method",
                "effort_share": 60,
                "status": "finding" if patterns else "no-finding",
                "finding": (
                    candidate_changes[0]["change"]
                    if candidate_changes
                    else "No recurring method-fixable pattern was found."
                ),
                "source": "current Project Work Signatures and locally persisted action payload",
            },
            {
                "action_id": "research-latest-findings",
                "effort_share": 20,
                "status": "finding",
                "finding": (
                    "Use an isolated post-work review, prefer improving an existing umbrella "
                    "method, retain only working lessons and ledger every candidate mutation."
                ),
                "source": (
                    "local Post-Work Learning fork; source evidence @ "
                    "fcbd1076a93841fa88855acce810e342a5b78101"
                ),
            },
            {
                "action_id": "explore-related-fields",
                "effort_share": 20,
                "status": "finding",
                "finding": (
                    "Carry forward the previous analysis blind spots and keep reusable work "
                    "procedure separate from persona or one-session narrative."
                ),
                "source": (
                    "local Blind Spot Review and Work Method Extraction forks; source "
                    "evidence @ ffe2d10042041dcc23325f013e8b7e607e069952 and "
                    "04c72cc26c04e12c673405b94c8a42400287d403"
                ),
                "related_field": "constraint-aware analysis and work-method distillation",
                "relationship": (
                    "Both turn experience into a reusable method while exposing missing "
                    "knowledge instead of fabricating it."
                ),
            },
        ]

        result = {
            "record_type": "local-improvement-review",
            "record_version": 1,
            "status": status,
            "project_id": project_id,
            "summary": (
                "A reusable local method candidate was staged from repeated work."
                if candidate_changes
                else "The observed pressure was not a durable method weakness."
            ),
            "observations": observations,
            "patterns": patterns,
            "research_routes": research_routes,
            "candidate_changes": candidate_changes,
            "blueprint_relationships": [
                {
                    "from": "fatigue",
                    "relationship": "informs",
                    "to": "project-review",
                    "activation_boundary": "after investigation-required repetition",
                },
                {
                    "from": "curiosity",
                    "relationship": "implements",
                    "to": "steal",
                    "activation_boundary": "only for donor implementation evidence",
                },
                {
                    "from": "steal",
                    "relationship": "informs",
                    "to": "map-implementations-to-blueprint",
                    "activation_boundary": "after a staged candidate and recovery exist",
                },
            ],
            "constraint_report": {
                "method_used": (
                    "Hermes isolated background review plus Super Hermes independent "
                    "structure/time/constraint reflection"
                ),
                "maximized": [
                    "reusable method extraction",
                    "transient-versus-method separation",
                    "recovery and authority visibility",
                ],
                "sacrificed": [
                    "fresh external web discovery",
                    "semantic code synthesis",
                    "active outcome comparison",
                ],
                "blind_spots": [
                    "The candidate has not yet completed representative future work.",
                    "Perspective has not yet proved that the repetition was avoidable.",
                    "Offline donor evidence can become stale until a later Steal refresh.",
                    "A structured method cannot prove the global Project outcome by itself.",
                ],
                "prior_blind_spots_addressed": prior_blind_spots,
                "conservation_law": (
                    "More autonomous method capture requires stronger evidence and promotion "
                    "gates to prevent faster learning from becoming faster error persistence."
                ),
            },
            "learning_method_ids": [item["method_id"] for item in self.sources["methods"]],
            "donor_selection_fixed": False,
            "runtime_dependency_on_upstream": False,
            "automatic_apply": False,
            "next_step": "blueprint-mapping",
            "reviewed_at": utc_now(),
        }
        return validate_local_improvement_review(result)


def validate_local_improvement_review(value: dict[str, Any]) -> dict[str, Any]:
    """Fail closed when local review drifts from Blueprint and evidence boundaries."""
    if value.get("record_type") != "local-improvement-review":
        raise SelfImprovementError("Local improvement review type is invalid")
    if value.get("status") not in {"candidate", "no-change"}:
        raise SelfImprovementError("Local improvement review status is invalid")
    routes = value.get("research_routes")
    if not isinstance(routes, list) or [
        (item.get("action_id"), item.get("effort_share")) for item in routes
    ] != RESEARCH_ROUTES:
        raise SelfImprovementError("Local review must preserve Curiosity 60/20/20")
    for observation in value.get("observations", []):
        if not observation.get("evidence_ids") or not observation.get("summary"):
            raise SelfImprovementError("Local review observations require evidence")
    changes = value.get("candidate_changes")
    if not isinstance(changes, list):
        raise SelfImprovementError("Local review candidate changes are invalid")
    if value["status"] == "candidate" and not changes:
        raise SelfImprovementError("Candidate review requires a candidate change")
    if value["status"] == "no-change" and changes:
        raise SelfImprovementError("No-change review cannot contain candidate changes")
    for change in changes:
        if change.get("action") not in SUBTRACTION_FIRST_ACTIONS:
            raise SelfImprovementError("Local review change action is invalid")
        if change.get("blueprint_handoff") != "map-implementations-to-blueprint":
            raise SelfImprovementError("Every local candidate must enter Blueprint Mapping")
        if not change.get("test_plan") or not change.get("recovery"):
            raise SelfImprovementError("Local candidate requires tests and recovery")
    if value.get("automatic_apply") is not False:
        raise SelfImprovementError("Local improvement review cannot apply itself")
    if value.get("next_step") != "blueprint-mapping":
        raise SelfImprovementError("Local improvement review must hand off to Blueprint Mapping")
    if value.get("runtime_dependency_on_upstream") is not False:
        raise SelfImprovementError("Local improvement cannot require donor runtime availability")
    if value.get("donor_selection_fixed") is not False:
        raise SelfImprovementError("Learning Review cannot freeze a permanent donor set")
    report = value.get("constraint_report")
    if not isinstance(report, dict) or not report.get("blind_spots"):
        raise SelfImprovementError("Local review requires a constraint and blind-spot report")
    return value


class MethodCandidateStore:
    """Persist immutable, locally runnable method candidates and constraint history."""

    def __init__(self, root: Path, *, fault_injector: Any | None = None) -> None:
        self.root = Path(root)
        self.candidates = self.root / "candidates"
        self.history = self.root / "constraint-history.jsonl"
        self.fault_injector = fault_injector

    def _inject(self, point: str) -> None:
        if self.fault_injector is not None:
            self.fault_injector(point)

    def stage(
        self,
        review: dict[str, Any],
        *,
        source_action_id: str,
    ) -> dict[str, Any] | None:
        review = validate_local_improvement_review(review)
        if review["status"] == "no-change":
            return None
        stable_input = {
            "source_action_id": source_action_id,
            "project_id": review["project_id"],
            "candidate_changes": review["candidate_changes"],
            "learning_method_ids": review["learning_method_ids"],
        }
        candidate_id = f"candidate-method-{value_sha256(stable_input)[:20]}"
        candidate_dir = self.candidates / candidate_id
        manifest_path = candidate_dir / "manifest.json"
        if manifest_path.exists():
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            validate_improvement_candidate(existing, candidate_dir=candidate_dir)
            stored_review = json.loads(
                (candidate_dir / "review.json").read_text(encoding="utf-8")
            )
            self._append_constraint_history(
                candidate_id, stored_review["constraint_report"]
            )
            return existing

        change = review["candidate_changes"][0]
        method = change["proposed_method"]
        skill_text = render_candidate_skill(method, review)
        skill_sha256 = hashlib.sha256(skill_text.encode("utf-8")).hexdigest()
        manifest = {
            "record_type": "improvement-method-candidate",
            "record_version": 1,
            "candidate_id": candidate_id,
            "status": "staged-not-active",
            "project_id": review["project_id"],
            "source_action_id": source_action_id,
            "method_name": method["name"],
            "change_action": change["action"],
            "source_evidence_ids": sorted(
                {
                    evidence_id
                    for observation in review["observations"]
                    for evidence_id in observation["evidence_ids"]
                }
            ),
            "learning_method_ids": review["learning_method_ids"],
            "donor_selection_fixed": False,
            "runtime_dependency_on_upstream": False,
            "skill_sha256": skill_sha256,
            "review_sha256": value_sha256(review),
            "test_plan": change["test_plan"],
            "tests": {
                "portable_name": True,
                "required_sections": True,
                "source_evidence_linked": True,
                "integrity_verified": True,
            },
            "automatic_apply": False,
            "blueprint_mapping_required": True,
            "next_step": "map-implementations-to-blueprint",
            "recovery": change["recovery"],
            "constraint_report": review["constraint_report"],
            "staged_at": utc_now(),
            "manifest_sha256": None,
        }
        manifest["manifest_sha256"] = value_sha256(
            {key: item for key, item in manifest.items() if key != "manifest_sha256"}
        )
        self.candidates.mkdir(parents=True, exist_ok=True)
        temporary_dir = Path(
            tempfile.mkdtemp(prefix=f".{candidate_id}.", dir=self.candidates)
        )
        try:
            self._atomic_write(temporary_dir / "SKILL.md", skill_text.encode("utf-8"))
            self._atomic_write(
                temporary_dir / "review.json",
                (
                    json.dumps(review, ensure_ascii=False, indent=2, sort_keys=True)
                    + "\n"
                ).encode("utf-8"),
            )
            self._atomic_write(
                temporary_dir / "manifest.json",
                (
                    json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
                    + "\n"
                ).encode("utf-8"),
            )
            validate_improvement_candidate(manifest, candidate_dir=temporary_dir)
            self._inject("after-candidate-files-before-publish")
            if candidate_dir.exists():
                shutil.rmtree(temporary_dir)
                existing = json.loads(manifest_path.read_text(encoding="utf-8"))
                validate_improvement_candidate(existing, candidate_dir=candidate_dir)
                return existing
            temporary_dir.replace(candidate_dir)
        except Exception:
            if temporary_dir.exists():
                shutil.rmtree(temporary_dir)
            raise
        self._append_constraint_history(candidate_id, review["constraint_report"])
        return manifest

    def read(self, candidate_id: str) -> dict[str, Any]:
        path = self.candidates / candidate_id / "manifest.json"
        if not path.exists():
            raise SelfImprovementError(f"Unknown improvement candidate: {candidate_id}")
        value = json.loads(path.read_text(encoding="utf-8"))
        return validate_improvement_candidate(value, candidate_dir=path.parent)

    def candidate_path(self, candidate_id: str) -> Path:
        return self.candidates / candidate_id

    def recent_constraint_reports(self, limit: int = 5) -> list[dict[str, Any]]:
        if not self.history.exists():
            return []
        records = [
            json.loads(line)
            for line in self.history.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return [item["constraint_report"] for item in records[-max(1, limit) :]]

    def _append_constraint_history(
        self, candidate_id: str, constraint_report: dict[str, Any]
    ) -> None:
        self.history.parent.mkdir(parents=True, exist_ok=True)
        existing = []
        if self.history.exists():
            existing = [
                json.loads(line)
                for line in self.history.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        if any(item.get("candidate_id") == candidate_id for item in existing):
            return
        record = {
            "candidate_id": candidate_id,
            "constraint_report": constraint_report,
            "recorded_at": utc_now(),
        }
        with self.history.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
            temporary_path.replace(path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise


def validate_improvement_candidate(
    value: dict[str, Any], *, candidate_dir: Path
) -> dict[str, Any]:
    """Validate a complete local method candidate and its files."""
    if value.get("record_type") != "improvement-method-candidate":
        raise SelfImprovementError("Improvement candidate type is invalid")
    if value.get("status") != "staged-not-active":
        raise SelfImprovementError("Improvement candidate must remain staged and inactive")
    if value.get("runtime_dependency_on_upstream") is not False:
        raise SelfImprovementError("Improvement candidate cannot require upstream runtime")
    if value.get("donor_selection_fixed") is not False:
        raise SelfImprovementError("Improvement candidate cannot freeze a donor set")
    if value.get("automatic_apply") is not False:
        raise SelfImprovementError("Improvement candidate cannot apply itself")
    if value.get("next_step") != "map-implementations-to-blueprint":
        raise SelfImprovementError("Improvement candidate must enter Blueprint Mapping")
    expected = value_sha256(
        {key: item for key, item in value.items() if key != "manifest_sha256"}
    )
    if value.get("manifest_sha256") != expected:
        raise SelfImprovementError("Improvement candidate manifest integrity failure")
    skill_path = candidate_dir / "SKILL.md"
    review_path = candidate_dir / "review.json"
    if not skill_path.is_file() or not review_path.is_file():
        raise SelfImprovementError("Improvement candidate files are incomplete")
    if hashlib.sha256(skill_path.read_bytes()).hexdigest() != value.get("skill_sha256"):
        raise SelfImprovementError("Improvement candidate Skill integrity failure")
    return value


def render_candidate_skill(method: dict[str, Any], review: dict[str, Any]) -> str:
    """Render a portable candidate Skill that carries procedure, proof and limits."""
    name = str(method.get("name") or "")
    if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) is None:
        raise SelfImprovementError("Candidate method name must use portable kebab-case")
    steps = [str(item).strip() for item in method.get("steps", []) if str(item).strip()]
    if not steps:
        raise SelfImprovementError("Candidate method requires steps")
    preflight = [str(item).strip() for item in method.get("preflight", []) if str(item).strip()]
    verification = [
        str(item).strip() for item in method.get("verification", []) if str(item).strip()
    ]
    blind_spots = [
        str(item).strip()
        for item in method.get("blind_spots_to_check", [])
        if str(item).strip()
    ]
    lines = [
        "---",
        f"name: {name}",
        f"description: Use when {method.get('trigger', 'matching work')} recurs.",
        "metadata:",
        '  status: "candidate-not-active"',
        '  authority: "none"',
        "---",
        "",
        f"# {name.replace('-', ' ').title()}",
        "",
        "This is a staged method candidate generated from verified repeated work. It does not "
        "become active until Blueprint Mapping, review and Carson's later decision.",
        "",
        "## Pre-flight",
        "",
    ]
    lines.extend(f"- {item}" for item in preflight)
    lines.extend(["", "## Procedure", ""])
    lines.extend(f"{index}. {item}" for index, item in enumerate(steps, 1))
    lines.extend(["", "## Tools", ""])
    lines.extend(f"- `{item}`" for item in method.get("tools", []))
    lines.extend(["", "## Verification", ""])
    lines.extend(f"- {item}" for item in verification)
    lines.extend(["", "## Blind spots to check", ""])
    lines.extend(f"- {item}" for item in blind_spots)
    lines.extend(
        [
            "",
            "## Evidence",
            "",
            *[
                f"- `{evidence_id}`"
                for observation in review["observations"]
                for evidence_id in observation["evidence_ids"]
            ],
            "",
            "## Recovery",
            "",
            "Reject this candidate and retain the current method. No active capability depends "
            "on this file.",
            "",
        ]
    )
    return "\n".join(lines)


def _method_name(current_method: dict[str, Any]) -> str:
    raw = str(current_method.get("work_type") or "repeated-work").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-") or "repeated-work"
    input_shape = str(current_method.get("input_shape") or "")
    shape_digest = input_shape.rsplit("-", 1)[-1]
    suffix = f"-{shape_digest[:8]}" if re.fullmatch(r"[a-f0-9]{20}", shape_digest) else ""
    return f"improve-{slug}{suffix}"


def _method_steps(current_method: dict[str, Any]) -> list[str]:
    steps = [str(item).strip() for item in current_method.get("steps", []) if str(item).strip()]
    if not steps:
        steps = ["Perform the matching work using the current verified method."]
    return [
        *steps,
        "Record the actual outcome and compact Work Signature.",
        "If the same avoidable work recurs, return evidence to Perspective rather than "
        "silently repeating it.",
    ]
