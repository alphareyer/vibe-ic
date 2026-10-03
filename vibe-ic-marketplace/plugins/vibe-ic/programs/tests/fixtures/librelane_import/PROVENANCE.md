# librelane_import fixtures (llv1 W6)

Trimmed from the two LibreLane arms of the CMP3 3-tool comparison. Both were
read only, and neither source was written:

| Fixture | Host | Source run | Source flow.log sha256 |
|---|---|---|---|
| `8HD-4/runs/cmp3` | 192.168.1.120 | `~<user>/cmp3/librelane_8HD-4/design/runs/cmp3` | `303263b98a32083a0beefacbbcbc757e36615b65f20d888efa6636f34e466dc2` |
| `8HD-9/runs/cmp3` | 192.168.1.105 | `~<user>/cmp3/librelane_8HD-9/design/runs/cmp3` | `35ae73fe4a6daf99b8ad021ca2b6520a538f5df8efddd262ecaefac81fcc5994` |

Each run: `librelane --manual-pdk --pdk gf180mcuD --run-tag cmp3 config.yaml`
(LibreLane 3.1.0.dev1, Classic flow), rc 0, `Flow complete.`.

The two typed sign-off payloads were recovered from those producer runs in
full, rather than retained as bounded log previews.  Both producer runs have
the same bytes for each report (the host-specific run identity remains in the
run tree):

| Report | Bytes | SHA256 | Intended result |
|---|---:|---|---|
| `65-klayout-drc/reports/drc.klayout.json` | 14342 | `c904540dc1752065161d65a76e446d9ad469884028410273e21ffce70446e193` | `total: 0` |
| `70-netgen-lvs/reports/lvs.netgen.json` | 7746 | `590040e59a132a0c7d58d3472c23478a86e9bf8f550d4975582be8e2712ef022` | E1 `verdict: match` |

The producer tool identity recorded with the runs is LibreLane v3.1.0.dev1,
KLayout 0.30.12, Netgen 1.5.324, Magic 8.3.684, OpenROAD
26Q3-3002-gda11f47e14, Yosys 0.69+ (git `4d572059c`), Python 3.12.3, and
PDK `gf180mcuD`.  The four prior 4096-byte report blobs remain under
`negative_controls/` with their original hashes and a `.json.preview` suffix.

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
    every file a rule imports, except the complete typed DRC/LVS JSON reports
    listed above.
- For every OpenROAD/Odb step from the first `OpenROAD.Floorplan` up to
  `OpenROAD.RCX` (the PnR span, STA sessions included): the first 4096 bytes
  of its tool logs.
- Every byte string naming the home directory (`/home/<user>/`) became `/host/` (shipped files may not
  name a home directory). The recorded run roots therefore read
  `/host/cmp3/librelane_8HD-*/design/runs/cmp3`.

Payload files other than the two typed DRC/LVS JSON reports are cut to 4096
bytes where stated above. The importer copies and binds bytes; it does not
claim that a cut DEF, SPEF or GDS is the measurement of the real run. At the
two typed report boundaries the importer decodes the producer JSON and refuses
an incomplete object before writing its canonical copy.

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
