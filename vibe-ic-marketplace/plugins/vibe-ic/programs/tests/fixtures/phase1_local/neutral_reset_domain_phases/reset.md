# NeutralCore

The module **`reset_bridge`** has the following hardware interfaces defined.

| Signal | Direction | Type | Width | Description |
|---|---|---|---:|---|
| clk_i | input | logic | 1 | Main rising-edge clock, period 20 ns. |
| clk_aux_i | input | logic | 1 | Auxiliary rising-edge clock, period 32 ns. |
| rst_n | input | logic | 1 | System-supplied active-low global reset for both clock domains. |
| ready_o | output | logic | 1 | Ready output. |

## Reset phase and domain contract

| Clock | Reset | Polarity | Assertion | Release | Release synchronizer stages |
|---|---|---|---|---|---:|
| clk_i | rst_n | active-low | asynchronous | synchronous to clk_i | 2 |
| clk_aux_i | rst_n | active-low | asynchronous | synchronous to clk_aux_i | 2 |

The same external reset asserts both domains. Each domain releases reset only
after its own two rising clock edges. Activity in one domain cannot release
the other domain. This declaration makes no implementation or timing claim.
