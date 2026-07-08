# iMarine 政策助理 — LLM + RAG 政策輔助報告模組

以**事實基礎（Grounding）與可追溯性**為核心的政策問答與報告產出系統。法規與航港局新聞稿經過治理、切段、向量化後建立知識庫；每一則回答都只根據檢索到的證據生成，並在句尾標註來源，避免 LLM 捏造資訊。

對應規格：`docs/spec.md`、`docs/mvp-spec.md`、Issue #1（`docs/issues.md`）。

---

## 功能總覽

| 介面 | 說明 |
|---|---|
| 💬 **助理對話** | 對話代理：記得上下文，**由模型自己判斷要不要查知識庫、查什麼**（追問會依歷史改寫成完整問題），串流輸出，回答政策事實時才標示來源。 |
| 📊 **報告產出** | 輸入政策議題，產出 `background / policy_basis / international_cases / recommendations` 四章節結構化報告，逐段標註來源，可下載 Markdown。 |
| 📚 **知識庫管理** | 來源啟用/停用（停用後不再進檢索）、一鍵重新抓取與索引、chunk 內容瀏覽。 |
| ⚙️ **模型設定** | 透過 OpenAI 相容端點串接任意模型：本地 Ollama 或 OpenAI / OpenRouter / Groq / Together 等 API，介面即時切換 base_url / api_key / model 並測試連線。 |

---

## 知識庫來源

每個來源（`source_id`）即一個獨立知識庫，可在「知識庫管理」個別啟用/停用；停用後不再進入檢索。替代能源專區依 iMarine 側邊選單的「項目」拆成 5 個獨立知識庫，而非混在同一個。

| source_id | 知識庫 | 類型 | 來源 |
|---|---|---|---|
| `law_moj_shipping_port_act` | 商港法 | 🔵 regulation | 全國法規資料庫 HTML |
| `motcmpb_press_release_rss` | 航港局新聞稿 | 🟡 news | 航港局 RSS |
| `ae_overview` | 替代能源專區：專區簡介 | 🟢 alt_energy | iMarine 靜態 JSON |
| `ae_fuel` | 替代能源專區：替代能源介紹 | 🟢 alt_energy | 6 種燃料 + 優缺點 + 減碳分析 |
| `ae_intl` | 替代能源專區：國際趨勢與發展 | 🟢 alt_energy | UN/IMO/EU 政策、港口加注、人才、綠色走廊 |
| `ae_taiwan` | 替代能源專區：臺灣政策與實踐 | 🟢 alt_energy | 國內政策/法規、港口/船舶/船員、三大航商、工作平台 |
| `ae_education` | 替代能源專區：教育資源 | 🟢 alt_energy | 研討會、培訓課程 |

> 替代能源內容來自 iMarine 航港發展資料庫（`imarine.motcmpb.gov.tw/#/alternativeenergy`）SPA 背後的靜態 JSON。connector 把異質內容（文章 Markdown / 燃料介紹 / 優缺點比較 / 港口加注 / HTML 區塊）正規化成統一 `sections` 後依段落切段。

---

## 系統架構

