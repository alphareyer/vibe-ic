#!/usr/bin/env python3
"""stated_vector_bus_oracle_gen.py — drive a STATED vector over the design's
own memory-mapped register bus and compare against the STATED answer.

NOT A GATE. A producer imported by `testbench_gen`; it declares no
`ENFORCEMENT:` intent because it is wired into no flow clause, exactly like its
siblings `known_answer_vector_tb_gen`, `register_bus_driver_gen`,
`arith_oracle_tb_gen` and `cpu_boot_latency_oracle_tb_gen`.

WHY THIS EXISTS — the measurement, not a theory.

`known_answer_vector_tb_gen` has two routes and a design can fall between them:

  * the PORT route binds a vector field to a DUT port of the value's own
    width. A peripheral whose transport is a register file exposes no
    `message` port and no `digest` port, so it refuses.
  * the REGISTER-BUS route in `register_bus_driver_gen` drives a struct-typed
    host->device / device->host bus pair, and it refuses anything that is not
    a `(plaintext -> ciphertext)` block vector, by name:

        this driver drives a (plaintext -> ciphertext) block vector;
        got inputs=['message'] outputs=['digest']

Measured on a sha256 IC, front door: 11 of 11 L10 cases fell to the substance
floor (`VIBEIC_TB_ORACLE: NONE`), so Step 4's coverage evidence had a ZERO
denominator while the design's own input stated, for six of those cases, BOTH
the message AND the digest, and stated the whole transport it is driven over:

    cs / we / address / write_data / read_data   (L9/L3 port table)
    BLOCK<i>  W   the message-block window       (L4 summary table)
    DIGEST<i> R   the result window              (L4 summary table)
    CTRL.INIT / CTRL.NEXT / CTRL.MODE            (L4 field table)
    STATUS.READY / STATUS.VALID                  (L4 field table)

This module is the third route: a `(message -> digest)` STREAMING vector over a
scalar memory-mapped bus. It is the shape a hash, a CRC, a compressor or any
other accumulate-then-read peripheral has, and nothing in it is specific to one
algorithm, vendor, node or SKU.

EVERYTHING IS DERIVED FROM THE DESIGN'S OWN INPUT, and every derivation has a
refusal beside it:

  * the BUS signals        the DUT's own port surface, by role vocabulary
  * the input WINDOW       an indexed W register family in L4
  * the output WINDOW      an indexed R register family in L4
  * the command BITS       CTRL field names, by role vocabulary
  * the handshake BITS     STATUS field names, by role vocabulary
  * the MODE value         the CTRL.MODE field's OWN description table, keyed
                           by the STATED width of the expected answer
  * the MESSAGE            the case's own `inputs`/`stimulus`
  * the ANSWER             the case's own `expected_outputs`/`expected`

NEVER A GOLDEN. This module computes no digest, imports no reference model and
reads no oracle tree. The expected value is the LITERAL the design's own
document states; the only transform applied to the message is the padding rule
the case's own cited standard defines, which is transport framing and not the
answer. A case that does not state its answer as a literal is REFUSED BY NAME —
`long_message_1m_bytes_of_a` is drivable because its message is stated as a
repeat spec and its digest as a literal; `random_message_...` is not, because
its expected value is an acceptance PERCENTAGE over a scope and grounding it
would require computing the answer.

FAIL-CLOSED. Any missing derivation returns `(None, reason)` and the caller
falls through to the substance floor, so a case nobody can drive still fails
the Step-4 gate honestly.

chip-AGNOSTIC: the vocabulary below is open role vocabulary (chip select, write
enable, address, write data, read data; block/data-in and digest/data-out
windows; init/next/mode and ready/valid bits). No chip, vendor, node or SKU
literal takes part, and no address, bit index or width is hard-coded.
"""
from __future__ import annotations

# --- sibling-import path (vibe-ic#2104) ------------------------------------
import os as _os                                                    # noqa: E402
import sys as _sys                                                  # noqa: E402

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
# ---------------------------------------------------------------------------

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: Bus role vocabulary. Ordered: the first token that matches a port EXACTLY
#: wins over any substring match, so `write_data` never binds to `data`.
_BUS_ROLES: Dict[str, Tuple[str, ...]] = {
    "cs":         ("cs", "chip_select", "csel", "sel", "psel", "en", "req",
                   "valid_i", "cs_i"),
    "we":         ("we", "write_enable", "wr", "wen", "rw", "pwrite", "we_i"),
    "address":    ("address", "addr", "paddr", "a", "addr_i", "address_i"),
    "write_data": ("write_data", "wdata", "data_in", "din", "pwdata",
                   "write_data_i", "wdata_i"),
    "read_data":  ("read_data", "rdata", "data_out", "dout", "prdata",
                   "read_data_o", "rdata_o"),
}
#: Optional, and CHECKED when present: a design that publishes an error flag
#: states that a legal access does not raise it.
_ERR_ROLES = ("error", "err", "pslverr", "fault", "error_o")
_CLK_ROLES = ("clk", "clock", "clk_i", "i_clk", "clk_in", "aclk", "pclk")
_RST_ROLES = ("rst", "reset", "rst_n", "rst_ni", "resetn", "i_rst", "rst_i",
              "reset_n", "areset_n", "presetn")

