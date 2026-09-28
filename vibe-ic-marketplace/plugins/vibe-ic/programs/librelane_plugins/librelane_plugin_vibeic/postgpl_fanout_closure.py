"""Second placement repair pass for buffers created by the first pass.

The installed RepairDesignPostGPL processes the original high fanout net and
may leave its newly inserted first level buffers over the declared limit.
This step runs before DetailedPlacement, which legalizes any new cells.
"""
from __future__ import annotations

import os
from librelane.steps.openroad import RepairDesignPostGPL
from librelane.steps.step import Step


@Step.factory.register()
class PostGPLFanoutClosure(RepairDesignPostGPL):
    """Repair residual placement fanout using the active SDC and Liberty."""

    id = "Vibeic.PostGPLFanoutClosure"
    name = "Post-Placement Fanout Closure"
    def get_script_path(self) -> str:
        return os.path.join(os.path.dirname(__file__), "postgpl_fanout_closure.tcl")
