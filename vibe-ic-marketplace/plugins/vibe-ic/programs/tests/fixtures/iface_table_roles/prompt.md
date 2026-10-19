# L3 — External Interface

## Module 對外端口

| 訊號 | 寬度 | 方向 | 描述 |
|---|---|---|---|
| `clk` | 1 | input | 系統時脈;所有資料於上升沿同步取樣 |
| `reset_n` | 1 | input | 同步 reset,**active-LOW**;assert(0)時內部歸 idle |
| `cs` | 1 | input | Chip select;`cs=1` 啟用 register file 存取 |
| `we` | 1 | input | Write enable;`we=1` 寫入 / `we=0` 讀取 |
| `address` | 8 | input | Register file address(範圍 `0x00 - 0x27`,完整 map 見 L5) |
| `write_data` | 32 | input | 寫入資料 |
| `read_data` | 32 | output | 讀出資料(只在 `cs=1` 且 `we=0` 時 valid) |
| `error` | 1 | output | 錯誤旗標(read undefined address 等);0 = no error |


# Register Map
These internal registers are accessed through the bus; they are not top-level ports.
| 位址 | 名稱 | Access | Width | 描述 |
|---|---|---|---|---|
| `0x00` | `INFO` | R | 32 | identity word |
| `0x08` | `CONFIG` | R/W | 32 | control word |
| `0x09` | `STATUS` | R | 32 | status word |
