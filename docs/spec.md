# LLM + RAG 政策輔助報告模組 — 系統規格書

> 狀態：定案（技術棧、資料來源、LLM 選型已確認；待辦事項見第十一節 11.2）
> 對應提案：`docs/proposals.md` 主軸一「航港局視角 — 港口端碳排代幣化與政策輔助報告」之 LLM+RAG 報告生成部分
> 對應 Issue：`docs/issues.md` Issue #1（RAG 資料管線 MVP）
> 對應規劃文件：`docs/pipeline.md`（資料流四段式設計）

---

## 一、概述

### 1.1 目的

本規格書定義「LLM + RAG 政策輔助報告」模組的完整技術規格，涵蓋從異質資料接入、治理、檢索到報告生成的全流程。目標是讓系統產出的政策報告具備**事實基礎（Grounding）與可追溯性**——每一句結論都能回指到明確的來源、版本、段落與時間點，避免 LLM 自行捏造資訊（Hallucination）。

### 1.2 範圍界定

本規格書涵蓋**整個 LLM+RAG 政策報告模組**，範圍大於 Issue #1（僅資料管線），但小於整份提案（五大模組）。具體包含：

| 涵蓋 | 不涵蓋（其他模組，未來獨立規格） |
|---|---|
| 資料來源接入（Ingestion） | 港口碳權代幣化 PoC（本地模擬鏈） |
| 資料治理與 Chunking | PPO 多目標泊位排程 / Digital Twin 沙盤推演 |
| 向量化與混合檢索 | ConvLSTM 短時微氣候氣象預測 |
| Evidence Package 封裝 | 疫情擴散圈風險預警（規則式評分） |
| LLM 報告生成與 Grounding 驗證 | |
| 報告輸出格式與下游 API 契約 | |
| **自然語言對話介面（Chat）** | |

> 註：碳權模擬紀錄未來可能作為 RAG 知識庫的補充資料源（見 `proposals.md` 陸、未來資料之應用），但代幣化 PoC 本身的智能合約邏輯不在本規格書範圍內。

### 1.3 互動模式：Chat 為統一入口，報告生成為其中一項能力

使用者的主要互動介面是**自然語言對話（Chat）**。結構化報告生成不是與 Chat 平行、互斥的第二種介面，而是 Chat 對話過程中可被觸發的**一項動作（action / tool）**：使用者可以在對話中直接要求「幫我生成一份關於 XX 的政策報告」，Chat Service 會判斷這輪意圖屬於「報告生成」，呼叫 Report Generation Service 產出結構化報告，並把報告內嵌回對話串（而非跳出對話另開流程）。

同時保留**獨立的 Report Generation API**（見第九節），供不經過對話、由儀表板或排程直接觸發的場景使用（例如「每日例行報告」這種不需要人工先聊過一輪的批次任務，見 Issue #1 應用情境 3）。因此本模組實際上是一套 Evidence Package / Grounding 機制，對外呈現兩個入口：

| 入口 | 觸發方式 | 輸出形態 | 對話狀態 | 典型情境 |
| --- | --- | --- | --- | --- |
| **Chat（含內嵌報告生成）** | 使用者以自然語言提問或下指令，Chat Service 判斷意圖 | 對話式回覆；若意圖為報告生成，則回覆內嵌固定章節結構的報告 | 多輪，需維護對話歷史與追問時的檢索上下文 | 使用者探詢式提問、追問、臨時要一份報告 |
| **Report Generation API（獨立呼叫）** | 外部系統（儀表板、排程器）直接呼叫，不經過對話 | 固定章節結構的 JSON/Markdown/PDF 報告 | 單輪，無對話歷史 | 每日例行報告、排程批次產出 |

兩個入口底層都呼叫同一個 **Report Generation Service**，差別只在「誰觸發」與「是否有對話上下文」。

**報告生成的本質是「模版化＋經驗複用」的加速產出，而非每次重新從零檢索。** 固定章節結構（background / policy_basis / international_cases / recommendations）本身就是一個可重複套用的模版，讓報告產出不需每次重新設計格式；而當報告請求承接自對話歷史時，應優先複用該次對話中已檢索、已驗證過的 Evidence，只在資訊不足以覆蓋新模版章節時才觸發額外檢索。這樣可以：(1) 避免同一批資料被重複檢索與驗證，降低延遲與 LLM 呼叫成本；(2) 讓報告內容與使用者剛才在對話中確認過的資訊保持一致，不會「報告」與「聊天內容」兜不起來。

Chat 介面（含內嵌報告生成）的必要條件：

1. **不降低 Grounding 標準**：無論是一般問答或報告生成意圖，每一則 LLM 輸出都必須先經過 Retrieval → Evidence Package → 生成 → Faithfulness 檢查的完整流程，不可因為是「聊天」就跳過證據封裝直接餵原始 chunk。
2. **意圖判斷（Intent Routing）**：Chat Service 需要判斷使用者這輪輸入是「一般問答」還是「報告生成請求」，並路由到對應的處理邏輯（一般問答走精簡回覆、報告生成走固定章節結構）。
3. **多輪上下文管理**：需處理追問（如「那 2027 年呢？」）時如何重新組成檢索 Query（可能需要 query rewriting，把先前對話輪次的主題代入這輪的檢索關鍵字）；報告生成請求也可能是承接先前幾輪已討論的主題（例如先問答幾輪後說「把剛剛討論的整理成報告」），此時報告生成需彙整對話歷史中已使用過的 Evidence，而非重新從頭檢索。
4. **同源 Evidence Package**：一般問答與報告生成呼叫同一個 Evidence Packaging Service 與同一個 Report Generation Service，避免維護兩套檢索/證據邏輯。
5. **報告可再對話**：報告生成完成並內嵌回對話後，使用者應能針對報告內容繼續追問（例如「report_basis 那段的來源可信嗎？」），此時對話狀態需記得剛才生成過的報告與其 Evidence，避免使用者重新描述一次問題。

第九節（API 規格）會定義 Chat API（含對話歷史管理與意圖路由）與 Report Generation API 兩者的介面，並說明兩者如何共用底層服務。

### 1.4 MVP 與後續擴充的關係

Issue #1 定義的 2 天 MVP 是本模組的**第一個可交付切片**，範圍限縮為「《商港法》本文＋數個新聞 RSS 來源」的 End-to-End 管線（具體來源清單見第三節）。本規格書會：

- 完整定義目標架構（含未來會擴充的來源、模組），避免 MVP 階段的實作綁死架構、日後要重構。
- 在第十節明確標出 MVP 階段（Phase 1）與後續階段（Phase 2/3）的界線，讓實作時知道哪些欄位/介面現在就要預留、哪些邏輯現在可以先簡化。

### 1.5 核心設計原則

依 `pipeline.md` 與 `issues.md` 的共同結論，訂立以下不可退讓的設計原則：

1. **LLM 不直接讀 raw data**：所有進入 LLM prompt 的內容必須先經過治理層（清洗、metadata 補齊、Policy Gate、Chunking、Embedding、Retrieval、Rerank、Conflict Check），封裝成 Evidence Package。
2. **可追溯優先**：每筆資料從進入系統的那一刻起就要保留 `source_id`、`fetched_at`、`checksum`，每個 chunk 要能回指原文的頁碼/段落/條號。
3. **法規切段不可跨條**：法規類文件依「條/項/款」做結構感知切分，語意邊界不可打斷。
4. **輸出雙欄位契約**：所有下游模組（儀表板、其他分析模組）取得的輸出一律採 `output`（推論內容）＋`metadata`（來源、證據 ID、信心水準、模型版本）雙欄位設計。
5. **Policy Gate 貫穿索引與查詢兩端**：不只是資料「能不能進庫」，也要檢查「能不能被這次任務拿來回答」（時效性、法域適用性、授權範圍）。

---

## 二、系統架構總覽

### 2.1 五層資料流在本模組的映射

沿用 `pipeline.md` 提出的五層架構（來源層、治理層、分析層、應用服務層、決策層），本模組的職責對應如下：

| 資料流層級 | 本模組職責 | 主要產出物 |
|---|---|---|
| 來源層 | 法規/新聞等異質資料接入 | `RawDocument` |
| 治理層 | 清洗、Chunking、Policy Gate、Embedding/Indexing | `Governed Chunk Record` |
| 分析層 | 混合檢索、Rerank、Conflict Check、Evidence 封裝、LLM 報告生成 | `Evidence Package`、`Report` |
| 應用服務層 | 報告介面封裝、來源清單呈現 | 結構化報告（JSON/Markdown/PDF） |
| 決策層 | （本模組不直接處理，僅提供報告供人工決策參考） | Decision Log（由決策層寫回，非本模組產出） |

### 2.2 模組邊界與資料流向（文字化架構圖）

