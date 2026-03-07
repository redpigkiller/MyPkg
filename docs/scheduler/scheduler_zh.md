# Scheduler — 極簡任務排程管理

[![English](https://img.shields.io/badge/Language-English-blue.svg)](scheduler.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](scheduler_zh.md)

`Scheduler` 是一個輕量級、跨平台的任務排程模組。專為並發執行耗時的背景任務（如：程式編譯、跑大規模模擬、密集 Python 運算）而設計，確保你的應用程式不會卡死，或是瞬間爆氣耗盡系統資源。

它內建了**靜態資源追蹤**、**自動失敗重試機制**、**即時事件回呼**，以及安全的**多執行緒管理機制**，完美支援終端機指令與標準 Python 函數的排程管理。

---

## 快速上手 (User Guide)

為了安全地並發執行任務，你需要建立各自獨立的 `Job` 物件（例如 `CmdJob` 或 `FuncJob`），並將它們交給 `JobManager` 來根據目前可用的電腦資源，協調它們的執行順序。

### 1. 啟動 JobManager

Manager 是你的系統守門員。你可以在一開始告訴它你的資源上限。

```python
from mypkg.scheduler import JobManager

# 限制最多開啟 4 個並行 Worker 執行緒，並且隨時最多只能有一個任務佔用 "gpu" 資源
manager = JobManager(max_workers=4, resources={"gpu": 1})

# 啟動背景派發迴圈
manager.start()
```

### 2. 建立任務 (Jobs)

任務是實際執行的單元。你可以使用 `CmdJob` 執行終端機指令，或是使用 `FuncJob` 執行你寫的 Python 函數。

```python
from mypkg.scheduler import CmdJob, FuncJob

# 一個簡單的終端機編譯指令
compile_job = CmdJob(
    name="build_app", 
    cmd="make clean && make -j4",
    cwd="/path/to/project",
)

# 一個需要特殊資源且具備自動重試邏輯的指令任務
sim_job = CmdJob(
    name="run_sim",
    cmd="./simulator --intensive",
    resources={"gpu": 1},  # 如果 GPU 正在被別人用，這個任務會自動乖乖排隊不搶佔
    max_retries=2,         # 失敗的話自動重試最多 2 次
    priority=10,           # 權重越高的數字越先被派發執行
)

# 一個自訂的 Python 函數任務
def calc_pi(precision):
    # 進行複雜運算...
    return 3.14

math_job = FuncJob(
    name="calculate_pi",
    func=calc_pi,
    kwargs={"precision": 100}
)
```

### 3. 加入排程與等待完成

一旦 Manager 處於啟動狀態，只要把這幾項任務丟給它加入排隊佇列即可。你可以隨時阻塞主程式等待所有人（或是特定單一任務）完工。

```python
# 把任務推入佇列排隊
manager.add(compile_job)
manager.add(sim_job)
manager.add(math_job)

# 等待全部任務執行完畢 (你的程式卡在這裡直到全數執行終止)
manager.wait()

print(f"模擬測試最後結果狀態：{sim_job.status}")
print(f"Python 函數運算結果：{math_job.result}")
```

### 4. 互動回呼與 Log 監控

你可以為任務掛上 Callback (回呼)，用來監控進度、處理失敗，甚至即時監聽主控台發出的特定關鍵字。

```python
test_job = CmdJob("test", "pytest -v")

# 監聽狀態改變
test_job.on_done(lambda job: print(f"✅ {job.name} 通過了！"))
test_job.on_fail(lambda job, err: print(f"❌ {job.name} 失敗了，錯誤：{err}"))

# 當任務的輸出內容符合某個 Regex 正規表達式時，即時觸發動作
test_job.watch(r"FAILED", lambda job, match: print("糟糕，有一個測資沒過！"))

# 隨時調閱最後 5 行捕捉下來的 Output 內容
print(test_job.tail(5))
```

### 5. 暫停與取消任務

如果你發現剛推進去的任務不需要跑了，或是你想讓電腦喘口氣暫停整個大會：

```python
# 從佇列中抽出這個任務（如果已經在跑了就會被強制砍掉）
manager.cancel(sim_job.id)

# 暫時凍結整個 Manager，不再派發任何新任務
manager.pause()

# 恢復正常營運
manager.resume()

# 禮貌地完整關閉 Manager 的背景執行緒 (會乖乖等正在跑的人跑完才收工)
manager.stop()
```

---

## API Reference (詳細控制)

### `JobManager` API

| 方法 | 說明 |
| --- | --- |
| `JobManager(max_workers=4, resources=None, log_dir=None)`| 初始化管理員。`resources` 為字典型態的容量上限 (`Dict[str, int]`)。不夠資源的排隊者會在旁邊等候。 |
| `.start()` / `.stop()`| 啟動或關閉 Manager 在背景輪詢派發的 Thread。停止時並不會強制砍斷正在執行的任務（RUNNING），而是等他們完成才讓 Thread 收工。 |
| `.add(job)`| 將一個 `Job` 物件加進佇列隊伍。如果任務要求的資源超出了 Manager 的物理上限，會在這一瞬間直接報 `ValueError`。 |
| `.cancel(target_id)`| 取消特定的任務（可以是字串型態的 UUID 或 UUID 實體物件），不僅會標註為 `CANCELLED` 還會對底下行程做強制終結。 |
| `.wait(target_id=None, timeout=None)`| 卡住你的主程式，直到特定某任務（或如果未給予 ID，則是等所有加入過的任務）處於打烊狀態（`DONE`, `FAILED`, `CANCELLED`）為止才放行。 |
| `.pause()` / `.resume()`| 暫停或重新激活 Manager 的內部派發引擎，控制何時能把下一個 `PENDING` 的任務拉出來執行。 |
| `.jobs()`, `.running()`, `.pending()`| 回傳列表（裝滿 `Job` 的清單），抓出此時此刻正處於相對應狀態下的任務有哪些人。 |

### `Job` 核心屬性

每一個抽象 `Job` 元件身上都掛有以下的標準公開財產：

- `.status` (`JobStatus`)：目前的執行狀態，只會是這五種之一：`"pending"`, `"running"`, `"done"`, `"failed"`, 或 `"cancelled"`。
- `.result` (`Any`)：成功跑完拿到的實體包裹（以 `CmdJob` 來說，它裡面就是離場的 `0`；以 `FuncJob` 來說，就是該函數 `return` 回來的東西）。
- `.error` (`str \| None`)：萬一它任務失敗了，這裡面裝的就是最直接的錯誤字串訊息或是 Traceback。
- `.is_cancelled` (`bool`)：如果它真的遭遇了被迫取消的命運，這會是 `True`。

### `CmdJob` 建立參數
* `name`：方便印 Log 出來認親的顯示名稱字串。
* `cmd`：你在終端機實際要砸下去執行的指令字串。
* `cwd`：你想讓這個指令在哪個絕對路徑的資料夾裡起跑？
* `env`：全部的環境變數覆蓋字典。
* `priority`：代表排隊順位的優先級整數。數字越大越早叫號。
* `max_retries`：萬一失敗了，容忍它死而復生重新挑戰的扣打次數。
* `resources`：`Dict[str, int]` 限縮它這個任務跑下去會吸走電腦的那些資源標籤與量級。
* `max_log_lines`：記憶體內部保留歷史吐出結果（Deque）的清單長度。預設 `10000` 行。

### `FuncJob` 建立參數
和上面大同小異，但這東西接的是：
* `func`：Python 內可被呼叫的實體函式物件。
* `args`：Tuple 清單，代表你在呼叫該函式時帶進去的位置參數。
* `kwargs`：字典形式對應的 Keyword 函式進件參數。
> **注意**：`FuncJob` 完全是生存在你 ThreadPool 的背景執行緒裡的。因為 Python 有 GIL (全域直譯器鎖) 限制，這種 Job 大多數場合只適合處理輕度的 Python-bound 文件操作或 API 網路流請求，如果打算讓它死命咬住 CPU 高效能運算，請改用另外開 Process 程序的 `CmdJob` 才是解藥。