#: The INPUT window: an indexed register family the design writes the payload
#: into. The OUTPUT window: the indexed family it reads the answer out of.
_IN_WINDOW_ROLES = ("block", "data_in", "din", "message", "input_data",
                    "wdata", "msg", "payload", "buf", "inbuf")
_OUT_WINDOW_ROLES = ("digest", "hash", "data_out", "dout", "result",
                     "output_data", "outbuf", "rdata")

_CTRL_RE = re.compile(r"(?i)^(ctrl|control|cfg|config|command|cmd)(_shadowed)?$")
_STATUS_RE = re.compile(r"(?i)^(status|state|stat)$")
#: "begin a NEW computation" and "continue the one in progress".
_INIT_FIELD = ("init", "start", "go", "run", "launch", "kick", "begin")
_NEXT_FIELD = ("next", "cont", "continue", "more", "update", "step")
#: "the unit is idle" and "the answer is complete".
_READY_FIELD = ("ready", "idle", "not_busy", "ready_for_config", "done_ready")
_VALID_FIELD = ("valid", "output_valid", "out_valid", "done", "complete",
                "data_valid", "digest_valid")
#: A field whose value selects WHICH answer width the unit produces.
_MODE_FIELD = ("mode", "variant", "algo", "algorithm", "width_sel",
               "digest_len", "size_sel")

_ID_RE = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*\Z")
#: `NAME<i>` — an indexed member of a register family.
_INDEXED_RE = re.compile(r"(?i)^(?P<base>[a-z_][a-z0-9_]*?)_?(?P<i>\d+)$")
#: A hex run in a prose stimulus: `0x6162638000...`.
_HEX_RUN_RE = re.compile(r"(?i)\b0x(?P<h>[0-9a-f]+)")
#: `... + length 24 bit` / `24-bit message` — a STATED length in bits.
_BITLEN_RE = re.compile(r"(?i)\blength\s+(?P<n>\d[\d,_]*)\s*bits?\b"
                        r"|\b(?P<n2>\d[\d,_]*)\s*-?\s*bit\s+message\b")
#: `1,000,000 x 0x61 bytes` — a STATED repeat spec.
_REPEAT_RE = re.compile(r"(?i)(?P<n>\d[\d,_]*)\s*(?:x|×|\*)\s*"
                        r"0x(?P<b>[0-9a-f]{2})\b")
#: A quoted ASCII literal message: `"abc" with MODE=0`.
_ASCII_RE = re.compile(r"[\"“「]([ -~]{1,256})[\"”」]")
#: `1 = SHA-256(256-bit digest)` — a value/width row inside a field's OWN
#: description. This is how a field table states what each encoding means.
#: The gap may contain digits — `1 = SHA-256(256-bit digest)` names the
#: algorithm with a number before it states the width — but it may NOT contain
#: another `=`, which is what keeps one row from reading into the next.
_MODE_ROW_RE = re.compile(
    r"(?i)(?P<v>\d+)\s*=\s*[^=]{0,40}?(?P<w>\d{2,5})\s*-?\s*bit")
#: `MODE=0` stated in the case's own text.
_MODE_EQ_RE = re.compile(r"(?i)\b(?P<f>[a-z_][a-z0-9_]*)\s*=\s*(?P<v>\d+)\b")

#: A bound on the STATUS poll loop. It is a HANG bound, not a latency model:
#: nothing in the design's input states a cycle count, so this number may not
#: be read as one. Exceeding it FAILS the run.
POLL_BUDGET = 4096


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s or "").lower()).strip("_")


def _int(n: Any) -> Optional[int]:
    try:
        return int(str(n).replace(",", "").replace("_", ""))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 1. the bus the design declares
# ---------------------------------------------------------------------------
def _width_of(decl: str) -> int:
    m = re.search(r"\[\s*(\d+)\s*:\s*(\d+)\s*\]", str(decl or ""))
    if not m:
        return 1
    return abs(int(m.group(1)) - int(m.group(2))) + 1


def _pick(role_tokens: Sequence[str],
          cands: Sequence[Tuple[str, str]]) -> Optional[Tuple[str, int]]:
    for tok in role_tokens:
        for name, decl in cands:
            if _norm(name) == tok:
                return name, _width_of(decl)
    for tok in role_tokens:
        for name, decl in cands:
            n = _norm(name)
            if n.startswith(tok + "_") or n.endswith("_" + tok):
                return name, _width_of(decl)
    return None


