# MapBV — Register & Bit Mapping

Describe registers, SRAM mappings, and logic operations with intuitive Python objects — complete with **bidirectional value sync**, **symbolic evaluation**, and **structure introspection**.

## Quick Start

```python
from mypkg import MapBV

# Declare registers
reg0 = MapBV("REG0", 16, tags={"type": "RW", "addr": 0x100})
reg1 = MapBV("REG1", 16, tags={"type": "RO"})
padding = MapBV(0, 2)               # 2-bit constant

# Define SRAM word: {REG0[3:0], padding, REG1[1:0]}
sram = MapBV("SRAM_00", 8)
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
# Named variable with metadata
reg = MapBV("REG0", 16, tags={"type": "RW", "addr": 0x100})

# Constant (immutable — writes emit a warning)
padding = MapBV(0, 4)
```

### 2. Linking & Bidirectional Sync

Link small signals into a larger word. Changes propagate **both ways** automatically.

```python
sram = MapBV("SRAM", 8)
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
```

Slices can also be **link targets**:

```python
reg[7:0].link(field_a, field_b)  # Restructure partial region
```

### 4. Logic & Shift Operators

```python
result = reg0 & reg1            # AND
result = reg0 | 0x00FF          # OR with int
result = ~reg0                  # Invert
result = reg0 << 4              # Left shift
result = reg0[7:0] ^ reg1[7:0]  # Slices support operators too

# Chainable
expr = (reg0 & 0x0F) | (reg1 ^ MapBV(0xFF, 16))
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
reg0_red   = MapBV("REG0", 16, tags={"color": "red"})
reg0_green = MapBV("REG0", 16, tags={"color": "green"})

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
# CONST None   tags=None
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
reg == 0x42         # → True/False
```

---

## API Reference

| Class | Description |
|:------|:------------|
| `MapBV(name_or_value, width, tags=None)` | Main BitVector — named variable or constant |
| `MapBVSlice` | Proxy from `bv[high:low]`, supports read/write/link/operators |
| `MapBVExpr` | Expression tree from logic/shift operators |
| `StructSegment` | Frozen dataclass with `.bv` and `.slice_range` |

### `MapBV` Properties & Methods

| Member | Type | Description |
|:-------|:-----|:------------|
| `.name` | `str` | Name of the MapBV (`"CONST"` for constants) |
| `.width` | `int` | Bit width |
| `.value` | `int` | Current value (read/write, bidirectional if linked) |
| `.is_const` | `bool` | Whether this is a constant |
| `.tags` | `dict\|None` | User metadata (`None` for constants) |
| `.link(*parts)` | method | Define as concatenation of parts (MSB→LSB) |
| `.unlink()` | method | Remove link, snapshot value |
| `.eval(ctx)` | method | Symbolic evaluation with context dict |
| `.structure` | `list[StructSegment]` | Linked composition (empty if unlinked) |
| `.copy(name)` | method | Deep-copy to independent MapBV |
| `.snapshot(name)` | method | Alias for `.copy()` |
| `.to_hex()` | method | Zero-padded hex string |
| `.to_bin()` | method | Zero-padded binary string |
| `.concat(*parts)` | classmethod | Create linked MapBV from parts |
