# AES LEC — the 693 是 NOT_MEASURED，不是 693 個錯 (cyaes2, 2026-09-10, 8hd-3/.121)

> **先讀這一行**：AES 的 LEC **從來沒有出現過任何一個 non-equivalent point**。
> 每一輪、每一個深度，`non_equivalent_points = 0`，從未產生過一個反例。
> 本頁所有 "unproven" 數字都是 **NOT_MEASURED**（證明能力/預算的陳述），
> **絕不是**「有幾個東西是錯的」。被 kill 的 run 量的是容器，不是矽。

> **693（pre-DFT miter）與 2989（post-DFT）是不同的受測體，永遠不可混用。**
> 2989 那一條的 `induction_wall_kind = miter_inconsistent`：base case UNSAT，
> 3023 點只比對過 34 點 — 一個不自洽的 miter 你指它什麼它就報什麼，它沒有決定任何事。

---

## 結論（三個問題，依序）

**1. 48 GiB 的 seq16 rung：已 TERMINAL，rc 0，未 OOM。**
`701 → 693`（proven 3277 → 3285，`Proved 8 previously unproven $equiv cells`），
wall **27890.96 s (7h44m51s)**，**實測** peak **16.4 GiB**（三具互相獨立的儀器一致），
checkpoint sha256 `a8bed3d7…3a3dd3c`。16 GiB 那次不是策略失敗，是**差 0.4 GiB / 2.5%**。

**2. #2194 acceptance 2 — 肯定，且這次帶鑑別力。**
`read_rtlil` 之後 `equiv_status` **會**報出既有的已證點，**不會**從零開始。
我用**兩個已知狀態相異**的 checkpoint 各跑一個全新 process 當雙臂：
seq4 → `3277 proven / 701 unproven`；seq16 → `3285 proven / 693 unproven`。
若重啟會丟失證明狀態，兩臂都會報 `0 / 3978`；它們報出各自 checkpoint 的離場狀態。
機制上這是結構性的：`equiv_induct.cc` 以 `setPort(B) = getPort(A)` 標記已證，
而 `equiv_status`／`equiv_induct` 都以 `getPort(A) != getPort(B)` 判定未證 —
**證明狀態就是 netlist 本身**，RTLIL 序列化丟不掉它。**沒有默默重做已證工作。**

**3. 693 以現行 miter 不可能收斂 — AES 需要 RTL/miter 層級的答案，但和 sha256 的不同種。**
不是深度牆，是 **reachability** 問題。五個獨立量測全部指向同一處（見下）。
最關鍵的一條、也是今晚新增的：**在任何已量測的深度，state bit 從未收斂過一個。**
seq16 收掉的 8 個全部是組合邏輯 buffer/input（`u_prim_buf_in.out_o` / `.in` /
`data_out_clear_i`）；剩下的 693 **全部是 state**。不是「收得少」，是**歸零**。

---

## 1 — seq16 rung 終局（全部由該 run 自己的證據重新驗過，非轉抄）

| 項目 | 值 | 來源 |
|---|---|---|
| rc / OOMKilled | **0 / false** | `DONE.json` `native_rc`, `state.OOMKilled` |
| unproven before → after | **701 → 693** | `native.log` L68 / L1531（`equiv_status` 前後各一次） |
| proven before → after | 3277 → 3285 | 同上 |
| wall | **27890.96 s** (7h44m51s) | `DONE.json`；yosys 自報 27887.19 s |
| **實測 peak RSS** | **≈16.4 GiB** | yosys `MEM: 16762.54 MB peak`；cgroup `memory.peak` 17597411328 B = 16782.2 MiB；process `VmHWM` 17166072 kB。**三者一致** |
| checkpoint sha256 | `a8bed3d78cc2661c88654db28dc51753811ac5fb0e9a1355f944363b79a3dd3c` | 我重新 `sha256sum` 過，相符 |
| CNF @ step16 | 73068832 clauses / 28168197 variables | `native.log` L820 |
| 誘導步驟 | **step 1–16 全部 `Proof for induction step failed`** | `native.log` L775–L821 |
| per-cell fallback | **success 8 / failed 693** | 我自己 `grep -c` 過 |

