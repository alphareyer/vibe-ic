# Portable historical RESULT proofs

The three original historical commits were squash-landed and may be absent from a clean clone. Their original RESULT contents and claims are not rewritten here.

| Original commit | Original RESULT Git blob | Claims / holding |
|---|---|---|
| d6ea69acfdac0d1a9810a1d554ed608802011df5 | 61f9072c428bfad5d8293ef877b04146ff887371 | 16 / 15 |
| 4f2d47cf848bd2e69e95f82adba5d6dba5c2fbc1 | af1a5387379801be456df70444a95664a602e865 | 19 / 18 |
| afbf611ceb1965d0ebcbeb298991f925c43a59d3 | 97cf7c8dd51749134ba803a04f0235ef33a98733 | 20 / 19 |

Recovered on 2026-09-12 from the authentic batch-72 recovery bundle, whose SHA-256 is `98eb0cb65d11ee940e3893f9a273b1fa9244d599888fbb1f770b2ed1270cfa7f`, matching its pre-existing 2026-08-23 checksum. The full backup was restored in a private object store, all three blobs read, and native connectivity fsck returned 0. No synthetic commits or substitute refs were created.

HISTORICAL_CHECKPOINTS.json carries 18 original Git objects: each original commit, the tree objects along its exact docs/capture/2026-08-21-jcap-ppa/RESULT.md path, and the original blob. It is a path proof, not a complete clone or a claim that every parent/sibling object is packaged. Each object is checked using Git's type/size/data identity; the immutable requested commit SHA anchors the complete tree-to-blob chain. A sidecar count or bare blob digest cannot replace that linkage.

The history reader uses ordinary local Git evidence when available, otherwise authenticates this read-only archive. It neither fetches history nor writes objects/refs into the caller's repository. Missing/tampered evidence and incorrect historical count labels remain errors. Current counts still come only from the current RESULT tables.
