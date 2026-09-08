# Historical commit archive

The source branches cited by `RESULT.md` were merged into a squash landing and
then deleted. Git does not promise to retain unreachable commit objects, so a
fresh main-only clone no longer resolves nine source commit IDs even though the
report must retain those IDs as historical evidence.

`HISTORICAL_COMMITS.bundle` preserves exactly those nine named commits and the
intervening source history. It is an incremental Git bundle whose prerequisites
are all ancestors of the repository commit that carries it.

## Re-derived 2026-09-08, and why the previous ids cannot come back

THE ARCHIVE WAS BROKEN BY A HISTORY REWRITE, NOT BY ROT. On 2026-09-07 this
repository's history was rewritten to remove text that may not be published. A
rewrite re-creates every object, so the nine commits below kept their content
and lost their identities on the same day, and the bundle — which named them by
the identities they no longer have, and depended on prerequisite objects the
rewrite also replaced — stopped verifying:

```text
error: Repository lacks these prerequisite commits:
error: ca5027e0357fd9499e63a00971b0f3e6712dc5aa
error: 05732dd2676d474a45e281d5a7c926be536e914d
```

THOSE IDS ARE GONE PERMANENTLY. No clone, mirror, reflog or lookup service will
resolve them, because the objects they named were never merely unreferenced —
they were replaced. Re-creating them would mean undoing the rewrite, which would
put back exactly the text the rewrite existed to remove.

WHAT WAS DONE INSTEAD. Each of the nine was mapped, one old id to one new id,
through the rewrite's own commit map, and the bundle was regenerated from the
commits those new ids name — the SAME nine commits, under the identities they
have now. No commit was re-authored, re-dated or re-worded, and no file content
was reconstructed. Every new id was confirmed to resolve here before it was
written down, and every one of the bundle's prerequisites was confirmed to be an
ancestor of `origin/main`, so the archive is usable from an ordinary clone
rather than from the machine that made it.

WHAT THIS ARCHIVE STILL CANNOT DO, unchanged: it proves identity and
recoverability. It does not by itself prove that a source branch landed; squash
landing must still be established by content comparison.

- Bundle SHA-256: `d5557eebfd7edad47bd72fafa2aa7c49e4320c9dea09b46c1a68f09bf8db64af`
- Bundle bytes: `7072612`
- Common declared base: `2bc61f80cfb710ff8a1d42f34710e9c4c9cd1dd9`
- Source: this repository's own object database after the 2026-09-07 rewrite;
  no file content was reconstructed.

Archived refs:

```text
0868b367e4a82613457a0506c9076b001d5543ac refs/archive/ppa-eco/followup
0db88ff44964faaf66ce4b07b0e96f651b083cfd refs/archive/ppa-eco/docs-correction
29bc6db304d5b0f3823e5f89f0decacf37955594 refs/archive/ppa-eco/backlog-b
2b5c19ea636c5668be0b41ecc4805eb46fca8b82 refs/archive/ppa-eco/backlog-a
45c5cf0e728772ebc58eaf4621d5be3d1200137f refs/archive/ppa-eco/tip-main
502fd2a03baab3e6b956ae033701cb5d67835721 refs/archive/ppa-eco/axis-count
96f349004a527169b115151aa107085274fe420e refs/archive/ppa-eco/incident-merge
c93cae43356fba5939a19c246a52ce84fe02af9d refs/archive/ppa-eco/incident
f3fa1d3d86910c17f69d5a33d58d4de732eed2ff refs/archive/ppa-eco/exemption
```

Verification and optional recovery:

```sh
git bundle verify docs/campaigns/ppa-eco-axis-audit/HISTORICAL_COMMITS.bundle
git bundle list-heads docs/campaigns/ppa-eco-axis-audit/HISTORICAL_COMMITS.bundle
git fetch docs/campaigns/ppa-eco-axis-audit/HISTORICAL_COMMITS.bundle \
  'refs/archive/ppa-eco/*:refs/archive/ppa-eco/*'
```