本 ladder 三段：`equiv_simple_full` 1.93 GiB / 1338.06 s → `equiv_induct_seq4`
5.55 GiB / 6341.42 s → `equiv_induct_seq16` **16.4 GiB / 27890.96 s**。
**max = 16.4 GiB，sum = 23.9 GiB。** fresh-process 形狀只需 max — #2194 的修法是對的，
而 16.4 GiB 正好落在 #2194 自己那個「單一 rung 16.3 GB」的數字上：**佐證，不是反證**。

### 另有一個 job 仍在跑（seq64 / 72 GiB），它不會改變結論
`aes-lec693-seq64-d0f605-72g-04`，20:57:48 啟動，從我上面那個 seq16 checkpoint 續跑。

- **它在前進，不只是活著**，兩具獨立儀器：
  (a) yosys pid 3229196 `utime 10938 s / wall 10965 s` = **99.7% CPU** — 在算，不是卡在鎖上；
  (b) RSS 以 **~0.90 GiB 一階的離散階梯**單調上升（每階 = 多一個 timestep 的 ~4.30M clauses），
  由 3.35 GiB(20:58) 升到 **28.75 GiB(00:00)**。
- **最後 flush 到 log 的進度行**：`Proving induction step 18. (81672422 clauses over
  31484347 variables)` → `Proof for induction step failed.`
  （log 停在 21:42 是 yosys 經 pipe 的 block buffering，不是停擺；
  依 RSS 階梯律推估目前約在 **step 29**，但**已量測的地板是 step 18**。）
- **它已經把 step 1–16 原封不動重做了一遍**（clause 數與 seq16 run 差 <0.002%），
  再次全部 failed。**已量測失敗深度因此由 16 延伸到 18。**
- 推估：step 65 約需 **61 GiB**（cap 72 GiB 內），但近段每步已要 ~20–28 min 且在增長，
  距 step 65 還有 36 步 → **樂觀下界也要 7 小時以上**，實際會更久。
- **建議：不要讓 scoreboard 等它。** 依上表的收斂規律，它的期望產出是 **0**。
  另註風險：host 目前 MemAvailable 68 GiB，它若長到 61 GiB 會壓迫同機其他 job。
  **我沒有動它** — 那是前一棒的 live job，殺它的決定屬於 owner。

---

## 3 — 為什麼 693 不可能以現行 miter 收斂（五個獨立量測）

1. **每一個深度都失敗。** seq4 敗於 step 1–4；seq16 敗於 step 1–16；seq64 目前敗於 step 1–18。
   深度牆會被更深的 rung 跨過 — 這個不會。
2. **會收的只由 fallback 收，而且全是組合邏輯。** seq16 收掉的 8 個全是
   `u_prim_buf_in.out_o` / `.in` / `data_out_clear_i`。**state bit 收斂數 = 0。**
   fallback 產率 seq4 收 2553 → seq16 收 8，**~300 倍崩塌**。
3. **剩下的 693 全部是 state**（我自己重數）：`u_aes_ghash` 391（`ghash_state_q`/
   `hash_subkey_q`/`s_q`）、`u_reg.data_out_*_qs` 128、`u_aes_control` 132、
   `u_aes_cipher_control` 35、alert/其他 7。也就是說：**唯一有能力收點的機制，
   對剩下這一整類從未成功過一次。**
4. **切割已量測封閉。** cone-closed：四個 seed 的 `%ci*` 全部收斂到同一個
   223449-cell 不動點（承載 3978 中的 3333 點，82.8%），最佳切割只省 ~17%。
   truncated cone（over-approximation，成功即真credit）在 909/2688/19100 cells
   三個深度 rc 0 跑完，**proved 全部為 0**。
5. **#2151 的 pin-permutation screen：accepted = 0，且是結構性不適用。**
   用 landed code 本身跑（`5c9263296`）：693 行中 `parse_unproven_points` 只留 29，
   dropped 664（gold/gate 名稱本就不同），screen **accepted=0 rejected=29**，
   單一理由「instance absent from the gold netlist」。原因是它是 **gate-vs-gate**
   分類器（post-layout LEC 的形狀），而這裡是 **RTL-vs-gate**：gold 側只解析出
   **12** 個 instance，gate 側 **39732** 個 — 沒有共用 cell instance，
   pin permutation 連「表達」都不可能，遑論找到。**sha256 的病因對 AES 已排除，是量測排除，不是論證排除。**