```
┌──────────────────────────── 使用者介面（Streamlit app.py）─────────────────────────────┐
│   💬 助理對話            📊 報告產出              📚 知識庫管理                            │
└───────┬──────────────────────┬────────────────────────┬──────────────────────────────┘
        │                      │                        │
        ▼                      ▼                        ▼
┌───────────────┐    ┌──────────────────┐    ┌────────────────────────────┐
│ Chat Agent     │    │ Report Generator │    │ Ingestion Pipeline          │
│ generation/    │    │ generation/      │    │ ingestion/pipeline.py       │
│ agent.py       │    │ report.py        │    │  ├ law.py（商港法）· rss.py  │
│（tool-calling： │    │（四章節 JSON）    │    │  └ alt_energy.py（替代能源）  │
│  自行決定檢索）  │   └────────┬─────────┘    └──────────────┬─────────────┘
└───────┬───────┘             │                             │
   需檢索│                     │                     governance/chunking.py
        ▼                     ▼                     （法規依條、新聞/替代能源依段落切段）
┌──────────────────────────────────────┐                    │
│ Hybrid Retrieval                       │                    ▼
│ indexing/retrieval.py                  │        indexing/embedding.py
│  Dense(pgvector HNSW) + Lexical(bigram)│        （EmbeddingGemma-300m → vector(768)）
│  → RRF 融合 → 只取啟用中來源            │                    │
└───────────────┬──────────────────────┘                    ▼
                ▼                                   ┌───────────────────────┐
┌──────────────────────────┐                        │  PostgreSQL + pgvector │
│ Evidence Package          │◀───────────────────────│  public.sources        │
│ evidence/packaging.py     │                        │  public.raw_documents  │
│（evidence_items+locator+  │                        │  public.chunks         │
│  confidence+policy_verdict)│                       └───────────────────────┘
└───────────────┬──────────┘                                    ▲
                ▼                                                │
┌──────────────────────────┐        ┌───────────────────────────┴───┐
│ LLM Generation（串流）     │───────▶│ Audit（audit schema）          │
│ generation/llm.py         │  紀錄  │ audit.generation_runs          │
│ → generation/provider.py  │        │ audit/recorder.py              │
│   OpenAI 相容(Ollama/API) │        │ （JSONL 每日封存匯出）          │
│ + 規則式 citation 檢查     │        └────────────────────────────────┘
└──────────────────────────┘
```

> 同一套 Retrieval → Evidence Package → Generation 底層，對話與報告兩個入口共用，確保證據來源與驗證標準一致。

---

## 資料流（Pipeline）

### 流程圖

```mermaid
flowchart TD
    subgraph ING["索引階段 · 離線 / 手動觸發"]
        S["來源<br/>商港法 HTML · 航港局 RSS · 替代能源 JSON"] --> C["Connector.fetch()"]
        C --> R["RawDocument<br/>source_id · checksum 去重"]
        R --> K["Chunking<br/>法規依條 · 新聞/替代能源依段落"]
        K --> E["Embedding<br/>EmbeddingGemma-300m → 768 維"]
        E --> DB[("PostgreSQL + pgvector<br/>sources · raw_documents · chunks")]
    end

    subgraph QRY["查詢階段 · 即時"]
        U["前端 policy 頁<br/>AI 政策輔助報告 · 綜合對話"]
        U -->|"GET /api/sources"| KB["右欄真實知識庫清單<br/>依五類分組"]
        U -->|"POST /api/chat"| A["Chat Agent 規劃迴圈<br/>要不要查 · 查什麼"]
        A --> H["Hybrid Retrieval<br/>Dense + Lexical → RRF"]
        H --> P["Evidence Package<br/>evidence_items + locator"]
        P --> G["LLM Generation<br/>Ollama gemma3n:e4b"]
        G --> V["規則式 citation 檢查"]
        V --> ANS["回答 + 引用<br/>串流輸出"]
        V --> AUD[("Audit<br/>audit.generation_runs")]
    end

    KB -.->|"讀取來源清單"| DB
    H -.->|"僅檢索啟用中來源"| DB
```

### 索引階段（Ingestion，離線 / 手動觸發）

```
Source Registry 登錄（sources 表）
  → Connector.fetch()           法規：解析全國法規資料庫 HTML 76 條
                                新聞：feedparser 解析航港局 RSS
                                替代能源：iMarine 靜態 JSON，依項目分成 5 個知識庫
  → RawDocument（source_id / fetched_at / checksum，內容存本地 data/raw）
  → checksum 去重
  → Chunking                    法規：依「條」為原子單位，不跨條
                                新聞/替代能源：依段落，250–500 tokens、~15% overlap
  → Metadata Enrichment         section_path / published_at / credibility_score …
  → Embedding                   EmbeddingGemma-300m → vector(768)
  → 寫入 chunks 表（含 HNSW 向量索引）
```

### 查詢階段（Query，即時）

```
使用者輸入（含對話歷史）
  → Chat Agent 規劃迴圈          模型判斷：要不要查？查什麼？（追問依歷史改寫成完整 query）
                                最多 2 次；招呼/閒聊直接跳到回答
  → Hybrid Retrieval            Dense(pgvector cosine, HNSW) + Lexical(bigram ILIKE)
                                → RRF 融合 → 過濾「僅啟用中來源」→ Top-K（累積去重）
  → 串流回答                     透過供應商模型（Ollama/API），對話式輸出；
                                回答政策事實時標 [ev_xxx]，閒聊/追問不強制引用
  → 規則式 Faithfulness          正則比對 evidence_id → citation coverage
  → Audit                       寫入 audit.generation_runs（JSONL 封存）
```

