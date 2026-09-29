"""Fetch a bounded HTTP response under one wall deadline, including DNS/open.

The urllib timeout applies to individual socket operations. A separate owned
process lets the caller stop a resolver, connection, header, or body stall at
the same monotonic deadline. Only the process created by this call is killed.
"""
from __future__ import annotations

import json
import multiprocessing
import tempfile
import time
from pathlib import Path
from typing import List


def _read_response(url: str, deadline_s: float, max_bytes: int,
                   chunk_bytes: int, io_timeout_s: float,
                   byte_label: str) -> bytes:
    import urllib.request

    deadline = time.monotonic() + deadline_s
    chunks: List[bytes] = []
    total = 0
    with urllib.request.urlopen(  # noqa: S310 — caller verifies pinned hash
            url, timeout=min(io_timeout_s, deadline_s)) as response:
        # read1 returns available bytes without waiting to fill the chunk.
        read1 = response.read1
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"overall fetch deadline {deadline_s}s exceeded")
            chunk = read1(min(chunk_bytes, max_bytes - total + 1))
            if time.monotonic() >= deadline:
                raise TimeoutError(f"overall fetch deadline {deadline_s}s exceeded")
            if not chunk:
                return b"".join(chunks)
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"{byte_label} byte ceiling {max_bytes} exceeded")
            chunks.append(chunk)


def _worker(url: str, result: Path, error: Path, deadline_s: float,
            max_bytes: int, chunk_bytes: int, io_timeout_s: float,
            byte_label: str) -> None:
    try:
        result.write_bytes(_read_response(url, deadline_s, max_bytes,
                                          chunk_bytes, io_timeout_s, byte_label))
    except Exception as exc:  # noqa: BLE001 — transport named refusal
        error.write_text(json.dumps({"kind": type(exc).__name__,
                                     "message": str(exc)}))


def fetch(url: str, *, deadline_s: float, max_bytes: int,
          chunk_bytes: int, io_timeout_s: float, byte_label: str) -> bytes:
    """Return bounded bytes or refuse; the whole operation has one deadline."""
    deadline = time.monotonic() + deadline_s
    with tempfile.TemporaryDirectory(prefix="vibeic-bounded-fetch-") as scratch:
        result = Path(scratch) / "response"
        error = Path(scratch) / "error"
        worker = multiprocessing.get_context("fork").Process(
            target=_worker,
            args=(url, result, error, deadline_s, max_bytes,
                  chunk_bytes, io_timeout_s, byte_label))
        try:
            worker.start()
            worker.join(max(0.0, deadline - time.monotonic()))
            if worker.is_alive():
                worker.kill()
                worker.join()
                raise TimeoutError(f"overall fetch deadline {deadline_s}s exceeded")
            if time.monotonic() >= deadline:
                raise TimeoutError(f"overall fetch deadline {deadline_s}s exceeded")
            if error.is_file():
                failure = json.loads(error.read_text())
                kind = failure["kind"]
                message = failure["message"]
                if kind == "TimeoutError":
                    raise TimeoutError(message)
                if kind == "ValueError":
                    raise ValueError(message)
                raise OSError(f"{kind}: {message}")
            if worker.exitcode != 0 or not result.is_file():
                raise OSError(f"fetch worker exited {worker.exitcode} without a response")
            data = result.read_bytes()
            if time.monotonic() >= deadline:
                raise TimeoutError(f"overall fetch deadline {deadline_s}s exceeded")
            return data
        finally:
            if worker.is_alive():
                worker.kill()
                worker.join()
            worker.close()
