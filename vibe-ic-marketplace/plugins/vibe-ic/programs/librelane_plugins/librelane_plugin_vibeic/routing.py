"""Step 21 (routing, lane mig99): two vibe-ic steps around LibreLane's router.

``Vibeic.DetailedRoutingSeeded`` is ``OpenROAD.DetailedRouting`` with the
router's seed as a declared variable. LibreLane's ``drt.tcl`` hard-codes
``-or_seed 42``; a seed is a PPA lever (review70 step 21: a third, seed arm),
so this step runs the image's OWN ``drt.tcl`` with exactly that one literal
replaced by ``$::env(VIBEIC_DRT_OR_SEED)``. If the image's script does not
carry the literal exactly once, the step refuses instead of guessing where
the seed goes. Everything else (the antenna loop, the per-run ``drt-run-N``
directories, the KLayout marker database) is the stock step's.

``Vibeic.NamedViolationReroute`` runs after the detailed route on its ODB:
the runner's named-violation reroute (``phase3_one_shot_runner.
_named_violation_reroute_tcl``, one copy, written to
``VIBEIC_NVR_TCL`` by the caller) rips up ONLY the nets the router's own
marker report names and re-routes them, bounded, inside a wire transaction
that rolls back a pass that made the count worse. It targets DRT-0701, where
post-route verification publishes violations the routing loop never saw.
The step copies the detailed route's report (``VIBEIC_NVR_DRC_REPORT``) to
the path the Tcl was emitted for (``VIBEIC_NVR_REPORT_PATH``) and records the
count before and after as metrics; it never rewrites a count.
"""
from __future__ import annotations

import os
import re
import shutil
from typing import Optional, Tuple

from librelane.common import Path
from librelane.config import Variable
from librelane.state import State
from librelane.steps.openroad import DetailedRouting, OpenROADStep
from librelane.steps.step import MetricsUpdate, Step, StepError, ViewsUpdate

__all__ = ["DetailedRoutingSeeded", "NamedViolationReroute"]

#: The one line of the image's `drt.tcl` that fixes the seed.
SEED_LITERAL = re.compile(r"(?m)^(\s*lappend drt_args -or_seed) 42\s*$")


def seeded_script(stock: str) -> str:
    """The stock detailed-route script with its seed taken from the config."""
    hits = SEED_LITERAL.findall(stock)
    if len(hits) != 1:
        raise ValueError(
            f"drt.tcl carries {len(hits)} `lappend drt_args -or_seed 42` lines; "
            "the seed cannot be located")
    return SEED_LITERAL.sub(r"\1 $::env(VIBEIC_DRT_OR_SEED)", stock)


def marker_count(path: str) -> Optional[int]:
    """Markers in a TritonRoute `-output_drc` report (one `violation type:`
    line each); None when there is no report."""
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf8", errors="replace") as stream:
        return sum(1 for line in stream if "violation type:" in line)


@Step.factory.register()
class DetailedRoutingSeeded(DetailedRouting):
    """OpenROAD.DetailedRouting with the router seed as a declared variable."""

    id = "Vibeic.DetailedRoutingSeeded"
    name = "Detailed Routing (declared seed)"

    config_vars = DetailedRouting.config_vars + [
        Variable(
            "VIBEIC_DRT_OR_SEED",
            int,
            "The `detailed_route -or_seed` value. LibreLane's drt.tcl fixes 42.",
            default=42,
        ),
    ]

    def get_script_path(self) -> str:
        stock = super().get_script_path()
        with open(stock, encoding="utf8") as stream:
            text = stream.read()
        try:
            seeded = seeded_script(text)
        except ValueError as exc:
            raise StepError(f"{self.id}: {exc} ({stock})") from exc
        out = os.path.join(self.step_dir, "drt_seeded.tcl")
        with open(out, "w", encoding="utf8") as stream:
            stream.write(seeded)
        return out


@Step.factory.register()
class NamedViolationReroute(OpenROADStep):
    """Rip up and re-route only the nets the router's own report names."""

    id = "Vibeic.NamedViolationReroute"
    name = "Named-violation reroute (vibe-ic)"

    config_vars = OpenROADStep.config_vars + [
        Variable(
            "VIBEIC_NVR_TCL",
            Path,
            "The reroute pass, emitted by phase3_one_shot_runner."
            "_named_violation_reroute_tcl for VIBEIC_NVR_REPORT_PATH.",
        ),
        Variable(
            "VIBEIC_NVR_DRC_REPORT",
            Path,
            "The detailed route's own `-output_drc` report (its final run).",
        ),
        Variable(
            "VIBEIC_NVR_REPORT_PATH",
            str,
            "The report path the reroute Tcl reads and rewrites.",
        ),
    ]

    def get_script_path(self) -> str:
        return os.path.join(os.path.dirname(__file__), "named_violation_reroute.tcl")

    def run(self, state_in: State, **kwargs) -> Tuple[ViewsUpdate, MetricsUpdate]:
        target = str(self.config["VIBEIC_NVR_REPORT_PATH"])
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(str(self.config["VIBEIC_NVR_DRC_REPORT"]), target)
        before = marker_count(target)
        views, metrics = super().run(state_in, **kwargs)
        after = marker_count(target)
        shutil.copyfile(target, os.path.join(self.step_dir, "named_viol_after.drc"))
        metrics = dict(metrics)
        if before is not None:
            metrics["vibeic__route__named_reroute__markers_before"] = before
        if after is not None:
            metrics["vibeic__route__named_reroute__markers_after"] = after
        return views, metrics
