"""A STATED EDA image identity, for tests that reach `librelane_contract.resolve_image`.

`resolve_image` has no stored fallback. With no switch `image` and no
`VIBEIC_LIBRELANE_IMAGE`, it asks the plugin's one runtime resolver,
`_eda_pin.image_reference()`, which asks THIS HOST's docker. Unstubbed, a test
that never declares an image therefore inherits a host fact:
- on a docker host it resolves whatever image that host holds, and passes;
- inside the vibeic-eda image (no docker) it refuses `LL_IMAGE_NOT_RESOLVABLE`.
The same test would then give two verdicts depending on where it ran.

This is the pattern `_runtime_pair_fixture` uses (v1.25.5): the identity is
stubbed at `_eda_pin`, so `resolve_image` still composes its own reference
through the real resolver path. It is per-module on purpose, NOT a conftest
autouse, because a suite-wide stub would also disable the resolver for tests
written to exercise it (`test_librelane_state_bridge` refuses by name when it
does not resolve).

Not a real image, on purpose: a path that tried to run it fails loudly instead
of borrowing a host image.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _eda_pin as _pin  # noqa: E402

__all__ = ["STATED_DIGEST", "stated_image", "state_the_image"]

STATED_DIGEST = "sha256:" + "7c" * 32


def stated_image(digest: str = STATED_DIGEST) -> str:
    """The reference `resolve_image` composes from the stated identity."""
    return f"{_pin.IMAGE_REPO_DEFAULT}@{digest}"


def state_the_image(monkeypatch, digest: str = STATED_DIGEST) -> str:
    """Make `_eda_pin` answer `digest` without asking docker; returns the reference."""
    monkeypatch.delenv(_pin.IMAGE_REPO_ENV, raising=False)
    monkeypatch.setattr(_pin, "resolved_image_digest",
                        lambda env=None, *, allow_pull=False: digest)
    # `image_reference` names only a reference this host HOLDS for the digest.
    # A stated host is not asked what it holds either: whatever digest the
    # (stated) resolver answers is held under the published repository.
    monkeypatch.setattr(_pin, "local_references_for_digest",
                        lambda d: ((stated_image(d),), ""))
    return stated_image(digest)