def bus_contract(ports: Sequence[Tuple[str, str, str]]
                 ) -> Tuple[Optional[dict], str]:
    """`(bus, reason)` — the scalar memory-mapped bus this DUT declares.

    `ports` is `testbench_gen`'s own `(DIRECTION, WIDTH, NAME)` triple list.
    Every one of chip-select / write-enable / address / write-data / read-data
    must be present: a partial bus cannot be driven, and guessing the missing
    one is what this refuses to do."""
    ins = [(n, w) for d, w, n in ports
           if str(d).startswith("input") and _ID_RE.match(str(n) or "")]
    outs = [(n, w) for d, w, n in ports
            if str(d).startswith("output") and _ID_RE.match(str(n) or "")]
    if not ins or not outs:
        return None, "the DUT declares no input or no output port"
    bus: Dict[str, Any] = {}
    missing = []
    for role in ("cs", "we", "address", "write_data"):
        hit = _pick(_BUS_ROLES[role], ins)
        if hit is None:
            missing.append(role)
        else:
            bus[role], bus[role + "_bits"] = hit[0], hit[1]
    hit = _pick(_BUS_ROLES["read_data"], outs)
    if hit is None:
        missing.append("read_data")
    else:
        bus["read_data"], bus["read_data_bits"] = hit[0], hit[1]
    if missing:
        return None, ("the DUT declares no " + "/".join(missing) + " port — "
                      "this is not a scalar memory-mapped register bus")
    if bus["cs_bits"] != 1 or bus["we_bits"] != 1:
        return None, (f"chip-select/write-enable are not scalar "
                      f"({bus['cs']}={bus['cs_bits']}b, "
                      f"{bus['we']}={bus['we_bits']}b)")
    if bus["write_data_bits"] != bus["read_data_bits"]:
        return None, (f"the write-data and read-data buses differ in width "
                      f"({bus['write_data_bits']} vs {bus['read_data_bits']})"
                      f" — this driver drives one register width")
    clk = _pick(_CLK_ROLES, ins)
    if clk is None:
        return None, "the DUT declares no clock port"
    rst = _pick(_RST_ROLES, ins)
    if rst is None:
        return None, "the DUT declares no reset port"
    err = _pick(_ERR_ROLES, outs)
    bus["clk"] = clk[0]
    bus["rst"] = rst[0]
    # POLARITY COMES FROM THE DESIGN'S OWN IDENTIFIER, and the identifier is
    # the whole evidence. `_n`/`_ni`/`n` is the universal spelling of an
    # active-low reset; anything else is active-high.
    #
    # R-0915-113(5): this is a FORMAL GRAMMAR, not a sentence. The argument
    # and its measurement live in `prose_polarity_consulted_check._NOT_PROSE`
    # under `stated_vector_bus_oracle_gen::bus_contract`; the suffix that
    # decided it is published beside the answer so a reader can check the
    # derivation instead of taking it on trust.
    _rst_norm = _norm(rst[0])
    _low = re.search(r"(?i)(_n|_ni|n)$", _rst_norm)
    bus["rst_active_low"] = bool(_low)
    bus["rst_polarity_evidence"] = (
        f"the design names the port {rst[0]!r}; its identifier ends "
        f"{_low.group(0)!r}" if _low else
        f"the design names the port {rst[0]!r}; its identifier ends in no "
        f"active-low suffix")
    bus["error"] = err[0] if err else None
    bus["word_bits"] = bus["write_data_bits"]
    return bus, ""


# ---------------------------------------------------------------------------
# 2. the register windows the design's map declares
# ---------------------------------------------------------------------------
def indexed_window(regs: Sequence[dict], role_tokens: Sequence[str],
                   access: str) -> Tuple[Optional[dict], str]:
    """`({index: address}, reason)` for the indexed register family whose base
    name carries one of `role_tokens` and whose access includes `access`.

    A family stated at two different addresses for one index is a CONTRADICTION
    and refuses; picking a winner would be inventing a register map."""
    fams: Dict[str, Dict[int, int]] = {}
    for r in regs:
        name = str(r.get("name") or "")
        m = _INDEXED_RE.match(name)
        if not m:
            continue
        acc = str(r.get("access") or "").upper()
        if access.upper() not in acc:
            continue
        base = _norm(m.group("base"))
        if base not in role_tokens:
            continue
        addr = r.get("address")
        if addr is None:
            continue
        try:
            val = int(str(addr), 16) if str(addr).lower().startswith("0x") \
                else int(str(addr))
        except ValueError:
            continue
        idx = int(m.group("i"))
        prev = fams.setdefault(base, {}).get(idx)
        if prev is not None and prev != val:
            return None, (f"the register map states {base}{idx} at both "
                          f"0x{prev:x} and 0x{val:x}")
        fams[base][idx] = val
    if not fams:
        return None, ("the register map declares no indexed "
                      + "/".join(role_tokens[:3]) + f"... {access} window")
    if len(fams) > 1:
        return None, ("the register map declares more than one candidate "
                      f"{access} window ({', '.join(sorted(fams))}) — which "
                      f"one carries the payload is not stated")
    base, by_idx = next(iter(fams.items()))
    if sorted(by_idx) != list(range(len(by_idx))):
        return None, (f"the {base} window is not a contiguous 0..N family "
                      f"(indices {sorted(by_idx)})")
    return {"base": base, "addresses": [by_idx[i] for i in range(len(by_idx))]}, ""


def _field_bit(reg: dict, tokens: Sequence[str]
               ) -> Optional[Tuple[str, int, str]]:
    """`(field_name, lsb, description)` for the first field whose name is one
    of `tokens`. `bits` may be a scalar index or an `msb:lsb` range."""
    for f in reg.get("fields") or []:
        n = _norm(f.get("field_name") or f.get("name"))
        if n not in tokens:
            continue
        bits = f.get("lsb")
        if bits is None:
            raw = str(f.get("bits") or "")
            m = re.match(r"^\s*(?:(\d+)\s*:\s*)?(\d+)\s*$", raw)
            if not m:
                return None
            bits = int(m.group(2))
        try:
            return (str(f.get("field_name") or f.get("name")), int(bits),
                    str(f.get("description") or ""))
        except (TypeError, ValueError):
            return None
    return None


