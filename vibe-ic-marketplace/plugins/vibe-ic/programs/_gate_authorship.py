#!/usr/bin/env python3
"""WHO INVOKED THIS PROGRAM -- the one fact its document cannot otherwise carry.

R-0915-152, third cut, reader side. `flow_compliance_check` has to answer, for a declared
`required_output` that is also this step's own gate `--json` target, "did the RUN produce
this, or did the AUDIT?". Three earlier cuts each answered it with something the document
or the tree could not actually say:

  * TIMING (`absent before the gate`) -- which made the verdict depend on how many passes
    had run: MISSING once, PASS forever after;
  * DELETING what the gate wrote (#1981) -- which made the auditor mutate the tree it
    audits, and only half worked anyway;
  * the document's own PROGRAM NAME (#2005) -- which works only where the program that
    writes the document is not ALSO a program the flow lists under that step's
    `programs:`.

MEASURED on the shipped flow (70 steps): of the 33 declared-output/own-gate-`--json`
pairs, **28, across 22 steps, are that last shape** -- the writer is both a declared
producer of the step and the step's own gate, so it writes a byte-identical document
whichever side invoked it, and `_is_gate_verdict_document` correctly refuses to guess.
For all 28 the question falls through to the timing facts, which means the auditor's
authorship NOTE is the only thing answering it. That is why hardening the note did not
close the finding: the note is not a corroborating record, it is the sole record.

A PROGRAM CANNOT NAME ITS OWN ROLE. The same checker is run by the orchestrator so its
exit status can block (#306) AND by the audit as a gate clause; nothing inside it differs
between the two. So the CALLER states the role, per spawn, and the callee stamps what it
was told. A program invoked with no role stated is a PRODUCER -- that is the run's own
path, and it must stay the default so that nothing outside an audit changes behaviour.

WHY THIS IS NOT IN `_atomic_artefact`. That module's `write_text` says, in its own
docstring, "this module is not the place to also alter what gets written", and it is
right: a general-purpose atomic writer that silently edits payloads would stamp every
artefact a gate subprocess happens to write, not the one document the question is about.
Stamping is therefore a deliberate call a writer makes on its own payload, beside the
`verdict` it is already composing.

chip-AGNOSTIC: nothing here reasons about any IC, vendor, SKU or process.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

__all__ = [
    "ROLE_ENV",
    "ROLE_AUDIT",
    "ROLE_PRODUCER",
    "DOC_KEY",
    "caller_role",
    "child_env_additions",
    "stamp",
    "role_of",
]

#: Set by the CALLER on the subprocess it spawns, never by the program about itself.
ROLE_ENV = "VIBEIC_FCC_ROLE"

ROLE_AUDIT = "audit"
ROLE_PRODUCER = "producer"

#: The key a stamped document carries. Top level, beside `verdict`: a reader that already
#: descends one level for an identity stamp finds it without a second convention.
DOC_KEY = "invoked_as"

_ROLES = (ROLE_AUDIT, ROLE_PRODUCER)


def caller_role() -> str:
    """The role this process was invoked in. PRODUCER unless a caller said otherwise.

    Defaulting to producer is the whole safety property: an unstated role is the run's
    ordinary path, so no program outside an audit-spawned gate changes what it writes.
    An unrecognised value is also producer -- a typo in an environment must not silently
    reclassify a run's evidence as the auditor's.
    """
    got = os.environ.get(ROLE_ENV, "").strip().lower()
    return got if got in _ROLES else ROLE_PRODUCER


def child_env_additions(invocation_env: str, invocation_id: str) -> Dict[str, str]:
    """What an AUDIT adds to the environment of a gate subprocess it spawns.

    Returned rather than applied, because the auditor's own `main` is called IN PROCESS by
    the `stageN_compliance` wrappers: mutating `os.environ` would outlive the call that
    made it and mark a later, unrelated spawn as the auditor's.
    """
    return {ROLE_ENV: ROLE_AUDIT, invocation_env: invocation_id}


def stamp(doc: Any, role: Optional[str] = None) -> Any:
    """Record in `doc` the role its writer was invoked in. Returns `doc`.

    Additive and idempotent: one new top-level key on a mapping, nothing reordered and
    nothing removed, so a consumer reading any existing field is unaffected. A payload
    that is not a mapping is returned untouched -- there is nowhere honest to put the key,
    and inventing a wrapper object would change the document's shape for every reader.
    """
    if not isinstance(doc, dict):
        return doc
    doc[DOC_KEY] = role if role in _ROLES else caller_role()
    return doc


def role_of(doc: Any) -> Optional[str]:
    """The role a document states it was written in, or None when it does not say.

    None is NOT producer. It is "this document predates the stamp, or its writer has not
    been taught it" -- a different answer, and the caller must fall back to whatever it
    did before rather than read silence as a claim.
    """
    if not isinstance(doc, dict):
        return None
    got = doc.get(DOC_KEY)
    if isinstance(got, str) and got.strip().lower() in _ROLES:
        return got.strip().lower()
    return None
