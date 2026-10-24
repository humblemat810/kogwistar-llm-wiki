from __future__ import annotations

import json
import os
import statistics
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
SEED = 0x4B4F475749535441
ACCOUNT_COUNT = 32
OP_COUNT = 2000
SAMPLES = 5
BATCH_SIZES = (1, 32, 256)


def py_transfers(count: int, account_count: int, seed: int) -> list[dict[str, int]]:
    state = seed
    result: list[dict[str, int]] = []
    for _ in range(count):
        state = (state * 6364136223846793005 + 1442695040888963407) & ((1 << 64) - 1)
        source = (state >> 32) % account_count
        state = (state * 6364136223846793005 + 1442695040888963407) & ((1 << 64) - 1)
        target = (state >> 32) % account_count
        if target == source:
            target = (target + 1) % account_count
        state = (state * 6364136223846793005 + 1442695040888963407) & ((1 << 64) - 1)
        amount = (state >> 32) % 5 + 1
        result.append({"from": source, "to": target, "amount": amount})
    return result


def init_python_db() -> Any:
    import sqlite3

    connection = sqlite3.connect(":memory:")
    connection.executescript(
        "CREATE TABLE accounts(id INTEGER PRIMARY KEY, balance INTEGER NOT NULL);"
        "CREATE TABLE transfers(id INTEGER PRIMARY KEY, from_id INTEGER NOT NULL, "
        "to_id INTEGER NOT NULL, amount INTEGER NOT NULL);"
    )
    connection.executemany(
        "INSERT INTO accounts(id, balance) VALUES (?, 1000000)",
        ((account_id,) for account_id in range(ACCOUNT_COUNT)),
    )
    connection.commit()
    return connection


