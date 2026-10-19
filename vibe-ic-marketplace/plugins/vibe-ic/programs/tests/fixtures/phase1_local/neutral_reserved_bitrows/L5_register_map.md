# Register Map
| 位址 | 名稱 | Access | Width | 描述 |
|---|---|---|---|---|
| `0x08` | `CTRL` | R/W | 32 | Control |
| `0x09` | `STATUS` | R | 32 | Status |

## CTRL Register(0x08)Bit Fields
| Bit | 名稱 | 功能 |
|---|---|---|
| 0 | `START` | start command |
| 1-31 | reserved | 讀回 0,寫入忽略 |

## STATUS Register(0x09)Bit Fields
| Bit | 名稱 | 功能 |
|---|---|---|
| 0 | `READY` | ready to accept a command |
| 1-31 | reserved | 讀回 0 |
