# #2177 verified on main, and the same bound found one supervisor out

_Measured 2026-09-09 on host `8hd-3` (192.168.1.121, 32 cores, load 4–6 from
other lanes) against live `origin/main` `610cae2cc` (v1.20.0). Every figure is
from that tree unless it says otherwise._

## Conclusion first

1. **The issue's cited site and number are wrong; its conclusion is right.**
   `tools/ci/repo_hygiene_gates.sh` contains no `600` and its line 1790 is a
   comment. The `600` is `gate_host_independence_check.audit(timeout=600)`,
   and that parameter is already an IDLE tolerance: `_run_gate` hands it to
   `_watchdog` as `stall_grace_s`, not as a deadline.
2. **The repair #2177 asks for has already landed**, and is verified below by
   driving it rather than by reading the commit. `96871a7f7` (2026-09-08
   06:10 +0800, `Refs #2177`) is an ancestor of `610cae2cc`, and the
   `tools/ci/_gate_dispatch.sh` blob it landed (`6e11937006c2`) is the blob
   main carries. The issue has no comment and was never closed.
3. **The same shape is still live one supervisor out**, and that is what this
   branch repairs.

## The four acceptance directions, driven on `610cae2cc`

Both arms are the REAL 155-gate set (`repo_hygiene_gates.sh --shard 0/1
--shard-labels <all 155 labels>`) under the REAL supervisor
(`repo_hygiene_parallel._run`), on one host, back to back, at a 300 s lease.
They differ in ONE argument: `inflight_path`.

| arm | `inflight_path` | rc | elapsed | summary | gates attested |
|---|---|---|---|---|---|
| CONTROL — the pre-fix caller | `None` | **199** | 600.5 s | **absent** | 54 of 155 |
| FIX — main as it stands | the channel | 1 | **2247 s** | written | **154 of 155** |

**1 — a gate far past its own lease COMPLETES.** In the FIX arm
`every program is reachable` took **527 s** and PASSed — **1.76×** its own
300 s lease — and the whole supervised job ran **2247 s**, 7.5× the lease,
to a written summary of 155 declared / 154 ran / 135 PASS / 5 FAIL / 14 NOT
CHECKED. The CONTROL arm was killed at 600 s *inside that same gate*.
878 in-flight rows were accepted against 154 completed-gate records — the
renewal signal the pre-fix caller did not have.

Stated exactly: this host did not produce a single gate at the issue's 983 s
figure today (`gates are host-independent` measured **353 s** here against the
shipped profile's 2556 s), so the honest claim is the one above — 1.76× on one
gate, 7.5× on the job — not "a 983 s gate ran".

**2 — a silent worker IS killed, and the kill names the gate in flight.**
Driven: a real child announced gate 1 of 2 through the real
`_gate_inflight_progress.py` and then went silent.

    rc=199, elapsed 25.2 s of a 20 s lease
    "... no forward progress for 20.001s of a 20s stall lease; elapsed_s=25.002;
     in flight: 'gates are host-independent' (gate 1 of 2, reported nothing at
     all since it started); 0 of 2 assigned gate(s) completed"

The gate that was NOT in flight is absent from that clause. The same drive
with `inflight_path=None` is killed too — correctly — but names both assigned
labels and no gate in flight.

**3 — the mutation restoring the wall clock goes RED.** The CONTROL row is
that mutation, on the real subject: rc 199 at 600 s, **no summary at all**,
and **101 of 155 gates never reached a verdict**.

**4 — membership of the measurements this bound threw away.** Sweep over
`/home/reyerchu` for the shard-kill signature *"attestation progress ended
before assigned gates completed"*, positive-controlled (the known 2026-09-02
instance is in the result). Three DISTINCT runs, deduplicated by content:

| when | host | shard | gate in flight | what was thrown away |
|---|---|---|---|---|
| 2026-08-23 | 8hd-3 | arm A shard 0 | `an argued direction is pinned` | "5 wiring error(s) … the set certifies nothing" |
| 2026-08-28 19:34 | 8HD-8 (.114) | arm A shard 5 | `every program is reachable` | no summary (rc=199); 42 of 139 NOT CHECKED |
| 2026-09-02 17:54 | 8hd-3 | arm B shard 6 | `PDK via patch vs layer min width` | 6 of 9 shards NO RECORD; 28 of 156 NOT CHECKED; the landing FAILed |

The 2026-09-02 run appears in three files (`_land0902/land2.log`,
`RUN2_configured_1ec22dabc.log` — byte-identical, md5 `ea4b800f…` — and the
agent transcript `1c9727c9…jsonl`); it is ONE run, not three.

**Since `96871a7f7` landed (2026-09-08 06:10) there is not one production
instance on this host.** The only 2026-09-09 hit in the sweep is this lane's
own CONTROL arm.

