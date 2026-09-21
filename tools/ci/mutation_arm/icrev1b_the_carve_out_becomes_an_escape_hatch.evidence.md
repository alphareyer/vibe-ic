# icrev1b — MUTATION ARM, both runs

Lane `icrev1`, host **8HD-d (192.168.1.112)**, image
`ghcr.io/vibeic/vibeic-eda:0.3.67` (id `4e9f54efe77a`), base
`cbca058b30624a9f2ccc5e127e7b77f72e98af0a` (tree `722be1e9c3cc`), 1-min load
under 1. Each arm run ALONE. Selector:
`programs/tests/test_tool_gate_opens_when_the_tool_is_present.py`.

    tree R  = live main, untouched
      -> 1 failed, 222 passed in 26.13s                                  RED
         FAILED ...::test_every_defined_gate_is_registered
         AssertionError: defined but not registered:
             [('test_mcp_tool_program_path_resolves_check', '_HAVE_SOURCES')];
             registered but no longer defined: []

    tree G  = main + this branch's two-line registration
      -> 224 passed in 27.19s                                            GREEN

    tree M  = G + icrev1b_the_carve_out_becomes_an_escape_hatch.patch
      -> 1 failed, 224 passed in 27.15s                                  RED
         FAILED ...::test_the_gate_is_CLOSED_when_the_tool_is_absent[
                     test_mcp_tool_program_path_resolves_check-_HAVE_SOURCES]
         AssertionError: test_mcp_tool_program_path_resolves_check._HAVE_SOURCES
             is True while shutil.which finds nothing

## What each arm proves

R is the census's unattributed singleton, reproduced on today's main: a `_HAVE_*`
definition that neither register accounts for. It is not about the MCP program at
all -- `next/icmainred` (the gate fixture) and `next/icmainred2`/`icmainred3` (the
program) are ADJACENT to it and none of the three registers the pair; measured,
every one of those branches' copy of this file has zero occurrences of
`mcp_tool_program_path_resolves_check`.

M is the falsification of the LIST CHOICE, not of the registration. The mutation
keeps the pair registered -- `test_every_defined_gate_is_registered` stays green
in M -- and only misfiles it as `which`-keyed. `_HAVE_SOURCES` is
`(_PLUGIN_ROOT / "mcp-eda" / "src").is_dir()`, which no `which` fake can move, so
the WHICH_GATES baseline fires at once. Without M, "it is in a list" and "it is in
the RIGHT list" would be the same claim.

## Membership, not counts

    collected on R : 223 nodes   evidence/gate_nodes_main.txt
    collected on G : 224 nodes   evidence/gate_nodes_fixed.txt
    diff R -> G    : exactly one node ADDED, none removed:
      ...::test_a_declared_non_which_gate_is_really_not_which_keyed[
            test_mcp_tool_program_path_resolves_check-_HAVE_SOURCES-
            the plugin's own mcp-eda/src directory]

That added node is the point of the change: registering the pair BUYS the
exclusion guard rather than merely declaring the gate accounted for.

Node lists are kept on the lane host at
`/home/reyerchu/_lane_icrev1/evidence/gate_nodes_{main,fixed}.txt`.
