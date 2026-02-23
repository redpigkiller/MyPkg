# CFG 模組文件

[![English](https://img.shields.io/badge/Language-English-blue.svg)](cfg.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](cfg_zh.md)

> **套件**：`mypkg.cfg`, `mypkg.fsm`, `mypkg.mcu`
> **依賴**：`pip install mypkg[cfg]`（安裝 networkx）

---

## 概覽

CFG 模組分三層：

| 層 | 模組 | 用途 |
|----|------|------|
| Layer 1 | `mypkg.cfg` | 通用 CFG 核心（圖形分析演算法） |
| Layer 2 | `mypkg.fsm` | FSM 狀態機分析（建立在 CFG 上） |
| Layer 3 | `mypkg.mcu` | MCU compiler 分析（Liveness + DCE） |

---

## Layer 1 — `mypkg.cfg`

### `BasicBlock`

CFG 中的節點，代表一段直線執行的指令序列。

```python
from mypkg.cfg import BasicBlock

bb = BasicBlock(
    id="entry",
    insns=["MOV A, #0", "CJNE A, #10, L1"],  # 任意物件 list
    meta={"src_line": 1},                      # 使用者自訂 metadata
)
```

| 屬性 | 型別 | 說明 |
|------|------|------|
| `id` | `str` | 唯一識別符（同時作為 graph node key） |
| `insns` | `list` | 指令序列（可以是字串或任意 IR 物件） |
| `meta` | `dict` | 任意 metadata |

---

### `CFG`

有向控制流程圖，以 networkx DiGraph 為底層。

#### 建構

```python
from mypkg.cfg import CFG

cfg = CFG()

# 新增 block
entry = cfg.add_block("entry", insns=["MOV A, #0"], meta={})
bb1   = cfg.add_block("bb1",   insns=["ADD A, #5"])
end   = cfg.add_block("end",   insns=["NOP"])

# 新增有向邊（可帶 label / cond）
cfg.add_edge("entry", "bb1", label="A != 10", cond="branch_true")
cfg.add_edge("entry", "end", label="A == 10", cond="branch_false")
cfg.add_edge("bb1",   "end")

# 指定 entry / exit
cfg.set_entry("entry")
cfg.set_exit("end")
```

> **注意**：新增相同 id 的 block 會 raise `ValueError`；邊的兩端 block 不存在會 raise `KeyError`。

#### 存取

```python
bb = cfg.get_block("entry")   # 取得 BasicBlock
cfg.blocks                    # 所有 block 的 list
cfg.entry                     # entry BasicBlock（或 None）
cfg.exit                      # exit BasicBlock（或 None）
cfg.predecessors("bb1")       # bb1 的前驅 block list
cfg.successors("entry")       # entry 的後繼 block list
cfg.edge_attrs("entry","bb1") # 邊的屬性 dict
"entry" in cfg                # 是否包含 block
len(cfg)                      # block 數量
```

#### 遍歷

```python
for bb in cfg.dfs():               # 深度優先（從 entry 開始）
    print(bb.id)

for bb in cfg.bfs():               # 廣度優先
    ...

order = cfg.reverse_postorder()    # 反向後序（Dataflow analysis 標準順序）
# → list[BasicBlock]，loop header 保證在 body 之前
```

#### 可達性分析

```python
cfg.can_reach("entry", "end")   # True / False

# 從 entry 走不到的 block
dead = cfg.find_unreachable()   # list[BasicBlock]

# 強連通分量（依拓撲順序）
sccs = cfg.find_sccs()          # list[list[str]]
# → [["entry"], ["header","body"], ["end"]]
```

#### 迴圈分析

```python
# DFS back-edge：尾 → 祖先節點，代表有 loop
backs = cfg.find_back_edges()   # list[tuple[str,str]]
# → [("body", "header")]

# 自然迴圈
loops = cfg.find_natural_loops()  # list[NaturalLoop]
loop = loops[0]
print(loop.header)       # "header"
print(loop.body)         # {"header", "body"}
print(loop.back_edge)    # ("body", "header")

# Dead loop：進入後無法抵達 exit 的 cycle
dead_loops = cfg.find_dead_loops()
# → [["stuck"]]   若 "stuck" 是自迴圈且走不到 exit
```

**Dead loop 定義**：在 SCC 中，所有 node 都無法抵達 `exit` block 的 cycle。

#### 支配分析

```python
# Immediate dominator（Lengauer-Tarjan）
idom = cfg.dominators()
# → {"entry":"entry", "bb1":"entry", "end":"bb1"}

# Immediate post-dominator（從 exit 反向）
ipost = cfg.post_dominators()

# 支配樹（networkx DiGraph）
tree = cfg.dominator_tree()
```

#### 線性化

```python
order = cfg.linearize("rpo")           # 反向後序（預設，可處理 cycle）
order = cfg.linearize("topological")   # 拓撲排序（若有 cycle 會 raise）
# → list[str]，block id 的有序列表
# 輸出範例：["entry", "header", "body", "end"]
```

使用情境：把 linearize 的結果交給你的 codegen，依序輸出每個 block 的 insns，再由 codegen 補 branch/jump 指令。

---

## Layer 2 — `mypkg.fsm`

FSM（Finite State Machine）目標硬體的分析工具，使用 **Interprocedural CFG（ICFG）** 模型，支援 `call`/`ret` 的子程式語義。

### `FSMGraph`

```python
from mypkg.fsm import FSMGraph
from mypkg.cfg import CFG

fsm = FSMGraph(max_call_depth=2)   # 預設最大 call 深度（可不設）
```

#### 主流程（Main Flow）

```python
# 新增 state（actions = BasicBlock.insns）
fsm.add_state("IDLE",  actions=["clr_counter"])
fsm.add_state("FETCH", actions=["load_insn"])
fsm.add_state("DONE")

# 新增 transition（= 有向邊）
fsm.add_transition("IDLE",  "FETCH", cond="start")
fsm.add_transition("FETCH", "IDLE",  cond="loop_back")
fsm.add_transition("FETCH", "DONE",  cond="halt")

# 指定 reset state 和 terminal state
fsm.set_reset("IDLE")
fsm.set_terminal("DONE")   # 可選；若未設定，dead loop 以「無出邊的 SCC」判斷
```

#### 子程式（Subroutine / Function）

```python
# 建立 function CFG
fn_cfg = CFG()
fn_cfg.add_block("fn_body", insns=["ADD R0, #1"])
fn_cfg.add_block("fn_ret",  insns=["ret"])
fn_cfg.add_edge("fn_body", "fn_ret")
fn_cfg.set_entry("fn_body")
fn_cfg.set_exit("fn_ret")

# 加入 FSMGraph
fsm.add_function("my_func", fn_cfg)

# 標記 call site（main flow 的某個 block 呼叫 function）
fsm.add_call_site(fn="main", block_id="FETCH", callee="my_func")

# 標記 return block
fsm.add_return("my_func", "fn_ret")
```

#### 分析

```python
# Dead state（從 reset 走不到的 state）
dead_states = fsm.find_dead_states()      # list[BasicBlock]

# Dead loop（cycle 中無法抵達 terminal 的 state 群）
dead_loops = fsm.find_dead_loops()        # list[list[str]]

# Call depth 檢查
depth = fsm.check_call_depth(max_depth=2)   # 超過 max_depth 則 raise ValueError
# 若不傳 max_depth，使用建構子的 max_call_depth；若兩者都沒設則只回傳深度

# Single return 檢查（每個 function 恰好 1 個 ret block）
fsm.check_single_return("my_func")    # 不符合則 raise ValueError
```

#### 輸出

```python
order = fsm.linearize()      # 主流程的 state 排列順序 list[str]
cfg   = fsm.get_cfg()        # 取得主流程 CFG
cfg   = fsm.get_cfg("my_func")  # 取得 function CFG
```

---

## Layer 3 — `mypkg.mcu`

MCU compiler 的最小分析集，用於 register allocation 前置準備。

### `LivenessAnalysis`

Backward dataflow analysis，計算每個 block 的 live-in / live-out 變數集合。

```python
from mypkg.cfg import CFG
from mypkg.mcu import LivenessAnalysis

# 你需要提供一個 def_use_fn：(insn) → (def_set, use_set)
def my_def_use(insn):
    # 根據你的 IR / ASM 格式解析
    # 回傳 (此指令定義的變數集, 此指令使用的變數集)
    ...
    return defs, uses

la = LivenessAnalysis(cfg, def_use_fn=my_def_use)
la.run()

la.live_in["entry"]     # frozenset，在 entry block 入口處 live 的變數
la.live_out["bb1"]      # frozenset，在 bb1 block 出口處 live 的變數

la.is_live_at_entry("bb1", "x")   # True / False
la.is_live_at_exit("entry", "x")  # True / False
```

**演算法**：iterative worklist（backward），收斂至 fixed point。

### `eliminate_dead_blocks`

結構性 dead block 刪除：從 CFG 移除不可達 block（in-place）。

```python
from mypkg.mcu import eliminate_dead_blocks

removed = eliminate_dead_blocks(cfg)   # list[BasicBlock]
# cfg 已被修改（不可達 block 被刪除）
```

> **注意**：只做結構性刪除（unreachable from entry），不做指令層的 dead code elimination。

---

## 完整範例

```python
from mypkg.cfg import CFG
from mypkg.fsm import FSMGraph

# 建立一個帶子程式的 FSM
fsm = FSMGraph(max_call_depth=2)
fsm.add_state("IDLE")
fsm.add_state("WORK", actions=["call my_fn"])
fsm.add_state("DONE")
fsm.add_transition("IDLE", "WORK", cond="start")
fsm.add_transition("WORK", "IDLE", cond="loop")
fsm.add_transition("WORK", "DONE", cond="halt")
fsm.set_reset("IDLE")
fsm.set_terminal("DONE")

fn = CFG()
fn.add_block("fn_a", insns=["ADD R0, #1"])
fn.add_block("fn_ret", insns=["ret"])
fn.add_edge("fn_a", "fn_ret")
fn.set_entry("fn_a")
fn.set_exit("fn_ret")
fsm.add_function("my_fn", fn)
fsm.add_call_site("main", "WORK", "my_fn")
fsm.add_return("my_fn", "fn_ret")

# 執行所有檢查
assert fsm.find_dead_states() == []
assert fsm.find_dead_loops() == []
fsm.check_call_depth()          # depth=1 ≤ 2, OK
fsm.check_single_return("my_fn")

# 輸出 state 順序
print(fsm.linearize())   # ["IDLE", "WORK", "DONE"]
```
