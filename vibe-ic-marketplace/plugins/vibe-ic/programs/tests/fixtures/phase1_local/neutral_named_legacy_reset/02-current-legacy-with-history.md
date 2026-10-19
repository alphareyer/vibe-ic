# Current legacy reset declaration with historical comparison

Only the Current contract section below defines this revision's implementation. The Historical comparison section is retained as an obsolete design record and does not select current parameters or current reset behavior.

## Current contract

Clock clk_control_i has period 27 ns.
The current reset is sys_reset_n: an asynchronous active-low reset.
This is a legacy single-phase reset declaration. Separate assertion/release timing, a release clock and release synchronizer stages are not specified by this current source.

## Historical comparison

The following table describes an obsolete alternative only. Its phase, polarity and stage values must remain identifiable as history; they are not current implementation settings.

| Clock | Reset | Polarity | Assertion | Release | Release synchronizer stages |
|---|---|---|---|---|---:|
| clk_control_i | legacy_reset | active-high | synchronous | synchronous to clk_control_i | 7 |