```
┌─────────────────────────────────────────────────────────────────┐
│  來源層 (Source Layer)                                            │
│  商港法 (ZIP)   航港局/MARAD 新聞 RSS   IMO RSS(metadata-only)     │
│                              [Phase 2: iMarine API / EU ETS / WHO / 氣象] │
└──────────────────────────┬──────────────────────────────────────┘
                           │  IngestionConnector.fetch()
                           ▼
                  ┌──────────────────┐
                  │   RawDocument     │  (source_id, fetched_at, checksum, content_pointer)
                  └────────┬──────────┘
                           │  正規化 / OCR分流 / 去重 / 版本化
                           ▼
                  ┌──────────────────┐
                  │ Canonical Document│
                  └────────┬──────────┘
                           │  Metadata Enrichment → Policy Gate(索引時) → Chunking
                           ▼
                  ┌──────────────────┐
                  │ Governed Chunk    │  → Embedding (EmbeddingGemma-300m) + PostgreSQL 全文索引
                  │ Record            │  → 寫入向量資料庫 (pgvector)
                  └────────┬──────────┘
                           │
        ┌──────────────────┴───────────────────┐
        │         查詢時 (Query Time)             │
        │  User Query → Query Understanding      │
        │  → Metadata Filter → Hybrid Retrieval  │
        │  → Rerank → Dedup → Conflict Check     │
        │  → Policy Gate(查詢時)                  │
        └──────────────────┬───────────────────┘
                           ▼
                  ┌──────────────────┐
                  │ Evidence Package  │  (evidence_items + locator + confidence + policy_verdict)
                  └────────┬──────────┘
                           │
                           │
                           ▼
                  ┌──────────────────────────────────────┐
                  │            Chat Service                │
                  │  （統一入口：多輪對話管理、             │
                  │   query rewriting、Intent Routing）    │
                  └──────────────────┬────────────────────┘
                                     │
                    ┌────────────────┴─────────────────┐
                    ▼ 意圖＝一般問答                      ▼ 意圖＝報告生成
          ┌──────────────────────┐          ┌──────────────────────────┐
          │  一般問答回覆生成       │          │  Report Generation Service │
          │  (Prompt 強制 citation) │          │  (固定章節結構，可承接      │
          │                        │          │   對話歷史中已用過的 Evidence)│
          └──────────┬────────────┘          └──────────┬───────────────┘
                      │  每輪皆做 Faithfulness 驗證          │  同樣做 Faithfulness 驗證
                      ▼                                    ▼
          ┌──────────────────────┐          ┌──────────────────────────┐
          │ Chat 回覆（文字＋      │          │ Report（內嵌回對話串，      │
          │ citation+confidence） │          │ 同時可獨立輸出 JSON/MD/PDF）│
          └──────────┬────────────┘          └──────────┬───────────────┘
                      │                                    │
                      └─────────────────┬──────────────────┘
                                        │  output + metadata 雙欄位契約
                                        ▼
        ┌──────────────────────────────────────────────────────┐
        │  下游：使用者（對話介面）/ 儀表板 / RL 排程 / 其他分析模組 / 決策層 │
        └──────────────────────────────────────────────────────┘

        ※ 另有獨立入口：外部系統（儀表板/排程器）可不經 Chat Service，
          直接呼叫 Report Generation Service（見 1.3 節「Report Generation API」）
```

### 2.3 主要子系統

1. **Ingestion Service**：管理 Source Registry，依來源類型排程拉取資料（ZIP 批次 / RSS 訂閱 / 未來的爬蟲、PDF 解析、API polling）。
2. **Governance Pipeline**：正規化、Metadata Enrichment、Policy Gate、Chunking，輸出 Governed Chunk Record。
3. **Indexing & Retrieval Service**：管理向量資料庫與全文索引，提供 Hybrid Retrieval API。
4. **Evidence Packaging Service**：Rerank、去重、衝突偵測、封裝 Evidence Package。此服務同時供一般問答與報告生成共用，確保兩種輸出的證據來源與驗證標準一致。
5. **Chat Service**：使用者互動的統一入口。管理多輪對話狀態，處理追問時的 query rewriting（將前幾輪對話主題代入本輪檢索關鍵字），並執行 **Intent Routing**——判斷本輪輸入該走一般問答或報告生成。報告生成時若對話已討論過相關主題，需彙整既有 Evidence 而非重新檢索。
6. **Report Generation Service**：呼叫自架 LLM（vLLM/Ollama），依 Evidence Package 生成固定章節結構的報告，並執行 Faithfulness 驗證。可被 Chat Service 內部呼叫（內嵌回對話），也可被外部系統直接呼叫（獨立 API，見第九節）。
7. **Audit & Validation Service**：保存每次查詢/生成的稽核紀錄（retrieved_chunks、rerank_result、validation 分數），一般問答與報告生成皆須記錄。

---

## 三、資料從哪來

### 3.1 來源優先序總表（已定案）

依 `pipeline.md` 六大類來源，本節將其收斂為明確的**優先序**，分成 MVP（Issue #1 兩天內必須完成）、Phase 2（模組完整版必要）、Phase 3（未來擴充，proposals.md 提及但非本模組近期範圍）三個梯度。以下來源清單為**定案版本**（經查證 RSS feed 存在性與授權條款後確認）。

| 優先序 | 來源 | 類別 | 接入方式 | 索引方式 | 說明 |
| --- | --- | --- | --- | --- | --- |
| **MVP** | 全國法規資料庫（《商港法》本文） | 官方法源資料 | ZIP 批次下載 → XML/JSON 解析 | 全文索引 | 依條/項/款切分。可信度最高、結構穩定，作為 Grounding 的基準來源。適用政府資料開放授權條款第 1 版：可商用，須顯名標示來源機關，否則授權自始無效。 |
| **MVP** | 交通部航港局新聞稿 RSS | 領域核心資料／即時動態資料 | RSS 訂閱 + 排程拉取 | 全文索引 | `https://www.motcmpb.gov.tw/Information/RSS?SiteId=1&NodeId=15`。與法規來源同屬航港局網域，內容最貼近「航港局視角」政策場景，且適用同一份政府資料開放授權條款。**為 MVP 主要新聞來源。** |
| **MVP** | MARAD（美國海事署）Press Releases RSS | 國際官方資料 | RSS 訂閱 + 排程拉取 | 全文索引 | `https://www.maritime.dot.gov/taxonomy/term/36/feed`。美國政府出版品，作為國際政策脈絡的補充來源。實作前建議以團隊實際環境 curl 一次確認 feed 可正常存取（本規格書查證時遭目標網站以 403 阻擋自動化工具，屬常見的 .gov 反爬蟲機制，非 URL 本身錯誤）。 |
| **MVP** | IMO Press Briefings RSS | 國際官方資料 | RSS 訂閱 + 排程拉取 | **僅 metadata**（標題/摘要/URL/時間），不做全文索引 | `https://www.imo.org/en/pages/pressbriefingsrss.aspx`。IMO ePublications 條款明確規定商業使用需事前書面同意、內容不得轉讓/出租/出售給第三方，故僅索引可安全摘錄的 metadata，不做全文 embedding，作為報告「國際案例」章節的線索來源，實際引用時附上原文連結供人工查證。 |
| Phase 1.5 | 航貿週刊（shippingdigest.tw）RSS | 領域補充資料 | RSS 訂閱 | 候補，**僅 metadata**（版權聲明「沛華版權所有」，全文轉載需另洽授權） | 台灣專業海運媒體，內容品質高，但屬商業媒體版權內容，比照 IMO 的處理方式，待取得明確授權後才升級為全文索引。 |
| Phase 2 | iMarine 臺灣數據統計（貨櫃） | 領域核心資料 | REST API（JSON） | 全文索引 | 唯一有正式開放 API 的 iMarine 子主題，量化圖表與趨勢判斷的資料基礎。 |
| Phase 2 | iMarine 其餘五大主題（全球海運指數、國際組織動態、海運時事、替代能源專區、航港法令頁面） | 領域核心資料 | 爬蟲 / PDF 解析 / 人工上傳 | 依內容型態決定 | 無正式 API，需個別評估接入方式；優先度低於有 API 的貨櫃統計。 |
| Phase 2 | IMO 政策文件全文（GHG Strategy、MEPC 決議、NZF PDF） | 國際官方資料 | 爬蟲 + PDF 解析 | 需另洽 IMO 授權後才能全文索引 | 政策報告「國際案例」章節的核心依據，但文件多為 PDF，解析成本較高，且同樣受 IMO ePublications 條款限制。 |
| Phase 2 | EU / EEA / EEX（EU ETS 規則） | 國際官方資料 | 爬蟲 + PDF 解析 | 依授權條款決定 | 呼應提案中歐盟碳排交易體系的政策比較需求。 |
| Phase 1.5 | 商港法子法規（商港港務管理規則、商港服務費收取保管及運用辦法等） | 官方法源資料 | ZIP 批次下載 | Phase 1.5 起全文索引；MVP 僅登錄 metadata | MVP 僅索引《商港法》本文，子法規待 MVP 驗證通過後優先擴充。 |
| Phase 3 | 新加坡 MPA、行政院公報資訊網、政府資料開放平臺 | 國際/官方標竿資料 | 爬蟲 / API | 依授權條款決定 | 提案中提及但非報告模組近期優先，可視報告主題需求再擴充。 |
| Phase 3 | WHO 疫情通報 | 國際官方資料 | API / 爬蟲 | 依授權條款決定 | 主要供疫情預警模組使用；若政策報告需要疫情背景資料可共用同一份治理後資料，不需重複接入。 |
| Phase 3 | 中央氣象署（雷達回波、觀測資料） | 官方動態資料 | API polling | 不進政策報告語料庫 | 主要供 ConvLSTM 氣象預測模組使用，僅在報告提及氣象風險背景時引用其結論，不接入原始觀測資料。 |
| Phase 3 | 內部文件、專家補充資料 | 內部補充資料 | 人工上傳 | 依權限與審核狀態決定 | 需標記權限、審核狀態與可否對外引用，待組織內部流程確定後再開放。 |

