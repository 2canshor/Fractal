from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

from fractal.reality import (
    ExecutionGate,
    RealityCheckError,
    RealityCheckRunner,
    validate_execution_receipts,
    verification_plan_sha256,
)


def write_gate(tmp_path: Path, *, exit_code: int = 0) -> ExecutionGate:
    (tmp_path / "input.txt").write_text("material\n")
    script = (
        "from pathlib import Path; "
        "Path('product.txt').write_text(Path('input.txt').read_text().upper()); "
        f"raise SystemExit({exit_code})"
    )
    return ExecutionGate(
        gate_id="tests_passed",
        command=(sys.executable, "-c", script),
        cwd=tmp_path,
        materials=("input.txt",),
        products=("product.txt",),
        timeout_seconds=10,
    )


def test_reality_check_executes_and_binds_command_materials_and_products(
    tmp_path: Path,
) -> None:
    gate = write_gate(tmp_path)
    receipts = RealityCheckRunner().run_all([gate])
    derived = validate_execution_receipts(
        receipts,
        required_gate_ids={"tests_passed"},
        expected_plan_sha256=verification_plan_sha256([gate]),
    )

    assert derived == {"tests_passed": True}
    receipt = receipts[0]
    assert receipt["byproducts"]["exit_code"] == 0
    assert receipt["materials"]["input.txt"] != receipt["products"]["product.txt"]
    assert receipt["byproducts"]["stdout_bytes"] == 0
    assert receipt["status"] == "passed"


def test_failed_reality_check_does_not_become_a_passing_claim(tmp_path: Path) -> None:
    with pytest.raises(RealityCheckError, match="gate failed"):
        RealityCheckRunner().run_all([write_gate(tmp_path, exit_code=7)])

    missing_command = ExecutionGate(
        gate_id="tests_passed",
        command=(str(tmp_path / "missing-runner"),),
        cwd=tmp_path,
        materials=("input.txt",),
    )
    with pytest.raises(RealityCheckError, match="gate failed"):
        RealityCheckRunner().run_all([missing_command])


def test_receipt_tampering_and_plan_drift_fail_closed(tmp_path: Path) -> None:
    gate = write_gate(tmp_path)
    receipts = RealityCheckRunner().run_all([gate])
    tampered = copy.deepcopy(receipts)
    tampered[0]["byproducts"]["exit_code"] = 0
    tampered[0]["products"]["product.txt"] = "0" * 64
    with pytest.raises(RealityCheckError, match="integrity failure"):
        validate_execution_receipts(
            tampered,
            required_gate_ids={"tests_passed"},
            expected_plan_sha256=verification_plan_sha256([gate]),
        )

    different_plan = ExecutionGate(
        gate_id="tests_passed",
        command=(sys.executable, "-c", "raise SystemExit(0)"),
        cwd=tmp_path,
        materials=("input.txt",),
    )
    with pytest.raises(RealityCheckError, match="authorised plan"):
        validate_execution_receipts(
            receipts,
            required_gate_ids={"tests_passed"},
            expected_plan_sha256=verification_plan_sha256([different_plan]),
        )
