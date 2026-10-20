# Landing references and closure record

## Closure practice

- Record the actual repair commit, release, verification scope and material limits in the issue or pull request before closing it.
- Use a separate `Fixes #NUMBER` or `Closes #NUMBER` line for each resolved item in the normal shipping commit. Use `Refs` for duplicate/rejected alternatives and still-open work.
- Before pushing a commit whose closing keywords will close an issue, post a qualified landing comment that clearly distinguishes qualified source from published source. After verifying remote main, tag and release, add the publication link.
- Preserve published history. A normal follow-up metadata commit can link earlier shipped fixes without rewriting their commits or making a new technical acceptance claim.

## Audit of the reported review closures

GitHub was queried directly. The nine items below had explanatory comments before their original closure; the missing convention was formal closing references in main commit messages. The broader two-day audit covered 150 closed items and 275 main commits. This audit does not rerun or certify an IC.

| Item | Existing explanatory comment | Comment UTC / original closure UTC | Source or disposition |
| --- | --- | --- | --- |
| #3310 | [comment](https://github.com/vibeic/vibe-ic/issues/3310#issuecomment-6290672251) | 12:18:52 / 12:18:54 | v1.30.39 |
| #3311 | [comment](https://github.com/vibeic/vibe-ic/issues/3311#issuecomment-6290673436) | 12:18:56 / 12:18:58 | v1.30.39 |
| #3312 | [comment](https://github.com/vibeic/vibe-ic/issues/3312#issuecomment-6290674278) | 12:18:59 / 12:19:00 | v1.30.39 |
| #3313 | [comment](https://github.com/vibeic/vibe-ic/issues/3313#issuecomment-6290675172) | 12:19:02 / 12:19:04 | v1.30.39 measurement/applicability repair; normal PDK opening-layer wiring REOPENED |
| #3314 | [comment](https://github.com/vibeic/vibe-ic/issues/3314#issuecomment-6291302482) | 12:53:39 / 12:53:41 | Original six-obligation clarification |
| #3315 | [comment](https://github.com/vibeic/vibe-ic/issues/3315#issuecomment-6290154426) | 11:48:28 / 11:48:29 | v1.30.38 |
| #3316 | [comment](https://github.com/vibeic/vibe-ic/issues/3316#issuecomment-6290675975) | 12:19:05 / 12:19:07 | v1.30.39 |
| #3317 | [comment](https://github.com/vibeic/vibe-ic/pull/3317#issuecomment-6289603253) | 11:14:52 / 11:14:53 | v1.30.36 |
| #3318 | [comment](https://github.com/vibeic/vibe-ic/issues/3318#issuecomment-6291303589) | 12:53:43 / 12:53:45 | ['v1.30.36', 'v1.30.38', 'v1.30.39'] |

All original closure timestamps in the table are on 2026-10-20. Issue #3313 has subsequently been reopened for a distinct unfinished normal-input wiring obligation; it is deliberately excluded from this commit's closing keywords. No bare-die distance limit or default IO supply-pad ratio is invented.

## Version and historical proof corrections

- `363d195431fc53d42652e7ca99cbfa61e4058289` is v1.30.42, tree `7b522dd910553d02f408fd80984380c2884bf0e6`; v1.30.41 is `de86f6cad102a4d2499bb6667774fee4713e135a`.
- The original Step5 executed consumer receipt has SHA256 `031cfc5401d6cf55ff955ff0f2f68a9938639a9fa1d8413f327a2986a6999335`. Six declared obligations close as five producer dispositions plus one proved reset-name binding. The initial 1/6 request and public citation-omission status are different records. The preserved original row and proof subset resolve this particular claim without rerunning Phase2.
- A completed review or source repair does not sign manual ESD/assembly decisions, provider order/upload/acceptance, a new whole-IC result or Ultra qualification.

## Earlier source-fix batches linked by this shipping commit

| Release | Published source | Resolved source items |
| --- | --- | --- |
| v1.30.36 | `bce7ed06ead8bdb68f38512cde5be3e50d373d22` | #3249, #3317 |
| v1.30.37 | `b1ec0af14e0cfe04973d3b783f24a3a7c21302ec` | #3303, #3304, #3305, #3306, #3307 |
| v1.30.38 | `2bcfd4697a860dafe864090b4576264e9d90b624` | #3308, #3315, #3320, #3323; integrated PR #3321 and #3325 |
| v1.30.39 | `ad1e201d434fa39279bf5efc315a277b3cd9fa80` | #3310, #3311, #3312, #3316; prior #3313 scope retained, current wiring open |
| v1.30.40 | `14392975569e3fc2331b940fbd57f4c3fa38f546` | #3319, #3324, #3327, #3329, #3330; earlier partial #3328 scope |
| v1.30.41 | `de86f6cad102a4d2499bb6667774fee4713e135a` | #3309, #3322 |
| v1.30.42 | `363d195431fc53d42652e7ca99cbfa61e4058289` | #3333 and integrated PR #3331; #3328 original current CDC component subsequently measured |

PR #3326 was rejected as a regressing alternative; PR #3332 was closed as already covered. Their code is not falsely recorded as adopted. #3314 and #3318 are clarification/review closures rather than new product fixes.
