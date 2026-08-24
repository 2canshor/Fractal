"""Exact, single-use authority receipts for consequential Fractal actions."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fractal.models import utc_now


class ReceiptError(RuntimeError):
    """Raised when authority is absent, stale, mismatched, or already used."""


RECEIPT_ACTIONS = {"build", "activate", "reject", "restore", "publish"}


def verify_primary_user_turn(evidence: dict[str, str]) -> dict[str, str]:
    """Verify one primary-user message against its exact append-only session record."""
    required = {
        "session_path",
        "turn_id",
        "message_id",
        "message_sha256",
    }
    if set(evidence) != required:
        raise ReceiptError("Authority evidence fields are incomplete or unexpected")
    session_path = Path(evidence["session_path"])
    if not session_path.is_file():
        raise ReceiptError("Authority session evidence is unavailable")
    match: dict[str, Any] | None = None
    for line in session_path.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        payload = item.get("payload", {})
        if (
            item.get("type") == "response_item"
            and payload.get("type") == "message"
            and payload.get("id") == evidence["message_id"]
        ):
            match = item
            break
    if match is None:
        raise ReceiptError("Authority message is missing from the session record")
    payload = match["payload"]
    metadata = payload.get("internal_chat_message_metadata_passthrough", {})
    if payload.get("role") != "user" or metadata.get("turn_id") != evidence["turn_id"]:
        raise ReceiptError("Authority message does not belong to the stated primary-user turn")
    texts = [
        block["text"] for block in payload.get("content", []) if block.get("type") == "input_text"
    ]
    # Matches `jq -r ... .text | shasum -a 256`: jq adds one output newline.
    digest = hashlib.sha256(("\n".join(texts) + "\n").encode()).hexdigest()
    if digest != evidence["message_sha256"]:
        raise ReceiptError("Authority message digest does not match")
    return {
        "session_path": str(session_path),
        "turn_id": evidence["turn_id"],
        "message_id": evidence["message_id"],
        "message_sha256": digest,
    }


class AuthorityReceiptStore:
    """Issue and atomically consume receipts bound to exactly one action and target."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.receipts = self.root / "receipts"
        self.lock_path = self.root / ".authority-receipts.lock"

    def issue(
        self,
        *,
        action: str,
        project_id: str,
        project_revision: int,
        target: dict[str, Any],
        expected_state: dict[str, Any],
        authority_evidence: dict[str, str],
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        """Issue a receipt only from verified primary-user turn evidence."""
        if action not in RECEIPT_ACTIONS:
            raise ReceiptError(f"Unsupported authority action: {action}")
        if not project_id or project_revision < 0 or not target or not expected_state:
            raise ReceiptError("Authority receipt requires exact Project, target, and state")
        verified = verify_primary_user_turn(authority_evidence)
        receipt = {
            "record_type": "authority-receipt",
            "record_version": 1,
            "receipt_id": f"authority-receipt-{uuid.uuid4()}",
            "action": action,
            "project_id": project_id,
            "project_revision": project_revision,
            "target": target,
            "expected_state": expected_state,
            "issued_by": "primary-user",
            "issued_at": utc_now(),
            "expires_at": expires_at,
            "authority_evidence": verified,
            "state": "issued",
            "claimed_at": None,
            "completed_at": None,
            "failure": None,
        }
        self._atomic_json_write(self._path(receipt["receipt_id"]), receipt)
        return receipt

    def claim(
        self,
        receipt_id: str,
        *,
        action: str,
        project_id: str,
        project_revision: int,
        target: dict[str, Any],
        expected_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Atomically claim a matching receipt; replay and concurrent use fail closed."""
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                receipt = self.read(receipt_id)
                if receipt["state"] != "issued":
                    raise ReceiptError(
                        f"Authority receipt is not reusable; current state is {receipt['state']}"
                    )
                if receipt["expires_at"] is not None:
                    try:
                        expiry = datetime.fromisoformat(
                            str(receipt["expires_at"]).replace("Z", "+00:00")
                        )
                    except ValueError as error:
                        raise ReceiptError("Authority receipt expiry is invalid") from error
                    if expiry.tzinfo is None:
                        raise ReceiptError("Authority receipt expiry must include a timezone")
                    if datetime.now(UTC) >= expiry:
                        raise ReceiptError("Authority receipt has expired")
                expected = {
                    "action": action,
                    "project_id": project_id,
                    "project_revision": project_revision,
                    "target": target,
                    "expected_state": expected_state,
                }
                observed = {key: receipt[key] for key in expected}
                if observed != expected:
                    raise ReceiptError("Authority receipt scope does not match this action")
                receipt["state"] = "claimed"
                receipt["claimed_at"] = utc_now()
                self._atomic_json_write(self._path(receipt_id), receipt)
                return receipt
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def finish(self, receipt_id: str, *, succeeded: bool, failure: str | None = None) -> None:
        """Close one claimed receipt as succeeded or failed without making it reusable."""
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                receipt = self.read(receipt_id)
                if receipt["state"] != "claimed":
                    raise ReceiptError("Only a claimed authority receipt can finish")
                receipt["state"] = "succeeded" if succeeded else "failed"
                receipt["completed_at"] = utc_now()
                receipt["failure"] = None if succeeded else (failure or "unspecified failure")
                self._atomic_json_write(self._path(receipt_id), receipt)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def read(self, receipt_id: str) -> dict[str, Any]:
        """Read one receipt without changing it."""
        path = self._path(receipt_id)
        if not path.is_file():
            raise ReceiptError(f"Unknown authority receipt: {receipt_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _path(self, receipt_id: str) -> Path:
        if not receipt_id.startswith("authority-receipt-") or "/" in receipt_id:
            raise ReceiptError("Invalid authority receipt id")
        return self.receipts / f"{receipt_id}.json"

    @staticmethod
    def _atomic_json_write(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)
