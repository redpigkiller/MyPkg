# StageTracker — 多階段流程紀錄

[![English](https://img.shields.io/badge/Language-English-blue.svg)](stage_tracker.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](stage_tracker_zh.md)

專為循序、多階段流程設計的日誌與進度追蹤器。具備執行追蹤、自動累積錯誤、檢查點機制 (checkpointing) 和自動生成總結報告等進階功能。

## 核心特色

- **階段生命週期管理**: 將執行過程清晰劃分為多個階段 (例如："讀取" -> "處理" -> "上傳")。
- **執行緒安全**: 使用 `threading.local()`，讓每個執行緒擁有獨立的階段歷史和錯誤追蹤紀錄，但仍可共用日誌處理程序 (log handlers)。
- **累積錯誤處理**: 不像標準 logging 將錯誤訊息散落各處，`StageTracker` 會集中收集它們，允許你提早中斷 (`checkpoint`) 或等待最後統一輸出總結報告。
- **豐富的報告生成**: 自動整理各階段的錯誤與警告，產生排版乾淨的總結報告區塊輸出到終端機。
- **延遲求值 (Lazy Evaluation)**: `tracker.info(..., data=my_dict)` 會延後消耗運算資源的 JSON 序列化，直到目標 handler 確定需要輸出時才執行。

## 基本用法

### 扁平模式 (Flat Mode)
最適合由上而下、循序執行的腳本。

```python
from mypkg.utils.stage_tracker import StageTracker

# 共用實例模式
tracker = StageTracker("MainTracker")

# 開始 "Initialization" 階段
tracker.set_stage("Initialization")
tracker.info("Starting workflow", track=True)
tracker.warning("Debug mode enabled")

# 隱式結束 "Initialization" 階段並開啟 "Data Processing" 階段
tracker.set_stage("Data Processing")
tracker.error("File 'corrupt.txt' is corrupt") # 紀錄錯誤但不中斷
tracker.error("File 'missing.txt' not found")

# 因為上方有紀錄兩個 error，執行這行將會觸發 StageFailedError
try:
    tracker.checkpoint()
except Exception as e:
    print(e)
    
tracker.summary()
```

### 內文管理器模式 (Context Manager Mode)
適合用在獨立區塊、迴圈、或者較複雜的巢狀邏輯中。

```python
from mypkg.utils.stage_tracker import StageTracker

tracker = StageTracker("ContextTracker")

with tracker.stage("Download"):
    tracker.info("Downloading files...")
    # 離開區塊時自動檢查健康狀態。
    # 若在區塊中有任何 `tracker.error()` 被呼叫，StageFailedError 將於此拋出。

with tracker.stage("Parsing"):
    tracker.fatal("Out of memory!") # 紀錄嚴重錯誤並立即拋出 StageFailedError
```

*注意事項：請勿在同一個 `StageTracker` 執行上下文中混合使用 Flat Mode 與 Context Manager Mode。*

## API 參考

### 設定檔 (Configuration)
* `add_console_handler(level="INFO", fmt="...")`: 新增終端機輸出。（初始化時會自動加入）。若環境有安裝 `rich` 則會啟用更豐富的顏色標示。
* `add_file_handler(path, level="DEBUG", fmt="...", max_bytes=0, backup_count=0)`: 新增日誌檔案輸出，可選擇加入檔案滾動保留 (log rotation) 的支援。
* `reset(keep_handlers=False)`: 清空所有累積的錯誤紀錄、階段歷史以及當前階段資訊。適合在同一個執行緒重啟 workflow 之前使用。

### 日誌紀錄 (Logging)
* `debug(msg, track=False, **kwargs)`: 標準 debug 追蹤器。
* `info(msg, track=False, **kwargs)`: 標準 info 追蹤器。設為 `track=True` 時會將其放入最後的報告區塊。
* `warning(msg, track=True, **kwargs)`: 警告追蹤器，預設會被追蹤與計入報告。
* `error(msg, **kwargs)`: 錯誤追蹤器。會新增一筆 error issue 並累加計數，但該當下「不會」拋出異常。
* `fatal(msg, **kwargs)`: 紀錄嚴重級別的錯誤，並且「立刻」拋出 `StageFailedError` 異常以中斷程式。

### 方法 (Methods)
* `set_stage(name)`: 開啟一個新的 flat-mode 階段，並同時檢查前一個階段的健康狀態是否有 error 發生。
* `checkpoint()`: 如果目前階段累積了任何 error 層級以上的紀錄，則拋出 `StageFailedError`。
* `summary(title="EXECUTION SUMMARY") -> bool`: 印出總結報告。若在此之前沒有任何錯誤發生則回傳 `True`，否則為 `False`。
* `get_issues(stage=None, level=None)`: 回傳符合條件的 `Issue` dataclasses 列表。層級條件可傳入單一個 `ErrorLevel` 或是其列表。
