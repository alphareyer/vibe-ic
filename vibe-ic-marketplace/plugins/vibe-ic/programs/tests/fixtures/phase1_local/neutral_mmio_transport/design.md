# Product
A register peripheral with a memory-mapped register interface using cs, we and address.
The top module is neutral_reg.

# External Interface
| Signal | Width | Direction | Description |
|---|---|---|---|
| clk | 1 | input | rising-edge clock |
| reset_n | 1 | input | synchronous active-low reset |
| cs | 1 | input | chip select |
| we | 1 | input | write enable |
| address | 8 | input | register address |
| write_data | 32 | input | write bus |
| read_data | 32 | output | read bus |
| error | 1 | output | invalid access |

# Register Map
| Address | Name | Access | Width | Description |
|---|---|---|---|---|
| 0x08 | CTRL | R/W | 32 | Control |
| 0x09 | STATUS | R | 32 | Status |

## CTRL Register (0x08) Bit Fields
| Bit | Name | Function |
|---|---|---|
| 0 | START | start command |
| 1-31 | reserved | read 0, ignore writes |

## STATUS Register (0x09) Bit Fields
| Bit | Name | Function |
|---|---|---|
| 0 | READY | ready to accept a command |
| 1-31 | reserved | read 0 |

# Clock
Clock period is 20 ns.
