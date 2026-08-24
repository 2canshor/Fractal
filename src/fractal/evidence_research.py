"""Perspective-guided, provenance-first evidence exploration for Cause Research."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from fractal.storage import value_sha256
from fractal.system_review import IndependentBranch, verify_branch_independence


class EvidenceExplorationError(RuntimeError):
    """Raised when research breadth or provenance is incomplete."""


@dataclass(frozen=True, slots=True)
class ExplorationQuestion:
    question_id: str
    perspective: str
    query: str
    source_scope: str

    def to_dict(self) -> dict[str, str]:
        value = asdict(self)
        if any(not str(item).strip() for item in value.values()):
            raise EvidenceExplorationError("Exploration question fields cannot be empty")
        if self.source_scope not in {"external", "internal"}:
            raise EvidenceExplorationError("Exploration question source scope is invalid")
        return value


Retriever = Callable[[ExplorationQuestion], list[dict[str, Any]]]
BranchReasoner = Callable[[str, list[dict[str, Any]]], dict[str, Any]]
CauseReconciler = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


def build_evidence_exploration_plan(
    *,
    topic: str,
    questions: list[ExplorationQuestion],
    maximum_questions: int,
) -> dict[str, Any]:
    """Freeze broad perspective questions before retrieval or later filtering."""
    if not topic.strip():
        raise EvidenceExplorationError("Evidence Exploration requires a topic")
    if maximum_questions < 2:
        raise EvidenceExplorationError("Evidence Exploration budget must allow breadth")
    records = [question.to_dict() for question in questions]
    if len(records) < 2 or len(records) > maximum_questions:
        raise EvidenceExplorationError("Evidence Exploration question count violates its budget")
    question_ids = [record["question_id"] for record in records]
    if len(question_ids) != len(set(question_ids)):
        raise EvidenceExplorationError("Evidence Exploration question ids must be unique")
    perspectives = {record["perspective"] for record in records}
    scopes = {record["source_scope"] for record in records}
    if len(perspectives) < 2:
        raise EvidenceExplorationError("Evidence Exploration requires multiple perspectives")
    if scopes != {"external", "internal"}:
        raise EvidenceExplorationError("Cause Research requires external and internal questions")
    return {
        "record_type": "evidence-exploration-plan",
        "record_version": 1,
        "topic": topic.strip(),
        "collection_mode": "quantity-over-quality",
        "perspective_count": len(perspectives),
        "maximum_questions": maximum_questions,
        "questions": records,
        "deduplication": "deferred-until-after-harvest",
        "relevance_filtering": "deferred-until-after-harvest",
        "automatic_conclusion": False,
    }


def run_evidence_exploration(
    plan: dict[str, Any],
    *,
    retriever: Retriever,
) -> dict[str, Any]:
    """Retrieve every planned question and retain raw duplicates and No Finding."""
    if plan.get("record_type") != "evidence-exploration-plan":
        raise EvidenceExplorationError("Evidence Exploration plan identity is invalid")
    if (
        plan.get("collection_mode") != "quantity-over-quality"
        or plan.get("deduplication") != "deferred-until-after-harvest"
        or plan.get("relevance_filtering") != "deferred-until-after-harvest"
    ):
        raise EvidenceExplorationError("Evidence Exploration cannot filter before harvest")
    findings = []
    question_receipts = []
    for raw_question in plan["questions"]:
        question = ExplorationQuestion(**raw_question)
        retrieved = retriever(question)
        if not isinstance(retrieved, list):
            raise EvidenceExplorationError("Evidence retriever must return a list")
        if not retrieved:
            question_receipts.append(
                {
                    "question_id": question.question_id,
                    "status": "no-finding",
                    "reason": "The registered retriever returned no source.",
                }
            )
            continue
        question_receipts.append(
            {
                "question_id": question.question_id,
                "status": "finding",
                "finding_count": len(retrieved),
            }
        )
        for position, finding in enumerate(retrieved):
            required = {
                "source_id",
                "source",
                "summary",
                "observed_at",
                "evidence_id",
            }
            if not isinstance(finding, dict) or any(
                not str(finding.get(field, "")).strip() for field in required
            ):
                raise EvidenceExplorationError("Research finding provenance is incomplete")
            findings.append(
                {
                    **finding,
                    "question_id": question.question_id,
                    "perspective": question.perspective,
                    "source_scope": question.source_scope,
                    "raw_position": position,
                    "retained_before_filtering": True,
                }
            )
    evidence_ids = [finding["evidence_id"] for finding in findings]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise EvidenceExplorationError("Research findings require unique evidence ids")
    return {
        "record_type": "evidence-exploration-result",
        "record_version": 1,
        "topic": plan["topic"],
        "collection_mode": "quantity-over-quality",
        "question_receipts": question_receipts,
        "raw_findings": findings,
        "raw_finding_count": len(findings),
        "deduplicated": False,
        "relevance_filtered": False,
        "independent_source_scopes_retained": True,
        "automatic_conclusion": False,
        "hands_off_to": "cause-research",
        "recovery": "Discard the exploration result; no canonical conclusion was changed.",
    }


def exploration_result_as_collector(
    result: dict[str, Any],
) -> Callable[[str, dict[str, Any]], list[dict[str, Any]]]:
    """Expose one frozen exploration result to the System Review investigator."""

    def collect(stage: str, _context: dict[str, Any]) -> list[dict[str, Any]]:
        if stage != "cause-research":
            return []
        return [
            {
                "evidence_id": finding["evidence_id"],
                "source": finding["source"],
                "payload": finding,
            }
            for finding in result["raw_findings"]
        ]

    return collect


def run_independent_cause_research(
    exploration: dict[str, Any],
    *,
    branch_reasoner: BranchReasoner,
    reconciler: CauseReconciler,
) -> dict[str, Any]:
    """Run isolated external and internal reports before their first reconciliation."""
    if exploration.get("record_type") != "evidence-exploration-result":
        raise EvidenceExplorationError("Cause Research requires an exploration result")
    by_scope = {
        scope: [
            finding
            for finding in exploration["raw_findings"]
            if finding["source_scope"] == scope
        ]
        for scope in ("external", "internal")
    }
    if any(not findings for findings in by_scope.values()):
        raise EvidenceExplorationError(
            "Independent Cause Research requires findings in both source scopes"
        )
    reports = {}
    branches = []
    all_evidence_ids = {
        finding["evidence_id"] for finding in exploration["raw_findings"]
    }
    for scope, findings in by_scope.items():
        role = "external-research" if scope == "external" else "internal-review"
        report = branch_reasoner(role, findings)
        used_evidence_ids = report.get("evidence_ids")
        available = {finding["evidence_id"] for finding in findings}
        if (
            not str(report.get("summary", "")).strip()
            or not isinstance(used_evidence_ids, list)
            or not used_evidence_ids
            or not set(used_evidence_ids).issubset(available)
        ):
            raise EvidenceExplorationError(
                f"Cause Research branch used unavailable evidence: {role}"
            )
        context_sha256 = value_sha256(
            {
                "role": role,
                "evidence_ids": [finding["evidence_id"] for finding in findings],
                "question_ids": [finding["question_id"] for finding in findings],
            }
        )
        output_artifact_id = f"cause-report:{value_sha256(report)}"
        reports[role] = {
            **report,
            "role": role,
            "output_artifact_id": output_artifact_id,
            "initial_context_sha256": context_sha256,
        }
        branches.append(
            IndependentBranch(
                branch_id=f"branch:{role}",
                role=role,
                initial_context_sha256=context_sha256,
                input_artifact_ids=tuple(used_evidence_ids),
                output_artifact_id=output_artifact_id,
                source_ids=tuple(
                    finding["source_id"]
                    for finding in findings
                    if finding["evidence_id"] in used_evidence_ids
                ),
                selected_agent_id=str(report.get("reasoner_id", "registered-reasoner")),
                result_summary=report["summary"],
            )
        )
    independence = verify_branch_independence(
        branches,
        required_roles={"external-research", "internal-review"},
    )
    reconciliation = reconciler(
        reports["external-research"], reports["internal-review"]
    )
    if (
        not isinstance(reconciliation.get("agreements"), list)
        or not isinstance(reconciliation.get("disagreements"), list)
        or not str(reconciliation.get("local_diagnosis", "")).strip()
        or not reconciliation.get("evidence_ids")
        or not set(reconciliation["evidence_ids"]).issubset(all_evidence_ids)
    ):
        raise EvidenceExplorationError("Cause Research reconciliation is incomplete")
    return {
        "record_type": "independent-cause-research",
        "record_version": 1,
        "cause_research_result": {
            "status": "completed",
            "independent_branches_verified": independence["independent"],
            "external_research_artifact_id": reports["external-research"][
                "output_artifact_id"
            ],
            "internal_review_artifact_id": reports["internal-review"][
                "output_artifact_id"
            ],
            "branch_reports": reports,
        },
        "reconciliation_result": {
            "status": "completed",
            "agreements": reconciliation["agreements"],
            "disagreements": reconciliation["disagreements"],
            "local_diagnosis": reconciliation["local_diagnosis"],
            "evidence_ids": reconciliation["evidence_ids"],
        },
        "automatic_conclusion": False,
        "automatic_change": False,
        "recovery": "Discard both branch reports and reconciliation; raw evidence remains.",
    }