**What the sweep could NOT see:** the other fleet hosts. SSH to .105, .108,
.112 and .114 is refused (`Permission denied (publickey,password)`) and no
key is present, so a zero there would mean "could not look". The landed
lane's own cross-host membership — 18 voided runs over 5 hosts — is quoted
second-hand and was not re-derived here.

**The cost model is wrong for exactly the gates that get killed.** Two of the
three gates named above are not the profile's heavy ones:
`every program is reachable` is **absent from `hygiene_gate_profile.json`**
(so the planner costs it at `DEFAULT_SECONDS = 10`) and measured **527 s**
here; `PDK via patch vs layer min width` is profiled at **1 s**. That is why
raising the bound could never have been the fix — the planner cannot predict
which gate will be slow.

## The residual this branch repairs

`gatekeeper_review.repo_hygiene_gate` supervises the COORDINATOR with

    _wd.run_supervised(command, env=env, log_path=progress,
                       stall_grace_s=_HYGIENE_STALL_GRACE_S,   # 1800
                       poll_s=5, hard_ceiling_s=inf, ...)

and its own refusal text names its only two signals: *"no output or
completed-gate record advanced for 1800s"*. The in-flight files are private to
each worker — `repo_hygiene_parallel` writes one per shard per arm and says
"nothing downstream reads this channel" — so nothing that supervisor can see
moves while one long gate is in flight.

**MEASURED:** an outer `run_supervised` over a child that wrote **20 accepted
in-flight rows** and nothing else was killed at exactly its lease. The outer
supervisor is blind to the channel.

**AND IN PRODUCTION IT HAS ONE SIGNAL, NOT TWO.** `log_path` is fed by
`progress_out`, which is fed by `--gate-progress`, and **no caller in this
repository passes it** — `grep -rn "gate-progress"` returns exactly one hit,
the flag's own declaration. On the canonical self-verification command that
`skills/core-agent-loop/SKILL.md:506` documents:

    python3 programs/gatekeeper_review.py --base origin/main --head HEAD \
        --role core-agent --json /tmp/gk.json     # --scope defaults to full

`log_path` is `None`, and the only renewal left is the coordinator's stdout —
which prints nothing between `pool.map` starting and returning. The lease is
then a pure wall clock over the whole hygiene tier.

`tools/gatekeeper-land.sh` is NOT exposed: it passes `--scope structural` (no
hygiene) and runs `repo_hygiene_gates.sh` directly in its own lane with no
shard supervisor at all.

**On the magnitude, stated honestly.** Derived from the shipped artefacts —
`hygiene_shard_plan.plan` over the 155 labels `--list` prints, costed by the
committed `hygiene_gate_profile.json` — the two heaviest buckets are
SINGLETONS at 2556 s and 646 s, so after the second finishes no arm-A shard
can complete another gate for **≥1910 s** against an **1800 s** lease. That is
the repo's own shipped cost model. It is NOT what this host measured today:
`gates are host-independent` ran in 353 s here and the whole serial tier in
2247 s, so 8-way sharded this host has margin. **No production instance of the
OUTER kill was observed** — the mechanism is measured, the magnitude is
derived from the shipped profile, and the two are reported separately on
purpose.

## The repair

No bound is raised, nothing is wrapped in `timeout`, no kill becomes a SKIP.
`repo_hygiene_parallel.OuterProgressRelay` converts measured sub-unit progress
into the one signal that supervisor can see — one stdout line — and only for
rows `InflightReader` has ACCEPTED, so a shard cannot hold the outer lease
open with rows the protocol refuses, and a channel that goes bad freezes its
score and is reported once. The line names the arm, the shard and the gate in
flight, which is also the live log a 40-minute tier never had. With no shard
channel it starts no thread and prints nothing.

**Both directions, driven**
(`test_issue2177_the_review_side_lease_sees_sub_unit_progress.py`):

| arm | tree | outcome |
|---|---|---|
| child reports sub-units, relay entered | this branch | `natural` — survives a lease a quarter of its runtime |
| same child, relay not entered (the pre-patch caller) | this branch | `stalled`, rc 199 |
| the same file, unpatched `610cae2cc` | main | `('stalled', 199)` — the driven arm fails for the RIGHT reason |

The child resolves the relay through `getattr`, so the pre-fix tree RUNS the
arm and answers wrongly instead of aborting on an AttributeError before it
observed anything.

## Two things found on the way, neither claimed as this lane's

* **`program inventory fresh` is RED on live main.** Measured on a
  `git clean -xdf` pristine clone of `610cae2cc`: rc 1,
  `programs_tree_all_py` committed 5015 / tree 5017 and `test_files`
  committed 3529 / tree 3531. This branch regenerates the artefact because it
  adds a test file, and that regeneration also absorbs those two.
* `gate_alive` is CPU/output liveness, so a process blocked on a lock looks
  wedged and a spin loop looks alive. That is the landed fix's own recorded
  trade-off and this relay inherits it: strictly better than a wall clock,
  which is fooled by every long gate.