### 機制解釋
`equiv_induct` 的誘導假設是「所有比較點連續 N 個 cycle 都相等」，這是**無約束初始狀態**的誘導。
在有 PRNG mask domain 與 GF(2^128) GHASH state 的 masked AES 上，可達狀態集是狀態空間中
消失量級的子集，於是該假設容許了設計永遠進不去的狀態，**任何 N 都會失敗**。
前一棒已直接量到這件事：raw relation「admits invalid arbitrary initial states」，
而那個 arbitrary-state witness 的 `mode 110100` 已被他們用歸納證出的 mode-domain
invariant 排除。**`equiv_induct` 只提供深度；深度供不出 invariant。**

---

## 唯一還開著的路 — 而它是唯一被時間上限掐死的那一條

把「全population 比較 + 已證 invariant」合起來跑的 joint induction（`equiv_miter -whole` /
joint_induction_v2）**不是失敗，是 TIMEOUT**：
`ERROR: Called with -verify and proof did time out!`，rc 1，死在 **induction step 1**，
題目只有 **9388212 clauses / 3628303 variables**。

對照組很刺眼：

| 路線 | 每 query 時間上限 | 實際花費 | 題目大小 | 結局 |
|---|---|---|---|---|
| `equiv_induct -seq 16` | **無上限** | 27863 s（~1741 s/step） | 73.07M clauses | 跑完，失敗 16/16 |
| joint induction + invariants | **180 s** | 被上限砍斷 | **9.39M** clauses（小 7.8 倍） | **TIMEOUT，NOT_MEASURED** |

**唯一一條在機制上能回答 reachability 的路線，是唯一一條被上限掐斷的路線，
而那個上限比深度階梯被白白允許的每步時間緊約 10 倍。**
它不是被量測否決的 — 它**從來沒有在可比的信封下被量測過**。

我沒有動這個上限（規則：不得為了讓東西過而抬預算）。這是**下一個實驗的定義**，
不是今晚的 credit。

---

## AES 需要什麼（相對 sha256）

| | sha256 | AES |
|---|---|---|
| 病因 | **pairing**（pin permutation） | **reachability**（不可達狀態） |
| 已落地解法 | #2151 screen：145.8 s → 134 survivors → 103 renames，175x | **不適用，已量測（accepted=0）** |
| 需要的答案 | 一個可證對稱的置換 | 把 mask / mode / GHASH domain 的**已證 invariant 餵進誘導**，或讓 masked GHASH state 在 RTL 層可結構對應 |
| 屬性 | 都是 RTL / miter 建構層的工作 — **都不是再加一個 rung** | |

---

## 今晚可誠實記入的 credit
已完成的 **bounded** 結果成立：22986 個 raw predicate 於 frame 1–5 全數 asserted，
`SAT proof finished - no model found: SUCCESS!`，且腐化控制組正確回傳 SAT witness。
即 **bounded 到 depth 5，且在任何深度都從未找到過反例**。
unbounded credit 維持 **3285 / 3978，693 NOT_MEASURED**。

---

## 本頁每個數字的證據路徑
`/home/reyerchu/fleet-artefacts/cpu-reconcile-a94753e-wide-121/v197-aes-repair/`
- seq16 終局：`lec701-seq16-48g-20260909-cyaes121/output/{DONE.json,native.log,rss_samples.tsv,memory_samples.tsv}`
- acceptance 2 雙臂（本棒新跑）：`acc2-cyaes2/{INPUT_SHA256.txt,acc2_seq4.ys,acc2_seq16.ys,out/}`，容器 `acc2-cyaes2-{seq4,seq16}`，rc 0
- seq64 在飛：`lec693-seq64-72g-cyaes121/output/{native.log,rss_samples.tsv,MANIFEST.json}`
- 切割：`lec701-partition-probe-cyaes121/output/{probe2.log,probe3.log,trunc.log}`
- #2151 screen：`screen2151-aes-cyaes121/output/screen.log`
- invariant / whole-miter timeout：`findings.md` L46–L56、`joint_induction_v2/{run.log,native.rc}`
- 前一棒總表：`CYAES121_FINDINGS.md`

---

# 附錄 A（cyaes2 第二輪）— 最後一條 NOT_MEASURED 已量測，且 AES 的真正阻擋不在 LEC

## A1 — invariant 路線：已量測，不收斂，且**沒有反例**
前一輪我把 joint induction + 已證 mode invariants 列為「唯一未在可比信封下量測」的路線。
現在量了。**唯一改動的參數是移除 `-timeout 180`**（`-maxsteps 2`、reset 場景、
`-prove-asserts -verify`、22990 條 assertion 全部原封不動；diff 只有一個 hunk）。

