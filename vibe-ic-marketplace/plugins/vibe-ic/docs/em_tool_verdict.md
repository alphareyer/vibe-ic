# Step 25: OpenROAD power-grid EM verdict

On the DIE path, step 25's direct engine is OpenROAD. LibreLane has no EM step, so
OpenROAD `check_current_density` supplies the power-grid verdict and the
existing blocking authority gate audits its native evidence. The retained
Python density screen is disclosed for comparison while HARVEST completes.
No retained density calculation or geometry extraction is deleted here.
The existing direct routing/admission label stays intact. One shared native
authority predicate drives DIE report regeneration, production and consumption;
an explicit dual selection enables the same audit outside the DIE path.

The auditor reads each net's native `Verdict` and CSV `Status`, `Cuts`, and
`Basis`. Every solved resistor must have a report row. `NO_AREA`, `NO_LIMIT`,
missing rails, absent capability, empty reports, changed inputs, unbound image
identity, and stale or altered outputs cannot produce PASS. A measured native
violation still FAILs. Missing evidence retains the existing authority gate's
INCOMPLETE/rc 2 vocabulary, with the tool audit explicitly NOT_MEASURED.

The invocation records the executing container's image ID and the actual tool
version and executable hash. Command/deck hashes and every loaded file's hash
are printed in the tool session log. Input hashes must agree before and after
the command; SDC/SPEF must remain unchanged and the power basis must be complete.
Output hashes bind the native log, per-net resistor files and density CSVs.
The A/B disclosure reads freshly audited rows, rather than cached summaries.

Routing limits retain the LEF DCCURRENTDENSITY/THICKNESS conversion and 10%
margin. Per-cut limits come from the PDK registry overlay, never a fallback
literal in the runner. The GF180MCU entry cites DRM section 14.2, Table 14.4:
0.18 mA per 0.26 um via at 125 C, unidirectional current. The source revision,
source-file SHA, temperature and lifetime basis travel with each limit.
Document commit/file hashes are typed as `git:` and `sha256:` identities;
they cannot masquerade as the revision of the installed PDK. Original raw-hex
receipts of those exact document hashes remain auditable without rewriting them.
The auditor rederives authority from the hashed technology LEF and trusted
registry; a self-consistent but inflated report limit cannot pass.

This is a power-grid DC EM measurement on the stated SDC/SPEF and source model.
Signal EM remains NOT_MEASURED. The currently released official image lacks
the complete native report capability and must therefore remain NOT_MEASURED;
merged source alone does not establish that a released image contains it.

## HARVEST destinations

| Row | Retained lesson | Destination and deletion condition |
| --- | --- | --- |
| 1 | Layer-specific J limits, including vias | OpenROAD #37/#40 plus the native report audit; require measured shipped capability |
| 2 | DRM per-cut authority and provenance | PDK registry `em_limits` overlay and independently rederived audit |
| 3 | LEF routing units and margin | OpenROAD #37 LEF-native units; explicit existing margin in the limits writer |
| 4 | Drawn width rather than LEF minimum | OpenROAD #37/#40 drawn-shape implementation |
| 5 | Via enclosure metal absent from DEF wires | OpenROAD #37/#40 ODB geometry |
| 6 | Abutting IO/pad PG pin metal | OpenROAD #37 padded-ring test and native drawn rectangles |
| 7 | Width across a chain of abutting rectangles | OpenROAD #37/#40 narrowest-position rule and spatial index |
| 8 | OBS is not current-carrying geometry | Native PSM uses wire and port metal; retained code unchanged |
| 9 | Empty or unjudged population never clean | Native skip counters plus CSV/count/verdict audit |
| 10 | Current conservation against net supply | Existing `em_peak_current_authority_check` stays |
| 11 | Same layout and fresh invocation | DEF/input/output hashes, timestamps and command/deck/image evidence |
| 12 | Both power and ground rails | Expected rails independently read from DEF; one report per net |
| 13 | A/B cannot promote partial tool results | Retained A/B reader consumes fresh audited rows |
| 14 | State actual source/power model | Existing model and power-basis record; reject incomplete or changed SDC/SPEF |
| 15 | Pre-route PDN search | Separate retained pre-route sizing code; outside this deletion scope |
| 16 | Supply pad planning from measured current | Separate retained planner; outside this deletion scope |

All custom arms remain present. Retirement needs completed destination reviews
and a measured official image, including the original design-relevant scopes.
