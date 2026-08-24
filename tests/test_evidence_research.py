from __future__ import annotations

import pytest

from fractal.evidence_research import (
    EvidenceExplorationError,
    ExplorationQuestion,
    build_evidence_exploration_plan,
    exploration_result_as_collector,
    run_evidence_exploration,
    run_independent_cause_research,
)


def questions() -> list[ExplorationQuestion]:
    return [
        ExplorationQuestion(
            "external-structure",
            "structural alternative",
            "current open source orchestration evidence",
            "external",
        ),
        ExplorationQuestion(
            "external-counterexample",
            "failure and counterexample",
            "orchestration failure recovery evidence",
            "external",
        ),
        ExplorationQuestion(
            "internal-history",
            "Fractal history",
            "prior Project reversals and outcomes",
            "internal",
        ),
    ]


def test_plan_requires_breadth_and_both_source_scopes() -> None:
    plan = build_evidence_exploration_plan(
        topic="Why does the System Review stop between Flows?",
        questions=questions(),
        maximum_questions=6,
    )
    assert plan["collection_mode"] == "quantity-over-quality"
    assert plan["perspective_count"] == 3
    assert plan["deduplication"] == "deferred-until-after-harvest"

    with pytest.raises(EvidenceExplorationError, match="multiple perspectives"):
        build_evidence_exploration_plan(
            topic="One view only",
            questions=[
                ExplorationQuestion("a", "same", "external query", "external"),
                ExplorationQuestion("b", "same", "internal query", "internal"),
            ],
            maximum_questions=2,
        )


def test_harvest_retains_duplicates_and_no_finding_before_later_filtering() -> None:
    plan = build_evidence_exploration_plan(
        topic="Why does the System Review stop between Flows?",
        questions=questions(),
        maximum_questions=6,
    )

    def retrieve(question: ExplorationQuestion) -> list[dict]:
        if question.question_id == "internal-history":
            return []
        return [
            {
                "source_id": "same-source",
                "source": "https://example.test/primary-source",
                "summary": f"Raw result for {question.question_id}",
                "observed_at": "2026-08-24",
                "evidence_id": f"evidence-{question.question_id}",
            }
        ]

    result = run_evidence_exploration(plan, retriever=retrieve)
    assert result["raw_finding_count"] == 2
    assert [finding["source_id"] for finding in result["raw_findings"]] == [
        "same-source",
        "same-source",
    ]
    assert result["deduplicated"] is False
    assert result["relevance_filtered"] is False
    assert result["question_receipts"][-1]["status"] == "no-finding"
    assert result["automatic_conclusion"] is False


def test_exploration_result_becomes_read_only_cause_research_evidence() -> None:
    plan = build_evidence_exploration_plan(
        topic="Cause",
        questions=questions(),
        maximum_questions=3,
    )

    def retrieve(question: ExplorationQuestion) -> list[dict]:
        return [
            {
                "source_id": question.question_id,
                "source": "canonical-or-primary-source",
                "summary": "Observed evidence",
                "observed_at": "2026-08-24",
                "evidence_id": f"evidence-{question.question_id}",
            }
        ]

    result = run_evidence_exploration(plan, retriever=retrieve)
    collector = exploration_result_as_collector(result)
    assert collector("find-problems", {}) == []
    collected = collector("cause-research", {})
    assert len(collected) == 3
    assert {item["evidence_id"] for item in collected} == {
        "evidence-external-structure",
        "evidence-external-counterexample",
        "evidence-internal-history",
    }


def test_exploration_rejects_missing_provenance() -> None:
    plan = build_evidence_exploration_plan(
        topic="Cause",
        questions=questions(),
        maximum_questions=3,
    )
    with pytest.raises(EvidenceExplorationError, match="provenance"):
        run_evidence_exploration(
            plan,
            retriever=lambda _question: [{"summary": "No source"}],
        )


def test_cause_research_runs_isolated_reports_before_reconciliation() -> None:
    plan = build_evidence_exploration_plan(
        topic="Cause",
        questions=questions(),
        maximum_questions=3,
    )

    def retrieve(question: ExplorationQuestion) -> list[dict]:
        return [
            {
                "source_id": question.question_id,
                "source": "canonical-or-primary-source",
                "summary": "Observed evidence",
                "observed_at": "2026-08-24",
                "evidence_id": f"evidence-{question.question_id}",
            }
        ]

    exploration = run_evidence_exploration(plan, retriever=retrieve)
    observed_roles = []

    def branch_reasoner(role: str, findings: list[dict]) -> dict:
        observed_roles.append(role)
        assert all(
            finding["source_scope"] == ("external" if role == "external-research" else "internal")
            for finding in findings
        )
        return {
            "summary": f"Independent {role} report",
            "evidence_ids": [finding["evidence_id"] for finding in findings],
            "reasoner_id": role,
        }

    def reconcile(external: dict, internal: dict) -> dict:
        assert external["role"] == "external-research"
        assert internal["role"] == "internal-review"
        return {
            "agreements": ["Both observed an orchestration gap."],
            "disagreements": ["They assign different causal weight."],
            "local_diagnosis": "The runtime validates stages but needs evidence travel.",
            "evidence_ids": [
                "evidence-external-structure",
                "evidence-internal-history",
            ],
        }

    result = run_independent_cause_research(
        exploration,
        branch_reasoner=branch_reasoner,
        reconciler=reconcile,
    )
    assert observed_roles == ["external-research", "internal-review"]
    assert result["cause_research_result"]["independent_branches_verified"] is True
    assert result["reconciliation_result"]["status"] == "completed"
    assert result["automatic_change"] is False


def test_cause_research_branch_cannot_read_the_other_scope() -> None:
    plan = build_evidence_exploration_plan(
        topic="Cause",
        questions=questions(),
        maximum_questions=3,
    )

    def retrieve(question: ExplorationQuestion) -> list[dict]:
        return [
            {
                "source_id": question.question_id,
                "source": "source",
                "summary": "Evidence",
                "observed_at": "2026-08-24",
                "evidence_id": f"evidence-{question.question_id}",
            }
        ]

    exploration = run_evidence_exploration(plan, retriever=retrieve)

    def contaminated(role: str, findings: list[dict]) -> dict:
        evidence_ids = [finding["evidence_id"] for finding in findings]
        if role == "external-research":
            evidence_ids.append("evidence-internal-history")
        return {"summary": "Contaminated", "evidence_ids": evidence_ids}

    with pytest.raises(EvidenceExplorationError, match="unavailable evidence"):
        run_independent_cause_research(
            exploration,
            branch_reasoner=contaminated,
            reconciler=lambda _external, _internal: {},
        )