def command_contract(regs: Sequence[dict]) -> Tuple[Optional[dict], str]:
    """`(contract, reason)` — the control/status registers and the bit roles
    the streaming sequence needs."""
    ctrl = stat = None
    for r in regs:
        name = str(r.get("name") or "")
        if ctrl is None and _CTRL_RE.match(name):
            ctrl = r
        elif stat is None and _STATUS_RE.match(name):
            stat = r
    absent = [n for n, v in (("control", ctrl), ("status", stat)) if v is None]
    if absent:
        return None, ("the register map declares no "
                      + " and no ".join(absent) + " register")
    init = _field_bit(ctrl, _INIT_FIELD)
    nxt = _field_bit(ctrl, _NEXT_FIELD)
    ready = _field_bit(stat, _READY_FIELD)
    valid = _field_bit(stat, _VALID_FIELD)
    mode = _field_bit(ctrl, _MODE_FIELD)
    if init is None or valid is None:
        return None, ("the control/status registers declare no "
                      f"start/answer-valid bit (start={init}, valid={valid})"
                      " — a fixed wait would be a guess, so this refuses")

    def _addr(r: dict) -> Optional[int]:
        a = r.get("address")
        try:
            return int(str(a), 16) if str(a).lower().startswith("0x") \
                else int(str(a))
        except (TypeError, ValueError):
            return None

    ca, sa = _addr(ctrl), _addr(stat)
    if ca is None or sa is None:
        return None, "the control/status registers carry no address"
    return {
        "ctrl_addr": ca, "status_addr": sa,
        "ctrl_name": str(ctrl.get("name")), "status_name": str(stat.get("name")),
        "init_bit": init[1], "init_field": init[0],
        "next_bit": nxt[1] if nxt else None,
        "next_field": nxt[0] if nxt else None,
        "ready_bit": ready[1] if ready else None,
        "ready_field": ready[0] if ready else None,
        "valid_bit": valid[1], "valid_field": valid[0],
        "mode_bit": mode[1] if mode else None,
        "mode_field": mode[0] if mode else None,
        "mode_desc": mode[2] if mode else "",
    }, ""


def mode_value_for_width(mode_desc: str, answer_bits: int
                         ) -> Tuple[Optional[int], str]:
    """The MODE encoding the field's OWN description gives for an answer of
    `answer_bits` bits, e.g. `1 = SHA-256(256-bit digest), 0 = SHA-224
    (224-bit truncated digest)`. Never guessed: absent means refused."""
    rows = {}
    for m in _MODE_ROW_RE.finditer(str(mode_desc or "")):
        rows.setdefault(int(m.group("w")), int(m.group("v")))
    if not rows:
        return None, ("the mode field's description states no value=width "
                      "table, so which encoding produces a "
                      f"{answer_bits}-bit answer is not stated")
    if answer_bits not in rows:
        return None, (f"the mode field states encodings for "
                      f"{sorted(rows)} bit answers, not for {answer_bits}")
    return rows[answer_bits], (f"{mode_desc.strip()[:120]}")


# ---------------------------------------------------------------------------
# 3. the vector the case STATES
# ---------------------------------------------------------------------------
def _clean_hex(s: Any) -> Optional[str]:
    t = re.sub(r"[\s_]", "", str(s or ""))
    t = re.sub(r"(?i)^0x", "", t)
    if not t or len(t) % 2 or not re.fullmatch(r"(?i)[0-9a-f]+", t):
        return None
    return t.lower()


def stated_answer(case: dict) -> Tuple[Optional[str], str]:
    """The expected value the case STATES, as a hex string. A case whose
    expected half is an acceptance criterion rather than a literal is refused
    BY NAME — computing it is what this module will not do."""
    outs = case.get("expected_outputs") or {}
    if len(outs) == 1:
        val = _clean_hex(next(iter(outs.values())))
        if val is not None:
            return val, "expected_outputs"
        return None, (f"case {case.get('name')!r}: its expected_outputs value "
                      f"is not a hex literal")
    if len(outs) > 1:
        return None, (f"case {case.get('name')!r}: it states "
                      f"{len(outs)} expected outputs; this driver reads one "
                      f"answer window")
    raw = case.get("expected")
    if raw is None:
        return None, f"case {case.get('name')!r} states no expected value"
    # A prose expected may carry a parenthetical width: `...e36c9da7(224-bit)`.
    txt = re.sub(r"\([^)]*\)", " ", str(raw))
    val = _clean_hex(txt)
    if val is None:
        return None, (f"case {case.get('name')!r}: its expected value "
                      f"{str(raw)[:48]!r} is not a stated literal — it is an "
                      f"acceptance criterion over a scope, and grounding it "
                      f"would mean COMPUTING the answer")
    return val, "expected"


