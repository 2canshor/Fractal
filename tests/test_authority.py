from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from fractal.authority import AuthorityReceiptStore, ReceiptError, verify_primary_user_turn


def evidence(tmp_path: Path, label: str = "approve") -> dict[str, str]:
    text = f"{label}\n"
    message_id = f"msg-{label}"
    turn_id = f"turn-{label}"
    path = tmp_path / f"session-{label}.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "id": message_id,
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                    "internal_chat_message_metadata_passthrough": {"turn_id": turn_id},
                },
            }
        )
        + "\n"
    )
    return {
        "session_path": str(path),
        "turn_id": turn_id,
        "message_id": message_id,
        "message_sha256": hashlib.sha256((text + "\n").encode()).hexdigest(),
    }


def test_authority_must_come_from_the_exact_primary_user_turn(tmp_path: Path) -> None:
    valid = evidence(tmp_path)
    assert verify_primary_user_turn(valid)["message_id"] == "msg-approve"
    invalid = {**valid, "message_sha256": "0" * 64}
    with pytest.raises(ReceiptError, match="digest"):
        verify_primary_user_turn(invalid)
    session = Path(valid["session_path"])
    item = json.loads(session.read_text())
    item["payload"]["role"] = "assistant"
    session.write_text(json.dumps(item) + "\n")
    with pytest.raises(ReceiptError, match="primary-user turn"):
        verify_primary_user_turn(valid)


def test_receipt_scope_is_exact_and_replay_fails(tmp_path: Path) -> None:
    store = AuthorityReceiptStore(tmp_path / "authority")
    receipt = store.issue(
        action="activate",
        project_id="project-a",
        project_revision=7,
        target={"version": "0.1.0-alpha.4", "manifest_sha256": "a" * 64},
        expected_state={"active_version": "0.1.0-alpha.2", "version_state": "candidate"},
        authority_evidence=evidence(tmp_path),
    )
    with pytest.raises(ReceiptError, match="scope"):
        store.claim(
            receipt["receipt_id"],
            action="restore",
            project_id="project-a",
            project_revision=7,
            target=receipt["target"],
            expected_state=receipt["expected_state"],
        )
    store.claim(
        receipt["receipt_id"],
        action="activate",
        project_id="project-a",
        project_revision=7,
        target=receipt["target"],
        expected_state=receipt["expected_state"],
    )
    store.finish(receipt["receipt_id"], succeeded=True)
    with pytest.raises(ReceiptError, match="not reusable"):
        store.claim(
            receipt["receipt_id"],
            action="activate",
            project_id="project-a",
            project_revision=7,
            target=receipt["target"],
            expected_state=receipt["expected_state"],
        )


def test_concurrent_claim_allows_exactly_one_winner(tmp_path: Path) -> None:
    store = AuthorityReceiptStore(tmp_path / "authority")
    receipt = store.issue(
        action="build",
        project_id="project-a",
        project_revision=7,
        target={"version": "0.1.0-alpha.4"},
        expected_state={"candidate_absent": True},
        authority_evidence=evidence(tmp_path),
    )

    def claim() -> bool:
        try:
            store.claim(
                receipt["receipt_id"],
                action="build",
                project_id="project-a",
                project_revision=7,
                target=receipt["target"],
                expected_state=receipt["expected_state"],
            )
        except ReceiptError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: claim(), range(2)))
    assert sorted(results) == [False, True]


def test_expired_receipt_fails_before_it_can_be_claimed(tmp_path: Path) -> None:
    store = AuthorityReceiptStore(tmp_path / "authority")
    receipt = store.issue(
        action="build",
        project_id="project-a",
        project_revision=7,
        target={"version": "0.1.0-alpha.4"},
        expected_state={"candidate_absent": True},
        authority_evidence=evidence(tmp_path, "expired"),
        expires_at="2000-01-01T00:00:00Z",
    )
    with pytest.raises(ReceiptError, match="expired"):
        store.claim(
            receipt["receipt_id"],
            action="build",
            project_id="project-a",
            project_revision=7,
            target=receipt["target"],
            expected_state=receipt["expected_state"],
        )


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("action", "restore"),
        ("project_id", "project-b"),
        ("project_revision", 8),
        ("target", {"version": "wrong"}),
        ("expected_state", {"candidate_absent": False}),
    ],
)
def test_every_receipt_scope_dimension_fails_closed(
    tmp_path: Path,
    field: str,
    wrong_value: object,
) -> None:
    store = AuthorityReceiptStore(tmp_path / f"authority-{field}")
    receipt = store.issue(
        action="build",
        project_id="project-a",
        project_revision=7,
        target={"version": "0.1.0-alpha.4"},
        expected_state={"candidate_absent": True},
        authority_evidence=evidence(tmp_path, f"scope-{field}"),
    )
    call = {
        "action": "build",
        "project_id": "project-a",
        "project_revision": 7,
        "target": receipt["target"],
        "expected_state": receipt["expected_state"],
    }
    call[field] = wrong_value
    with pytest.raises(ReceiptError, match="scope"):
        store.claim(receipt["receipt_id"], **call)
    assert store.read(receipt["receipt_id"])["state"] == "issued"