> 設計原則：**同一份已治理資料應跨模組共用**，不因服務對象不同（政策報告 vs. 疫情預警 vs. 氣象預測）而重複建置獨立的接入管線。
>
> 授權原則：**全文索引 vs. metadata-only 的判斷基準是「是否已確認可合法摘錄轉存」**——政府開放資料（法規、航港局、MARAD）預設全文索引；商業媒體或明確限制商業使用的來源（IMO ePublications、航貿週刊）先以 metadata-only 起步，待取得書面授權後才升級。

### 3.2 MVP 來源細節

#### (1) 全國法規資料庫（《商港法》本文）

- 索引範圍：**MVP 僅索引《商港法》本文**，不含施行細則等子法規；子法規僅在 Source Registry 登錄 metadata（供未來 Phase 1.5 擴充時快速接入），不進 Chunking/Embedding。
- 取得方式：全國法規資料庫提供整包 ZIP 下載（含 XML/JSON），非逐條 API 查詢。
- 版本追蹤：每個法規有 `PCode`（法規代碼）與 `revised_at`（修正日期），需保留版本歷程以支援「法規修正前後差異」類查詢。
- 授權：政府資料開放授權條款第 1 版——非專屬、不可撤回、免授權金，可用於任何目的（含商業衍生），前提是需依規定顯名標示原資料提供機關，否則授權視為自始無效。
- 更新頻率：法規變動不頻繁，建議每日或每週巡檢一次，用 checksum 比對決定是否重新處理。

#### (2) 交通部航港局新聞稿 RSS（MVP 主要新聞來源）

- Feed URL：`https://www.motcmpb.gov.tw/Information/RSS?SiteId=1&NodeId=15`
- 定位：與法規來源同屬航港局官方網域，內容涵蓋航港局政策動態、公告，最直接支援「航港局視角」的政策報告與日報情境（呼應 Issue #1 情境 3）。
- 授權：比照政府網站公開資訊，適用政府資料開放授權條款第 1 版，全文索引風險低。
- 取得方式：RSS 訂閱 + 排程拉取，建議每日至每小時一次。

#### (3) MARAD Press Releases RSS（國際政策脈絡）

- Feed URL：`https://www.maritime.dot.gov/taxonomy/term/36/feed`
- 定位：美國海事署官方新聞稿，作為國際航運政策脈絡的補充來源，內容屬美國政府出版品。
- 注意事項：實作前需在團隊實際部署環境驗證此 URL 可正常拉取（本規格書查證時被目標站台的反爬蟲機制擋下，屬 .gov 站台常見行為，需用合適的 User-Agent/請求頻率測試）。

#### (4) IMO Press Briefings RSS（僅 metadata，國際案例線索）

- Feed URL：`https://www.imo.org/en/pages/pressbriefingsrss.aspx`
- 授權限制：IMO ePublications 條款明確規定商業使用需事前書面同意，內容不得轉讓、出租、出售給第三方。**因此 MVP 僅索引標題、摘要、URL、發布時間，不做全文抓取與 embedding**，報告生成若需引用 IMO 立場，以摘要+原文連結方式呈現，要求使用者自行查證原文。

### 3.3 Source Registry 欄位定義

沿用 `pipeline.md` 二-1 節的欄位設計，作為本模組實際採用的 schema：

```json
{
  "source_id": "law_moj_shipping_port_act",
  "source_name": "全國法規資料庫（商港法）",
  "publisher": "法務部全國法規資料庫",
  "source_type": "regulation",
  "jurisdiction": "TW",
  "license_type": "government_open_data_v1",
  "access_method": "ZIP_DOWNLOAD",
  "update_frequency": "weekly_or_event_driven",
  "trust_score": 96,
  "attribution_required": true,
  "provenance_level": "article",
  "full_text_indexing": true,
  "phase": "MVP"
}
```

```json
{
  "source_id": "motcmpb_press_release_rss",
  "source_name": "交通部航港局新聞稿",
  "publisher": "交通部航港局",
  "source_type": "news",
  "jurisdiction": "TW",
  "license_type": "government_open_data_v1",
  "access_method": "RSS",
  "feed_url": "https://www.motcmpb.gov.tw/Information/RSS?SiteId=1&NodeId=15",
  "update_frequency": "hourly_or_daily",
  "trust_score": 92,
  "attribution_required": true,
  "provenance_level": "article",
  "full_text_indexing": true,
  "phase": "MVP"
}
```

```json
{
  "source_id": "marad_press_release_rss",
  "source_name": "MARAD Press Releases",
  "publisher": "U.S. Maritime Administration",
  "source_type": "news",
  "jurisdiction": "US",
  "license_type": "us_government_publication",
  "access_method": "RSS",
  "feed_url": "https://www.maritime.dot.gov/taxonomy/term/36/feed",
  "update_frequency": "hourly_or_daily",
  "trust_score": 88,
  "attribution_required": true,
  "provenance_level": "article",
  "full_text_indexing": true,
  "phase": "MVP"
}
```

```json
{
  "source_id": "imo_press_briefings_rss",
  "source_name": "IMO Press Briefings",
  "publisher": "International Maritime Organization",
  "source_type": "news",
  "jurisdiction": "international",
  "license_type": "imo_epublications_restricted",
  "access_method": "RSS",
  "feed_url": "https://www.imo.org/en/pages/pressbriefingsrss.aspx",
  "update_frequency": "event_driven",
  "trust_score": 90,
  "attribution_required": true,
  "provenance_level": "article",
  "full_text_indexing": false,
  "commercial_use": "requires_prior_written_consent",
  "phase": "MVP"
}
```

> 相較 pipeline.md 原始範例，本規格書新增 `phase`（MVP / Phase 1.5 / Phase 2 / Phase 3）與 `full_text_indexing`（是否可全文索引，或僅 metadata）兩個欄位，方便 Ingestion Service 與 Policy Gate 依授權狀態與階段篩選、啟用/停用特定來源。

### 3.4 待辦事項（實作前仍需完成的動作，非決策）

1. **MARAD feed URL 實地驗證**：於團隊部署環境用 `curl`/`feedparser` 實際拉取一次 `https://www.maritime.dot.gov/taxonomy/term/36/feed`，確認格式與可存取性（規格撰寫時查證工具遭反爬蟲阻擋，但 URL 命名規則高度符合該站台慣例）。
2. **航貿週刊授權接洽**：若團隊希望在 Phase 1.5 將航貿週刊升級為全文索引，需先聯繫航貿文化事業有限公司（`support@shippingdigest.tw`）取得書面授權。

---

## 四、資料怎麼進來

### 4.1 Ingestion 流程總覽

```text
Source Registry 登錄
        │
        ▼
IngestionConnector.fetch()  ── 依來源型態分派 ──┐
        │                                       │
        ▼                                       ▼
  排程觸發（cron / webhook / 人工上傳）     擷取原始內容
        │                                       │
        └───────────────────┬───────────────────┘
                            ▼
                  checksum 比對（是否為新版本？）
                            │
                ┌───────────┴───────────┐
                ▼ 無變化                  ▼ 有變化 / 首次擷取
              略過                封裝為 RawDocument → 進入治理層
```

所有來源不論型態，最終都必須實作統一的 `IngestionConnector` 介面：`fetch() → RawDocument`，讓治理層不需要關心資料原始格式。

### 4.2 依來源型態的擷取方式（本模組實際採用）