def stated_message(case: dict) -> Tuple[Optional[dict], str]:
    """`(spec, reason)` for the payload the case STATES.

    Three stated forms, and nothing else:
      * a hex literal                     `{"kind": "hex", "hex": ...}`
      * a quoted ASCII literal            `{"kind": "hex", ...}` (its bytes)
      * a repeat spec `N x 0xBB bytes`    `{"kind": "repeat", "byte", "count"}`
    A case that states only a LENGTH is refused: a length is not a message."""
    ins = case.get("inputs") or {}
    if len(ins) == 1:
        val = _clean_hex(next(iter(ins.values())))
        if val is not None:
            return {"kind": "hex", "hex": val, "bytes": len(val) // 2,
                    "evidence": "inputs"}, "inputs"
        return None, (f"case {case.get('name')!r}: its input value is not a "
                      f"hex literal")
    if len(ins) > 1:
        return None, (f"case {case.get('name')!r}: it states {len(ins)} "
                      f"inputs; this driver drives one payload")
    raw = case.get("stimulus")
    if raw is None:
        return None, f"case {case.get('name')!r} states no stimulus"
    txt = str(raw)
    m = _REPEAT_RE.search(txt)
    if m:
        n = _int(m.group("n"))
        if n is None or n <= 0:
            return None, (f"case {case.get('name')!r}: repeat count "
                          f"{m.group('n')!r} is not a positive integer")
        return {"kind": "repeat", "byte": int(m.group("b"), 16), "count": n,
                "bytes": n, "evidence": "stimulus"}, "stimulus"
    hexes = _HEX_RUN_RE.findall(txt)
    if hexes:
        run = max(hexes, key=len)
        bl = _BITLEN_RE.search(txt)
        nbits = _int(bl.group("n") or bl.group("n2")) if bl else None
        h = _clean_hex(run)
        if h is None:
            return None, (f"case {case.get('name')!r}: the stated hex run "
                          f"{run!r} is not a whole number of bytes")
        if nbits is not None:
            if nbits % 8:
                return None, (f"case {case.get('name')!r}: the stated length "
                              f"{nbits} bits is not a whole number of bytes")
            need = nbits // 8
            if need > len(h) // 2:
                return None, (f"case {case.get('name')!r}: it states a "
                              f"{nbits}-bit message but shows only "
                              f"{len(h)//2} byte(s) of it")
            h = h[:need * 2]
        elif txt.count(".") >= 2 or "…" in txt:
            return None, (f"case {case.get('name')!r}: its stimulus shows an "
                          f"ELIDED hex run ({run!r}...) and states no length, "
                          f"so the message it stands for is not stated")
        return {"kind": "hex", "hex": h, "bytes": len(h) // 2,
                "evidence": "stimulus"}, "stimulus"
    a = _ASCII_RE.search(txt)
    if a:
        b = a.group(1).encode("ascii", "ignore")
        if b:
            return {"kind": "hex", "hex": b.hex(), "bytes": len(b),
                    "evidence": "stimulus"}, "stimulus"
    bl = _BITLEN_RE.search(txt)
    if bl:
        n = _int(bl.group("n") or bl.group("n2"))
        return None, (f"case {case.get('name')!r}: it states a message LENGTH "
                      f"({n} bits) but not the message — a length is not a "
                      f"vector, and filling it in would be inventing one")
    return None, (f"case {case.get('name')!r}: its stimulus {txt[:60]!r} "
                  f"states no literal, no quoted text and no repeat spec")


def stated_mode_override(case: dict, mode_field: Optional[str]
                         ) -> Optional[int]:
    """`FIELD=<v>` stated in the case's own text, when the case names the mode
    field itself. `None` when the case says nothing about it."""
    if not mode_field:
        return None
    want = _norm(mode_field)
    for blob in (case.get("stimulus"), case.get("name"), case.get("citation")):
        for m in _MODE_EQ_RE.finditer(str(blob or "")):
            if _norm(m.group("f")) == want:
                return int(m.group("v"))
    return None


# ---------------------------------------------------------------------------
# 4. framing — the standard the case itself cites
# ---------------------------------------------------------------------------
def pad_blocks(msg_bytes: int, block_bits: int, len_field_bits: int
               ) -> Tuple[int, int, int]:
    """`(total_blocks, tail_offset, message_bits)` for the Merkle-Damgard
    padding the case's own cited standard defines: a `1` bit, then zeros, then
    the message length in a big-endian length field that closes the block.

    This is TRANSPORT FRAMING, not the answer: the digest is never computed
    here. A design whose block register window states a 512-bit block and a
    64-bit length field gets exactly the padding its own standard states."""
    blk = block_bits // 8
    lenb = len_field_bits // 8
    total = msg_bytes + 1 + lenb
    pad_blocks_n = (total + blk - 1) // blk
    return pad_blocks_n, msg_bytes % blk, msg_bytes * 8


