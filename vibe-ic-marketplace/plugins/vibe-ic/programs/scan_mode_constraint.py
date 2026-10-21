#!/usr/bin/env python3
"""Emit and validate the source-bound scan-shift timing constraint."""
from __future__ import annotations

import hashlib
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path


SCAN_MODE_SDC_REL = "phase2/stage2/dft/scan_mode.sdc"


def _decimal_text(value) -> str | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not number.is_finite() or number <= 0:
        return None
    text = format(number.normalize(), "f")
    return text + ".0" if "." not in text else text


def frequency_period(frequency_mhz) -> tuple[str | None, str | None]:
    """Canonical MHz and nanosecond period strings, or ``(None, None)``."""
    frequency = _decimal_text(frequency_mhz)
    if frequency is None:
        return None, None
    with localcontext() as context:
        context.prec = 28
        period = Decimal("1000") / Decimal(frequency)
    period_text = format(period.normalize(), "f")
    if "." not in period_text:
        period_text += ".0"
    return frequency, period_text


def _tcl_port(name) -> str | None:
    if not isinstance(name, str) or not name.strip():
        return None
    value = name.strip()
    if any(ch in value for ch in ("{", "}", "\\", "\n", "\r")):
        return None
    return "{" + value + "}"


def render(binding: dict) -> tuple[bytes | None, dict | None, list[str]]:
    chain = binding.get("chain") if isinstance(binding, dict) else None
    chain = chain if isinstance(chain, dict) else {}
    frequency, period = frequency_period(chain.get("frequency_mhz"))
    if frequency is None:
        return None, None, []
    clock = _tcl_port(chain.get("clock"))
    if clock is None:
        return None, None, ["declared scan clock cannot be represented safely in SDC"]
    text = (
        "# Vibe-IC source-bound scan-shift timing constraint\n"
        f"# L20 path: {binding.get('path')}\n"
        f"# L20 sha256: {binding.get('sha256')}\n"
        f"# frequency_mhz: {frequency}\n"
        f"# period_ns: {period}\n"
        f"create_clock -name {{vibeic_scan_shift_clock}} -period {period} "
        f"[get_ports {clock}]\n"
        "set_case_analysis 1 [get_ports {shift}]\n"
        "set_case_analysis 1 [get_ports {test}]\n"
    ).encode("utf-8")
    receipt = {
        "kind": "scan_shift_clock",
        "path": SCAN_MODE_SDC_REL,
        "sha256": hashlib.sha256(text).hexdigest(),
        "l20_path": binding.get("path"),
        "l20_sha256": binding.get("sha256"),
        "clock": chain.get("clock"),
        "frequency_mhz": frequency,
        "period_ns": period,
    }
    return text, receipt, []


def emit(project: Path, binding: dict) -> tuple[dict | None, list[str]]:
    text, receipt, problems = render(binding)
    if problems or text is None or receipt is None:
        return receipt, problems
    path = project / SCAN_MODE_SDC_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text)
    return receipt, []


def validate(project: Path, binding: dict,
             receipt: dict | None) -> tuple[bool, str]:
    expected, expected_receipt, problems = render(binding)
    if problems:
        return False, "; ".join(problems)
    if expected is None:
        return (receipt is None,
                "L20 declares no scan-shift frequency" if receipt is None else
                "receipt claims scan-shift timing that current L20 does not declare")
    if not isinstance(receipt, dict) or receipt != expected_receipt:
        return False, "scan-mode receipt differs from the current L20 contract"
    path = project / SCAN_MODE_SDC_REL
    try:
        actual = path.read_bytes()
    except OSError:
        return False, "scan-mode SDC is missing"
    if actual != expected:
        return False, "scan-mode SDC bytes differ from the current L20 contract"
    return True, "scan-mode SDC is current and source-bound"
