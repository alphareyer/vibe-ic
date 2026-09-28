"""Owner approval required before a flow-step waiver can affect a verdict.

The dated statement is a reviewable record, not a cryptographic signature.
Automation may report a proposed deferral but may not supply this approval.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Mapping

OWNER = "reyerchu"
MACHINE_MARKERS = ("auto_synthesized", "_autogen", "_env_unavailable",
                   "_fpga_skip", "_pdk_substitution")


def refusal(entry: Any, document: Any = None) -> str:
    """Return a reason for refusing a waiver, or empty string when owner signed."""
    if not isinstance(entry, Mapping):
        return "entry is not an object"
    if any(entry.get(key) is True for key in MACHINE_MARKERS):
        return "machine-generated waiver marker is present"
    if isinstance(document, Mapping) and document.get("_generator") in {
            "waivers_materialize.py", "phase3_one_shot_runner.py"}:
        return "waiver document was machine-generated"
    if entry.get("approver") != OWNER:
        return "approver is not the owner reyerchu"
    stamp = entry.get("approved_at")
    if not isinstance(stamp, str) or not stamp.strip():
        return "owner approval date is missing"
    try:
        datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        try:
            date.fromisoformat(stamp)
        except ValueError:
            return "owner approval date is not ISO-8601"
    statement = entry.get("owner_statement")
    if not isinstance(statement, str) or len(statement.strip()) < 20:
        return "owner's approval statement is missing"
    return ""