| 臂 | 輸入 | 結果 | wall | rc |
|---|---|---|---|---|
| **GREEN** | 未改動 | base case 1 **UNSAT**、induction step 1 SAT、base case 2 **UNSAT**、induction step 2 SAT → `Proof failed!` | **959 s** | 1 |
| **RED**（控制） | 同一份輸入，注入一顆**真實 gate FF** 的 D 反相（`$flatten\_75620_…create_ff$22625_gate`），22990 條 assertion 不變 | **`model found for base case: FAIL!`** | **67 s** | 1 |

**紅臂是紅的，而且很快** → 儀器有鑑別力，綠臂的結果不是盲測。
**綠臂 `model found for base case` 出現次數 = 0** → **仍然沒有任何反例**。
失敗的是 **induction step**，不是 base case。

**結論：invariant 路線在 maxsteps 2 已 MEASURED-CLOSED。** 它不是逾時，是真的不收斂；
而且它的失敗形狀正好證實了前一輪的機制診斷——問題在**可達狀態**，不在時間預算。
（`-timeout 180` 原本連 induction step 1 都跑不完：舊 run 死在那裡，新 run 用 959 s 跑完全部四個問題。
沒有調高任何 gate 上限；`-verify` 全程保留，不收斂就是 rc 1。）

## A2 — 真正的阻擋：AES 從未進入 Phase 3，而 LEC 從未在正式語料樹上跑過
用 tip（`afc23f380`）的 `flow_compliance_check.py --strict` 量正式語料樹
`benchmark-data/ic/opentitan_aes`（132 份 report、113 個 phase2 檔、真實 synth netlist —
是真的 run tree，不是被剝過的快照；但**沒有 phase3 目錄**）：

```
Steps: 69 total (0/47 executed PASS)
PASS=0  FAIL=4  MISSING=40  WAIVED-DEFERRED=2  SKIPPED=20  PASS-VOIDED=3
MISSING 之中：32 個 blocked-by-upstream of step 2；4 個 blocked-by-upstream of step 0.5ic
STRUCTURAL: registered=246 invoked=246 no_verdict=0
```

**根因 3 個，不是 47 個**（其餘全是 membership）：

| 根因 | 觀測到的事實 | 下游 |
|---|---|---|
| **D1** Phase-1 Doc Extraction FAIL | `reports/audit/phase1/expert_parse_track.*` 不存在；`l17_channel_catalog_consumer_contract_check` rc=1 BLOCKING | step 1 → step 2 → 32 步 |
| **0.5ic** Submission Template MISSING | `input/submission_template/{slots/*.yaml,NO_TEMPLATE.txt,SELF_TAPEOUT.txt}` 皆不存在 | 4 步 |
| **Step 4** Simulation FAIL | `phase2/stage1/sim/{results.xml,pass.flag}` 不存在 | — |

`Step 2` 本身**不是**設計失敗：它的 basis 是
`derived-from-upstream — consequence of step(s) 1, 0.5ic`。

**而 Step 13（LEC）的狀態是 MISSING —— 它從來沒有在這棵樹上跑過。**
我這條 lane 整晚工作的 693，位於一個 step-2 失敗之後 11 步的位置，
**不在 AES 的關鍵路徑上**。

---

# 附錄 B（cyaes2 第三輪）— D1 被量測剝到剩一個裁示

全部在自有 clone（tip `f91aaa391`）與自有 scratch 內完成，未動任何共用 checkout。

## B1 — D1 的阻擋鏈：量測到的三層，我關掉兩層
`D1` 不是單一根因，是一條**BLOCKING gate 鏈**。每一層都由「跑它」關掉，A/B 皆在同一棵樹上：

| 層 | 阻擋者 | 我做了什麼 | 之後 |
|---|---|---|---|
| 1 | `phase1_expert_parse_track --check-report` → 報告不存在 | 以 IC-Expert 身分讀 `design_input.txt`（input-only，§4.05）作答 6 條 expectation，再跑一次消費它 | **rc 0**，examined 6 expectation、6 finding；報告產生 |
| 2 | `l17_channel_catalog_consumer_contract_check` rc=1 **BLOCKING** | 以 tip 重跑 Phase 1（`phase1_one_shot_runner`，rc 0） | **gate PASS，0 error** |
| 3 | `l8_clock_period_actionability_check` rc=1 | — | **仍紅（見 B3）** |