def apply_python_batch(connection: Any, transfers: list[dict[str, int]]) -> None:
    with connection:
        for item in transfers:
            cursor = connection.execute(
                "UPDATE accounts SET balance = balance - ? WHERE id = ? AND balance >= ?",
                (item["amount"], item["from"], item["amount"]),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("unknown account or insufficient funds")
            cursor = connection.execute(
                "UPDATE accounts SET balance = balance + ? WHERE id = ?",
                (item["amount"], item["to"]),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("unknown destination account")
            connection.execute(
                "INSERT INTO transfers(from_id, to_id, amount) VALUES (?, ?, ?)",
                (item["from"], item["to"], item["amount"]),
            )


def python_snapshot(connection: Any) -> dict[str, Any]:
    balances = [row[0] for row in connection.execute("SELECT balance FROM accounts ORDER BY id")]
    count = connection.execute("SELECT COUNT(*) FROM transfers").fetchone()[0]
    trace_hash = 0xCBF29CE484222325
    for row in connection.execute("SELECT from_id, to_id, amount FROM transfers ORDER BY id"):
        for byte in struct.pack("<qqq", *row):
            trace_hash = ((trace_hash ^ byte) * 0x100000001B3) & ((1 << 64) - 1)
    return {
        "contract_version": 1,
        "balances": balances,
        "transfer_count": count,
        "total_cents": sum(balances),
        "trace_digest": f"{trace_hash:016x}",
    }


class RustWorker:
    def __init__(self, executable: Path) -> None:
        self.process = subprocess.Popen(
            [str(executable)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self.corrupt_next_callback_id = False

    @staticmethod
    def _python_callback(event: dict[str, Any]) -> dict[str, Any]:
        if event.get("contract_version") != 1 or event.get("operation") != "mean_and_l2_norm":
            raise RuntimeError("unsupported Python callback contract")
        values = event.get("values")
        if not isinstance(values, list) or len(values) > 100_000:
            raise RuntimeError("invalid or oversized callback values")
        import numpy as np

        array = np.asarray(values, dtype=np.float64)
        return {"mean": float(array.mean()) if array.size else 0.0,
                "l2_norm": float(np.linalg.norm(array))}

    def call(self, operation: str, **values: Any) -> dict[str, Any]:
        assert self.process.stdin is not None and self.process.stdout is not None
        request = {"contract_version": 1, "operation": operation, **values}
        self.process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        response = json.loads(self.process.stdout.readline())
        while response.get("kind") == "python_callback_request":
            callback_id = response.get("callback_id")
            result = self._python_callback(response)
            if self.corrupt_next_callback_id:
                callback_id = f"{callback_id}-stale"
                self.corrupt_next_callback_id = False
            callback_reply = {
                "contract_version": 1,
                "kind": "python_callback_result",
                "callback_id": callback_id,
                "result": result,
            }
            self.process.stdin.write(json.dumps(callback_reply, separators=(",", ":")) + "\n")
            self.process.stdin.flush()
            response = json.loads(self.process.stdout.readline())
        if "error" in response:
            raise RuntimeError(response["error"])
        return response

    def close(self) -> None:
        assert self.process.stdin is not None
        self.process.stdin.close()
        result = self.process.wait(timeout=20)
        if result != 0:
            stderr = self.process.stderr.read() if self.process.stderr else ""
            raise RuntimeError(f"Rust worker exited {result}: {stderr}")


def timed_samples(run: Any) -> tuple[float, list[float], dict[str, Any]]:
    run()  # warm-up, excluded
    durations: list[float] = []
    final_result = None
    for _ in range(SAMPLES):
        start = time.perf_counter()
        final_result = run()
        durations.append((time.perf_counter() - start) * 1000)
    return statistics.median(durations), durations, final_result


def batches_for(batch_size: int) -> list[list[dict[str, int]]]:
    trace = py_transfers(OP_COUNT, ACCOUNT_COUNT, SEED)
    return [trace[index : index + batch_size] for index in range(0, len(trace), batch_size)]


def main() -> int:
    executable = ROOT / "target" / "release" / ("dual-language-seam-poc.exe" if os.name == "nt" else "dual-language-seam-poc")
    build_start = time.perf_counter()
    subprocess.run(
        ["cargo", "build", "--release", "--offline", "--manifest-path", str(ROOT / "Cargo.toml")],
        check=True,
        cwd=ROOT,
    )
    build_seconds = time.perf_counter() - build_start
    if not executable.exists():
        raise FileNotFoundError(executable)

    results: list[dict[str, Any]] = []
    reverse_seam_results: list[float] = []

    for batch_size in BATCH_SIZES:
        def run_python() -> dict[str, Any]:
            connection = init_python_db()
            for batch in batches_for(batch_size):
                apply_python_batch(connection, batch)
            snapshot = python_snapshot(connection)
            connection.close()
            return snapshot

        worker_start = time.perf_counter()
        worker = RustWorker(executable)
        worker_start_ms = (time.perf_counter() - worker_start) * 1000
        try:
            worker.call("reset", account_count=ACCOUNT_COUNT)
            before = worker.call("snapshot")
            try:
                worker.call("apply", transfers=[
                    {"from": 0, "to": 1, "amount": 1},
                    {"from": ACCOUNT_COUNT + 10, "to": 2, "amount": 1},
                ])
            except RuntimeError:
                pass
            else:
                raise AssertionError("Rust accepted a batch containing an unknown account")
            if worker.call("snapshot") != before:
                raise AssertionError("Rust did not roll back a failed transfer batch")

            python_db = init_python_db()
            python_before = python_snapshot(python_db)
            try:
                apply_python_batch(python_db, [
                    {"from": 0, "to": 1, "amount": 1},
                    {"from": ACCOUNT_COUNT + 10, "to": 2, "amount": 1},
                ])
            except RuntimeError:
                pass
            else:
                raise AssertionError("Python accepted a batch containing an unknown account")
            if python_snapshot(python_db) != python_before:
                raise AssertionError("Python did not roll back a failed transfer batch")
            python_db.close()

            worker.call("run_seeded", count=OP_COUNT, account_count=ACCOUNT_COUNT, batch_size=batch_size, seed=SEED)
            rust_native_snapshot = worker.call("snapshot")

            def run_rust_native() -> dict[str, Any]:
                worker.call("run_seeded", count=OP_COUNT, account_count=ACCOUNT_COUNT, batch_size=batch_size, seed=SEED)
                return worker.call("snapshot")

            def run_mixed() -> dict[str, Any]:
                worker.call("reset", account_count=ACCOUNT_COUNT)
                for batch in batches_for(batch_size):
                    worker.call("apply", transfers=batch)
                return worker.call("snapshot")

            python_median, python_samples, python_result = timed_samples(run_python)
            rust_median, rust_samples, rust_result = timed_samples(run_rust_native)
            mixed_median, mixed_samples, mixed_result = timed_samples(run_mixed)
            expected = python_result
            if rust_native_snapshot != expected or rust_result != expected or mixed_result != expected:
                raise AssertionError(
                    f"parity failure at batch_size={batch_size}: "
                    f"python={expected}, rust={rust_result}, mixed={mixed_result}"
                )
            results.extend(
                [
                    {"batch_size": batch_size, "mode": "python_native", "median_ms": python_median, "samples_ms": python_samples},
                    {"batch_size": batch_size, "mode": "rust_native", "median_ms": rust_median, "samples_ms": rust_samples},
                    {"batch_size": batch_size, "mode": "python_to_rust_jsonl", "median_ms": mixed_median, "samples_ms": mixed_samples},
                ]
            )
            for item in results[-3:]:
                item["rust_worker_start_ms"] = round(worker_start_ms, 3)

            callback_values = [index / 17.0 for index in range(4096)]
            expected_mean = sum(callback_values) / len(callback_values)
            expected_l2 = sum(value * value for value in callback_values) ** 0.5
            worker.call(
                "python_step", callback_id=f"numpy-warmup-{batch_size}",
                callback_values=callback_values,
            )
            reverse_start = time.perf_counter()
            for index in range(25):
                callback_result = worker.call(
                    "python_step", callback_id=f"numpy-step-{batch_size}-{index}",
                    callback_values=callback_values,
                )
                actual = callback_result["result"]
                if abs(actual["mean"] - expected_mean) > 1e-9 or abs(actual["l2_norm"] - expected_l2) > 1e-8:
                    raise AssertionError("Rust-to-Python NumPy callback result mismatch")
            reverse_seam_results.append((time.perf_counter() - reverse_start) * 1000 / 25)

            worker.corrupt_next_callback_id = True
            try:
                worker.call("python_step", callback_id=f"bad-correlation-{batch_size}", callback_values=[1.0])
            except RuntimeError as error:
                if "correlation id mismatch" not in str(error):
                    raise
            else:
                raise AssertionError("Rust accepted a mismatched Python callback correlation id")
        finally:
            worker.close()

    print(json.dumps({
        "contract_version": 1,
        "operations": OP_COUNT,
        "accounts": ACCOUNT_COUNT,
        "samples": SAMPLES,
        "warmup_runs": 1,
        "rust_build_seconds_included_in_workload_timings": False,
        "rust_build_seconds": round(build_seconds, 3),
        "rust_worker_start_ms_is_separate": True,
        "parity": "PASS",
        "atomic_rollback_parity": "PASS",
        "rust_to_python_callback": {
            "python_library": "NumPy",
            "values_per_callback": 4096,
            "callbacks_per_sample": 25,
            "mean_latency_ms_by_existing_batch_size": reverse_seam_results,
            "warmup_callbacks_per_worker": 1,
            "correlation_validation": "PASS",
            "note": "toy persistent JSONL round-trip; no database transaction is open during callback",
        },
        "results": results,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
