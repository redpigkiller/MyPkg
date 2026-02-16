# MyPkg — Data Types for IC Design & Verification

A lightweight Python toolkit for IC design & verification engineers.

---

## Installation

```python
# Add MyPkg to your Python path, then:
from mypkg import MapBV, NumBV
```

Dependencies:
```bash
pip install fxpmath    # NumBV only
```

---

## Data Types

### [MapBV](mypkg/data_types/mapbv_README.md) — Register & Bit Mapping

位元映射、暫存器結構、雙向同步、邏輯運算、符號化求值。

```python
reg = MapBV("REG0", 16, tags={"type": "RW", "addr": 0x100})
sram = MapBV("SRAM", 8)
sram.link(reg[3:0], padding, field[1:0])

sram.value = 0xFF       # 寫 SRAM → regs 自動更新
sram.eval({"REG0": 0xA}) # 模擬不改值
```

### [NumBV](mypkg/data_types/numbv_README.md) — Fixed-Point Arithmetic

定點數運算、自動飽和、Q-format、auto-limit。

```python
a = NumBV(16, 8, value=0.75)   # Q8.8
b = a * 1.5                    # → NumBV(val=1.125, width=16)
a += 0.5                       # in-place

x = NumBV(8, 0, signed=True, value=120)
y = x + 10                     # → val=127 (auto-saturate!)
```

---

## License

MIT