## B2 — 為什麼第 2 層會紅：語料樹的 Phase-1 文件比量它的 gate 老得多
AES 的 `L17_CHANNEL_SIGNAL_CATALOG.json` 由 `extract_l17_channels **v0.1.62**` 產生（tip 是 **v1.20.15**）。
它 `extraction_status=EXTRACTION_FOUND_NOTHING`、0 channel、0 global signal，
卻在 `dependency_graph` 裡帶著 **AXI** 條文（`ARVALID precedes RVALID … RLAST`）與
`total_signals_including_ACLK_ARESETn` —— 而 AES 是 **TL-UL** 週邊。

**這不是未歸檔的 plugin 缺陷。** 我先查了原始碼才下判斷：
`phase1_protocol_spec_extract.py` 已有具名修復（註解 `TEMPLATE LEAK (fixed)`），
`if not channels and not global_sigs:` 直接回 `dependency_graph: {}`。
所以那份 AXI 條文是**早於該修復產生的陳舊資料**，不是現行程式會寫出的東西。
以 tip 重跑後，`dependency_graph` 確實變成 `{}`，兩條 finding 全部消失。

**我的更正**：我一度把這寫成 plugin boilerplate 缺陷並準備開 issue；讀了 emit 條件後
它是資料陳舊。開 issue 會是錯的。

## B3 — 剩下的那一層是裁示，不是運算：`L8_CLOCK_PERIOD_UNRESOLVABLE`
```
L8 declares 3 clock record(s) but NOT ONE yields a positive frequency via
freq_mhz / period_ns / freq_hz — the exact three keys the SDC generator reads.
The consumer therefore falls through to a hard-coded plugin default and pins a
FABRICATED create_clock period as if the design had specified it.
```
三筆紀錄的 `freq_hz / freq_mhz / period_ns` **全為 null**，`source` 是
`synthesised-from-L9.top_ports` —— 時脈**埠**被抽出了，**頻率從來沒有被宣告過**。

我回查設計輸入：全文唯一的 MHz 是 `at 100 MHz`，那是熵消耗率的參照條件，不是目標時脈；
`the desired clock period in ps` 出自合成流程 README，是旋鈕不是值。
**AES 的設計輸入確實沒有宣告 sky130A 的目標時脈週期。** 這個 gate 拒絕讓 SDC 產生器
捏一個出來是**對的**，而它要的東西沒有人能用運算得到。

## B4 — 誠實的計分：根因有進展，PASS 沒有
| | BEFORE（原樹，舊文件） | AFTER（tip 重跑 Phase 1） |
|---|---|---|
| executed PASS | **0 / 47** | **0 / 48** |
| FAIL | 4（`P0 2 4 D1`） | 6（`P0 2 4 5 D1 0.5ic`） |
| MISSING | 40 | 39 |
| SKIPPED | 20 | 19 |
| blocked-by-upstream of step 2 | 32 | **32（未動）** |

FAIL 由 4 升到 6 不是退步：`0.5ic` 由 MISSING→FAIL、`5` 由 SKIPPED-CONDITION→FAIL，
是兩個先前**沒有被量測**的步驟開始回傳真實判決。依裁示，量到的非零是進展，
MISSING / SKIPPED 從來不是 pass。**但 PASS 仍然是 0，32 步仍卡在 step 2 之後。**

## B5 — 雙軌一致（兩個獨立來源指向同一件事）
我作為 IC-Expert 獨立寫的 expectation #2 要求
`L9_INTEGRATION_SPEC.chip_top_interfaces` 與 `L17…global_signals` 帶 `clk_i` / `clk_edn_i`；
確定性 gate 獨立回報 `global_signals_declared: 0` 且消費端
`phase2_scaffold_gen.derive_signals` **自行發明了 `clk` / `rst_n`**。
tip 重跑後兩把時脈確實落進 L1/L8/L9，但 **`L9.chip_top_interfaces` 仍是 `[]`** —— 
expectation #2 在 tip 上仍然成立。

