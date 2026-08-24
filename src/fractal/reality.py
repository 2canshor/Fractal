"""Reality Check execution receipts for consequential build gates.

The receipt shape adapts Hermes Agent's passive verification ledger and
in-toto's command/material/product link metadata.  Fractal keeps its own
authority model: these receipts prove execution facts only.
"""

from __future__ import annotations

import hashlib
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fractal.models import utc_now
from fractal.storage import value_sha256

IGNORED_DIRECTORY_NAMES = {".git", ".pytest_cache", ".ruff_cache", ".venv", "__pycache__"}


class RealityCheckError(RuntimeError):
    """Raised when a claimed execution receipt is missing or does not verify."""


@dataclass(frozen=True, slots=True)
class ExecutionGate:
    """One exact command and the artifacts it must observe."""

    gate_id: str
    command: tuple[str, ...]
    cwd: Path
    materials: tuple[str, ...]
    products: tuple[str, ...] = ()
    timeout_seconds: int = 300

    def to_dict(self) -> dict[str, Any]:
        if not self.gate_id.strip() or not self.command:
            raise RealityCheckError("Execution gate requires an id and command")
        if self.timeout_seconds <= 0:
            raise RealityCheckError("Execution gate timeout must be positive")
        root = self.cwd.expanduser().resolve()
        if not root.is_dir():
            raise RealityCheckError(f"Execution gate cwd does not exist: {root}")
        return {
            "gate_id": self.gate_id,
            "command": list(self.command),
            "cwd": str(root),
            "materials": list(self.materials),
            "products": list(self.products),
            "timeout_seconds": self.timeout_seconds,
        }


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _artifact_hashes(root: Path, paths: tuple[str, ...]) -> dict[str, str]:
    records: dict[str, str] = {}
    for item in paths:
        candidate = (root / item).resolve()
        try:
            relative = candidate.relative_to(root)
        except ValueError as error:
            raise RealityCheckError(f"Artifact escapes execution root: {item}") from error
        if not candidate.exists():
            raise RealityCheckError(f"Required artifact does not exist: {item}")
        files = [candidate]
        if candidate.is_dir():
            files = [
                path
                for path in sorted(candidate.rglob("*"))
                if path.is_file()
                and not any(
                    part in IGNORED_DIRECTORY_NAMES
                    for part in path.relative_to(root).parts
                )
            ]
        for path in files:
            relative_path = path.relative_to(root).as_posix()
            records[relative_path] = _sha256_bytes(path.read_bytes())
        if candidate.is_dir() and not files:
            records[f"{relative.as_posix()}/"] = _sha256_bytes(b"")
    return records


def verification_plan_sha256(gates: list[ExecutionGate]) -> str:
    """Bind human build authority to the exact executable Reality Check plan."""
    values = [gate.to_dict() for gate in gates]
    gate_ids = [item["gate_id"] for item in values]
    if len(gate_ids) != len(set(gate_ids)):
        raise RealityCheckError("Execution gate ids must be unique")
    return value_sha256(values)


class RealityCheckRunner:
    """Run exact argv without a shell and return artifact-bound receipts."""

    def run(self, gate: ExecutionGate) -> dict[str, Any]:
        plan = gate.to_dict()
        root = Path(plan["cwd"])
        materials = _artifact_hashes(root, gate.materials)
        started_at = utc_now()
        monotonic_start = time.monotonic()
        try:
            completed = subprocess.run(
                list(gate.command),
                cwd=root,
                check=False,
                capture_output=True,
                timeout=gate.timeout_seconds,
            )
            exit_code = completed.returncode
            stdout = completed.stdout
            stderr = completed.stderr
            failure = None
        except subprocess.TimeoutExpired as error:
            exit_code = -1
            stdout = error.stdout or b""
            stderr = error.stderr or b""
            failure = f"timed out after {gate.timeout_seconds} seconds"
        except OSError as error:
            exit_code = -1
            stdout = b""
            stderr = str(error).encode("utf-8", errors="replace")
            failure = f"could not execute command: {error}"
        duration_seconds = round(time.monotonic() - monotonic_start, 6)
        product_hashes = _artifact_hashes(root, gate.products) if gate.products else {}
        receipt = {
            "record_type": "reality-check-execution-receipt",
            "record_version": 1,
            "gate": plan,
            "plan_sha256": value_sha256(plan),
            "materials": materials,
            "products": product_hashes,
            "byproducts": {
                "exit_code": exit_code,
                "stdout_sha256": _sha256_bytes(stdout),
                "stdout_bytes": len(stdout),
                "stderr_sha256": _sha256_bytes(stderr),
                "stderr_bytes": len(stderr),
            },
            "status": "passed" if exit_code == 0 else "failed",
            "failure": failure,
            "started_at": started_at,
            "finished_at": utc_now(),
            "duration_seconds": duration_seconds,
            "receipt_sha256": None,
        }
        receipt["receipt_sha256"] = value_sha256(
            {key: value for key, value in receipt.items() if key != "receipt_sha256"}
        )
        return receipt

    def run_all(self, gates: list[ExecutionGate]) -> list[dict[str, Any]]:
        verification_plan_sha256(gates)
        receipts = []
        for gate in gates:
            receipt = self.run(gate)
            receipts.append(receipt)
            if receipt["status"] != "passed":
                raise RealityCheckError(
                    f"Reality Check gate failed: {gate.gate_id} "
                    f"(exit {receipt['byproducts']['exit_code']})"
                )
        return receipts


def validate_execution_receipts(
    receipts: list[dict[str, Any]],
    *,
    required_gate_ids: set[str],
    expected_plan_sha256: str,
) -> dict[str, bool]:
    """Validate receipts and derive booleans; never accept supplied booleans."""
    gate_ids = [item.get("gate", {}).get("gate_id") for item in receipts]
    if len(gate_ids) != len(set(gate_ids)) or set(gate_ids) != required_gate_ids:
        raise RealityCheckError("Reality Check receipts are incomplete or duplicated")
    observed_plan = [item["gate"] for item in receipts]
    if value_sha256(observed_plan) != expected_plan_sha256:
        raise RealityCheckError("Reality Check receipts do not match the authorised plan")
    derived: dict[str, bool] = {}
    for receipt in receipts:
        if receipt.get("record_type") != "reality-check-execution-receipt":
            raise RealityCheckError("Reality Check receipt type is invalid")
        expected_receipt = value_sha256(
            {key: value for key, value in receipt.items() if key != "receipt_sha256"}
        )
        if receipt.get("receipt_sha256") != expected_receipt:
            raise RealityCheckError("Reality Check receipt integrity failure")
        if receipt.get("status") != "passed" or receipt.get("byproducts", {}).get(
            "exit_code"
        ) != 0:
            raise RealityCheckError("Reality Check receipt does not prove a passing execution")
        gate_id = receipt["gate"]["gate_id"]
        derived[gate_id] = True
    return derived
