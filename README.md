# PyBitVector (PBV)

A lightweight Python library for IC design & verification engineers.  
Describe registers, SRAM mappings, and logic operations with intuitive Python objects — complete with **bidirectional value sync**, **symbolic evaluation**, and **structure introspection**.

---

## Installation

```python
# Simply add MyPkg to your Python path, then:
from mypkg import BV
```

---

## Quick Start

```python
from mypkg import BV

# Declare registers
reg0 = BV("REG0", 16, tags={"type": "RW", "addr": 0x100})
reg1 = BV("REG1", 16, tags={"type": "RO"})
padding = BV(0, 2)                    # 2-bit constant

# Define SRAM word: {REG0[3:0], padding, REG1[1:0]}
sram = BV("SRAM_00", 8)
sram.link(reg0[3:0], padding, reg1[1:0])

# Write regs → read SRAM
reg0.value = 0x5;  reg1.value = 0x2
print(sram.to_hex())                   # → 0x52

# Write SRAM → regs auto-update
sram.value = 0xFF
print(reg0.to_hex(), reg1.to_hex())    # → 0x000F 0x0003
```

---

## Features

### 1. Declaration

```python
# Named variable with metadata
reg = BV("REG0", 16, tags={"type": "RW", "addr": 0x100})

# Constant (immutable — writes emit a warning)
padding = BV(0, 4)
```

### 2. Linking & Bidirectional Sync

Link small signals into a larger word. Changes propagate **both ways** automatically.

```python
sram = BV("SRAM", 8)
sram.link(reg0[3:0], padding, reg1[1:0])   # MSB → LSB order

# Read: concatenates children's current values
# Write: splits bits and pushes to each child
```

Multiple SRAMs can link to the **same register slices** — they all stay in sync:

```python
sram_red   = BV.concat(reg0[7:0], reg1[7:0], name="RED")
sram_green = BV.concat(reg0[7:0], reg1[7:0], name="GREEN")
# Both read/write the same underlying registers
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
expr = (reg0 & 0x0F) | (reg1 ^ BV(0xFF, 16))
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

When same-name registers exist under different conditions (e.g., "red" vs. "green"), use `BV.key()` to create tag-specific context keys:

```python
reg0_red   = BV("REG0", 16, tags={"color": "red"})
reg0_green = BV("REG0", 16, tags={"color": "green"})

sram_red   = BV.concat(reg0_red[3:0],   reg1_red[3:0],   name="RED")
sram_green = BV.concat(reg0_green[3:0], reg1_green[3:0], name="GREEN")

# Eval red scenario only
sram_red.eval({
    BV.key("REG0", {"color": "red"}): 0x1,
    BV.key("REG1", {"color": "red"}): 0x2,
})  # → 0x12

# Eval green scenario only
sram_green.eval({
    BV.key("REG0", {"color": "green"}): 0x5,
    BV.key("REG1", {"color": "green"}): 0x2,
})  # → 0x52
```

**Context key priority:**
1. `BV.key(name, tags)` — exact match (all tag key-value pairs must match)
2. `"name"` string — applies to all BVs with that name
3. Current `.value` (fallback)

### 6. Structure Introspection `.structure`

Inspect the composition of a linked BV — ideal for **Design Rule Checks**:

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
# Quick concat (no need to pre-declare + link)
word = BV.concat(a, b, c, name="WORD")

# Snapshot current value into an independent copy
backup = reg.copy("REG0_backup")

# Remove link structure, keep current value
sram.unlink()

# Pythonic integrations
len(reg)            # → 16  (bit width)
int(reg)            # → integer value
reg == 0x42         # → True/False (value comparison)
```

---

## API Reference

| Class | Description |
|---|---|
| `BV(name_or_value, width, tags=None)` | Main BitVector — named variable or constant |
| `BVSlice` | Proxy from `bv[high:low]`, supports read/write/link/operators |
| `BVExpr` | Expression tree from logic/shift operators |
| `StructSegment` | Frozen dataclass with `.bv` and `.slice_range` |

### `BV` Properties & Methods

| Member | Type | Description |
|---|---|---|
| `.name` | `str` | Name of the BV (`"CONST"` for constants) |
| `.width` | `int` | Bit width |
| `.value` | `int` | Current value (read/write, bidirectional if linked) |
| `.is_const` | `bool` | Whether this is a constant |
| `.tags` | `dict\|None` | User metadata (`None` for constants) |
| `.link(*parts)` | method | Define as concatenation of parts (MSB→LSB) |
| `.unlink()` | method | Remove link, snapshot value |
| `.eval(ctx)` | method | Symbolic evaluation with context dict |
| `.structure` | `list[StructSegment]` | Linked composition (empty if unlinked) |
| `.copy(name)` | method | Deep-copy to independent BV |
| `.snapshot(name)` | method | Alias for `.copy()` |
| `.to_hex()` | method | Zero-padded hex string |
| `.to_bin()` | method | Zero-padded binary string |
| `.concat(*parts)` | classmethod | Create linked BV from parts |

---

## License

MIT