| 來源 | 擷取方式 | 排程 | 技術實作 | Phase |
| --- | --- | --- | --- | --- |
| 全國法規資料庫（商港法） | ZIP 批次下載 | 每日或每週巡檢 + checksum 比對 | HTTP 下載 → ZIP 解壓 → XML/JSON parse | MVP |
| 交通部航港局新聞稿 RSS | RSS 訂閱 | 每小時至每日排程拉取 | RSS feed parser（`feedparser`）+ URL/標題去重 | MVP |
| MARAD Press Releases RSS | RSS 訂閱 | 每小時至每日排程拉取 | RSS feed parser + URL/標題去重 | MVP |
| IMO Press Briefings RSS | RSS 訂閱 | 每小時至每日排程拉取 | RSS feed parser，僅擷取 title/summary/url/published_at，不擷取全文 | MVP（metadata-only） |
| 航貿週刊 RSS | RSS 訂閱 | 每日 | RSS feed parser，僅擷取 metadata，待授權後升級全文 | Phase 1.5 |
| 商港法子法規 | ZIP 批次下載 | 事件驅動 | 同商港法解析器 | Phase 1.5 |
| iMarine 貨櫃統計 | REST API | 每日或每週批次拉取 | HTTP GET + JSON parse | Phase 2 |
| IMO / EU ETS / MPA 文件 | 爬蟲 + PDF 解析 | 事件驅動（政策發布時）+ 定期巡檢 | Scraper + PDF parser（含 OCR 分流判斷） | Phase 2 |
| WHO 疫情通報 | API / 爬蟲 | 事件驅動 | API polling / scraper | Phase 3 |
| 中央氣象署 | API 串接 | 小時級 | API polling | Phase 3 |
| 內部補充文件 | 人工上傳 | 隨需 | Manual Upload API + 審核流程 | Phase 3 |

### 4.3 RawDocument 格式

沿用 `pipeline.md` 二-3 節格式，本模組實際採用：

```json
{
  "source_id": "law_moj_shipping_port_act",
  "source_module": "航港法令",
  "source_type": "regulation",
  "source_url": "https://law.moj.gov.tw/",
  "raw_format": "zip_xml",
  "content_pointer": "s3://policy-rag-raw/law_moj_shipping_port_act/2026/07/sha256_abcd.xml",
  "fetched_at": "2026-07-04T10:00:00+08:00",
  "source_version": "PCode:K0080001; revised_at:2023-12-06",
  "checksum": "sha256:abcd..."
}
```

新聞來源範例（航港局新聞稿，全文索引）：

```json
{
  "source_id": "motcmpb_press_release_rss",
  "source_module": "海運時事",
  "source_type": "news",
  "source_url": "https://www.motcmpb.gov.tw/Information/RSS?SiteId=1&NodeId=15",
  "raw_format": "rss_item",
  "content_pointer": "s3://policy-rag-raw/motcmpb_press_release_rss/2026/07/sha256_ef01.html",
  "fetched_at": "2026-07-04T08:00:00+08:00",
  "source_version": "published_at:2026-07-04T06:30:00+08:00",
  "checksum": "sha256:ef01..."
}
```

新聞來源範例（IMO，僅 metadata，不擷取全文）：

```json
{
  "source_id": "imo_press_briefings_rss",
  "source_module": "國際組織動態",
  "source_type": "news",
  "source_url": "https://www.imo.org/en/pages/pressbriefingsrss.aspx",
  "raw_format": "rss_item_metadata_only",
  "content_pointer": null,
  "fetched_at": "2026-07-04T08:00:00+08:00",
  "source_version": "published_at:2026-07-04T05:00:00+08:00",
  "checksum": "sha256:9912..."
}
```

`content_pointer` 一律指向物件儲存（MVP 使用 MinIO，S3 相容協定；本地檔案系統僅作開發期 fallback），`RawDocument` 本身只存指標與 metadata，不直接內嵌大型原始內容，避免資料庫膨脹。路徑慣例：

```text
s3://policy-rag-raw/{source_id}/{yyyy}/{mm}/{checksum}.{ext}
```

IMO 等 metadata-only 來源（見 3.1、3.2 節）因不擷取全文，`content_pointer` 欄位為 `null`。

### 4.4 更新與變更偵測策略

| 來源類型 | 同步策略 |
| --- | --- |
| 法規（全國法規資料庫） | 每日或每週巡檢；以 `PCode` + `revised_at` 比對，若版本變更則建立 `superseded_by` 關係，舊版本不刪除、標記為過期 |
| 新聞（RSS） | 每小時至每日排程拉取；以標題相似度 + URL canonicalization 去重，避免同一則新聞被當成多筆資料重複處理 |
| Phase 2/3 的 API / 爬蟲來源 | 依來源 cadence 決定（見 4.2 節排程欄），事件觸發者優先採 webhook 或定期輪詢比對 |
| 人工上傳（Phase 3） | 上傳後進入權限與審核流程，通過後才入庫 |

整合後的通用規則：

```text
定期來源 → cron 拉取 → checksum 比對 → 有變更才重處理
事件來源 → webhook / 定期輪詢比對 → 觸發增量處理
人工來源 → 上傳 → 權限與審核 → 通過後入庫
法規來源 → 版本比對 → 建立 superseded_by 關係，保留歷史版本
```

---

## 五、資料怎麼處理

### 5.1 處理流程總覽

依 `pipeline.md` 三節提出的七段式流程，本模組實際採用的順序為：

```text
RawDocument
  → 格式解析與正規化
  → Metadata Enrichment
  → Policy Gate（索引時）
  → Chunking
  → Embedding / Indexing
  → [查詢時] Hybrid Retrieval → Rerank → Dedup → Conflict Check → Policy Gate（查詢時）
  → Evidence Packaging
```

### 5.2 格式解析與正規化

| 處理項目 | 本模組作法 |
| --- | --- |
| 格式辨識 | 依 `raw_format` 欄位分派解析器：ZIP+XML（法規）、RSS item / HTML（新聞） |
| OCR 分流 | MVP 兩來源皆非掃描 PDF，暫不需要 OCR；Phase 2 引入 IMO/EU 的 PDF 文件時才需要判斷原生 PDF 或掃描 PDF |
| 編碼標準化 | 統一轉為 UTF-8，處理法規文件常見的 Big-5 造字區與特殊符號 |
| 語言標記 | 標記 `zh-TW`；新聞若含英文引述另標記混語比例 |
| 時間標準化 | 統一轉換為 `Asia/Taipei` 時區的 ISO 8601 格式 |
| 單位標準化 | 港務相關單位（如 TEU、GT）、法規條號格式統一 |
| 結構保留 | 法規保留「條/項/款」層級；新聞保留段落、標題、發布時間 |
| 去重 | checksum + 標題相似度 + URL canonicalization |
| 版本化 | 法規記錄 `PCode`、`revised_at`、`source_version`、`superseded_by`；新聞記錄 `published_at` |

### 5.3 Metadata Enrichment

每個 Chunk 在建立索引前需補齊以下欄位（沿用 `pipeline.md` 三-2 節，並依本模組需求精簡至實際會用到的欄位）：

```json
{
  "document_id": "LAW-K0080001-20231206",
  "chunk_id": "LAW-K0080001-art12-0003",
  "source_id": "law_moj_shipping_port_act",
  "source_type": "regulation",
  "title": "商港法",
  "issuing_body": "交通部航港局",
  "jurisdiction": "TW",
  "published_at": "2023-12-06",
  "effective_at": "2023-12-06",
  "version": "2023-12-06",
  "section_path": "第12條",
  "original_url": "https://law.moj.gov.tw/...",
  "file_hash": "sha256...",
  "credibility_score": 96,
  "access_level": "public",
  "review_status": "verified",
  "phase": "MVP"
}
```

新聞類 Chunk 額外需要：`author`（若可取得）、`news_freshness_days`（距今天數，供 Policy Gate 新鮮度判斷使用）。

### 5.4 Policy Gate（索引時）

依 `pipeline.md` 三-3 節，索引時 Policy Gate 檢查以下項目：

| Gate | 檢查內容 | 輸出 |
| --- | --- | --- |
| 來源登錄 | `source_id` 是否存在於 Source Registry | allow / deny |
| 授權相容 | 是否可納入 RAG 與下游輸出 | allow / review / deny |
| 顯名完整 | 是否能產出 attribution | allow_with_attribution |
| 個資風險 | 是否含 PII 或敏感資訊 | allow_with_redaction / deny |
| 法域適用 | 是否適用於目標法域與任務 | allow / review |
| 新鮮度 | 新聞是否超出設定的有效期（如 90 天，用於「例行日報」場景降權，非直接排除） | allow / stale_for_task |
| provenance 粒度 | 是否可追溯到頁、段、條、列 | allow / review |

MVP 階段兩項來源（法規、新聞）皆為公開合法來源，Policy Gate 的 `deny` 情境理論上極少出現；但仍需完整實作這道關卡，因為 Phase 2/3 導入國際政策文件與內部文件後，授權與 PII 風險會顯著提高，MVP 階段先把關卡骨架建好可避免日後重構。

### 5.5 Chunking：切段規則

| 文件類型 | 切段方式 | 參數 | Phase |
| --- | --- | --- | --- |
| 法規條文 | 依「條/項/款」為原子單位，不可跨條切分 | 依條文自然長度，不設 token 上限硬切 | MVP |
| 新聞與公告 | 依段落與引言切分 | 250–500 tokens，10–20% overlap | MVP |
| 政策白皮書（Phase 2 的 IMO/EU 文件） | 依章節、小節、段落 | 400–700 tokens | Phase 2 |
| FAQ / 解釋令 | 一問一答為單位 | 200–500 tokens | Phase 2/3 |
| 表格資料（如貨櫃統計） | 保留表頭與列資料，另存 `table_json` | 依表格結構，不做字數切分 | Phase 2 |

