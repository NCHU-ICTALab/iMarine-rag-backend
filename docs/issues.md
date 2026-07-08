## Issue #1: [Data Pipeline] LLM 政策輔助報告：RAG 數據接入、治理與檢索接口開發

**Assignee:** @ChienI
**Status:** 📝 To Do
**Priority:** 🔴 High (2天內完成 MVP)
**Target Date:** [請填入兩天後的日期]

### 📝 任務描述 (Description)

為了支援「永續智能航港生態系」的 LLM + RAG 政策輔助報告模組，確保生成的報告具備最高等級的**事實基礎（Grounding）與可追溯性**。我們需要建立一套標準化的資料管線（Data Pipeline）。
任務涵蓋從**異質資料接入 (Ingestion)**、**資料治理與切段 (Processing & Chunking)**，到最終封裝成 LLM 專用的 **Evidence Package (證據包)** 供下游調用。

### 🎯 應用情境 (Use Cases)

1. **🚨 突發狀況應變：** 自動撈取過往處理做法與海運時事新聞（動態資料）。
2. **📜 新政策應對：** 國際組織（IMO/EU）發布新政策時，比對「臺灣航港法規」與「歷史案例」（靜態+標竿資料），給出建議。
3. **📰 例行性日報：** 抓取每日產業新聞、港口動態，產出結構化日報。

---

### 🗂️ 階段一：資料從哪來 & 怎麼進來 (Ingestion & Registry)

**目標：將異質資料統一封裝為 `RawDocument` 格式。**

- [ ] **建立 Source Registry (來源登錄機制)：**
  - 定義基礎 Metadata（如：`source_id`, `publisher`, `trust_score`, `update_frequency`）。
- [ ] **實作領域核心資料接入 (iMarine)：**
  - 串接 iMarine 臺灣數據統計 (貨櫃進出口) JSON API。
- [ ] **實作官方法源資料接入：**
  - 從「全國法規資料庫」下載 ZIP，並解析 XML/JSON（重點鎖定《商港法》及相關航港法令）。
- [ ] **實作國際標竿與動態資料接入 (爬蟲 / RSS)：**
  - 開發 IMO (Net-Zero Framework等)、EU ETS、新加坡 MPA 政策通告的爬蟲或 PDF 解析。
  - 串接 WHO 疫情通報、重點海運時事新聞 (RSS/爬蟲)。
- [ ] **統一輸出格式：** 將上述擷取結果統一封裝為 `RawDocument` 格式 (需包含 `source_url`, `fetched_at`, `checksum` 等欄位)。

---

### ⚙️ 階段二：資料怎麼處理 (Processing, Chunking & Policy Gate)

**目標：清洗資料並建立語意索引，確保切段合理且通過政策閘門。**

- [ ] **格式解析與正規化：**
  - 統一時間格式 (ISO 8601)、單位標準化 (TEU, GT)，處理基礎清洗。
- [ ] **動態切段 (Adaptive Chunking)：**
  - **法規：** 嚴格依「條/項/款」切分，不可跨條語意。
  - **新聞/政策文件：** 依段落/章節切分 (設定 250-700 tokens，10-20% overlap)。
- [ ] **元資料增豐 (Metadata Enrichment)：**
  - 為每個 Chunk 補齊 `document_id`, `chunk_id`, `effective_at`, `credibility_score` 與 `section_path` (便於回指原文)。
- [ ] **建立混合檢索索引 (Hybrid Indexing)：**
  - 將 Chunk 寫入向量資料庫 (推薦 PostgreSQL + pgvector)。
  - 支援 Vector (Dense) + BM25 (全文/關鍵字) + Metadata Filter (時間/來源過濾) 查詢。

---

### 📦 階段三：資料怎麼出去 (Retrieval & Evidence Packaging)

**目標：查詢時進行檢索與重排，最終輸出 LLM 專用的 `Evidence Package`。**

- [ ] **開發檢索 API 端點 (Retrieval API)：**
  - 接收 User Query，執行 Hybrid Retrieval 撈出 Top-K chunks。
- [ ] **實作 Evidence Packaging (證據封裝)：**
  - 在將資料餵給 LLM 之前，將檢索結果封裝成 `Evidence Package` JSON 格式。
  - 封裝內容需包含：任務型別 (`task_type`)、證據清單 (`evidence_items`含頁碼/段落 locator)、信心水準 (`confidence`)。
- [ ] **定義下游模組契約 (Output Contract)：**
  - 確保 API 輸出包含 `output` (推論內容) 與 `metadata` (來源、證據 ID、模型版本)，以便後續儀表板與排程引擎串接使用。

---

### 💡 開發備註與 MVP 降級策略 (Notes for @ChienI)

* **時間考量：** 兩天內要寫完這麼龐大的架構很難，請優先完成 **「一個法規來源 (全國法規資料庫)」** + **「一個動態新聞來源」** 的完整管線 (End-to-End)。
- **Chunking 最重要：** 法規切段務必做到「依條文切」，這對報告生成的準確度影響最大。
- **Evidence Package：** 這是本次企劃的亮點（防幻覺、高可信度），接口吐出來的 JSON 一定要帶有 `evidence_items` 以及 `locator`（出處段落），讓 LLM 的 Prompt 可以強制要求它標註來源。
