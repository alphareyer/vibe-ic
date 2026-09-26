"""A MATCHING runtime pair, for fixtures that measure what happens AFTER fan-out.

#2120 made `benchmark_dispatch` reconcile three runtime identities — the pinned
image is present, the container a run selects exists, and its digest IS the pin
— once, before it launches the first worker. That check is a live question about
the MACHINE, and the behavioural fixtures around the coordinator are not about
the machine: they are about ordering, re-entry, routing and the AI-review
handshake, all of which happen after the pair has already agreed.

WHY A STUB IS THE HONEST ANSWER HERE, AND NOT A WEAKENING
=========================================================
Left unstubbed, every one of those fixtures inherits a host fact. On a host
holding the pinned image with the derived container present they pass; on a host
where the container has not been created they all refuse before reaching the
behaviour under test — the same verdict for a routing bug and for a container
nobody started. That is the host-dependence this repo refuses to build verdicts
on, and it would be READ as a regression in whichever module happened to run.

Several of these fixtures also replace `subprocess.run` ON THE MODULE OBJECT to
fake the runner. `_eda_pin` reaches docker through that same module, so an
unstubbed pair would be answered by a runner fake that knows nothing about
docker and fails in a way that says nothing about either.

WHAT THIS DOES NOT DO. It does not touch the gate, the pin, or any threshold,
and it is deliberately per-module rather than an autouse fixture in a shared
`conftest`: a suite-wide stub would silently disable the check for every test
written after it, including one written to exercise it. The MISMATCH direction —
absent container, a container running a build that is not the pin, matching
pair — is measured in `test_issue2120_runtime_pair_preflight.py`, against the
pin and the PREVIOUS pin. Neither of those is a literal any more: a version
label, and the digest that carried it, both went stale under a pin move (#2163).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _eda_pin as _pin  # noqa: E402

__all__ = ["PAIR_DIGEST", "assume_matching_runtime_pair"]

#: THE IDENTITY THE PAIR AGREES ON, stated rather than resolved. Reading
#: `_pin.IMAGE_DIGEST` here resolves it FROM THIS HOST (b4b098acc): with a cold
#: per-process cache and no override env that is `docker image ls --digests`,
#: sent through whatever `subprocess.run` fake the calling module installed --
#: an AttributeError on a SimpleNamespace, an IndexError on an argv, or a
#: refusal naming an undescribed route, depending on the fake. Whether it
#: happened depended on which test warmed the cache first, so these fixtures
#: went red alone and green in a suite. A resolve that DID succeed also
#: exported `VIBEIC_EDA_IMAGE` into `os.environ` outside `monkeypatch`, leaking
#: a host identity into every later test. Not a real image, on purpose: a path
#: that tried to run it would fail loudly instead of borrowing a host image.
PAIR_DIGEST = "sha256:" + "5a" * 32


def assume_matching_runtime_pair(monkeypatch) -> None:
    """State the precondition: the pinned bytes are here and hold the container.

    Stubbed at `_eda_pin`, not at the preflight or the dispatcher, so the module
    under #2120 still composes its own verdict, its own codes and its own
    evidence from these answers. The identity itself is stubbed too, so no
    answer here is a question put to this host's docker. A stub of `preflight()` itself would let
    the composition rot untested.
    """
    monkeypatch.setattr(_pin, "resolved_image_digest",
                        lambda env=None, *, allow_pull=False: PAIR_DIGEST)
    monkeypatch.setattr(_pin, "pinned_image_present",
                        lambda env=None: (f"repo@{PAIR_DIGEST}", ""))
    monkeypatch.setattr(_pin, "container_image_digest",
                        lambda _container: (PAIR_DIGEST, ""))