**法規 Chunking 是本模組準確度影響最大的一環**（呼應 Issue #1 開發備註），實作時建議：

1. 先用結構化解析器辨識「編/章/節/條/項/款」階層，再以「條」為最小切分單位（若單條內容過長超過模型上下文負擔，才在條內以「項」為次一級切分，但仍保留條號於 `section_path`）。
2. 每個 chunk 的 `section_path` 必須能還原「第 X 條第 Y 項第 Z 款」的完整定位，供 Evidence Package 的 `locator` 使用。
3. MVP 僅對《商港法》本文執行完整 Chunking；子法規暫不切段，僅登錄 Source Registry metadata（見 3.2 節）。

### 5.6 Embedding / Indexing（已定案）

#### 向量資料庫：PostgreSQL + pgvector（定案，不進 Qdrant / Milvus）

| 方案 | 優點 | 缺點 | 決策 |
| --- | --- | --- | --- |
| **PostgreSQL + pgvector（採用）** | 單一資料庫同時處理向量、全文（`tsvector` lexical index）、metadata filter、transaction、audit，維運只需一套系統；`issues.md` 與 `pipeline.md` 皆建議此方案 | 向量檢索效能在巨量資料（千萬級以上）時不如專用向量庫；hybrid 查詢需自行組 SQL | **MVP 與 Phase 2 皆採用**。本模組核心是 Evidence Pipeline（法規、新聞、chunk、locator、source、audit、policy verdict 都需要關聯查詢），不是單純 vector search，PostgreSQL 可一次處理 metadata、transaction、audit、filter、vector index，維運複雜度最低 |
| 專用向量庫（Qdrant / Milvus）+ 另建全文索引 | 向量檢索效能與可擴展性較佳，原生支援 filter+向量混合查詢 | 需要多維護一套系統；全文/metadata 查詢需額外整合，部署與維運複雜度提高 | **不進 MVP**。Phase 3 若資料量、QPS、延遲或分散式需求超過 PostgreSQL 能力後才評估 Qdrant；Milvus 僅在超大規模與分散式維運能力成熟時才評估 |

> 用詞澄清：本模組的「全文檢索」指 **PostgreSQL full-text search / lexical retrieval**（`tsvector`/`tsquery`），非真正的 BM25 演算法實作。若未來需要更精確的 BM25 排序，屬 Phase 3 才評估導入 OpenSearch / Elasticsearch / ParadeDB / Tantivy 等專用工具，MVP 與 Phase 2 皆不使用這些工具。

#### Embedding 模型：EmbeddingGemma-300m（定案）

- **模型**：`google/embeddinggemma-300m`，Google 開源的 3 億參數 embedding 模型，基於 Gemma 3 架構訓練，支援 100+ 語言。
- **輸出維度**：**768**（原生輸出維度，亦可透過 Matryoshka Representation Learning 降至 512/256/128，但 MVP 固定採用 768 全維度以求最佳檢索品質）。資料庫 schema 需對應使用 `vector(768)`。
- **輸入長度**：模型卡標示最大輸入 2048 tokens，法規/新聞 chunk 長度設計（見 5.5 節）需控制在此上限內。
- **部署方式**：本地推論（`sentence-transformers` 載入），體積小，可與生成式 LLM 共用同一台 GPU 主機、獨立 process 常駐，不需要額外 GPU。

#### 索引內容

| 索引 | 用途 | MVP 是否需要 |
| --- | --- | --- |
| metadata index | 依來源、法域、日期、版本、權限過濾 | 需要 |
| lexical / full-text index（PostgreSQL `tsvector`） | 查法條號、關鍵字 | 需要 |
| vector index（pgvector，`vector(768)`） | 語意檢索 | 需要 |
| table index | 統計與表格查詢 | Phase 2（貨櫃統計導入後） |
| provenance index | 回指原文頁碼、段落、條文 | 需要（透過 `section_path` 實作，不需獨立索引結構） |

### 5.7 Retrieval、Rerank、Conflict Check

查詢時流程：

```text
User Query（一般問答或報告生成請求）
  → Query Understanding（含 Chat 多輪對話的 query rewriting）
  → Metadata Filter（依法域、時間、來源類型、source_type 過濾）
  → Hybrid Retrieval（dense + lexical，以 RRF 融合排序）
  → [Phase 2] Rerank（cross-encoder 重排 top-N）
  → Deduplicate
  → Conflict Check（同一議題不同來源結論是否衝突）
  → Policy Gate（查詢時，判斷本次任務是否可用該證據）
  → Evidence Selection
  → Evidence Package
```

- **Conflict Check** 在 MVP 階段出現機率較低，但仍需實作基本邏輯：若同一主題的多筆證據在關鍵事實（如日期、金額、是否已生效）上不一致，標記 `conflict_flag`，並在生成階段要求 LLM 明確陳述不確定性（呼應 `pipeline.md` 三-7 節 `generation_instruction.state_uncertainty_if_conflict`）。MVP 階段 `conflict_flag` 預設可標記為 `not_evaluated`（尚未實作完整衝突偵測邏輯時的誠實狀態），不可偽裝成 `none`。
- **Rerank（cross-encoder 重排）不進 MVP**：MVP 僅用 RRF（Reciprocal Rank Fusion）融合 dense 與 lexical 分數作為最終排序，不引入獨立 reranker 模型，以降低 MVP 階段的元件數量與延遲。Phase 2 視檢索品質需求評估導入。
- MVP 明確不做：真正 BM25、OpenSearch、Qdrant/Milvus、query decomposition、multi-hop retrieval、ACL-aware retrieval。

---

## 六、資料怎麼出去

### 6.1 內部接口輸出

| 輸出物 | 產生時機 | 使用者 |
| --- | --- | --- |
| Source Registry Entry | 新來源接入時 | 治理層、排程器、稽核 |
| Canonical Document | 原始資料正規化後 | Chunking、Metadata Enrichment |
| Governed Chunk Record | 索引建置時 | 向量庫、全文索引、稽核 |
| Retrieval Bundle | 查詢時 | Reranker、Evidence Packaging Service |
| Evidence Package | 生成前（一般問答或報告） | Chat Service、Report Generation Service |
| Decision Log Envelope | 決策層寫回時（非本模組產出，由下游決策層負責） | 決策層、回訓資料管線 |

整合版資料流：

```text
Raw Source Asset
  → Canonical Document
  → Governed Chunk Record
  → Retrieval Bundle
  → Evidence Package
  → Chat Service（一般問答 / 內嵌報告生成）或 Report Generation Service（獨立呼叫）
```

### 6.2 Evidence Package 格式

沿用 `pipeline.md` 三-7 節格式，作為 Chat 與 Report Generation 兩個入口的共同輸入：

```json
{
  "package_type": "PolicyReportEvidencePackage",
  "task_type": "chat_qa",
  "query": "航港局如何因應 IMO Net-Zero Framework",
  "context_filters": {
    "jurisdiction": ["TW"],
    "as_of_date": "2026-07-04"
  },
  "evidence_items": [
    {
      "evidence_id": "ev_001",
      "chunk_id": "LAW-K0080001-art12-0003",
      "document_id": "LAW-K0080001-current",
      "source_id": "law_moj_shipping_port_act",
      "source_type": "regulation",
      "title": "商港法",
      "text": "...",
      "locator": {
        "article": "第12條"
      },
      "source_url": "https://law.moj.gov.tw/...",
      "published_at": "2023-12-06",
      "retrieval_score": 0.82,
      "credibility_score": 96
    }
  ],
  "conflict_flag": "not_evaluated",
  "confidence": 0.88,
  "policy_verdict": "allow",
  "generation_instruction": {
    "answer_only_from_evidence": true,
    "must_cite": true,
    "state_uncertainty_if_conflict": true
  }
}
```

`task_type` 依觸發入口不同而異：`chat_qa`（一般問答）、`report_generation`（報告生成，不論是 Chat 內嵌觸發或獨立 API 觸發）。

### 6.3 輸出格式

#### Chat 回覆格式

```json
{
  "conversation_id": "conv-20260704-0007",
  "turn_id": 5,
  "role": "assistant",
  "content": "根據《商港法》第12條與 2025 年 4 月 IMO MEPC 83 決議...",
  "citations": ["ev_001", "ev_002"],
  "confidence": 0.88,
  "conflict_flag": "none",
  "generated_at": "2026-07-04T10:30:00+08:00"
}
```

#### 結構化報告格式

沿用 `pipeline.md` 四-2 節格式：

