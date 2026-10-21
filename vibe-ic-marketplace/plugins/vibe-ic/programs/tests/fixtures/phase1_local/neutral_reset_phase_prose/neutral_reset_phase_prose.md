# NeutralCore

The module **`reset_bridge`** has the following hardware interfaces defined.

| Signal | Direction | Width | Type |
|---|---|---|---|
| core_clock | input | 1 | logic |
| entropy_clock | input | 1 | logic |
| rst_n | input | 1 | logic |

| Clock | Reset | Polarity | Assertion | Release | Release synchronizer stages | Deassertion guard ns |
|---|---|---|---|---|---:|---:|
| core_clock | rst_n | active-low | asynchronous | synchronous to core_clock | 2 | 0.7 |
| entropy_clock | rst_n | active-low | asynchronous | synchronous to entropy_clock | 2 | 0.9 |

rst_n remains the same active-low system reset for both domains, with asynchronous assertion and separate two-stage synchronous release.
