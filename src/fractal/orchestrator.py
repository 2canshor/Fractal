"""Durable lifecycle arrows for Fractal's existing review architecture.

The runtime coordinates deterministic state transitions and delegates only the
evidence-producing work to an investigator.  It does not own Project or System
Review authority.  Its execution ledger adapts the claimed/running/terminal
mechanic from Hermes Agent's cron execution ledger; see THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fractal.improvement import (
    RepetitionRecognition,
    WorkSignature,
    activate_value_behavior,
    curiosity_routes,
)
from fractal.lifecycle import LifecycleController
from fractal.models import utc_now
from fractal.storage import ProjectStore, value_sha256
from fractal.system_review import (
    SYSTEM_REVIEW_STAGES,
    record_system_review_stage,
    start_system_review,
)

TERMINAL_EXECUTION_STATES = {"completed", "failed", "unknown"}
TERMINAL_ACTION_STATES = {"completed", "failed", "unknown", "awaiting-human"}


class OrchestrationError(RuntimeError):
    """Raised when a durable lifecycle arrow cannot advance safely."""


class OrchestrationStore:
    """SQLite execution, action and System Review state with terminal immutability."""

    def __init__(
        self,
        path: Path,
        *,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._process_id = uuid.uuid4().hex
        self.fault_injector = fault_injector

    def _inject(self, point: str) -> None:
        if self.fault_injector is not None:
            self.fault_injector(point)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            connection = sqlite3.connect(self.path, timeout=5)
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("PRAGMA busy_timeout=5000")
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("PRAGMA foreign_keys=ON")
                self._ensure_schema(connection)
                with connection:
                    yield connection
            finally:
                connection.close()

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS executions (
                execution_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                execution_kind TEXT NOT NULL,
                project_id TEXT NOT NULL,
                project_revision INTEGER NOT NULL,
                process_id TEXT NOT NULL,
                pid INTEGER NOT NULL,
                status TEXT NOT NULL CHECK(status IN
                    ('claimed','running','completed','failed','unknown')),
                claimed_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                result_json TEXT,
                error TEXT
            );
            CREATE TABLE IF NOT EXISTS actions (
                action_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                execution_id TEXT NOT NULL,
                action_kind TEXT NOT NULL,
                project_id TEXT NOT NULL,
                review_id TEXT,
                stage TEXT,
                status TEXT NOT NULL CHECK(status IN
                    ('ready','running','completed','failed','unknown','awaiting-human')),
                owner_process_id TEXT,
                owner_pid INTEGER,
                payload_json TEXT NOT NULL,
                result_json TEXT,
                evidence_ids_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                error TEXT,
                FOREIGN KEY(execution_id) REFERENCES executions(execution_id)
            );
            CREATE TABLE IF NOT EXISTS system_reviews (
                review_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                project_snapshot_sha256 TEXT NOT NULL UNIQUE,
                state_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_actions_status_created
                ON actions(status, created_at, action_id);
            CREATE INDEX IF NOT EXISTS idx_reviews_project
                ON system_reviews(project_id, created_at DESC);
            """
        )
        action_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(actions)").fetchall()
        }
        if "owner_process_id" not in action_columns:
            connection.execute("ALTER TABLE actions ADD COLUMN owner_process_id TEXT")
        if "owner_pid" not in action_columns:
            connection.execute("ALTER TABLE actions ADD COLUMN owner_pid INTEGER")

    def claim_execution(
        self,
        *,
        idempotency_key: str,
        execution_kind: str,
        project_id: str,
        project_revision: int,
    ) -> tuple[dict[str, Any], bool]:
        """Persist an execution claim before dispatch, or return the existing claim."""
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM executions WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                return dict(existing), False
            execution_id = f"execution-{uuid.uuid4()}"
            connection.execute(
                """INSERT INTO executions(
                       execution_id, idempotency_key, execution_kind, project_id,
                       project_revision, process_id, pid, status, claimed_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, 'claimed', ?)""",
                (
                    execution_id,
                    idempotency_key,
                    execution_kind,
                    project_id,
                    project_revision,
                    self._process_id,
                    os.getpid(),
                    utc_now(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM executions WHERE execution_id=?", (execution_id,)
            ).fetchone()
            return dict(row), True

    def mark_execution_running(self, execution_id: str) -> dict[str, Any]:
        with self.transaction() as connection:
            changed = connection.execute(
                """UPDATE executions SET status='running', started_at=?
                   WHERE execution_id=? AND status='claimed'""",
                (utc_now(), execution_id),
            ).rowcount
            if changed != 1:
                raise OrchestrationError("Execution is not claimable")
            return dict(
                connection.execute(
                    "SELECT * FROM executions WHERE execution_id=?", (execution_id,)
                ).fetchone()
            )

    def latest_execution(self, idempotency_prefix: str) -> dict[str, Any] | None:
        """Return the latest base or compensation attempt for one logical arrow."""
        with self.transaction() as connection:
            row = connection.execute(
                """SELECT * FROM executions
                   WHERE idempotency_key=? OR idempotency_key LIKE ?
                   ORDER BY claimed_at DESC, execution_id DESC LIMIT 1""",
                (idempotency_prefix, f"{idempotency_prefix}:reconcile:%"),
            ).fetchone()
            return dict(row) if row is not None else None

    def finish_execution(
        self,
        execution_id: str,
        *,
        success: bool,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        status = "completed" if success else "failed"
        with self.transaction() as connection:
            changed = connection.execute(
                """UPDATE executions
                   SET status=?, finished_at=?, result_json=?, error=?
                   WHERE execution_id=? AND status IN ('claimed','running')""",
                (
                    status,
                    utc_now(),
                    json.dumps(result, ensure_ascii=False, sort_keys=True)
                    if result is not None
                    else None,
                    None if success else (error or "unknown failure"),
                    execution_id,
                ),
            ).rowcount
            if changed != 1:
                raise OrchestrationError("Terminal execution state is immutable")
            return dict(
                connection.execute(
                    "SELECT * FROM executions WHERE execution_id=?", (execution_id,)
                ).fetchone()
            )

    def create_action(
        self,
        *,
        execution_id: str,
        idempotency_key: str,
        action_kind: str,
        project_id: str,
        payload: dict[str, Any],
        review_id: str | None = None,
        stage: str | None = None,
        status: str = "ready",
    ) -> tuple[dict[str, Any], bool]:
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM actions WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if existing is not None:
                return dict(existing), False
            action_id = f"action-{uuid.uuid4()}"
            connection.execute(
                """INSERT INTO actions(
                       action_id, idempotency_key, execution_id, action_kind,
                       project_id, review_id, stage, status, payload_json, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    action_id,
                    idempotency_key,
                    execution_id,
                    action_kind,
                    project_id,
                    review_id,
                    stage,
                    status,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    utc_now(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM actions WHERE action_id=?", (action_id,)
            ).fetchone()
            return dict(row), True

    def next_ready_action(self, *, review_id: str | None = None) -> dict[str, Any] | None:
        where = "WHERE status='ready'"
        parameters: tuple[Any, ...] = ()
        if review_id is not None:
            where += " AND review_id=?"
            parameters = (review_id,)
        with self.transaction() as connection:
            row = connection.execute(
                f"SELECT * FROM actions {where} ORDER BY created_at, action_id LIMIT 1",
                parameters,
            ).fetchone()
            return dict(row) if row is not None else None

    def latest_action(self, review_id: str) -> dict[str, Any] | None:
        with self.transaction() as connection:
            row = connection.execute(
                """SELECT * FROM actions WHERE review_id=?
                   ORDER BY created_at DESC, action_id DESC LIMIT 1""",
                (review_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    def read_action(self, action_id: str) -> dict[str, Any]:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM actions WHERE action_id=?", (action_id,)
            ).fetchone()
            if row is None:
                raise OrchestrationError(f"Unknown action: {action_id}")
            return dict(row)

    def mark_action_running(self, action_id: str) -> dict[str, Any]:
        with self.transaction() as connection:
            changed = connection.execute(
                """UPDATE actions
                   SET status='running', started_at=?, owner_process_id=?, owner_pid=?
                   WHERE action_id=? AND status='ready'""",
                (utc_now(), self._process_id, os.getpid(), action_id),
            ).rowcount
            if changed != 1:
                raise OrchestrationError("Action is not ready")
            return dict(
                connection.execute(
                    "SELECT * FROM actions WHERE action_id=?", (action_id,)
                ).fetchone()
            )

    def finish_action(
        self,
        action_id: str,
        *,
        success: bool,
        result: dict[str, Any] | None = None,
        evidence_ids: list[str] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        status = "completed" if success else "failed"
        with self.transaction() as connection:
            changed = connection.execute(
                """UPDATE actions
                   SET status=?, result_json=?, evidence_ids_json=?, finished_at=?, error=?
                   WHERE action_id=? AND status='running'""",
                (
                    status,
                    json.dumps(result, ensure_ascii=False, sort_keys=True)
                    if result is not None
                    else None,
                    json.dumps(evidence_ids or [], ensure_ascii=False),
                    utc_now(),
                    None if success else (error or "unknown failure"),
                    action_id,
                ),
            ).rowcount
            if changed != 1:
                raise OrchestrationError("Terminal action state is immutable")
            return dict(
                connection.execute(
                    "SELECT * FROM actions WHERE action_id=?", (action_id,)
                ).fetchone()
            )

    def commit_action_with_successor(
        self,
        *,
        action_id: str,
        result: dict[str, Any],
        evidence_ids: list[str],
        successor_kind: str,
        successor_stage: str | None,
        successor_payload: dict[str, Any],
        successor_idempotency_key: str,
    ) -> dict[str, Any]:
        """Complete one action and create its next bounded action atomically."""
        now = utc_now()
        successor_id = f"action-{uuid.uuid4()}"
        with self.transaction() as connection:
            current = connection.execute(
                "SELECT * FROM actions WHERE action_id=?", (action_id,)
            ).fetchone()
            if current is None or current["status"] != "running":
                raise OrchestrationError("Learning action is not running")
            connection.execute(
                """UPDATE actions
                   SET status='completed', result_json=?, evidence_ids_json=?,
                       finished_at=?, error=NULL
                   WHERE action_id=? AND status='running'""",
                (
                    json.dumps(result, ensure_ascii=False, sort_keys=True),
                    json.dumps(evidence_ids, ensure_ascii=False),
                    now,
                    action_id,
                ),
            )
            self._inject("after-learning-result-before-successor")
            existing = connection.execute(
                "SELECT * FROM actions WHERE idempotency_key=?",
                (successor_idempotency_key,),
            ).fetchone()
            if existing is not None:
                return dict(existing)
            connection.execute(
                """INSERT INTO actions(
                       action_id, idempotency_key, execution_id, action_kind,
                       project_id, review_id, stage, status, payload_json, created_at
                   ) VALUES (?, ?, ?, ?, ?, NULL, ?, 'ready', ?, ?)""",
                (
                    successor_id,
                    successor_idempotency_key,
                    current["execution_id"],
                    successor_kind,
                    current["project_id"],
                    successor_stage,
                    json.dumps(successor_payload, ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM actions WHERE action_id=?", (successor_id,)
            ).fetchone()
            return dict(row)

    def save_review(self, review: dict[str, Any]) -> None:
        now = utc_now()
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT project_snapshot_sha256 FROM system_reviews WHERE review_id=?",
                (review["review_id"],),
            ).fetchone()
            if existing is not None and (
                existing["project_snapshot_sha256"] != review["project_snapshot_sha256"]
            ):
                raise OrchestrationError("System Review snapshot identity cannot change")
            connection.execute(
                """INSERT INTO system_reviews(
                       review_id, project_id, project_snapshot_sha256, state_json,
                       status, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(review_id) DO UPDATE SET
                       state_json=excluded.state_json,
                       status=excluded.status,
                       updated_at=excluded.updated_at""",
                (
                    review["review_id"],
                    review["project_id"],
                    review["project_snapshot_sha256"],
                    json.dumps(review, ensure_ascii=False, sort_keys=True),
                    review["status"],
                    review["started_at"],
                    now,
                ),
            )

    def start_review_with_action(
        self,
        review: dict[str, Any],
        *,
        execution_id: str,
        stage: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist a new review and its first executable stage atomically."""
        now = utc_now()
        idempotency_key = f"system-review:{review['review_id']}:{stage}"
        action_id = f"action-{uuid.uuid4()}"
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO system_reviews(
                       review_id, project_id, project_snapshot_sha256, state_json,
                       status, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    review["review_id"],
                    review["project_id"],
                    review["project_snapshot_sha256"],
                    json.dumps(review, ensure_ascii=False, sort_keys=True),
                    review["status"],
                    review["started_at"],
                    now,
                ),
            )
            self._inject("after-review-before-first-action")
            connection.execute(
                """INSERT INTO actions(
                       action_id, idempotency_key, execution_id, action_kind,
                       project_id, review_id, stage, status, payload_json, created_at
                   ) VALUES (?, ?, ?, 'system-review-stage', ?, ?, ?, 'ready', ?, ?)""",
                (
                    action_id,
                    idempotency_key,
                    execution_id,
                    review["project_id"],
                    review["review_id"],
                    stage,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM actions WHERE action_id=?", (action_id,)
            ).fetchone()
            return dict(row)

    def commit_review_stage(
        self,
        *,
        action_id: str,
        review: dict[str, Any],
        result: dict[str, Any],
        evidence_ids: list[str],
        next_stage: str | None,
        next_payload: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Persist review progress, terminal action and its successor atomically."""
        now = utc_now()
        with self.transaction() as connection:
            current = connection.execute(
                "SELECT * FROM actions WHERE action_id=?", (action_id,)
            ).fetchone()
            if current is None or current["status"] != "running":
                raise OrchestrationError("System Review action is not running")
            connection.execute(
                """UPDATE system_reviews
                   SET state_json=?, status=?, updated_at=?
                   WHERE review_id=? AND project_snapshot_sha256=?""",
                (
                    json.dumps(review, ensure_ascii=False, sort_keys=True),
                    review["status"],
                    now,
                    review["review_id"],
                    review["project_snapshot_sha256"],
                ),
            )
            self._inject("after-review-before-action-completion")
            connection.execute(
                """UPDATE actions
                   SET status='completed', result_json=?, evidence_ids_json=?,
                       finished_at=?, error=NULL
                   WHERE action_id=? AND status='running'""",
                (
                    json.dumps(result, ensure_ascii=False, sort_keys=True),
                    json.dumps(evidence_ids, ensure_ascii=False),
                    now,
                    action_id,
                ),
            )
            if next_stage is None:
                return None
            if next_payload is None:
                raise OrchestrationError("Next System Review stage requires a payload")
            self._inject("after-action-completion-before-next-action")
            next_action_id = f"action-{uuid.uuid4()}"
            connection.execute(
                """INSERT INTO actions(
                       action_id, idempotency_key, execution_id, action_kind,
                       project_id, review_id, stage, status, payload_json, created_at
                   ) VALUES (?, ?, ?, 'system-review-stage', ?, ?, ?, 'ready', ?, ?)""",
                (
                    next_action_id,
                    f"system-review:{review['review_id']}:{next_stage}",
                    current["execution_id"],
                    review["project_id"],
                    review["review_id"],
                    next_stage,
                    json.dumps(next_payload, ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM actions WHERE action_id=?", (next_action_id,)
            ).fetchone()
            return dict(row)

    def review_for_snapshot(self, snapshot_sha256: str) -> dict[str, Any] | None:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT state_json FROM system_reviews WHERE project_snapshot_sha256=?",
                (snapshot_sha256,),
            ).fetchone()
            return json.loads(row["state_json"]) if row is not None else None

    def read_review(self, review_id: str) -> dict[str, Any]:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT state_json FROM system_reviews WHERE review_id=?", (review_id,)
            ).fetchone()
            if row is None:
                raise OrchestrationError(f"Unknown System Review: {review_id}")
            return json.loads(row["state_json"])

    def recover_interrupted(self) -> dict[str, int]:
        """Mark only provably dead owners unknown; never infer side-effect success."""
        execution_count = 0
        action_count = 0
        with self.transaction() as connection:
            rows = connection.execute(
                "SELECT execution_id, pid, process_id FROM executions "
                "WHERE status IN ('claimed','running')"
            ).fetchall()
            for row in rows:
                if row["process_id"] == self._process_id:
                    continue
                try:
                    os.kill(int(row["pid"]), 0)
                except PermissionError:
                    continue
                except ProcessLookupError:
                    execution_count += connection.execute(
                        """UPDATE executions
                           SET status='unknown', finished_at=?, error=?
                           WHERE execution_id=? AND status IN ('claimed','running')""",
                        (
                            utc_now(),
                            "Owner exited before a durable terminal state; effects are unknown.",
                            row["execution_id"],
                        ),
                    ).rowcount
            action_rows = connection.execute(
                """SELECT action_id, owner_process_id, owner_pid FROM actions
                   WHERE status='running'"""
            ).fetchall()
            for row in action_rows:
                if row["owner_process_id"] == self._process_id:
                    continue
                if row["owner_pid"] is None:
                    continue
                try:
                    os.kill(int(row["owner_pid"]), 0)
                except PermissionError:
                    continue
                except ProcessLookupError:
                    action_count += connection.execute(
                        """UPDATE actions
                           SET status='unknown', finished_at=?, error=?
                           WHERE action_id=? AND status='running'""",
                        (
                            utc_now(),
                            "Action owner exited before a durable terminal state.",
                            row["action_id"],
                        ),
                    ).rowcount
        return {"executions": execution_count, "actions": action_count}


Investigator = Callable[[str, dict[str, Any]], tuple[dict[str, Any], list[str]]]
EvidenceCollector = Callable[[str, dict[str, Any]], list[dict[str, Any]]]
InvestigationReasoner = Callable[
    [str, dict[str, Any]], tuple[dict[str, Any], list[str]]
]


class CanonicalEvidenceInvestigator:
    """Acquire a canonical evidence bundle before asking one reasoner for judgement."""

    def __init__(
        self,
        reasoner: InvestigationReasoner,
        *,
        collectors: list[EvidenceCollector] | None = None,
    ) -> None:
        self.reasoner = reasoner
        self.collectors = list(collectors or [])

    def __call__(
        self,
        stage: str,
        context: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        project = context["project"]
        review = context["review"]
        snapshot_id = f"project-snapshot:{review['project_snapshot_sha256']}"
        artifacts = [
            {
                "evidence_id": snapshot_id,
                "source": "canonical-project-snapshot",
                "payload": {
                    "project_id": project["project_id"],
                    "revision": project["revision"],
                    "status": project["status"],
                    "lifecycle": project["lifecycle"],
                    "plan": project["plan"],
                    "progress": project["progress"],
                    "decisions": project["decisions"],
                    "evidence": project["evidence"],
                },
            }
        ]
        prior_results = {
            item["stage"]: item["result"] for item in review.get("stages", [])
        }
        collector_context = {
            "project": project,
            "review": review,
            "action": context["action"],
            "prior_results": prior_results,
        }
        for collector in self.collectors:
            collected = collector(stage, collector_context)
            if not isinstance(collected, list):
                raise OrchestrationError("Evidence collector must return a list")
            artifacts.extend(collected)
        evidence_ids = [artifact.get("evidence_id") for artifact in artifacts]
        if (
            any(not isinstance(item, str) or not item.strip() for item in evidence_ids)
            or len(evidence_ids) != len(set(evidence_ids))
            or any(not str(artifact.get("source", "")).strip() for artifact in artifacts)
        ):
            raise OrchestrationError("Investigator evidence bundle is incomplete or duplicated")
        bundle = {
            "record_type": "system-review-investigation-bundle",
            "record_version": 1,
            "stage": stage,
            "project_snapshot_sha256": review["project_snapshot_sha256"],
            "prior_results": prior_results,
            "artifacts": artifacts,
            "authority": "investigate-validate-persist-only",
        }
        result, used_evidence_ids = self.reasoner(stage, bundle)
        if not result:
            raise OrchestrationError("System Review reasoner returned no result")
        if not used_evidence_ids or not set(used_evidence_ids).issubset(set(evidence_ids)):
            raise OrchestrationError("System Review reasoner used unavailable evidence")
        return result, list(dict.fromkeys(used_evidence_ids))


class FractalOrchestrator:
    """Travel through existing Fractal arrows without acquiring their authority."""

    def __init__(self, project_store: ProjectStore, state_path: Path | None = None) -> None:
        self.project_store = project_store
        self.state = OrchestrationStore(
            state_path or (project_store.runtime_root / "orchestration" / "runtime.db")
        )

    def handle_fatigue(
        self,
        recognition: RepetitionRecognition,
        signature: WorkSignature,
        *,
        actor: str,
        platform: str,
    ) -> dict[str, Any]:
        """Turn a Fatigue trigger into a Perspective point and research action once."""
        if recognition.status != "investigation-required":
            raise OrchestrationError("Fatigue orchestration requires investigation-required")
        project = self.project_store.read(signature.project_id)
        idempotency_key = f"fatigue:{signature.project_id}:{signature.work_id}"
        execution, created = self.state.claim_execution(
            idempotency_key=idempotency_key,
            execution_kind="fatigue-to-perspective",
            project_id=signature.project_id,
            project_revision=project.revision,
        )
        if not created:
            execution = self.state.latest_execution(idempotency_key) or execution
            if execution["status"] == "completed":
                result = (
                    json.loads(execution["result_json"])
                    if execution["result_json"]
                    else None
                )
                return {"execution": execution, "result": result, "idempotent": True}
            if execution["status"] not in {"failed", "unknown"}:
                return {"execution": execution, "result": None, "idempotent": True}
            execution, created = self.state.claim_execution(
                idempotency_key=(
                    f"{idempotency_key}:reconcile:{execution['execution_id']}"
                ),
                execution_kind="fatigue-to-perspective-reconciliation",
                project_id=signature.project_id,
                project_revision=project.revision,
            )
            if not created:
                result = (
                    json.loads(execution["result_json"])
                    if execution["result_json"]
                    else None
                )
                return {"execution": execution, "result": result, "idempotent": True}
        self.state.mark_execution_running(execution["execution_id"])
        try:
            current = self.project_store.read(signature.project_id)
            matching_point = next(
                (
                    point
                    for point in current.lifecycle["review_points"]
                    if set(point.get("evidence_ids", []))
                    == set(recognition.evidence_work_ids)
                    and "Fatigue found repeated completed work" in point.get("reason", "")
                ),
                None,
            )
            if matching_point is None:
                review_write = LifecycleController(
                    self.project_store,
                    orchestrator=self,
                    auto_orchestrate_completion=False,
                ).record_review_point(
                    signature.project_id,
                    expected_revision=current.revision,
                    trigger="risk",
                    reason=(
                        "Fatigue found repeated completed work and requires whole-Project "
                        "Perspective before any local change."
                    ),
                    evidence_ids=list(recognition.evidence_work_ids),
                    actor=actor,
                    platform=platform,
                )
                project_revision = review_write.revision
            else:
                project_revision = current.revision
            value_activation = activate_value_behavior(
                "fatigue",
                trigger="verified-repetition",
                project_id=signature.project_id,
                project_status=current.status,
                evidence_ids=list(recognition.evidence_work_ids),
            )
            action, _ = self.state.create_action(
                execution_id=execution["execution_id"],
                idempotency_key=f"fatigue-research:{signature.project_id}:{signature.work_id}",
                action_kind="curiosity-implementation-research",
                project_id=signature.project_id,
                payload={
                    "trigger": "improvement-investigation",
                    "routes": curiosity_routes("improvement-investigation"),
                    "work_signature_ids": list(recognition.evidence_work_ids),
                    "current_method": {
                        "work_type": signature.work_type,
                        "input_shape": signature.input_shape,
                        "steps": list(signature.steps),
                        "tools": list(signature.tools),
                    },
                    "next_route": "perspective",
                    "value_activation": value_activation,
                    "automatic_change": False,
                },
            )
            result = {
                "project_revision": project_revision,
                "perspective_opened": True,
                "research_action_id": action["action_id"],
                "research_status": action["status"],
                "value_activation": value_activation,
            }
            finished = self.state.finish_execution(
                execution["execution_id"], success=True, result=result
            )
            return {"execution": finished, "result": result, "idempotent": False}
        except Exception as error:
            self.state.finish_execution(
                execution["execution_id"], success=False, error=str(error)
            )
            raise

    def handle_project_completion(self, project_id: str) -> dict[str, Any]:
        """Start and persist System Review exactly once for a completed snapshot."""
        project = self.project_store.read(project_id)
        review_candidate = start_system_review(project)
        existing_review = self.state.review_for_snapshot(
            review_candidate["project_snapshot_sha256"]
        )
        if existing_review is not None:
            existing_action = self.state.latest_action(existing_review["review_id"])
            if existing_action is not None:
                return {
                    "review": existing_review,
                    "action": existing_action,
                    "idempotent": True,
                }
            raise OrchestrationError("System Review exists without a durable stage action")
        execution, created = self.state.claim_execution(
            idempotency_key=(
                f"project-completion:{project.project_id}:"
                f"{review_candidate['project_snapshot_sha256']}"
            ),
            execution_kind="project-completion-to-system-review",
            project_id=project.project_id,
            project_revision=project.revision,
        )
        if not created:
            existing_review = self.state.review_for_snapshot(
                review_candidate["project_snapshot_sha256"]
            )
            if existing_review is not None:
                existing_action = self.state.latest_action(existing_review["review_id"])
                if existing_action is None:
                    raise OrchestrationError(
                        "System Review exists without a durable stage action"
                    )
                return {
                    "review": existing_review,
                    "action": existing_action,
                    "idempotent": True,
                }
            execution = self.state.latest_execution(
                f"project-completion:{project.project_id}:"
                f"{review_candidate['project_snapshot_sha256']}"
            ) or execution
            if execution["status"] not in {"failed", "unknown"}:
                raise OrchestrationError("Completion dispatch is still owned by a live attempt")
            execution, created = self.state.claim_execution(
                idempotency_key=(
                    f"project-completion:{project.project_id}:"
                    f"{review_candidate['project_snapshot_sha256']}:"
                    f"reconcile:{execution['execution_id']}"
                ),
                execution_kind="project-completion-system-review-reconciliation",
                project_id=project.project_id,
                project_revision=project.revision,
            )
            if not created:
                raise OrchestrationError(
                    "Completion reconciliation is already owned by another attempt"
                )
        self.state.mark_execution_running(execution["execution_id"])
        try:
            first_stage = SYSTEM_REVIEW_STAGES[0]
            action = self.state.start_review_with_action(
                review_candidate,
                execution_id=execution["execution_id"],
                stage=first_stage,
                payload={
                    "stage": first_stage,
                    "project_snapshot_sha256": review_candidate["project_snapshot_sha256"],
                    "authority": "investigate-validate-persist-only",
                },
            )
            result = {
                "review_id": review_candidate["review_id"],
                "review_status": review_candidate["status"],
                "next_action_id": action["action_id"],
                "next_stage": first_stage,
            }
            self.state.finish_execution(execution["execution_id"], success=True, result=result)
            return {"review": review_candidate, "action": action, "idempotent": False}
        except Exception as error:
            self.state.finish_execution(
                execution["execution_id"], success=False, error=str(error)
            )
            raise

    def reconcile_project(self, project_id: str) -> dict[str, Any]:
        """Recover a missed completion dispatch from canonical Project truth."""
        self.state.recover_interrupted()
        project = self.project_store.read(project_id)
        if project.status != "completed":
            return {"project_id": project_id, "status": "no-completion-to-dispatch"}
        return self.handle_project_completion(project_id)

    def run_next_system_review_stage(
        self,
        review_id: str,
        investigator: Investigator,
    ) -> dict[str, Any]:
        """Obtain, validate, persist and advance one non-human System Review stage."""
        review = self.state.read_review(review_id)
        if review["status"] == "awaiting-primary-user-decision":
            return {"review": review, "status": "awaiting-primary-user-decision"}
        if review["status"] == "completed":
            return {"review": review, "status": "completed"}
        action = self.state.next_ready_action(review_id=review_id)
        if action is None:
            raise OrchestrationError("System Review has no ready next-stage action")
        if action["stage"] == "your-decision":
            raise OrchestrationError("Primary-user decision cannot be investigator-executed")
        action = self.state.mark_action_running(action["action_id"])
        try:
            project = self.project_store.read(review["project_id"])
            result, evidence_ids = investigator(
                action["stage"],
                {
                    "project": project.to_dict(),
                    "review": review,
                    "action": json.loads(action["payload_json"]),
                },
            )
            updated = record_system_review_stage(
                review,
                stage=action["stage"],
                result=result,
                evidence_ids=evidence_ids,
            )
            next_stage = None
            next_payload = None
            if updated["status"] == "in_progress":
                next_stage = SYSTEM_REVIEW_STAGES[len(updated["stages"])]
                if next_stage != "your-decision":
                    next_payload = {
                        "stage": next_stage,
                        "project_snapshot_sha256": review["project_snapshot_sha256"],
                        "authority": "investigate-validate-persist-only",
                    }
                else:
                    next_stage = None
            next_action = self.state.commit_review_stage(
                action_id=action["action_id"],
                review=updated,
                result=result,
                evidence_ids=evidence_ids,
                next_stage=next_stage,
                next_payload=next_payload,
            )
            return {"review": updated, "next_action": next_action}
        except Exception as error:
            self.state.finish_action(
                action["action_id"], success=False, error=str(error)
            )
            raise

    def run_system_review_until_human(
        self,
        review_id: str,
        investigator: Investigator,
        *,
        max_stage_iterations: int = 16,
    ) -> dict[str, Any]:
        """Run the bounded automatic stage loop until Human Control must take over."""
        if max_stage_iterations < 1:
            raise ValueError("System Review stage budget must be positive")
        completed_stages = []
        for _ in range(max_stage_iterations):
            review = self.state.read_review(review_id)
            if review["status"] in {"awaiting-primary-user-decision", "completed"}:
                return {
                    "review": review,
                    "status": review["status"],
                    "completed_stages": completed_stages,
                    "budget_exhausted": False,
                }
            action = self.state.next_ready_action(review_id=review_id)
            if action is None:
                raise OrchestrationError("System Review has no recoverable ready stage")
            outcome = self.run_next_system_review_stage(review_id, investigator)
            completed_stages.append(action["stage"])
            if outcome["review"]["status"] == "awaiting-primary-user-decision":
                return {
                    "review": outcome["review"],
                    "status": "awaiting-primary-user-decision",
                    "completed_stages": completed_stages,
                    "budget_exhausted": False,
                }
        review = self.state.read_review(review_id)
        next_action = self.state.next_ready_action(review_id=review_id)
        return {
            "review": review,
            "status": "stage-budget-exhausted",
            "completed_stages": completed_stages,
            "next_stage": next_action["stage"] if next_action else None,
            "budget_exhausted": True,
        }

    def run_learning_review(
        self,
        action_id: str,
        *,
        reviewer: Any,
        candidate_store: Any,
    ) -> dict[str, Any]:
        """Complete queued Curiosity research using the locally forked learning method."""
        action = self.state.read_action(action_id)
        if action["action_kind"] != "curiosity-implementation-research":
            raise OrchestrationError("Learning Review requires a Curiosity research action")
        payload = json.loads(action["payload_json"])
        logical_action_id = str(payload.get("original_action_id") or action_id)
        evidence_id = (
            f"evidence-learning-review-{logical_action_id.removeprefix('action-')}"
        )
        if action["status"] == "completed":
            result = json.loads(action["result_json"] or "{}")
            self._record_learning_evidence(
                action=action,
                evidence_id=evidence_id,
                result=result,
            )
            return {"action": action, "result": result, "idempotent": True}
        if action["status"] != "ready":
            raise OrchestrationError("Curiosity research action is not ready")
        self.state.mark_action_running(action_id)
        try:
            project = self.project_store.read(action["project_id"])
            review = reviewer.review(project=project.to_dict(), action_payload=payload)
            manifest = candidate_store.stage(review, source_action_id=logical_action_id)
            result = {
                **review,
                "candidate_manifest": manifest,
                "candidate_path": (
                    str(candidate_store.candidate_path(manifest["candidate_id"]))
                    if manifest is not None
                    else None
                ),
                "canonical_evidence_id": evidence_id,
            }
            successor = self.state.commit_action_with_successor(
                action_id=action_id,
                result=result,
                evidence_ids=[evidence_id],
                successor_kind="perspective-review",
                successor_stage=None,
                successor_payload={
                    "source_learning_action_id": logical_action_id,
                    "canonical_evidence_id": evidence_id,
                    "candidate_id": manifest["candidate_id"] if manifest else None,
                    "review_status": review["status"],
                    "required_scope": "whole-project-perspective",
                    "automatic_change": False,
                },
                successor_idempotency_key=f"{action['idempotency_key']}:perspective",
            )
            completed = self.state.read_action(action_id)
            self._record_learning_evidence(
                action=completed,
                evidence_id=evidence_id,
                result=result,
            )
            return {
                "action": completed,
                "result": result,
                "next_action": successor,
                "idempotent": False,
            }
        except Exception as error:
            refreshed = self.state.read_action(action_id)
            if refreshed["status"] == "running":
                self.state.finish_action(action_id, success=False, error=str(error))
            raise

    def retry_failed_learning_review(self, action_id: str) -> dict[str, Any]:
        """Create one deterministic retry for failed or abandoned local learning."""
        action = self.state.read_action(action_id)
        if (
            action["action_kind"] != "curiosity-implementation-research"
            or action["status"] not in {"failed", "unknown"}
        ):
            raise OrchestrationError("Learning Review has no failed action to retry")
        payload = json.loads(action["payload_json"])
        payload["original_action_id"] = str(
            payload.get("original_action_id") or action_id
        )
        retry, _ = self.state.create_action(
            execution_id=action["execution_id"],
            idempotency_key=f"{action['idempotency_key']}:retry:{action_id}",
            action_kind=action["action_kind"],
            project_id=action["project_id"],
            payload=payload,
        )
        return retry

    def _record_learning_evidence(
        self,
        *,
        action: dict[str, Any],
        evidence_id: str,
        result: dict[str, Any],
    ) -> None:
        """Append completed local learning evidence to canonical Project truth."""
        from fractal.models import Change

        project = self.project_store.read(action["project_id"])
        existing = next((item for item in project.evidence if item["id"] == evidence_id), None)
        record = {
            "id": evidence_id,
            "kind": "research",
            "claim": str(result.get("summary") or "Local Learning Review completed."),
            "source": (
                f"fractal-local-learning:{action['action_id']}"
                + (
                    f"; candidate={result['candidate_manifest']['candidate_id']}"
                    if result.get("candidate_manifest")
                    else "; no-change"
                )
            ),
            "observed_at": str(result.get("reviewed_at") or utc_now()),
            "sha256": value_sha256(result),
        }
        if existing is not None:
            if existing != record:
                raise OrchestrationError("Canonical Learning Review evidence has drifted")
            return
        self.project_store.apply_changes(
            project.project_id,
            expected_revision=project.revision,
            changes=[Change("append", "/evidence", record)],
            actor="fractal-runtime",
            platform="local-learning",
            action="record-local-learning-review",
        )

    def retry_failed_system_review_stage(self, review_id: str) -> dict[str, Any]:
        """Create a new explicit attempt without rewriting failed execution history."""
        review = self.state.read_review(review_id)
        latest = self.state.latest_action(review_id)
        if latest is None or latest["status"] not in {"failed", "unknown"}:
            raise OrchestrationError("System Review has no failed stage to retry")
        expected_stage = SYSTEM_REVIEW_STAGES[len(review["stages"])]
        stage = expected_stage if latest["stage"] != expected_stage else latest["stage"]
        payload = json.loads(latest["payload_json"])
        payload["stage"] = stage
        action, created = self.state.create_action(
            execution_id=latest["execution_id"],
            idempotency_key=f"{latest['idempotency_key']}:retry:{latest['action_id']}",
            action_kind=latest["action_kind"],
            project_id=review["project_id"],
            review_id=review_id,
            stage=stage,
            payload=payload,
        )
        if not created:
            return action
        return action