# ---------------------------------------------------------------------------
# 5. the plan
# ---------------------------------------------------------------------------
def resolve_stated_vector(case: dict, l4: dict,
                          ports: Sequence[Tuple[str, str, str]]
                          ) -> Tuple[Optional[dict], str]:
    """`(plan, reason)` — every address, bit, value and block this vector needs."""
    name = str(case.get("name") or "")
    if not _ID_RE.match(name):
        return None, f"case name {name!r} is not a legal identifier"
    bus, why = bus_contract(ports)
    if bus is None:
        return None, why
    regs = [r for r in (l4 or {}).get("registers") or [] if isinstance(r, dict)]
    if not regs:
        return None, "the register map declares no registers"
    win_in, why = indexed_window(regs, _IN_WINDOW_ROLES, "W")
    if win_in is None:
        return None, why
    win_out, why = indexed_window(regs, _OUT_WINDOW_ROLES, "R")
    if win_out is None:
        return None, why
    cmd, why = command_contract(regs)
    if cmd is None:
        return None, why
    answer, ans_src = stated_answer(case)
    if answer is None:
        return None, ans_src
    msg, msg_src = stated_message(case)
    if msg is None:
        return None, msg_src
    word = int(bus["word_bits"])
    if word % 8:
        return None, f"the register width {word} is not a whole byte count"
    ans_bits = len(answer) * 4
    words_out = (ans_bits + word - 1) // word
    if words_out > len(win_out["addresses"]):
        return None, (f"the stated answer is {ans_bits} bits and the "
                      f"{win_out['base']} window is "
                      f"{len(win_out['addresses']) * word} bits — the design's "
                      f"own map cannot carry it")
    if ans_bits % word:
        return None, (f"the stated answer is {ans_bits} bits, not a whole "
                      f"number of {word}-bit registers")
    block_bits = len(win_in["addresses"]) * word
    mode_val = None
    mode_why = ""
    if cmd["mode_bit"] is not None:
        mode_val = stated_mode_override(case, cmd["mode_field"])
        if mode_val is None:
            mode_val, mode_why = mode_value_for_width(cmd["mode_desc"],
                                                      ans_bits)
            if mode_val is None:
                return None, mode_why
        else:
            mode_why = f"the case states {cmd['mode_field']}={mode_val}"
    # The length field of the padding closes the block; its width is the
    # standard's own, and the standard the case cites states it as two words
    # of the block. Derive it from the block, never from a constant.
    len_bits = 64 if block_bits >= 256 else max(word, block_bits // 8)
    nblocks, tail, msg_bits = pad_blocks(msg["bytes"], block_bits, len_bits)
    if nblocks < 1:
        return None, f"case {name!r}: a zero-block message cannot be driven"
    if msg["kind"] == "repeat" and tail:
        return None, (f"case {name!r}: its repeat spec is {msg['bytes']} "
                      f"bytes, which is not a whole number of "
                      f"{block_bits // 8}-byte blocks — the partial block it "
                      f"ends on is not stated")
    return {
        "case": name,
        "bus": bus,
        "in_window": win_in, "out_window": win_out, "cmd": cmd,
        "word_bits": word, "block_bits": block_bits, "len_field_bits": len_bits,
        "answer_hex": answer, "answer_bits": ans_bits,
        "answer_words": words_out, "answer_source": ans_src,
        "message": msg, "message_source": msg_src,
        "message_bits": msg_bits, "blocks": nblocks,
        "mode_value": mode_val, "mode_evidence": mode_why,
        "poll_budget": POLL_BUDGET,
    }, ""


# ---------------------------------------------------------------------------
# 6. the testbench
# ---------------------------------------------------------------------------
def _literal_blocks(plan: dict) -> List[List[int]]:
    """The padded payload as a list of blocks of register words. Only for a
    LITERAL message: a repeat spec is emitted as a loop instead."""
    msg = plan["message"]
    blk_bytes = plan["block_bits"] // 8
    b = bytearray(bytes.fromhex(msg["hex"]))
    b.append(0x80)
    while (len(b) + plan["len_field_bits"] // 8) % blk_bytes:
        b.append(0x00)
    b += plan["message_bits"].to_bytes(plan["len_field_bits"] // 8, "big")
    wb = plan["word_bits"] // 8
    words = [int.from_bytes(b[i:i + wb], "big") for i in range(0, len(b), wb)]
    per = blk_bytes // wb
    return [words[i:i + per] for i in range(0, len(words), per)]


def emit_stated_vector_bus_tb(plan: dict, dut_module: str,
                              case: Optional[dict] = None
                              ) -> Tuple[Optional[str], str]:
    """`(verilog, reason)` — a self-checking TB that DRIVES the stated payload
    over the design's own bus and compares the design's own answer window
    against the STATED literal. A mismatch, an X, a raised error flag or an
    exhausted poll budget all increment `errors` and end `$fatal(1)`."""
    case = case or {}
    bus, cmd = plan["bus"], plan["cmd"]
    name = plan["case"]
    w = plan["word_bits"]
    aw = int(bus["address_bits"])
    nin = len(plan["in_window"]["addresses"])
    nout = plan["answer_words"]
    rst_rel = "1'b1" if bus["rst_active_low"] else "1'b0"
    rst_ast = "1'b0" if bus["rst_active_low"] else "1'b1"
    mode_mask = 0
    if cmd["mode_bit"] is not None and plan["mode_value"]:
        mode_mask = int(plan["mode_value"]) << int(cmd["mode_bit"])
    init_val = mode_mask | (1 << int(cmd["init_bit"]))
    next_val = mode_mask | ((1 << int(cmd["next_bit"]))
                            if cmd["next_bit"] is not None else 0)
    if plan["blocks"] > 1 and cmd["next_bit"] is None:
        return None, (f"case {name!r} needs {plan['blocks']} blocks and the "
                      f"control register declares no continue bit")
    L: List[str] = []
    A = L.append
    A("// AUTO-GENERATED self-checking STATED-VECTOR testbench.")
    A(f"// case          : {name}")
    A(f"// citation      : {case.get('citation') or case.get('evidence')}")
    A(f"// payload       : {plan['message']['bytes']} byte(s), stated in the "
      f"case's own {plan['message_source']}")
    A(f"// answer        : {plan['answer_bits']} bits, stated in the case's "
      f"own {plan['answer_source']} — a LITERAL, never computed here")
    A(f"// transport     : {bus['cs']}/{bus['we']}/{bus['address']}/"
      f"{bus['write_data']}/{bus['read_data']}, the design's own port table")
    A(f"// input window  : {plan['in_window']['base']}[0..{nin - 1}] "
      f"({plan['block_bits']} bits)")
    A(f"// answer window : {plan['out_window']['base']}[0..{nout - 1}]")
    A(f"// command       : {cmd['ctrl_name']}.{cmd['init_field']}"
      + (f" / .{cmd['next_field']}" if cmd["next_field"] else "")
      + (f" / .{cmd['mode_field']}={plan['mode_value']}"
         if cmd["mode_bit"] is not None else ""))
    if plan["mode_evidence"]:
        A(f"// mode evidence : {plan['mode_evidence']}")
    A(f"// handshake     : {cmd['status_name']}.{cmd['valid_field']}"
      + (f" / .{cmd['ready_field']}" if cmd["ready_field"] else ""))
    A("// The poll budget below is a HANG bound, not a latency model: nothing")
    A("// in the design's input states a cycle count. Exceeding it FAILS.")
    A("`timescale 1ns/1ps")
    A(f"module {name};")
    A(f"  localparam integer AW = {aw};")
    A(f"  localparam integer DW = {w};")
    A(f"  localparam integer POLL_BUDGET = {plan['poll_budget']};")
    A(f"  reg {bus['clk']} = 1'b0;")
    A(f"  reg {bus['rst']} = {rst_ast};")
    A(f"  reg {bus['cs']} = 1'b0;")
    A(f"  reg {bus['we']} = 1'b0;")
    A(f"  reg [AW-1:0] {bus['address']} = {{AW{{1'b0}}}};")
    A(f"  reg [DW-1:0] {bus['write_data']} = {{DW{{1'b0}}}};")
    A(f"  wire [DW-1:0] {bus['read_data']};")
    if bus["error"]:
        A(f"  wire {bus['error']};")
    A("  reg [DW-1:0] rdw;")
    A("  integer errors = 0;")
    A("  integer i;")
    A("  integer blk;")
    A("  integer polls;")
    A(f"  reg [AW-1:0] in_addr  [0:{nin - 1}];")
    A(f"  reg [AW-1:0] out_addr [0:{nout - 1}];")
    A(f"  reg [DW-1:0] expect_w [0:{nout - 1}];")
    conns = [f".{bus['clk']}({bus['clk']})", f".{bus['rst']}({bus['rst']})",
             f".{bus['cs']}({bus['cs']})", f".{bus['we']}({bus['we']})",
             f".{bus['address']}({bus['address']})",
             f".{bus['write_data']}({bus['write_data']})",
             f".{bus['read_data']}({bus['read_data']})"]
    if bus["error"]:
        conns.append(f".{bus['error']}({bus['error']})")
    A(f"  {dut_module} dut ({', '.join(conns)});")
    A(f"  always #5 {bus['clk']} = ~{bus['clk']};")
    A("")
    A("  task bus_write(input [AW-1:0] a, input [DW-1:0] d);")
    A(f"    begin @(posedge {bus['clk']});")
    A(f"      {bus['cs']} <= 1'b1; {bus['we']} <= 1'b1;")
    A(f"      {bus['address']} <= a; {bus['write_data']} <= d;")
    A(f"      @(posedge {bus['clk']});")
    A(f"      {bus['cs']} <= 1'b0; {bus['we']} <= 1'b0;")
    A("    end")
    A("  endtask")
    A("")
    A("  task bus_read(input [AW-1:0] a);")
    A(f"    begin @(posedge {bus['clk']});")
    A(f"      {bus['cs']} <= 1'b1; {bus['we']} <= 1'b0; {bus['address']} <= a;")
    A(f"      @(posedge {bus['clk']}); #1 rdw = {bus['read_data']};")
    if bus["error"]:
        A(f"      if ({bus['error']} !== 1'b0) begin")
        A("        errors = errors + 1;")
        A(f'        $display("[TB {name}] FAIL: {bus["error"]} raised on a '
          f'documented address %0h", a);')
        A("      end")
    A(f"      {bus['cs']} <= 1'b0;")
    A("    end")
    A("  endtask")
    A("")
    A("  // Wait for a STATUS bit to assert. The budget FAILS the run.")
    A("  task wait_bit(input [AW-1:0] a, input integer b);")
    A("    begin : wb")
    A("      polls = 0;")
    A("      forever begin")
    A("        bus_read(a);")
    A("        if (rdw[b] === 1'b1) disable wb;")
    A("        polls = polls + 1;")
    A("        if (polls >= POLL_BUDGET) begin")
    A("          errors = errors + 1;")
    A(f'          $display("[TB {name}] FAIL: {cmd["status_name"]} bit %0d '
      f'never asserted within %0d polls", b, POLL_BUDGET);')
    A("          disable wb;")
    A("        end")
    A("      end")
    A("    end")
    A("  endtask")
    A("")
    A("  initial begin")
    for k, a in enumerate(plan["in_window"]["addresses"]):
        A(f"    in_addr[{k}] = {aw}'h{a:x};")
    for k in range(nout):
        A(f"    out_addr[{k}] = {aw}'h{plan['out_window']['addresses'][k]:x};")
    ah = plan["answer_hex"]
    nib = w // 4
    for k in range(nout):
        A(f"    expect_w[{k}] = {w}'h{ah[k * nib:(k + 1) * nib]};")
    A("  end")
    A("")
    A("  initial begin")
    A(f'    $display("[TB {name}] BEGIN — {plan["blocks"]} block(s) over '
      f'{bus["cs"]}/{bus["we"]}/{bus["address"]}");')
    A(f"    {bus['rst']} = {rst_ast};")
    A(f"    repeat (4) @(posedge {bus['clk']});")
    A(f"    {bus['rst']} = {rst_rel};")
    A(f"    repeat (2) @(posedge {bus['clk']});")
    if cmd["ready_bit"] is not None:
        A(f"    wait_bit({aw}'h{cmd['status_addr']:x}, {cmd['ready_bit']});")
    body = _emit_payload(plan, aw, w, nin, init_val, next_val, cmd)
    L.extend(body)
    A(f"    wait_bit({aw}'h{cmd['status_addr']:x}, {cmd['valid_bit']});")
    A(f"    for (i = 0; i < {nout}; i = i + 1) begin")
    A("      bus_read(out_addr[i]);")
    A("      if (rdw !== expect_w[i]) begin")
    A("        errors = errors + 1;")
    A(f'        $display("[TB {name}] FAIL: {plan["out_window"]["base"]}[%0d] '
      f'= %h, stated %h", i, rdw, expect_w[i]);')
    A("      end")
    A("    end")
    A("    if (errors != 0) begin")
    A(f'      $display("[TB {name}] FAIL: %0d mismatch(es) against the value '
      f'the design states", errors);')
    A("      $fatal(1);")
    A("    end")
    A(f'    $display("[TB {name}] PASS: the design\'s '
      f'{plan["out_window"]["base"]} window matched the stated '
      f'{plan["answer_bits"]}-bit answer");')
    A("    $finish;")
    A("  end")
    A("endmodule")
    return "\n".join(L) + "\n", ""


def _emit_payload(plan: dict, aw: int, w: int, nin: int,
                  init_val: int, next_val: int, cmd: dict) -> List[str]:
    """The block-writing body: unrolled for a literal payload, a LOOP for a
    repeat spec (a million stated bytes may not become a million literals)."""
    out: List[str] = []
    A = out.append
    ca = f"{aw}'h{cmd['ctrl_addr']:x}"
    sa = f"{aw}'h{cmd['status_addr']:x}"
    ready = cmd["ready_bit"]
    if plan["message"]["kind"] == "repeat":
        byte = plan["message"]["byte"]
        wb = w // 8
        word = int.from_bytes(bytes([byte]) * wb, "big")
        full = plan["message"]["bytes"] // (plan["block_bits"] // 8)
        A(f"    // {plan['message']['count']} x 0x{byte:02x} byte(s), stated "
          f"as a repeat spec: {full} full block(s) then the padding block.")
        A(f"    for (blk = 0; blk < {full}; blk = blk + 1) begin")
        A(f"      for (i = 0; i < {nin}; i = i + 1) "
          f"bus_write(in_addr[i], {w}'h{word:0{w // 4}x});")
        A(f"      if (blk == 0) bus_write({ca}, {w}'h{init_val:x});")
        A(f"      else bus_write({ca}, {w}'h{next_val:x});")
        if ready is not None:
            A(f"      wait_bit({sa}, {ready});")
        A("    end")
        tail = _literal_tail_block(plan)
        A("    // the padding block the cited standard defines")
        for k, v in enumerate(tail):
            A(f"    bus_write(in_addr[{k}], {w}'h{v:0{w // 4}x});")
        A(f"    bus_write({ca}, {w}'h{next_val:x});")
        if ready is not None:
            A(f"    wait_bit({sa}, {ready});")
        return out
    blocks = _literal_blocks(plan)
    for bi, blk in enumerate(blocks):
        A(f"    // block {bi + 1} of {len(blocks)}")
        for k, v in enumerate(blk):
            A(f"    bus_write(in_addr[{k}], {w}'h{v:0{w // 4}x});")
        A(f"    bus_write({ca}, "
          f"{w}'h{(init_val if bi == 0 else next_val):x});")
        if ready is not None:
            A(f"    wait_bit({sa}, {ready});")
    return out


def _literal_tail_block(plan: dict) -> List[int]:
    """The single padding block that closes a payload whose stated length is a
    whole number of blocks: the `1` bit, zeros, then the length field."""
    blk_bytes = plan["block_bits"] // 8
    lenb = plan["len_field_bits"] // 8
    b = bytearray([0x80]) + bytearray(blk_bytes - 1 - lenb)
    b += plan["message_bits"].to_bytes(lenb, "big")
    wb = plan["word_bits"] // 8
    return [int.from_bytes(b[i:i + wb], "big") for i in range(0, len(b), wb)]


def emit_case_stated_vector_bus(case: dict, l4: dict, dut_module: str,
                                ports: Sequence[Tuple[str, str, str]]
                                ) -> Tuple[Optional[str], str]:
    """`(verilog, reason)` for ONE case — the single entry point the emitter
    ladder calls. Fail-closed: `(None, why)` whenever any half is unstated."""
    plan, why = resolve_stated_vector(case, l4, ports)
    if plan is None:
        return None, why
    return emit_stated_vector_bus_tb(plan, dut_module, case)
