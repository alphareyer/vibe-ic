# Project-local FMEDA mechanism declaration

Status: implemented bounded codeword-boundary measurement, 2026-10-20. Owner: `fmeda_fault_injection_coverage.py`; ordinary consumer: `fmeda_coverage_check.py`. This is an author engineering declaration of implementation choices, not a requirement, safety verdict, correction capability, or full FMEDA claim.

The optional input is `phase2/stage1/fmeda_mechanisms.json`. Absence preserves existing auto-detection and classification. A present invalid declaration refuses; it never falls back to auto-detection. A previously declared report cannot be consumed after declaration removal.

```json
{
  "schema": "vibeic.fmeda-mechanisms.v1",
  "authority": "AUTHOR_ENGINEERING_DECLARATION",
  "top_module": "component",
  "rtl_files": [{"path": "phase2/stage1/rtl/component.sv", "sha256": "<64 lowercase hex>"}],
  "mechanisms": [{
    "id": "payload_integrity", "kind": "parity",
    "encoder": {"module": "payload_encode", "instance": "component.checker.encoder", "input": "data_i", "output": "code_o"},
    "checker": {"module": "payload_check", "instance": "component.checker", "input": "code_i", "detect": "err_o", "corrected_output": null},
    "data_width": 3, "code_width": 4
  }]
}
```

The exact current authored RTL/header inventory is collected by `_specrtl_common.rtl_source_files`; declaration order is recorded, and `testbench_gen.package_first_order` supplies only the native compilation order. No excluded or unlisted file, duplicate key/ID/path, symlink, path escape, unsupported port, invalid integer, or missing source is admissible. Top must equal `p0_tool_frontend_check._top(project)`, the existing L9 reader also used by `_cdc_netlist.source_binding`; the declaration cannot choose an alternate root. L9 bytes are part of the input identity.

The L9 identity binds the selected top and current document bytes. Top-level L9 port conformance remains owned by the existing P0/spec-conformance checks; this capability measures the declared mechanism leaf ports. The encoder may be the checker's internal expected-tag recomputation. Its reachable identity calibrates a standalone codeword model, not a claim that its output is the received-codeword wire or that the campaign injected an in-place whole-DUT instance.

A fresh bounded Yosys `read_slang --keep-hierarchy --single-unit --top <selected>; hierarchy -check -top <selected>; proc; write_json` run, without flatten/ABC/mapping, must establish each declared instance is reachable from that top and has the declared module and literal measured port directions/widths. All input ports of the standalone combinational mechanism must be accounted for; unsupported shapes refuse. The native artifact, command, return code, tool identity, source population, top, and declaration hashes are retained. The ordinary consumer rechecks current identity and validates the bound native hierarchy. It does not trust regex success, a self-reported width, or a PASS label.

Parameterized encoder/checker modules and their reachable module dependencies are currently unsupported, including a parameter whose override preserves all port widths. Slang's lowered JSON omits effective parameter values while standalone FI would instantiate defaults. A bounded source-declaration check therefore refuses such selected subtrees in both producer and consumer, including declarations containing macro/directive tokens that could expand into parameters; it never treats an absent regex match as native elaboration proof and does not implement macro expansion. Unrelated parameterized implementation modules remain permitted. Supporting effective specialization requires a separate explicit tool-derived parameter binding, not guessed defaults.

Each mechanism runs the existing real Icarus single-bit engine independently. Every selected stimulus requires one known fault-free encoding and DETECT=0. Detection-only mode requires `corrected_output:null` plus an explicit detect port: MATCH is typed `NA`, never invented 1; each fault is covered only when DETECT=1. Correction-capable mode retains the old DETECT OR MATCH rule. Explicit complete CLI wiring may also establish detection-only mode; incomplete/auto-guessed missing correction does not.

The expected census is the Cartesian product of the deterministic stimulus list and every bit `[0,code_width)`. Full baseline and fault rows plus raw transcript are retained without the legacy 512-row preview cap. The consumer recomputes census uniqueness/completeness, known baseline, row coverage, DC, and ASIL floor for every mechanism separately. No weighted average can mask a failing mechanism. It replays the retained raw transcript and compares all rows/counts; missing rows, invented match, baseline false alarm, altered source/ports/instance/top/declaration/native artifact, or stale evidence refuse.

The consumer's ordinary CLI/flow policy defaults to ASIL D. A non-D ASIL or explicit `--min-dc` must also be supplied to the consumer through its own CLI/flow invocation. The consumer refuses a report whose ASIL or effective floor differs from that independent policy; the report cannot lower its own acceptance threshold.
The same policy applies to complete explicit CLI measurements without a project manifest, including detection-only reports.
For measured legacy reports without ASIL/source fields, the consumer still uses D by default; no editable report marker selects the policy.

## Embedded parity functions and protocol endpoints

An endpoint can implement its check inside a local or package function, with no encoder module. Such a mechanism may replace the `encoder` member with the following typed `stimulus` member in the existing v1 declaration:

```json
{
  "id": "request_integrity", "kind": "parity",
  "checker": {
    "module": "protocol_endpoint", "instance": "protocol_endpoint",
    "input": "request_i", "detect": "fault_o", "corrected_output": null,
    "constant_inputs": {
      "clk_i": {"width": 1, "value": 0},
      "rst_n": {"width": 1, "value": 1}
    }
  },
  "data_width": 4, "code_width": 5,
  "stimulus": {"encoding": "even_parity", "data_bits": [0, 1, 2, 3], "check_bit": 4}
}
```

`data_bits[i]` identifies the codeword bit carrying stimulus payload bit `i`; `check_bit` carries its reduction parity. `even_parity` and `odd_parity` are the supported reference encodings. The payload and check bits must cover the entire codeword exactly once. This authority comes from the explicit engineering declaration, never a mechanism guess from a port shape, comment or prose mention.

The native hierarchy must resolve the actual endpoint module at the selected top or a reachable instance and match the protected input and one-bit detector output. All other inputs require exact-width, known constant bindings. Other endpoint outputs may be left unobserved and are not correction evidence. The existing combinational, parameter-free contract still applies; stateful protocols and other unsupported required mechanisms refuse as unmeasured rather than becoming N/A. The complete declared mechanism list is measured independently, so an unsupported required row cannot disappear behind another passing row.

The existing Icarus engine instantiates this original endpoint RTL, including its original local/package function and declared constants. Only the valid reference codewords and single-bit input faults are generated by the testbench; no substitute checker is synthesized as DUT evidence. Every valid codeword must have a known encoding and a quiet detector before any fault result is accepted. A tied-low detector measures zero coverage and fails the unchanged floor; a tied-high detector or wrong parity convention fails the baseline. MATCH remains typed `NA`. The ordinary consumer rechecks the same source, typed stimulus, hierarchy, complete raw census and independent ASIL/DC policy.

An absent manifest keeps the existing non-safety classification; this feature does not assign ASIL requirements to ordinary ICs. These measurements establish detection coverage at the declared endpoint input boundary, not whole-chip FMEDA, sequential protocol coverage, physical-instance injection or functional correctness of unobserved outputs.

Required controls: two reachable neutral mechanisms; native width/type/direction/unreachable and source/hash changes; duplicate keys/IDs; declaration removed; baseline false alarm; disabled detector for each mechanism; >512 faults with a row removed; per-mechanism floor masking; byte restoration; correction-mode compatibility; unchanged absent-declaration classification. No global gate, policy/threshold, version, or auto-pairing repair is included. Scope is the measured codeword boundary and current reachable implementation modules, not whole-chip safety qualification or FIT/SPFM/LFM/PMHF.
