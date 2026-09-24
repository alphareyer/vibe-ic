# SDR sign-off acceptance findings

2026-09-24 13:55 Asia/Taipei; read-only evidence from 192.168.1.120 `/home/reyerchu/_lane_icspm5/cs_run` and `cs.log`.

- Base branch `next/codex-sdraccept` at `26d95992fc78b0e730db07bc9defc29103761785`.
- `sdr_transaction/pre_repair.def`: 27,353 components; `candidate.def`: 27,381 components, 28 added and 35 changed placement or master. Added masters: buf_4 12, buf_2 10, clkbuf_1 3, buf_8 2, clkbuf_2 1. Names include `u_core/wire187..214` and `u_core/max_length204/206`. No diode master in these 28.
- KLayout `reports/phase3/drc_signoff.rpt` has 26 `<item>` elements. The 13 non-edge DF.13_MV markers and DF.14_MV are near newly added `u_core/wire*` buffers. NW.2a_LV around y=2734..2759 and NP.2/PP.2 around (2248,2745) are near added buffers and existing `VIBEIC_ACTIVE_ROW_FILL_*` instances. Three DF.13_MV near x=1..17,y=3 have no newly added/moved candidate instance nearby.
- `cs.log` contains both a NO_CANDIDATE pass and later an ADOPTED candidate; the adopted record says `router_drc_preserved_clean` 0 -> 0, without sign-off DRC/LVS evidence at decision time.