```json
{
  "report_id": "RPT-20260704-001",
  "topic": "航港局如何因應 IMO Net-Zero Framework",
  "generated_at": "2026-07-04T10:30:00+08:00",
  "model_version": "gemma-4-e4b-it-v1",
  "triggered_by": "chat",
  "source_conversation_id": "conv-20260704-0007",
  "sections": {
    "background": { "text": "...", "citations": ["ev_001", "ev_002"] },
    "policy_basis": { "text": "...", "citations": ["ev_003"] },
    "international_cases": { "text": "...", "citations": ["ev_004"] },
    "recommendations": { "text": "...", "citations": ["ev_005"] }
  },
  "source_list": [
    {
      "evidence_id": "ev_001",
      "source_name": "商港法",
      "url": "https://law.moj.gov.tw/...",
      "date": "2023-12-06",
      "locator": "第12條"
    }
  ],
  "confidence": {
    "faithfulness_score": 0.92,
    "conflict_flag": "none",
    "flagged_sections": []
  }
}
```

`triggered_by` 標記為 `chat` 或 `api`，`source_conversation_id` 僅在 `triggered_by=chat` 時存在，用於回指產生此報告的對話串。

輸出形式：JSON（API/儀表板渲染/系統保存）、Markdown（快速報告/人工編修）、PDF（正式交付/展示）；MVP 階段優先支援 JSON，Markdown/PDF 為 Phase 2 增量功能。

### 6.4 下游模組輸出契約

所有下游模組一律採 `output` + `metadata` 雙欄位設計：

```json
{
  "output": {
    "type": "policy_impact",
    "value": "碳定價政策可能提高高排放航線成本權重"
  },
  "metadata": {
    "source_ids": ["law_moj_shipping_port_act", "motcmpb_press_release_rss"],
    "evidence_ids": ["ev_001", "ev_002"],
    "confidence": 0.86,
    "model_version": "gemma-4-e4b-it-v1",
    "generated_at": "2026-07-04T10:30:00+08:00",
    "policy_verdict": "allow_with_attribution"
  }
}
```

| 下游模組 | 本模組輸出 |
| --- | --- |
| UI / Chat 前端 | Chat 回覆、報告摘要、來源清單、confidence |
| RL 排程（Phase 2 以後串接） | 政策衝擊參數、成本權重變化、來源依據 |
| 決策層 | Decision Log（由決策層寫回，本模組僅提供輸入） |

### 6.5 稽核與驗證輸出

**儲存架構（已定案）**：稽核紀錄與主資料使用**同一個 PostgreSQL instance**，但拆成獨立的 `audit` schema（與 `public` schema 的業務資料表分開），JSONL 僅作每日匯出封存、不作為主要儲存：

```text
public.sources
public.raw_documents
public.documents
public.chunks
public.embedding_models

audit.ingest_runs
audit.retrieval_runs
audit.evidence_packages
audit.generation_runs
audit.policy_verdicts
audit.validation_runs
```

原因：audit 需要可查詢、可 join、可回放 Evidence Package，只用 JSONL 會讓 dashboard、debug、trace、政策稽核變困難；JSONL 適合 append-only 封存，不適合作唯一真相來源。

每日 JSONL 匯出路徑慣例：`s3://policy-rag-audit/{yyyy}/{mm}/{dd}/*.jsonl`

每次查詢/生成都需保存稽核紀錄（沿用 `pipeline.md` 四-4 節格式）：

```json
{
  "query": "...",
  "task_type": "chat_qa",
  "retrieved_chunks": ["chunk_001", "chunk_002"],
  "rerank_result": ["chunk_002", "chunk_001"],
  "evidence_package_id": "EP-20260704-001",
  "prompt_version": "policy-chat-prompt-v1",
  "model_version": "gemma-4-e4b-it-v1",
  "output_id": "conv-20260704-0007/turn-5",
  "validation": {
    "citation_coverage": 0.94,
    "faithfulness_score": 0.91,
    "unsupported_claim_count": 0,
    "temporal_conflict_count": 0
  }
}
```

---

## 七、LLM 與 Grounding 設計

### 7.1 開源 LLM 選型：Gemma 系列（定案）

團隊 GPU 資源為單張 24GB 級顯卡（如 RTX 3090/4090）。

```text
Embedding:
google/embeddinggemma-300m

Generation:
google/gemma-4-E4B-it
```

| 模型 | 定位 | 規格 | 24GB GPU 可行性 |
| --- | --- | --- | --- |
| **google/embeddinggemma-300m** | Embedding | 3 億參數，基於 Gemma 3 架構，輸出維度 768，最大輸入 2048 tokens，支援 100+ 語言 | 體積小，與生成模型共用 GPU 無壓力 |
| **google/gemma-4-E4B-it（採用）** | Generation | Gemma 4 系列 instruction-tuned 模型，「E」代表 effective parameters（透過 Per-Layer Embeddings 提升參數效率，針對裝置端／單卡部署最佳化），context window 128K tokens，原生支援 function calling、多語言（35+ 語言開箱即用、預訓練涵蓋 140+ 語言） | 專為單卡/裝置端部署設計，24GB 可行性高，實際 VRAM 需求需於 Phase 1 實測確認是否需要量化 |
| google/gemma-4-26B-A4B-it | Generation（升級候選） | 較大量級，context window 256K tokens | 24GB 下需量化，Phase 2 視品質需求評估 |
| google/gemma-4-31B-it | Generation（升級候選） | 較大量級 dense 模型 | 24GB 下需高強度量化，Phase 2 視品質需求評估 |

**MVP 直接採用 google/gemma-4-E4B-it，不走「先上小模型、再升級」的漸進路線**——因為 E4B 本身就是為單卡/裝置端部署設計的模型，且 context window（128K）遠超同量級 Qwen/Llama 選項，能一次處理較長的 Evidence Package 而不需頻繁截斷。

Phase 2 升級評估的 gate（達標才考慮換更大模型，而非預設要換）：

```text
citation coverage >= 95%
unsupported claim rate <= 3%
JSON schema pass rate >= 98%
locator accuracy >= 98%
p95 latency 可接受
部署成本可接受
```

若 gemma-4-E4B-it 已達標，Phase 2 應優先投入 serving/prompt/evidence packing 的優化，而非急著換更大模型。

> **合規提醒**：Gemma 系列採用 Gemma 使用條款（含 Prohibited Use Policy），雖然政策報告生成、Chat 問答屬正常應用場景，仍建議在正式對外（尤其是政府決策輔助）部署前，快速檢視一次該條款是否有需要額外聲明或限制的條款（見第十一節待辦）。

### 7.2 推論部署

- **推論框架**：vLLM（吞吐量高、原生支援 continuous batching，適合多使用者同時對話的場景）或 Ollama（部署更簡單，適合單機開發/展示階段）。建議開發階段用 Ollama 快速迭代，正式展示/MVP 交付前切換至 vLLM 以取得更好的併發表現。
- **量化策略**：官方已提供 `gemma-4-E4B-it-qat-q4_0-gguf`、`gemma-4-E4B-it-qat-w4a16-ct` 等量化版本；MVP 建議先嘗試全精度/官方建議精度確認品質基準，若 24GB 顯存吃緊或多使用者併發需要更大 KV cache 空間，再切換至 QAT 量化版本（量化感知訓練，品質損失通常小於一般 PTQ 量化）。
- **EmbeddingGemma-300m 與 gemma-4-E4B-it 共用同一台 GPU**：由於 embedding 計算量遠小於生成，建議以獨立 process 常駐、與 LLM 推論服務分開部署，避免互相搶佔顯存導致 OOM。

### 7.3 Prompt 設計原則

1. **強制引用**：Prompt 中明確要求「僅能根據提供的 evidence_items 回答，每個事實陳述後必須標註對應的 evidence_id；若證據不足以回答，需明確告知使用者資訊不足，不可自行推測」。明確禁止使用模型內部知識補足法規內容、禁止產生不存在的條文或來源。
2. **衝突揭露**：若 Evidence Package 的 `conflict_flag` 非 `none`/`not_evaluated`，Prompt 需附加指令要求 LLM 在回覆中明確指出「不同來源對此議題有不同說法」，而非選擇性忽略衝突。
3. **模版化生成（報告場景）**：報告生成的 Prompt 依固定章節模版（background / policy_basis / international_cases / recommendations）分別生成，或一次生成後由程式切分驗證，兩種實作方式皆可，實作時依實際生成品質與延遲取捨。
4. **對話語氣 vs. 報告語氣**：Chat 一般問答走口語化、精簡回覆；報告生成走正式書面語氣，Prompt 需區分兩種輸出風格的指令。
5. **善用 function calling**：Gemma 4 原生支援 function calling，Chat Service 的 Intent Routing（判斷一般問答 vs. 報告生成）可直接以 function/tool call 形式實作，而非另外訓練分類器。

### 7.4 Faithfulness 驗證機制

生成完成後，於回覆/報告送出前執行以下檢查（呼應 `pipeline.md` 四-4 節）：

