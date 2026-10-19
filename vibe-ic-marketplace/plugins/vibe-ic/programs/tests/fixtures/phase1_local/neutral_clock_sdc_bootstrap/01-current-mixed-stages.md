# Current two-clock reset contract

This document is the current implementation contract of this revision.

## Current clocks

| Clock | Period |
|---|---:|
| clk_fast_i | 16 ns |
| clk_aux_i | 31 ns |

The two clocks are separate domains. Equal phase or a shared edge is not assumed.

## Current reset phase and domain contract

| Clock | Reset | Polarity | Assertion | Release | Release synchronizer stages |
|---|---|---|---|---|---:|
| clk_fast_i | sys_reset_n | active-low | asynchronous | synchronous to clk_fast_i | 2 |
| clk_aux_i | sys_reset_n | active-low | asynchronous | synchronous to clk_aux_i | 3 |

The external system drives sys_reset_n for both domains. Assertion is asynchronous in both domains. After external deassertion, the fast domain releases only after two rising clk_fast_i edges, and the auxiliary domain releases only after three rising clk_aux_i edges. A clock edge in the other domain cannot release an inactive domain. Each release synchronizer has asynchronous clearing and no clock enable.
