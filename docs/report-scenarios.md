# 輔助報告情境設計測試

以一組代表性政策題，系統性驗證「檢索 → Evidence Package → 生成 → citation」整條在各情境的品質，並抓出弱點與回歸。對應腳本：`scripts/eval_report.py`。

> **前置**：後端於 :8100 運行，且知識庫已跑過完整 ingestion（`POST /api/ingest/run`）。**若只 ingest 新聞、其他來源 0 chunks，報告會因證據不足而品質低落——這是最常見的「報告很爛」根因。**

## 執行

```bash
uv run python scripts/eval_report.py                 # 全部情境
uv run python scripts/eval_report.py --only imo_nzf  # 單一情境
uv run python scripts/eval_report.py --out results.md # 另存 markdown
```

LLM 輸出有隨機性，門檻採**軟性**：未達標以 ✗ 標記供人判讀，不是 CI 硬性 gate。

## 情境清單

| id | 題目 | 模版 | 期望來源類型 | 覆蓋率門檻 |
|---|---|---|---|---|
| `green_methanol` | 台灣港口綠色甲醇加注政策現況與建議 | policy_brief | alt_energy | 60% |
| `imo_nzf` | IMO 淨零框架對台灣航商與港口的影響與因應 | policy_brief | alt_energy | 60% |
| `shipping_port_act` | 商港法對商港管理與港務作業的規範與責任 | policy_brief | regulation | 50% |
| `shore_power` | 台灣港口岸電推動的政策與實踐現況 | policy_brief | alt_energy | 55% |
| `news_digest` | 近期國際海運與替代能源重點動態彙整 | news_digest | alt_energy | 50% |
| `seafarer_training` | 替代燃料時代的船員培訓與教育資源 | policy_brief | alt_energy | 50% |

情境刻意涵蓋不同知識庫：`shipping_port_act` 專測**法規**（`law_moj_shipping_port_act`）是否被檢索到、`news_digest` 測**新聞彙整**模版、其餘測**替代能源**各面向（燃料/國際/臺灣/教育）。

## 量測指標

| 指標 | 說明 | 判定 |
|---|---|---|
| citation coverage | 被引用的 evidence / 全部 evidence | 硬性：≥ 情境門檻 |
| 空章節 | 章節文字 < 60 字或含「證據不足/無法產出」 | 硬性：需為 0 |
| 期望來源類型 | source_list 是否含情境期望的 source_type | 硬性：需全數出現 |
| 不同來源數 | source_list 的 distinct evidence 數 | 軟性提醒 |
| 單一來源撐整段 | 某章節只引用 ≤1 個 evidence | 軟性提醒（品質信號） |

**pass = 三項硬性全過**（覆蓋率 + 無空章節 + 期望來源類型）。軟性項（來源數、單一來源撐整段）只提醒不擋 pass——例如「建議事項」章節常見只引用單一平台會議紀錄，是可改進的品質信號。

## 已知品質觀察（供後續改進）

- **KB 未 ingest 是最大殺手**：報告品質高度依賴知識庫是否灌滿；空庫時覆蓋率與內容都崩。
- **建議章節單一來源**：`policy_brief` 的「建議事項」常只引用 1 個 evidence（如替代燃料工作平台會議紀錄），建議未來在檢索/prompt 補強建議段的證據多樣性。
- **法規來源的相關性**：碳排/NZF 類題目不會（也不該）檢索到商港法；`shipping_port_act` 情境確保法規題確實命中 `regulation` 來源。