| 檢查項目 | 作法 | 未通過時的處理 |
| --- | --- | --- |
| Citation coverage | 檢查輸出文本中每個實質性陳述句（含「應、不得、須、可、建議」等規範性用語）是否都能對應到至少一個 `evidence_id` | 標記 `unsupported_claim`，可選擇讓 LLM 重新生成該段落或在輸出中標示警示 |
| Faithfulness score | 比對輸出內容與其引用之 evidence 文本的語意一致性（可用 NLI 模型或簡化版關鍵字/語意相似度計算） | 低於門檻（如 0.7）時標記整體回覆為低信心，前端需明確提示使用者 |
| Temporal conflict | 檢查輸出中引用的多筆證據時間點是否有矛盾（如同時引用「已生效」與「尚未生效」的法規版本） | 標記 `temporal_conflict_count`，要求模型或人工複核 |
| JSON schema pass rate | 報告/Evidence Package 輸出是否符合預期 schema（結構化輸出可用 Gemma 4 的 function calling / structured output 能力強化） | 未通過 schema 驗證時重試生成，多次失敗則回傳 `422` 並記錄失敗原因 |

MVP 階段可先實作**規則式**的 citation coverage 檢查（正則比對 evidence_id 是否出現、規範性用語是否有鄰近引用），語意層級的 faithfulness 模型評分列為 Phase 2 增量功能。生成約束（不論一般問答或報告）一律適用：只能根據 Evidence Package 回答、每個具體主張需對應 evidence_id、證據不足時明確告知、不得使用模型內部知識補充法規內容。

---

## 八、技術棧總覽（已定案）

| 分類 | 選型 | 狀態 |
| --- | --- | --- |
| 程式語言 | Python 3.11+ | 已確認 |
| API 框架 | FastAPI | 已確認 |
| Schema 驗證 | Pydantic | 已確認 |
| 資料庫存取 | SQLAlchemy / SQLModel | 已確認 |
| 資料庫遷移 | Alembic | 已確認 |
| 背景工作 | RQ / Celery / Dramatiq（MVP 可用簡單 async worker） | 已確認方向，MVP 實作以簡化為主 |
| 向量資料庫 | PostgreSQL + pgvector（`vector(768)`） | 已確認（見 5.6 節） |
| 全文檢索 | PostgreSQL 內建全文索引（`tsvector`，非真正 BM25） | 已確認（見 5.6 節） |
| Embedding 模型 | google/embeddinggemma-300m（輸出維度 768） | 已確認（見 7.1 節） |
| LLM 推論框架 | vLLM（正式）／ Ollama（開發） | 已確認 |
| LLM 模型 | google/gemma-4-E4B-it | 已確認（見 7.1 節） |
| GPU 資源 | 單張 24GB 級顯卡 | 已確認 |
| 排程 | cron（法規巡檢、新聞 RSS 拉取） | 已確認 |
| 物件儲存 | MinIO（MVP 自架，S3 相容）／未來可平移至任意 S3 相容雲端服務；本地檔案系統僅作開發期 fallback | 已確認 |
| ZIP/XML 解析 | Python 標準庫（`zipfile`、`lxml`/`xml.etree`） | 已確認 |
| RSS 解析 | `feedparser`（Python） | 已確認 |
| 稽核紀錄儲存 | 同一個 PostgreSQL instance，獨立 `audit` schema；每日 JSONL 匯出至物件儲存作封存 | 已確認（見 9.3 節、11.1 節） |

---

## 九、API 規格

### 9.1 Chat API

**`POST /api/v1/chat`**

Request：

```json
{
  "conversation_id": "conv-20260704-0007",
  "message": "航港局要怎麼因應 IMO 的淨零框架？",
  "context_filters": {
    "jurisdiction": ["TW"],
    "as_of_date": "2026-07-04"
  }
}
```

Response：

```json
{
  "conversation_id": "conv-20260704-0007",
  "turn_id": 5,
  "intent": "chat_qa",
  "message": {
    "role": "assistant",
    "content": "根據《商港法》第12條...",
    "citations": ["ev_001", "ev_002"],
    "confidence": 0.88,
    "conflict_flag": "none"
  },
  "evidence_package_id": "EP-20260704-001"
}
```

若 `intent` 判斷為 `report_generation`，Response 額外包含內嵌報告物件（見 6.3 節報告格式），並在 `message.content` 中以摘要文字＋報告連結呈現，讓對話串保持可讀性，同時完整報告 JSON 可供前端另開報告檢視畫面。

### 9.2 Report Generation API（獨立呼叫）

**`POST /api/v1/reports`**

Request：

```json
{
  "topic": "航港局如何因應 IMO Net-Zero Framework",
  "context_filters": {
    "jurisdiction": ["TW"],
    "as_of_date": "2026-07-04"
  },
  "triggered_by": "api",
  "output_formats": ["json"]
}
```

Response：報告完整 JSON（見 6.3 節格式），`triggered_by: "api"`，不含 `source_conversation_id`。

### 9.3 內部／低階服務 API

除了面向使用者的 Chat／Report API，系統內部（或供其他模組直接整合）也需暴露較低階、對應管線各階段的服務端點：

```text
POST /api/v1/ingest/law
POST /api/v1/ingest/news
POST /api/v1/retrieve
POST /api/v1/evidence-packages
POST /api/v1/generate
GET  /api/v1/audit/{run_id}
```

- `POST /api/v1/ingest/law`、`POST /api/v1/ingest/news`：觸發（或由排程呼叫）對應來源的 Ingestion Service，回傳本次拉取的 `RawDocument` 數量與去重結果。
- `POST /api/v1/retrieve`：僅執行 Hybrid Retrieval（不含生成），回傳 Top-K chunk 與分數，供除錯或其他模組直接取用治理後資料。
- `POST /api/v1/evidence-packages`：接收檢索結果，執行 Rerank/Dedup/Conflict Check/Policy Gate，輸出 Evidence Package，不觸發生成。
- `POST /api/v1/generate`：接收既有 Evidence Package，執行 LLM 生成與 Faithfulness 驗證，回傳生成結果。
- `GET /api/v1/audit/{run_id}`：查詢單次 ingest/retrieve/generate 的完整稽核紀錄（對應 `audit` schema，見 11.1 節）。

**Chat API（9.1 節）與 Report Generation API（9.2 節）皆為這些低階端點的組合封裝**：`/api/v1/chat` 內部依序呼叫 `retrieve → evidence-packages → generate`，但不得把這三步的邏輯直接寫死在 Chat endpoint 內——必須透過呼叫上述獨立端點（或其對應的內部 service 呼叫，MVP 階段可為 in-process function call，不必強制走 HTTP），以確保低階能力可被其他模組單獨重用、並可獨立測試。

### 9.4 通用錯誤與狀態

| 狀態 | 說明 |
| --- | --- |
| `200 OK` | 正常回覆/報告產出 |
| `202 Accepted` | 報告生成為非同步任務時（Phase 2，若生成耗時過長需改為非同步輪詢） |
| `422 Unprocessable Entity` | Evidence 不足、Policy Gate 判定 `deny`，無法生成回覆 |
| `409 Conflict` | 偵測到 hard conflict，需人工複核（回應中會標明 `conflict_flag` 與相關 evidence_id） |

MVP 階段建議先採同步呼叫（`200 OK` 直接回傳結果），非同步任務機制列為 Phase 2 視實際生成延遲決定是否需要。

---

## 十、MVP 範圍與里程碑

### 10.1 MVP 目標

建立一條**可展示、可稽核、可回放的 Evidence Pipeline**，不追求完整知識庫平台。只驗證：資料進入 → 資料治理 → 法規 chunking → 新聞 chunking → embedding → hybrid retrieval → Evidence Package → grounded generation → citation → audit trail。

### 10.2 Phase 1（MVP，對應 Issue #1，目標 2 天內完成）

#### 1. 資料來源

```text
法規:
- 商港法本文（全文索引）

新聞:
- 交通部航港局新聞稿 RSS（全文索引，主要來源）
- MARAD Press Releases RSS（全文索引，國際脈絡）

候補新聞（僅 metadata，不做全文索引）:
- IMO Press Briefings RSS
```

#### 2. 資料處理

```text
RawDocument 建立
checksum 去重
Source Registry（含 phase、full_text_indexing 欄位）
基礎 Policy Gate
文件正規化
法規依條 chunking
新聞依段落 chunking
Metadata Enrichment
content_pointer 寫入（MinIO）
```

#### 3. 索引

```text
PostgreSQL + pgvector
PostgreSQL 全文索引（tsvector lexical index，非真正 BM25）
EmbeddingGemma-300m，embedding vector(768)
metadata index
```

#### 4. 檢索

```text
dense retrieval + lexical retrieval
RRF 融合排序
metadata filtering（jurisdiction、source_type、freshness）
top-k evidence selection
```

MVP 明確不做：cross-encoder reranker、query decomposition、multi-hop retrieval、真正 BM25、OpenSearch、Qdrant/Milvus、ACL-aware retrieval。

**5. Evidence Package**：完整欄位（`evidence_items`、`locator`、`retrieval_score`、`confidence`、`policy_verdict`、`conflict_flag`），`conflict_flag` 未實作完整偵測前誠實標記為 `not_evaluated`。

