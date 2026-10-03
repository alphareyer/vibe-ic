# Issue #2868 bounded-preview controls

These four files are the exact 4096-byte blobs that were previously stored at
the authoritative DRC/LVS report paths.  They are deliberately outside both
`8HD-4/runs/cmp3/` and `8HD-9/runs/cmp3/`, and their `.json.preview` suffix keeps
them out of the tracked JSON/YAML parser population.

| Control | Bytes | SHA256 |
|---|---:|---|
| `8HD-4/.../drc.klayout.json.preview` | 4096 | `4f0e80861e66a73037003ffc279d8edb7c617edc17e124e80c14a45aa37ca02d` |
| `8HD-4/.../lvs.netgen.json.preview` | 4096 | `273472baa044cd2bf09c1c6392e31709cd0593b05ce747775df0f33d8f34bbf6` |
| `8HD-9/.../drc.klayout.json.preview` | 4096 | `4f0e80861e66a73037003ffc279d8edb7c617edc17e124e80c14a45aa37ca02d` |
| `8HD-9/.../lvs.netgen.json.preview` | 4096 | `273472baa044cd2bf09c1c6392e31709cd0593b05ce747775df0f33d8f34bbf6` |

The importer must refuse each control when it is put back at its producer
filename.  The complete reports in the sibling run trees are copied from the
real CMP3 producer outputs and are intentionally byte-identical across the two
host directories; their host-specific run identity remains in each tree's
`flow.log`, `resolved.json`, and step metadata.