---

## 技術棧

| 分類 | 選型 |
|---|---|
| 語言 / 框架 | Python 3.13、FastAPI、Streamlit、Pydantic、SQLModel |
| 資料庫 | PostgreSQL + pgvector（`vector(768)`，HNSW 索引） |
| 全文檢索 | PostgreSQL bigram ILIKE（lexical，非真正 BM25） |
| Embedding | `google/EmbeddingGemma-300m`（768 維，**本地**執行，不受供應商設定影響） |
| 生成模型 | **OpenAI 相容供應商**（Ollama 本地 / OpenAI / OpenRouter / Groq…），介面可切換；預設 Ollama `gemma3n:e4b`（Gemma 3n E4B-it，即 mvp-spec 的 gemma-4-E4B）。設定存於 `data/llm_config.json` |
| 稽核 | PostgreSQL `audit` schema 為主，JSONL 封存匯出 |

---

## 專案結構

```
rag-agent/
├── app.py                          # Streamlit 三分頁介面（對話 / 報告 / 知識庫管理）
├── src/rag_agent/
│   ├── main.py                     # FastAPI 入口
│   ├── config.py                   # 設定（DB URL、data_dir）
│   ├── ingestion/                  # 資料接入
│   │   ├── base.py                 #   IngestionConnector 抽象 + Source Registry
│   │   ├── law.py                  #   商港法 HTML 解析
│   │   ├── rss.py                  #   航港局新聞稿 RSS
│   │   ├── alt_energy.py           #   替代能源專區（依項目分 5 個知識庫）
│   │   └── pipeline.py             #   完整 ingest 服務（API/UI 共用）
│   ├── governance/chunking.py      # 法規依條、新聞/替代能源依段落切段
│   ├── indexing/
│   │   ├── embedding.py            #   EmbeddingGemma 向量化
│   │   └── retrieval.py            #   Hybrid Retrieval + RRF（僅啟用來源）
│   ├── evidence/packaging.py       # Evidence Package 封裝
│   ├── generation/
│   │   ├── provider.py             #   OpenAI 相容供應商（Ollama/API 切換）
│   │   ├── agent.py                #   對話代理（tool-calling 迴圈 + 串流）
│   │   ├── prompts.py              #   對話 / 規劃 / 報告 prompt
│   │   ├── llm.py                  #   生成介面 + citation 檢查
│   │   └── report.py               #   四章節結構化報告
│   ├── audit/
│   │   ├── models.py               #   audit.generation_runs
│   │   └── recorder.py             #   稽核寫入 / 讀取
│   ├── db/
│   │   ├── models.py               #   sources / raw_documents / chunks
│   │   ├── session.py              #   async engine、建表、HNSW / audit schema
│   │   └── queries.py              #   知識庫管理查詢（參數化）
│   └── api/routes/                 # ingest / retrieve / evidence / generate / chat / report / sources / audit
├── tests/                          # test_chunking / test_retrieval / test_kb_query
└── docs/                           # spec.md / mvp-spec.md / pipeline.md / issues.md
```

---

## 快速開始

### 1. 前置需求
- PostgreSQL（含 `pgvector` extension），預設連線 `postgresql+asyncpg://rag:rag@localhost:5432/rag_agent`
- 生成模型二選一：本地 **Ollama**（`ollama pull gemma3n:e4b`）或任一 OpenAI 相容 API 金鑰
- Embedding 於本地執行（EmbeddingGemma-300m，CPU 亦可）
- 環境變數可用 `.env` 覆寫 `database_url`、`data_dir`、`llm_*` 預設值

### 2. 安裝
```bash
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt
```

### 3. 建立知識庫（首次）
用 API 觸發抓取 → 切段 → 向量化（商港法 + 航港局新聞 + 替代能源 5 庫，共 7 個知識庫）：
```bash
uvicorn src.rag_agent.main:app --port 8100          # 先起後端
curl -X POST http://localhost:8100/api/ingest/run   # 另一個終端觸發
```
（或用 Streamlit 介面「📚 知識庫管理」分頁按 🔄 重新抓取 / 更新。）