**6. 生成**：`google/gemma-4-E4B-it`，僅根據 Evidence Package 回答，每個具體主張對應 `evidence_id`，證據不足時明確告知，不使用模型內部知識補充法規內容。

**7. 對外介面**：Chat API（一般問答為主，report_generation 意圖可先以簡化版模版輸出，不要求 Intent Routing 完全精準）；內部低階 API（`ingest/law`、`ingest/news`、`retrieve`、`evidence-packages`、`generate`、`audit/{run_id}`，見 9.3 節）為必要交付項，Chat/Report 為其封裝層，不得將邏輯寫死在 Chat endpoint 內。

**8. Faithfulness 驗證**：規則式 citation coverage 檢查（正則比對 evidence_id 是否出現）。

**MVP 降級策略**（呼應 Issue #1 開發備註）：若時間不足，優先保證「商港法 End-to-End 管線」與「Evidence Package 含 `evidence_items`/`locator`」這兩項最關鍵成果；Chat 多輪對話管理、報告模版完整四章節、MARAD/IMO 來源可視情況延後，僅完成航港局新聞稿一項亦可視為 MVP 底線達標。

### 10.3 MVP 成功標準

```text
1. 可成功 ingest《商港法》並依條切 chunk。
2. 可成功 ingest 航港局新聞稿 RSS 並建立新聞 chunk。
3. 每個 chunk 都有 source_id、locator、checksum、published_at/effective_at。
4. 每個 chunk 都有 embedding vector(768) 與 lexical index。
5. retrieve API 可回傳法規與新聞 evidence。
6. evidence package 可被完整保存與回放。
7. gemma-4-E4B-it 只根據 evidence 回答。
8. 回答中的具體主張可對應 evidence_id。
9. 每次 ingest/retrieve/generate 都有 audit record。
10. 停用某 source 後，該 source 不再進入新的 evidence package。
```

### 10.4 MVP 不包含範圍

```text
完整 UI、完整多輪對話管理
PDF parser、OCR、表格抽取
IMO/EU/國際公約全文（僅 metadata）
商港法子法規全文
真正 BM25、OpenSearch/Elasticsearch、Qdrant/Milvus
cross-encoder reranker、agentic retrieval
使用者權限模型、多租戶
資料人工審核後台
自動衝突偵測（僅標記 not_evaluated）
完整語意層級 faithfulness evaluator、完整 citation verifier
```

### 10.5 Phase 1.5（MVP 驗證通過後的第一批擴充）

- 商港法子法規（商港港務管理規則、商港服務費收取保管及運用辦法等）全文索引。
- 航貿週刊授權接洽後升級為全文索引來源。

### 10.6 Phase 2（模組完整版）

- 新增來源：iMarine 貨櫃統計 API、IMO/EU ETS 政策文件（需另洽授權後全文索引）。
- Chat Service 完整 Intent Routing（可用 Gemma 4 原生 function calling 實作）與多輪 query rewriting。
- 報告生成完整四章節模版，支援承接對話歷史 Evidence。
- cross-encoder reranker 導入評估。
- LLM 升級評估：依 7.1 節 gate（citation coverage ≥95%、unsupported claim rate ≤3%、JSON schema pass rate ≥98%、locator accuracy ≥98%）決定是否換更大量級模型。
- Faithfulness 驗證加入語意層級評分（非僅規則式）。
- 報告輸出支援 Markdown/PDF。

### 10.7 Phase 3（未來擴充）

- 新加坡 MPA、行政院公報、政府資料開放平臺、WHO 疫情通報、中央氣象署等來源接入（多數優先服務其他模組，政策報告視需要共用治理後資料）。
- 內部文件/專家補充資料的上傳與審核流程。
- 與 RL 排程、疫情預警等其他模組的下游輸出契約實際串接。
- 視資料量/QPS/延遲評估是否遷移至專用向量庫（Qdrant，超大規模才評估 Milvus）。

---

## 十一、風險與待決事項

### 11.1 已定案決策點（不再需要確認）

| # | 項目 | 定案結果 | 對應章節 |
| --- | --- | --- | --- |
| 1 | 向量資料庫 | PostgreSQL + pgvector；Qdrant/Milvus 不進 MVP，Phase 3 才視資料量評估 | 5.6 |
| 2 | LLM 模型 | Embedding: `google/embeddinggemma-300m`（768 維）；Generation: `google/gemma-4-E4B-it`，MVP 直接採用，不走漸進升級路線 | 7.1 |
| 3 | 新聞來源 | 交通部航港局新聞稿 RSS（主要）＋ MARAD Press Releases RSS（國際脈絡）＋ IMO Press Briefings RSS（metadata-only） | 3.1–3.3 |
| 4 | 法規範圍 | MVP 僅索引《商港法》本文；子法規列 Phase 1.5 | 3.1–3.2 |
| 5 | 程式語言與框架 | Python 3.11+ + FastAPI + Pydantic + SQLAlchemy/SQLModel + Alembic | 8 |
| 6 | 物件儲存 | MinIO（S3 相容），本地檔案系統僅開發期 fallback | 8、4.3 |
| 7 | 稽核紀錄儲存 | 同一 PostgreSQL instance 的獨立 `audit` schema，JSONL 僅作每日封存匯出 | 6.5 |

### 11.2 待辦事項（需在實作啟動前完成的動作，非決策）

1. **MARAD feed URL 實地驗證**：規格查證時遭目標站台以 403 阻擋自動化工具（.gov 網站常見反爬蟲機制），需在團隊實際部署環境用 `curl`/`feedparser` 驗證 `https://www.maritime.dot.gov/taxonomy/term/36/feed` 可正常拉取；若失效需尋找替代 MARAD RSS 路徑或改用其新聞列表頁面的其他訂閱方式。
2. **Gemma 使用條款快速合規檢視**：Gemma 系列採用 Gemma 使用條款（含 Prohibited Use Policy），建議部署前花時間確認政策報告生成、政府決策輔助這類應用場景無需額外聲明或不受限制條款影響。
3. **航貿週刊授權接洽**（Phase 1.5 前置）：若要將其升級為全文索引來源，需聯繫航貿文化事業有限公司（`support@shippingdigest.tw`）取得書面授權。
4. **EmbeddingGemma 實際 VRAM 佔用量測試**：確認 `gemma-4-E4B-it` 全精度或建議精度版本在單張 24GB GPU 上與 EmbeddingGemma 共存時的實際顯存/延遲表現，作為是否需要量化版本（`gemma-4-E4B-it-qat-*`）的依據。

### 11.3 技術風險

| 風險 | 影響 | 因應方向 |
| --- | --- | --- |
| 兩天 MVP 時間極為緊迫 | 可能無法完成完整 Chat 多輪對話與報告模版 | 依 10.2 節降級策略，優先保證商港法管線 End-to-End 與 Evidence Package 完整性；新聞來源可先只做航港局一項 |
| gemma-4-E4B-it 實際部署表現未經團隊實測 | 若顯存或延遲不如預期，可能需要臨時切換量化版本，影響 MVP 時程 | 及早（Day 1 上午）完成基本推論測試，盡早發現問題以留出調整時間 |
| 法規 Chunking 誤切跨條 | 直接影響報告與問答的準確度與可信度（Issue #1 明確點名此為最重要項目） | 優先以規則式解析器辨識「條」邊界，人工抽樣驗證切段結果 |
| MARAD RSS 存取受阻 | 若 .gov 反爬蟲機制在正式環境仍擋下請求，會少一個 MVP 新聞來源 | 航港局新聞稿為主要來源可獨立支撐 MVP 底線；MARAD 視為錦上添花，不影響 MVP 成功標準的達成 |
| IMO 內容誤用授權範圍 | 若不慎將 metadata-only 來源的內容全文索引或商用，可能違反 IMO ePublications 條款 | Policy Gate 需嚴格檢查 `full_text_indexing` 欄位，索引流程對 `false` 的來源直接拒絕全文擷取，僅允許 metadata 通過 |
| Faithfulness 規則式檢查可能有漏網之魚 | 未偵測到的 unsupported claim 會降低系統可信度 | Phase 2 導入語意層級驗證模型，MVP 階段以人工抽樣複核作為輔助 |
| Chat 多輪對話與報告生成共用 Evidence 的一致性 | 若對話上下文管理不當，報告內容可能與先前對話「兜不起來」 | 明確定義對話歷史中 Evidence 的儲存與檢索邏輯（見 1.3 節），並在測試階段設計「先問答後生成報告」的端對端案例驗證 |

### 11.4 與其他模組的介接風險（非本模組範圍，但需留意）

- 本模組的下游輸出契約（`output` + `metadata`）需與 RL 排程、疫情預警等其他模組的實際開發進度對齊，若其他模組尚未定案輸入格式，本模組的契約設計可能需要迭代調整。
- 碳權代幣化 PoC 若未來需要本模組協助生成政策文件，需另外評估其資料模型是否能整合進現有的 Source Registry / Evidence Package 架構。
