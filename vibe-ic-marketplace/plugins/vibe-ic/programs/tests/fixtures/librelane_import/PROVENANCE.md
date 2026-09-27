# librelane_import fixtures (llv1 W6)

Trimmed from the two LibreLane arms of the CMP3 3-tool comparison. Both were
read only, and neither source was written:

| Fixture | Host | Source run | Source flow.log sha256 |
|---|---|---|---|
| `8HD-4/runs/cmp3` | 192.168.1.120 | `~<user>/cmp3/librelane_8HD-4/design/runs/cmp3` | `303263b98a32083a0beefacbbcbc757e36615b65f20d888efa6636f34e466dc2` |
| `8HD-9/runs/cmp3` | 192.168.1.105 | `~<user>/cmp3/librelane_8HD-9/design/runs/cmp3` | `35ae73fe4a6daf99b8ad021ca2b6520a538f5df8efddd262ecaefac81fcc5994` |

Each run: `librelane --manual-pdk --pdk gf180mcuD --run-tag cmp3 config.yaml`
(LibreLane 3.1.0.dev1, Classic flow), rc 0, `Flow complete.`.

## What the trim kept, and what it changed
- `flow.log`: whole.
- `resolved.json`: only `DESIGN_NAME`.
- Every step folder that flow.log started: its `config.json`, reduced to
  `meta` (the step class the importer cross-checks). The one exception is
  `54-openroad-rcx/config.json`, which also keeps the step's own
  `DEFAULT_CORNER` (`nom_tt_025C_5v00` in both source runs, read from each
  source's own `54-openroad-rcx/config.json`): the importer takes the nominal
  SPEF from it rather than from a corner literal.
- For the LAST run of each step class that an `IMPORT_RULES` entry names:
  - `runtime.txt`;
  - `state_out.json` without `metrics`;
  - the first 4096 bytes of the step's own tool logs (`_step_logs`) and of
    every file a rule imports.
- For every OpenROAD/Odb step from the first `OpenROAD.Floorplan` up to
  `OpenROAD.RCX` (the PnR span, STA sessions included): the first 4096 bytes
  of its tool logs.
- Every byte string naming the home directory (`/home/<user>/`) became `/host/` (shipped files may not
  name a home directory). The recorded run roots therefore read
  `/host/cmp3/librelane_8HD-*/design/runs/cmp3`.

Payload files are cut to 4096 bytes. The importer copies and binds bytes; it
does not parse the payloads. The artefact-derived measurement of a cut DEF,
SPEF or GDS is not the measurement of the real run.

Two consequences of the cut, which the tests account for:
- Several DEFs share their first 4096 bytes. For example, the routed and
  filled DEF heads are byte-identical, so a digest match binds either one to
  either step's row.
- A cut netlist, SPEF or GDS reads `measured: false` to
  `_runner_measurement`. On the real run it does not.

## Why the run trees are tracked (review of W6)
`.gitignore`'s `runs/` rule had dropped all 764 run files, so the branch once
shipped this file alone. A narrow negation (the #1744 pattern) re-includes
exactly `librelane_import/*/runs/`; nothing else under `tests/` is swept in.
Both trees are small (about 0.9 MB each; the largest file is `flow.log`,
28 KB), so nothing further was trimmed.