---

### 使用方式 A：搭配 iMarine 前端（正式產品路徑）

前端 `iMarine-FrontEnd` 的「AI 政策輔助報告」頁對接本後端（綜合對話即時問答 + 產報告）。

```bash
# 終端 1 — 後端 API（用 8100 埠，避開 carbon PoC 的 8000）
cd rag-agent
uvicorn src.rag_agent.main:app --port 8100

# 終端 2 — 前端
cd ../iMarine-FrontEnd
cp .env.example .env          # 內含 VITE_POLICY_API=http://127.0.0.1:8100
npm run dev                   # → http://localhost:5173
```

開 http://localhost:5173 → 左側選「AI 政策輔助報告」→「綜合對話 · 全部來源」：
- **右欄來源**：後端有跑 → 顯示真實 7 個知識庫；**後端沒跑 → 自動 fallback 回 mock 展示資料**（航港法令會顯示 5 筆假資料，即為此）。
- **提問**：輸入框發問 → 即時 RAG 回答附引用。
- **產報告**：右欄勾選來源 → 輸入需求 → 選模版 → 「產生報告」。

> 遠端（SSH 連 DGX）：`ssh -L 5173:localhost:5173 -L 8100:localhost:8100 ...` 後在本機開 5173。

### 使用方式 B：Streamlit 一體介面（開發 / 單機展示）

```bash
streamlit run app.py          # 對話 / 報告 / 知識庫管理 / 模型設定 四分頁
```

---

## API 端點（低階，可獨立呼叫與測試）

| 方法 | 路徑 | 說明 |
|---|---|---|
| POST | `/api/ingest/run` | 抓取 → 切段 → 向量化 |
| GET | `/api/ingest/status` | chunk 統計 |
| POST | `/api/retrieve` | Hybrid Retrieval，回傳 Top-K |
| POST | `/api/evidence/package` | 檢索結果封裝為 Evidence Package |
| POST | `/api/generate` | 完整 retrieve → package → generate → audit |
| POST | `/api/chat` | 代理式對話：規劃檢索 → 生成 → citation → audit（含 evidence package、實際 provider/model） |
| POST | `/api/report` | 產報告：選來源（source_ids）+ 需求 + 模版 → 結構化報告（章節 + 引用 + audit） |
| GET | `/api/report/templates` | 可用報告模版清單（policy_brief / news_digest / free） |
| GET | `/api/sources` | 列出所有知識庫來源（source_type / chunk 數 / 啟用狀態），供前端右欄顯示真實知識庫 |
| GET | `/api/audit/logs` | 最近稽核紀錄 |

`/api/chat` 是 `retrieve → evidence package → generate` 的組合封裝（見 `api/routes/chat.py`），檢索/生成邏輯不寫死在端點內，底層端點仍可獨立呼叫與測試。

---

## MVP 完成度

10 項成功標準（`docs/spec.md` 10.3）皆已達成，包含：商港法依條切段、新聞 RSS 切段、chunk 含 locator/checksum、vector(768) + lexical 索引、Evidence Package 可回放、grounded 生成 + citation 對應、每次操作有 audit、**停用來源後不再進 evidence**。

MVP 後已加入：
- `/api/chat` 對外封裝端點（回傳實際 provider/model）與 `/api/sources` 知識庫清單端點。
- iMarine 替代能源專區接入：依項目分成 5 個獨立知識庫，共 178 chunks。
- 生成模型改為 Ollama `gemma3n:e4b`（Gemma 3n E4B-it）；相較 `gemma3:27b` 延遲 260s→76s。
- 前端 iMarine-FrontEnd「AI 政策輔助報告」的**綜合對話**已接 live：右欄顯示真實知識庫（`/api/sources`），提問走 `/api/chat`，回答附引用並照實顯示模型。

Phase 2 待辦：多輪 query rewriting、cross-encoder reranker、語意層級 faithfulness、報告 PDF 輸出、更多國際來源接入（MARAD / IMO / EU ETS / 新加坡 MPA）。