## B6 — 未跑的前置檢查，必須據實說
`/home/reyerchu/fleet_scripts/falsref.sh` **在本機不存在**（該目錄只有 8 個其他檔）。
因此本分支的 G/R 兩臂與 hygiene 臂 **NOT_MEASURED**。本分支只新增一份 docs、
不含測試也不改任何原始碼，故 TEST-ONLY / MUTATION 臂在構造上不適用；
但這是**我沒有跑**，不是我通過了。

---

# 附錄 C（cyaes2，同一棒的另一半）— §3 第 5 點已吸收進 shipped code

這一頁到附錄 B 為止都只有文件、沒有任何測試；single-writer lander 因此拒絕它
（`no green arm — the change does not pass its own tests`）。用 repo 自己的
`ci_targeted_test_select.py --base <live main>` 量，理由是機械的、也是對的：

```
UNMAPPED — 1 changed path(s) map to NO test set. ... it is the smoke floor,
which is not evidence about them:
    docs/AES_LEC_693_CYAES2_FINDINGS.md
```

**不是紅的，是沒有。** 我把只有這份文件的那棵樹拿去跑 selector 自己給的 smoke floor：
**495 passed / rc 0**。所以拒絕不是某個測試失敗，是這個 change 沒有帶任何「關於它自己」的證據。

於是這一半把本頁 §3 第 5 點**唯一那條可以變成程式的觀測**寫進 shipped code。

**觀測（本頁 §3.5）**：`classify_pin_permutation_points` 在 RTL-vs-gate miter 上，
gold 側只解析出 **12** 個 instance、gate 側 **39732**、交集 **0**；被 parse 出的
29 個點全部以同一個理由 `instance absent from the gold netlist` 被 rejected，
screen 回報 **`accepted: 0`**。

**缺陷不是它擋掉了什麼** — rejected 的點保留 cut point、仍然 unproven，
所以沒有任何 verdict 會因此變綠。缺陷是**從計數上看不出它其實一點都沒比對過**：
`accepted: 0` 與「比對過、確實沒有 permutation artefact」在 JSON 裡長得一模一樣。
本 lane 花掉一整個 run，才用手證明它是前者。這正是 repo 自己
`gate_zero_denominator_refuses_check` 那條 doctrine 說的 zero denominator，
而該 program 的 docstring 也明講：它只做 PROBE，「each fix is then its own
measured change」。這就是那個 fix。

**改了什麼**：population 非空、且**沒有任何一個點的 instance 同時存在於兩側**時，
回傳多一個 `not_applicable`（寫出兩側 namespace 大小與交集），每一筆 rejected 的
`reason` 也在原本那句真話後面接上同一件事實。`accepted` / `rejected` 的**成員一個沒動**，
phase3 runner 的 `_all_ok` 與 rename 行為因此逐字不變；改的只有「記錄說了什麼」。

**兩臂（都對 live main `f91aaa39` 量；main 之後移到 `756d84f4`，
而那兩個 commit 沒有碰本改動的任何一個檔案，已用 `git diff --name-only` 確認）**

| 臂 | 樹 | 結果 |
|---|---|---|
| **GREEN** | main + 本 branch 全部改動 | 該檔 **14 passed**；LEC 相關 20 檔 **279 passed / 10 skipped / rc 0** |
| **RED** | main sources + 只有新測試 | **1 failed / 13 passed** — `test_a_screen_that_could_pair_no_point_reports_a_zero_denominator` |

紅臂是**答錯**，不是「東西還不存在」：pre-fix 的樹跑得完整個函式並回傳一份完整的
dict，只是 reason 少了那句話。用 repo 自己的 `control_substance_check.py` 量：
`1 of 1 reported failures observed a VALUE`（第一版的寫法被它判為 TAUTOLOGICAL —
唯一失敗的比較是 `None == {...}` — 那一版已重寫，不是 waive）。

**誠實記分**：綠臂那 10 個 skip 全部是 container / native-yosys 綁定
（`vibeic-eda` 未在本機起、`native Yosys required`），**NOT RUN，不是 pass**；
其中沒有一個是關於本改動的分類器或 screen 記錄。
selector 給的完整 502 檔目標選集在 load average 127 的機器上仍在跑，
到本頁寫成為止 **0 個 FAILED/ERROR**，但尚未跑完，因此記為 **NOT_MEASURED**。

**這不改變本頁任何一個 LEC 數字。** 693 仍然是 NOT_MEASURED，
non_equivalent_points 仍然從頭到尾是 0，AES 的關鍵路徑仍然在 step 2 之前。
