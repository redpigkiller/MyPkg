# MapBV — Register & Bit Mapping

[![English](https://img.shields.io/badge/Language-English-blue.svg)](mapbv.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](mapbv_zh.md)

Describe registers, SRAM mappings, and logic operations with intuitive Python objects — complete with **bidirectional value sync**, **symbolic evaluation**, and **structure introspection**.

## Quick Start

```python
import mypkg.data_types.mapbv as mbv
from mypkg import MapBV

# Declare registers using factory functions
reg0 = mbv.var("REG0", 16, tags={"type": "RW", "addr": 0x100})
reg1 = mbv.var("REG1", 16, tags={"type": "RO"})
padding = mbv.const(0, 2)               # 2-bit constant

# Define SRAM word: {REG0[3:0], padding, REG1[1:0]}
sram = mbv.var("SRAM_00", 8)
sram.link(reg0[3:0], padding, reg1[1:0])

# Write regs → read SRAM
reg0.value = 0x5;  reg1.value = 0x2
print(sram.to_hex())                 # → 0x52

# Write SRAM → regs auto-update
sram.value = 0xFF
print(reg0.to_hex(), reg1.to_hex())  # → 0x000F 0x0003
```

---

## Features

### 1. Declaration

```python
import mypkg.data_types.mapbv as mbv

# Named variable with metadata
reg = mbv.var("REG0", 16, tags={"type": "RW", "addr": 0x100})

# Constant (immutable — writes emit a warning; value auto-masked to width)
padding = mbv.const(0, 4)
mask    = mbv.const(0xFF, 4)   # → value is 0xF (masked to 4 bits)
```

### 2. Linking & Bidirectional Sync

Link small signals into a larger word. Changes propagate **both ways** automatically.

```python
sram = mbv.var("SRAM", 8)
sram.link(reg0[3:0], padding, reg1[1:0])   # MSB → LSB order

# Read: concatenates children's current values
# Write: splits bits and pushes to each child
```

Multiple SRAMs can link to the **same register slices** — they all stay in sync:

```python
sram_red   = MapBV.concat(reg0[7:0], reg1[7:0], name="RED")
sram_green = MapBV.concat(reg0[7:0], reg1[7:0], name="GREEN")
```

### 3. Slicing `[high:low]`

Hardware-style inclusive bit range:

```python
reg.value = 0xABCD
reg[7:0].value          # → 0xCD
reg[15:8].value         # → 0xAB
reg[7:4].value = 0xF    # Set bits 7~4 only
reg[3]                  # → 1-bit slice
```

To compose a sub-region from fields, create an explicit VAR:

```python
lower = var("REG_LOWER", 8)
lower.link(field_a, field_b)
reg.link(reg[15:8], lower)  # upper half from slice, lower half from lower
```

### 4. Logic & Shift Operators

```python
result = reg0 & reg1            # AND
result = reg0 | 0x00FF          # OR with int
result = ~reg0                  # Invert
result = reg0 << 4              # Left shift
result = reg0[7:0] ^ reg1[7:0]  # Slices support operators too

# Chainable
expr = (reg0 & 0x0F) | (reg1 ^ mbv.const(0xFF, 16))
print(expr.value)
```

### 5. Symbolic Evaluation `.eval()`

What-if analysis **without** changing actual values:

```python
reg0.value = 0x5;  reg1.value = 0x2

# "What if REG0 were 0xA and REG1 were 0x3?"
simulated = sram.eval({"REG0": 0xA, "REG1": 0x3})
print(hex(simulated))           # → 0xA3

# Actual values are untouched
print(sram.to_hex())            # → 0x52  (still the original)
```

#### Tag-Aware Eval

When same-name registers exist under different conditions (e.g., "red" vs. "green"), use `MapBV.key()` to create tag-specific context keys:

```python
reg0_red   = mbv.var("REG0", 16, tags={"color": "red"})
reg0_green = mbv.var("REG0", 16, tags={"color": "green"})

sram_red.eval({
    MapBV.key("REG0", {"color": "red"}): 0x1,
    MapBV.key("REG1", {"color": "red"}): 0x2,
})  # → 0x12
```

### 6. Structure Introspection `.structure`

Inspect the composition of a linked MapBV — ideal for **Design Rule Checks**:

```python
for seg in sram.structure:
    print(f"{seg.bv.name} {seg.slice_range} tags={seg.bv.tags}")

# Output:
# REG0 (3, 0) tags={'type': 'RW', ...}
# Constant None   tags=None
# REG1 (1, 0) tags={'type': 'RO'}
```

### 7. Formatting

```python
reg.to_hex()        # → "0x00FF"
reg.to_bin()        # → "0b0000000011111111"
f"{reg:hex}"        # → "0x00FF"   (via __format__)
f"{reg:bin}"        # → "0b0000000011111111"
```

### 8. Utility Methods

```python
word = MapBV.concat(a, b, c, name="WORD")
backup = reg.copy("REG0_backup")
sram.unlink()
len(reg)            # → 16  (bit width)
int(reg)            # → integer value
reg.value_eq(0x42)  # → True/False  (use instead of ==)
```

### 9. Error Handling & Validation

`MapBV` provides detailed error messages to prevent invalid states and operations:

- **Invalid Names**: `ValueError("Invalid name '1bad': must be a valid Python identifier...")`
- **Out of Bounds Values**: `ValueError("Value 0x100 out of bounds for 8-bit MapBV (max 0xFF)")`
- **Invalid Width/Slice Range**: `ValueError("Invalid range [2:5]: width must be > 0, got -2")`
- **Slice Out of Range**: `IndexError("Slice [8:0] out of bounds for REG[7:0]")`
- **Operand Exceeds Width**: `ValueError("Operand 0x100 exceeds MapBV width 8 (max 0xFF)")`
- **Slicing on Custom Tags**: `ValueError("Slices cannot have custom tags; they inherit from their parent")`

---

## API Reference

| Class | Description |
|:------|:------------|
| `MapBV(parent, high, low)` | Core BitVector node — named variable, constant, or slice |
| `MapBVExpr` | Expression tree from logic/shift operators |
| `StructSegment` | Frozen dataclass with `.bv` and `.slice_range` |

### Factory Functions (Preferred)

| Function | Description |
|:---------|:------------|
| `const(value, width)` | Create an immutable constant (value auto-masked) |
| `var(name, width, tags=None)` | Create a named variable |

```python
import mypkg.data_types.mapbv as mbv
# or: from mypkg import const, var

padding = mbv.const(0, 2)
reg     = mbv.var("REG0", 16, tags={"type": "RW"})
```

### `MapBV` Properties & Methods

| Member | Type | Description |
|:-------|:-----|:------------|
| `.name` | `str` | Name (`"Constant"` for constants) |
| `.width` | `int` | Bit width |
| `.value` | `int` | Current value (read/write, bidirectional if linked) |
| `.typ` | `str` | Node type: `"CONST"`, `"VAR"`, or `"SLICE"` |
| `.tags` | `dict\|None` | User metadata (`None` for constants and slices) |
| `.value_eq(other)` | method | Value comparison (use instead of `==`) |
| `.link(*parts)` | method | Define as concatenation of parts (MSB→LSB) |
| `.unlink()` | method | Remove link, snapshot value |
| `.eval(ctx)` | method | Symbolic evaluation with context dict |
| `.structure` | `list[StructSegment]` | Linked composition (empty if unlinked) |
| `.copy(name)` | method | Deep-copy to independent MapBV |
| `.to_hex()` | method | Zero-padded hex string |
| `.to_bin()` | method | Zero-padded binary string |
| `.concat(*parts)` | classmethod | Create linked MapBV from parts |
| `.key(name, tags)` | staticmethod | Create hashable tag-aware eval context key |
