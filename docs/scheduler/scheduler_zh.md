# Scheduler — 跨平台任務排程器

[![English](https://img.shields.io/badge/Language-English-blue.svg)](scheduler.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](scheduler_zh.md)

## 什麼是 Scheduler？

當你在開發中需要同時執行好幾個耗時的任務（例如：跑好幾十個模擬測試、編譯多個程式碼專案），如果你手動一個一個點擊執行，或是寫一個簡單的 for 迴圈同時把它們全部打開，你的電腦可能會因為**瞬間耗盡所有 CPU 和記憶體而當機**。

**Scheduler (任務排程器)** 就是為了解決這個問題而誕生的！
它就像一個聰明的餐廳經理，負責幫你：
1. **控制同時間能處理的客人數 (資源管理)**：確保你的電腦不會被操壞。
2. **安排上菜順序 (依賴關係)**：確保「切菜」的任務完成後，才開始「炒菜」的任務。
3. **即時轉播現場狀況 (狀態監控)**：讓你知道現在哪些任務正在跑、哪些在排隊、哪些失敗了。

---

## 5 大核心概念 (Core Concepts)

在使用 Scheduler 之前，花一分鐘認識這五個核心機制，你就能完全掌握它！

1. **資源管理 (Resources)**
   - 電腦的 CPU 核心、網路頻寬等都是有限的「資源」。
   - 在建立 Scheduler 時，你會跟它說：「我總共有 4 個 local 資源」。
   - 當你要執行一個 `Job` (任務) 時，你可以設定它需要消耗 1 個 local 資源。Scheduler 就會確保同時間**最多只有 4 個 Job 在跑**。如果第 5 個 Job 來了，它必須排隊等待前面有人執行完並「歸還資源」後，才能開始。

2. **依賴關係 (Dependencies: `depends_on`)**
   - 透過設定 `depends_on=[任務A, 任務B]`，你可以告訴 Scheduler：「這個任務必須等『任務A』和『任務B』都順利執行完畢 (`DONE`) 才能開始」。
   - 如果依賴的前置任務不幸失敗 (`FAILED`)，這個任務就會自動被標記失敗，避免白做工。

3. **優先順序 (Priority)**
   - 數字越大的 Job 優先級越高。當資源空出來時，Scheduler 會先派發 `priority=100` 的任務，而不是 `priority=0` 的任務。

4. **超時中斷 (Timeout)**
   - 有些任務可能會因為程式 Bug 死當。你可以為 Job 設定 `timeout=600` (秒)。如果時間到了它還沒結束，Scheduler 會毫不留情地把它強制終止 (`KILL`)，避免佔用資源。

5. **互動操作 (Actions)**
   - 不同的任務可能有自己專屬的「特殊技能」。
   - 例如，跑完的指令任務 (CmdJob) 可以提供 `open_log` 的操作，讓你按個按鈕就能用預設套裝軟體打開日誌檔。每個任務可以做的事情都不同，你可以用 `.actions("任務名稱")` 來探索它會什麼招式！

---

## 快速上手示範

只要 3 個步驟，馬上讓 Scheduler 幫你管好你的任務！

```python
from mypkg import Scheduler, CmdJob

# 步驟 1：建立一個 Scheduler
# 告訴它：你最多只能同時使用 4 個名稱為 "local" 的資源
sched = Scheduler(resources={"local": 4}, log_dir="./logs")

# 步驟 2：建立工作任務 (Job)
# compile_job 是一個終端機指令任務
compile_job = CmdJob("compile", cmd="gcc main.c -o main")

# sim_job 也是一個指令任務，但它說：「我必須等 compile_job 跑完才能跑我喔！」
sim_job = CmdJob(
    name="run_sim", 
    cmd="./main", 
    depends_on=[compile_job],  # 設定依賴關係
    timeout=60                 # 60秒跑不完就砍掉
)

# 步驟 3：把任務交給 Scheduler，然後叫它開始工作！
sched.submit(compile_job, sim_job)
sched.run()          # 這個指令會卡住 (Blocking)，直到所有任務都跑完為止

sched.summary()      # 跑完後，印出漂亮的總結報表！
```

---

## 如何控制 Scheduler？ (API 詳解)

這裡列出了你可以如何操作 `Scheduler` 物件，每個指令都能讓你掌控全局。

### 📅 加入與啟動
* `submit(*jobs)`：把好幾個 Job 丟進排隊佇列中。
* `run()`：**會卡住程式**。它會開始派發任務，並一直等到所有任務都完成 (或者失敗) 後，才會換下一行程式碼執行。適合寫成腳本。
* `start()`：**不會卡住程式**。它會在背景開一個隱形人員 (Thread) 幫你繼續派發任務，你的程式碼可以立刻繼續往下執行。適合用在有 UI 的圖形介面程式。
* `wait()`：如果你用了 `start()`，但在某個時刻你想等它全部跑完，就呼叫 `wait()`。

### ⏸️ 暫停、繼續與停止
* `pause()`：暫停排程器。**正在執行中的 Job 會繼續跑完**，但不會再派發新的任務。
* `resume()`：恢復排程器。暫停期間排隊的任務會重新開始被派發。
* `stop()`：告訴 Scheduler：「別再派發新的任務了！」。但它非常有禮貌，會等**正在執行中**的 Job 乖乖跑完才真正結束。

### 🕹️ 單一任務互動
你想針對排隊中的特定一個任務 (`"任務名稱"`) 做事時：
* `get("name")`：把那個 `Job` 物件拿出來給你操作。
* `follow("name", n=20)`：像直播一樣！把這個任務輸出的最後 20 行內容即時印在你的 Terminal 上。
* `cancel("name")`：把**還在排隊**的任務直接抽掉，不跑了。
* `kill("name")`：把**正在跑**的任務直接暴力終止。
* `set_priority("name", n)`：這個任務太急了，給它插隊！把優先級調高。
* `actions("name")`：問這個任務：「你會什麼絕招？」。它會回傳它能執行的特殊操作名稱。
* `action("name", "絕招名稱")`：直接命令這個任務施展它的絕招（例如 `"open_log"`）。

### 📊 狀態與篩選
想知道大家現在都跑到哪裡了嗎？
* `status()`：在終端機印出簡潔的目前狀況表 (誰在排隊、誰在跑)。
* `summary()`：在最後印出總結帳單！(包含所有任務的花費時間、成功或失敗)。
* 你還可以直接調閱名單 (會回傳 Job 的清單)：
  * `sched.pending`：還在排隊的名單
  * `sched.running`：正在執行的名單
  * `sched.done`：順利完成的名單
  * `sched.failed`：執行失敗的名單
  * `sched.cancelled`：被取消的名單

---

## 內建的工作類型 (Built-in Jobs)

### CmdJob (本機終端機指令)
專門用來在你的電腦上執行文字介面指令。

```python
CmdJob(
    name="sim_01",
    cmd="python run.py --tc 01",            # 你要在終端機打的指令
    cwd="/proj/sim",                        # [選填] 你想切換到哪個資料夾下達指令？
    env={"SEED": "42"},                     # [選填] 需要額外給它什麼環境變數？
    priority=10,                            # 數字越大越先跑
    resources={"local": 1},                 # [選填] 它會吃掉 1 的 local 資源 (預設就是 1)
)
```

---

## 掛鉤與即時監控 (Hooks & Matchers)

### Hooks — 生命週期事件回呼

你可以在 Job 的各個生命週期階段掛上 callback，讓它在特定事件發生時自動通知你。

| Hook 事件 | 觸發時機 | Callback 簽名 |
|-----------|---------|---------------|
| `on_start` | Job 開始執行前 | `callback(job)` |
| `on_done` | Job 順利完成後 | `callback(job)` |
| `on_fail` | Job 失敗後 | `callback(job)` |
| `on_cancel` | Job 被取消時 | `callback(job)` |
| `on_output` | 每產出一行 stdout | `callback(line, job)` |

```python
job = CmdJob("sim", cmd="python run.py")

# 完成時通知
job.add_hook("on_done", lambda j: print(f"✅ {j.name} 完成！"))

# 失敗時發出警告
job.add_hook("on_fail", lambda j: print(f"❌ {j.name} 失敗！exit_code={j.exit_code}"))

# 即時監看輸出 (每行輸出都會觸發)
job.add_hook("on_output", lambda line, j: print(f"[{j.name}] {line}"))

# 不想監聽了？移除 hook
job.remove_hook("on_output", my_callback)
```

**讀取歷史輸出**
```python
job.tail(20)          # 直接拿最後 20 行產出
job.output_lines      # 取得完整 output 紀錄 (list[str])
```

### Matchers — 智慧型 Log 分析

Matcher 讓你定義一個「匹配函數」，當 output 中出現符合條件的內容時，自動觸發 callback。

```python
import re

# 偵測 output 中的 ERROR 字樣
def find_error(line):
    m = re.search(r"ERROR: (.+)", line)
    return m.group(1) if m else None  # 回傳 truthy → 觸發 callback

def on_error(matched_text, job):
    print(f"⚠️ Job {job.name} 遇到錯誤: {matched_text}")

job.add_matcher(find_error, on_error, name="error_finder", timing="realtime")
```

| 參數 | 說明 |
|------|------|
| `timing="realtime"` | 每行 output 產出時**即時** match |
| `timing="post"` | Job 結束後，**一次性**掃描所有 output |
| `once=True` | match 到第一次後自動移除 |

---

## 進階：打造你的專屬 Job (Custom Job)

有時候你可能不只是想打打終端機指令，而是想讓 Scheduler 幫你排隊執行某個你寫好的 **Python 函數**。

繼承 `Job` 基礎模板，重寫執行邏輯就能打造自己專屬的打工仔！

**Job 生命週期**：
```
on_start hooks → _pre_execute() → _execute() → _post_execute() → on_done / on_fail / on_cancel hooks
```

### 完整範例：把 Python Function 變成 Job

```python
import time
from mypkg.scheduler.job import Job, DONE, FAILED

class PythonJob(Job):
    """將任意 Python 函數變成可以讓 Scheduler 排隊的 Job"""

    def __init__(self, name, func, *args, **kwargs):
        # 1. 記得呼叫老爸 (super) 來設定基礎建設
        # cmd 可以隨便給一個能識別的字串
        super().__init__(name, cmd="[Python 可呼叫物件]")
        
        # 把參數存起來等等用
        self.func = func
        self.args = args
        self.kwargs = kwargs

        # ⭐️ 重點：為你的專屬 Job 裝備「專屬招式 (Action)」
        self.register_action("say_hi", "這是一個會說 Hi 的絕招", lambda: print(f"Hi! 我是 {name} 任務"))

    def _pre_execute(self):
        """[選填] 在 _execute 前自動呼叫，適合做初始化"""
        self._emit_line("正在做前置準備...")

    def _execute(self, log_file=None):
        """2. 這是最核心的邏輯，Scheduler 輪到它的時候會呼叫這裡"""
        try:
            self._emit_line(f"準備開始執行函數：{self.func.__name__}")
            
            # 實際執行你的 Python 程式碼
            result = self.func(*self.args, **self.kwargs)
            
            self._emit_line(f"執行成功！計算結果等於 {result}")
            
            # 3. 非常重要：你必須在最後誠實申報這份工作的狀態
            self.exit_code = 0          # 0 代表成功
            self.status = DONE          # 標記為順利結案
            
        except Exception as e:
            self._emit_line(f"糟糕，程式出錯了：{e}")
            self.exit_code = 1          # 非 0 代表失敗
            self.status = FAILED        # 標記為結案失敗


# ================================
# 🎉 實際測試看看！
# ================================
def compute_heavy_math(x, y):
    time.sleep(2) # 假裝運算很久
    return x ** y

# 放進你剛打造的 PythonJob 裡
my_job = PythonJob("math_task", compute_heavy_math, 2, 10)

# 掛上完成通知
my_job.add_hook("on_done", lambda j: print(f"🎉 {j.name} 計算完畢！"))

# 丟進排程器中執行
sched = Scheduler(resources={"local": 2})
sched.submit(my_job)
sched.run()

# 施展這個 Job 的專屬絕招！(會印出 Hi)
sched.action("math_task", "say_hi")
```

恭喜你！現在你完全了解 Scheduler 從底層概念到進階擴充的所有秘密了！
