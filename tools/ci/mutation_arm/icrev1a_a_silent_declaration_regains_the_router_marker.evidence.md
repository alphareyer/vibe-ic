# icrev1a — MUTATION ARM, all three runs (census cause C11)

Lane `icrev1`, host **8HD-d (192.168.1.112)**, image
`ghcr.io/vibeic/vibeic-eda:0.3.67` (id `4e9f54efe77a`), base
`cbca058b30624a9f2ccc5e127e7b77f72e98af0a` (tree `722be1e9c3cc`), 1-min load
under 1, every arm run ALONE. Selector = the eight files in this directory that
name `_effective_deliverable`, `_declared_deliverable`,
`publish_tapeout_declarations` or `_delivery_admission_refusal`:

    test_die_top_cell_is_the_layouts_not_the_cores.py
    test_ct03_floorplan_rect_and_tapeout_declarations.py
    test_hardmacro_delivery_ships_no_die_seal_ring.py
    test_issue2118_silence_is_not_a_refused_deliverable.py
    test_issue2118_a_hardmacro_has_no_die_to_declare.py
    test_r0915_95_an_agents_answer_is_not_a_declaration.py
    test_r0920_phase3_main_restores_the_container_env.py
    test_phase3_delivery_admission.py

    tree R  = live main, untouched
      -> 1 failed, 121 passed, 1 skipped in 20.68s                       RED
         FAILED ...test_hardmacro_delivery_ships_no_die_seal_ring.py::
                test_a_silent_declaration_is_NOT_DECLARED_and_no_derivation_replaces_it
         AssertionError: HARDMACRO / assert 'HARDMACRO' is None

    tree G  = main + this branch
      -> 122 passed, 1 skipped in 20.17s                                 GREEN

    tree M  = G + icrev1a_a_silent_declaration_regains_the_router_marker.patch
      -> 3 failed, 119 passed, 1 skipped in 20.31s                       RED
         FAILED ...::test_a_silent_declaration_is_NOT_DECLARED_and_no_derivation_replaces_it
         FAILED ...::test_a_template_nobody_answered_is_not_answered_by_the_derivation
         FAILED ...::test_a_silent_declaration_gets_NEITHER_name_even_with_a_route_stated

The skip is the same node in all three arms: this host has no container route,
so one PDK-diversion case reports NOT_VERIFIED. It is NOT a pass and it is not
counted as one.

## What M puts back, and what that shows

M is four lines: the `owner_attestation_refusals` escape that #2118 added, which
returns the derivation whenever the declaration is silent. It re-reddens C11's
own test AND both re-pinned cases — i.e. the three assertions that disagree with
each other on main can be moved together and only together. A check that cannot
fail is not a check, and this one fails on exactly the edit it is about.

The two publisher cases in
`test_issue2118_a_hardmacro_has_no_die_to_declare.py` stay GREEN under M. That is
deliberate and is the point of the fixture repair in the same commit: those tests
are about naming the rectangle under the deliverable the project DECLARES, and
they no longer depend on the resolver's silent path at all.

## Membership, not counts

    collected on R : 123 nodes   evidence/c11_nodes_main.txt
    collected on G : 123 nodes   evidence/c11_nodes_fixed.txt
    diff R -> G    : nothing added, nothing dropped, exactly two RENAMES
      test_a_template_nobody_answered_uses_the_derivation
        -> test_a_template_nobody_answered_is_not_answered_by_the_derivation
      test_a_silent_declaration_still_gets_its_size_rectangle
        -> test_a_silent_declaration_gets_NEITHER_name_even_with_a_route_stated

## The two measurements behind the verdict, reproducible

`evidence/probe_c11_derivation_source.py` — with nothing monkeypatched, a silent
declaration yields `_declared_deliverable = None` when no router artefact is on
disk, `DIE` with `input/submission_template/slots/*.yaml`, and `HARDMACRO` with
`input/submission_template/NO_TEMPLATE.txt`. The silent case and the stale-marker
case are the same case.

`evidence/probe_c11_publisher_real.py` — the publisher on a real fresh project
with the REAL `_declared_deliverable`, WITH #2118's change in place:
`published = ['top_cell']`, `die_area_um` and `macro_area_um` both in
`not_determined`. The consequence #2118 was written for does not arrive on the
population it names.

Both scripts are on the lane host at `/home/reyerchu/_lane_icrev1/evidence/`.

## RULING NEEDED

This branch and #2118 cannot both stand. It inverts two landed assertions in
favour of the owner ruling R-0915-95; the alternative is to delete or re-pin
`test_a_silent_declaration_is_NOT_DECLARED_and_no_derivation_replaces_it`, which
is the ruling's own test. Named here so the choice is visible, not buried.
