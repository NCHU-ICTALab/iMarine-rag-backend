# 一、資料從哪來

本系統的資料來源分為**領域核心資料、官方法源資料、官方統計與開放資料、國際政策資料、即時動態資料、內部補充資料**六大類。整體原則是以官方、原始、可追溯來源為優先，讓後續 LLM+RAG 生成的政策報告能回指到明確來源、版本、段落與時間點。第一份研究明確指出，政策報告若要可被採信，必須把官方法源、正式政策文件、官方統計與可追溯原始段落放在最上游，LLM 只負責組織、比對、解釋與撰寫。

## 1. 領域核心資料：iMarine 航港資料

iMarine 是本題目的核心領域資料來源，但它應被視為**異質資料集合**，涵蓋不同型態與不同接入方式的資料。第三份文件指出，iMarine 六大主題包含全球海運指數、臺灣數據統計、航港法令、國際組織動態、海運時事與替代能源專區；其中只有「臺灣數據統計／貨櫃」有正式開放 JSON API，其餘多為網頁、文件或需人工匯入的內容。

可納入的 iMarine 資料包括：

| 類別     | 內容               | 用途             |
| ------ | ---------------- | -------------- |
| 臺灣數據統計 | 貨櫃進出口、轉運、港口統計    | 量化分析、趨勢判斷、報告圖表 |
| 全球海運指數 | 航運指數、運價或市場變化     | 航運趨勢與國際背景      |
| 航港法令   | 航港相關法規與行政規則      | 法源依據、合規判斷      |
| 國際組織動態 | IMO、EU、MPA 等政策動態 | 國際比較與政策趨勢      |
| 海運時事   | 產業新聞、政策事件、港口動態   | 背景脈絡與最新事件      |
| 替代能源專區 | 綠色燃料、替代能源、減碳技術   | 碳排政策、能源轉型分析    |

其中 iMarine 貨櫃統計可透過 6 個 JSON API 端點取得；法令資料則需透過全國法規資料庫整包 ZIP 下載後切分；國際動態、時事、指數與替代能源多需爬蟲、PDF 解析、RSS 或人工上傳。

## 2. 官方法源與政策資料

臺灣情境下，核心法源資料至少應包含：

| 來源       | 內容                     | 用途          |
| -------- | ---------------------- | ----------- |
| 全國法規資料庫  | 中央法規、命令、司法解釋、條約協定      | 法源依據、條文比對   |
| 行政院公報資訊網 | 公報、法規、行政規則、草案預告        | 最新正式發布與草案狀態 |
| 主管機關網站   | 政策說明、FAQ、解釋令、施政計畫      | 政策口徑與執行依據   |
| 政府資料開放平臺 | JSON、CSV、XML、API 服務資料集 | 官方統計與量化分析   |

第一份文件建議，正式法規與命令、政策白皮書、官方統計與政府開放資料，應被設定為最高或高優先級來源，並以發布單位、日期、版本、生效狀態、可定位段落作為可信度評估條件。

## 3. 國際官方與標竿資料

因為題目涉及航運碳排、國際規範與跨境政策，資料來源也應納入國際官方資料。第二份文件將 EU、IMO、WHO 等官方來源列為可延伸資料來源，並要求在 Source Registry 中記錄法域、授權、更新頻率與可信度。

可納入的國際資料包括：

| 來源             | 內容                                      | 用途          |
| -------------- | --------------------------------------- | ----------- |
| IMO            | GHG Strategy、MEPC 決議、Net-Zero Framework | 國際航運減碳規範    |
| EU / EEA / EEX | EU ETS 航運規則、拍賣資料、碳市場資料                  | 歐盟碳定價與航運納管  |
| 新加坡 MPA        | Port Marine Circular、港口政策通告             | 港口治理與政策比較   |
| WHO            | 疫情通報、Disease Outbreak News              | 船舶航線與公共衛生風險 |
| 中央氣象署          | 氣象、海象、颱風、觀測資料                           | 動態風險與預測模型輸入 |

## 4. 內部資料與人工補充資料

除外部公開資料外，系統也可以納入內部文件、專家補充資料、會議紀錄、歷史報告、SOP、模擬結果與模型輸出。這類資料可用於補足組織脈絡與決策規則，但需要標記權限、審核狀態與可否對外引用。

---

# 二、資料怎麼進來

資料進入系統時，應先經過**來源登錄、擷取、格式封裝、版本標記與變更偵測**。三份文件共同指向一個設計：不同資料來源雖然形式不同，但進入系統後應統一轉成標準化的 RawDocument 或 Source Registry Entry，再進入後續治理與處理流程。

## 1. 先建立 Source Registry

每個資料來源進入系統前，必須先在 Source Registry 登錄。第二份文件指出，Source Registry 是整個接口層的入口，需記錄來源識別碼、發布者、法域、授權、更新頻率、存取方式、權威性、適用任務、是否含 PII 與 provenance 粒度。

建議登錄欄位如下：

```json
{
  "source_id": "imarine_container_api",
  "source_name": "iMarine 臺灣數據統計",
  "publisher": "交通部航港局",
  "source_type": "statistics_api",
  "jurisdiction": "TW",
  "license_type": "government_open_data",
  "access_method": "REST API",
  "update_frequency": "irregular",
  "trust_score": 90,
  "attribution_required": true,
  "provenance_level": "record"
}
```

## 2. 依來源型態採不同擷取方式

第三份文件將資料接入方式整理為 REST API、ZIP 批次下載、爬蟲、PDF 解析、RSS 訂閱與人工上傳等模式。

| 來源類型                  | 擷取方式     | 排程建議    | 技術方式                          |
| --------------------- | -------- | ------- | ----------------------------- |
| iMarine 貨櫃統計 JSON API | 批次拉取     | 每日或每週   | HTTP GET + JSON parse         |
| 全國法規資料庫               | 整包下載     | 每週或事件驅動 | ZIP download + XML/JSON parse |
| IMO / EU / MPA 文件     | 爬蟲與人工上傳  | 事件驅動    | Scraper + PDF parser          |
| 海運新聞與時事               | RSS / 爬蟲 | 即時或每日   | RSS feed + dedupe             |
| 氣象與動態資料               | API 串接   | 小時級或日級  | API polling                   |
| 專家補充文件                | 人工上傳     | 隨需      | Manual Upload API             |

## 3. 統一封裝成 RawDocument

為了避免各模組直接處理不同格式的原始資料，第三份文件建議定義 `IngestionConnector` 抽象介面，每個來源都實作 `fetch() → RawDocument`，將異質資料先收斂成一致格式。

建議 RawDocument 格式如下：

```json
{
  "source_id": "law_moj_zip",
  "source_module": "航港法令",
  "source_type": "regulation",
  "source_url": "https://law.moj.gov.tw/",
  "raw_format": "zip_xml",
  "content_pointer": "s3://raw/law/20260702.zip",
  "fetched_at": "2026-07-02T10:00:00+08:00",
  "source_version": "PCode:K0080001; revised_at:2023-12-06",
  "checksum": "sha256..."
}
```

## 4. 更新與變更偵測

資料進入時不應每次全量重建。第一份文件建議依來源類型設定不同同步策略：法規、公報、FAQ 可每日巡檢或事件觸發；官方統計依來源 cadence 拉取；新聞與研究可每小時至每日更新；內部文件則在上傳後進入審核流程。

整合後的做法：

```text
定期來源 → cron 拉取 → checksum 比對 → 有變更才重處理
事件來源 → webhook / change detection → 觸發增量 ETL
人工來源 → 上傳 → 權限與審核 → 通過後入庫
法規來源 → 版本比對 → 建立 superseded 關係
```

---

# 三、資料怎麼處理

資料處理分為兩條主線：第一條是**資料治理與標準化**，第二條是**RAG 檢索與證據封裝**。三份文件的共同結論是，LLM 不應直接讀 raw data，也不應直接讀未治理的 top-k chunks；所有資料都要先經過解析、正規化、metadata 補齊、policy gate、chunking、embedding/indexing、retrieval、rerank、conflict check，最後封裝成 Evidence Package。第二份文件明確提出七段式流程：「來源登錄 → 正規化 → 元資料增豐 → Chunking → Embedding/Indexing → Policy Gate → Evidence Packaging」。

## 1. 格式解析與正規化

原始資料可能是 JSON、CSV、XML、HTML、PDF、掃描 PDF、新聞網頁、法規 ZIP、Excel 或人工上傳文件。第一份文件建議先做檔案辨識、OCR 條件判斷、語言與編碼標準化、結構抽取、清洗與正規化，再進入切段與索引。

處理項目包括：

| 處理項目   | 說明                                         |
| ------ | ------------------------------------------ |
| 格式辨識   | 判斷 JSON、CSV、PDF、HTML、XML、ZIP、DOCX          |
| OCR 分流 | 原生 PDF 直接抽字；掃描 PDF 才進 OCR                  |
| 編碼標準化  | 統一 UTF-8，處理 Big-5 造字區與特殊字元                 |
| 語言標記   | zh-TW、en、混語比例                              |
| 時間標準化  | 統一 Asia/Taipei / ISO 8601                  |
| 單位標準化  | TEU、GT、CO2e、港口代碼、座標                        |
| 結構保留   | 表格、頁碼、條號、章節、註腳、附件                          |
| 去重     | checksum、標題相似度、URL canonicalization        |
| 版本化    | 法規 PCode、修正日期、source_version、superseded_by |

## 2. Metadata Enrichment

元資料不只是檔案描述，而是後續檢索、引用、版本控制與稽核的基礎。第一份文件提出完整 metadata 欄位，如 document_id、source_type、issuing_body、published_at、effective_at、version、original_url、file_hash、access_level、credibility_score、chunk_id、page_no、section_path、retrieval_score、rerank_score、contradiction_flag 等。

整合後，每筆可索引資料至少需要：

```json
{
  "document_id": "LAW-K0080001-20231206",
  "chunk_id": "LAW-K0080001-art12-0003",
  "source_id": "law_moj",
  "source_type": "regulation",
  "title": "商港法",
  "issuing_body": "主管機關",
  "jurisdiction": "TW",
  "published_at": "2023-12-06",
  "effective_at": "2023-12-06",
  "version": "2023-12-06",
  "section_path": "第12條",
  "original_url": "https://law.moj.gov.tw/...",
  "file_hash": "sha256...",
  "credibility_score": 96,
  "access_level": "public",
  "review_status": "verified"
}
```

## 3. Policy Gate

第二份文件強調，Policy Gate 應該在索引時與查詢時都存在，處理「能不能進庫」與「能不能被這個任務拿來回答」。

Policy Gate 應檢查：

| Gate          | 檢查內容                            | 輸出                          |
| ------------- | ------------------------------- | --------------------------- |
| 來源登錄          | source_id 是否存在於 Source Registry | allow / deny                |
| 授權相容          | 是否可納入 RAG 與下游輸出                 | allow / review / deny       |
| 顯名完整          | 是否能產出 attribution               | allow_with_attribution      |
| 個資風險          | 是否含 PII 或敏感資訊                   | allow_with_redaction / deny |
| 法域適用          | 是否適用於目標法域與任務                    | allow / review              |
| 新鮮度           | 是否超出資料有效期                       | allow / stale_for_task      |
| provenance 粒度 | 是否可追溯到頁、段、條、列                   | allow / review              |
| 衝突資訊          | 是否有 hard conflict               | review                      |
| 保存期限          | 是否已超出 retention                 | deny                        |

Policy Gate 的輸出建議採固定欄位：

```json
{
  "policy_verdict": "allow_with_attribution",
  "policy_reason_codes": ["ATTRIBUTION_REQUIRED"],
  "publication_ready": true
}
```

## 4. Chunking：切段與結構化

第三份文件建議 MVP 預設 recursive character splitting，400–512 token、10–20% overlap；法規文件則採結構感知切分，依條、項、款保留語意邊界。
第一份文件也指出，法規、FAQ、新聞、政策白皮書、表格附件不應使用同一切段策略。

整合版切段規則：

| 文件類型      | 切段方式           | 建議                 |
| --------- | -------------- | ------------------ |
| 法規條文      | 條／項／款為原子       | 不跨條切分              |
| 政策白皮書     | 依章節、小節、段落      | 400–700 tokens     |
| 新聞與公告     | 依段落與引言         | 250–500 tokens     |
| FAQ / 解釋令 | 一問一答           | 200–500 tokens     |
| 表格資料      | 保留表頭與列資料       | 另存 table_json      |
| 統計資料      | 結構化欄位 + 自然語言摘要 | 支援 RAG 與 dashboard |

## 5. Embedding / Indexing

資料切段後，需同時建立語意檢索與關鍵詞檢索能力。第一份文件與第二份文件都建議政策與法規場景不要只做 dense retrieval，而要採 hybrid retrieval，即 dense vector + BM25 / sparse / metadata filter。

MVP 可採：

```text
PostgreSQL
+ pgvector
+ full-text index
+ metadata filter
+ BGE-M3 embedding
```

建議索引內容包括：

| 索引                     | 用途                |
| ---------------------- | ----------------- |
| metadata index         | 依來源、法域、日期、版本、權限過濾 |
| full-text / BM25 index | 查法條號、港名、船名、政策術語   |
| vector index           | 語意檢索與跨語查詢         |
| table index            | 統計與表格查詢           |
| provenance index       | 回指原文頁碼、段落、條文      |

## 6. Retrieval、Rerank、Conflict Check

查詢時流程如下：

```text
User Query
→ Query Understanding
→ Metadata Filter
→ Hybrid Retrieval
→ Rerank
→ Deduplicate
→ Conflict Check
→ Evidence Selection
→ Evidence Package
```

第一份文件建議使用 BM25、dense retrieval、RRF 或加權融合，再用 cross-encoder / reranker 對候選段重排，並在送進 LLM 前進行去重與衝突偵測。

## 7. Evidence Packaging

Evidence Package 是整合後最重要的處理結果。第二份文件明確指出，Evidence Packaging 應在 top-k chunk 進 prompt 前完成 evidence selection、去重、衝突檢測、citation 組裝、粒度降噪與順序重排，必要欄位至少包括任務型別、claim scope、證據清單、citation、conflict flag、confidence、freshness、policy verdict 與 generation instructions。

建議格式：

```json
{
  "package_type": "PolicyReportEvidencePackage",
  "task_type": "policy_report",
  "query": "航港局如何因應 IMO Net-Zero Framework",
  "evidence_items": [
    {
      "evidence_id": "ev_001",
      "chunk_id": "IMO-MEPC83-0001",
      "source_id": "imo_official",
      "title": "IMO Net-Zero Framework",
      "text": "...",
      "locator": {
        "page": 3,
        "section": "MEPC 83"
      },
      "published_at": "2025-04-11",
      "policy_status": "pending_adoption",
      "credibility_score": 95
    }
  ],
  "conflict_flag": "none",
  "confidence": 0.88,
  "policy_verdict": "allow",
  "generation_instruction": {
    "must_cite": true,
    "state_uncertainty_if_conflict": true
  }
}
```

---

# 四、資料怎麼出去

資料出去時分成三個層級：**內部接口輸出、LLM/RAG 生成輸出、下游模組交換輸出**。第二份文件指出，數據接口層的核心輸出應是 `Governed Chunk Record`、`Retrieval Bundle`、`Evidence Package`，其中 Evidence Package 是下游 LLM 唯一應直接消費的物件。 第三份文件則補充，最終可輸出結構化報告 JSON、Markdown/PDF 與下游 API，並採「推論輸出＋metadata」雙欄位契約。

## 1. 內部接口輸出

內部輸出主要供系統模組、索引、檢索與稽核使用。

| 輸出物                   | 產生時機     | 使用者                          |
| --------------------- | -------- | ---------------------------- |
| Source Registry Entry | 新來源接入時   | 治理層、排程器、稽核                   |
| Canonical Document    | 原始資料正規化後 | chunking、metadata enrichment |
| Governed Chunk Record | 索引建置時    | 向量庫、全文索引、稽核                  |
| Retrieval Bundle      | 查詢時      | reranker、evidence packager   |
| Evidence Package      | 報告生成前    | LLM、dashboard、API            |
| Decision Log Envelope | 決策回寫時    | 決策層、回訓資料管線                   |

整合版資料流可寫成：

```text
Raw Source Asset
→ Canonical Document
→ Governed Chunk Record
→ Retrieval Bundle
→ Evidence Package
→ LLM / Agent / Dashboard Input
```

## 2. LLM/RAG 報告輸出

LLM 報告生成器只讀取 Evidence Package，並輸出可追溯的政策輔助報告。

建議報告輸出格式：

```json
{
  "report_id": "RPT-20260702-001",
  "topic": "航港局如何因應 IMO Net-Zero Framework",
  "generated_at": "2026-07-02T10:30:00+08:00",
  "model_version": "model-adapter-v1",
  "sections": {
    "background": {
      "text": "...",
      "citations": ["ev_001", "ev_002"]
    },
    "policy_basis": {
      "text": "...",
      "citations": ["ev_003"]
    },
    "international_cases": {
      "text": "...",
      "citations": ["ev_004"]
    },
    "recommendations": {
      "text": "...",
      "citations": ["ev_005"]
    }
  },
  "source_list": [
    {
      "evidence_id": "ev_001",
      "source_name": "IMO",
      "url": "...",
      "date": "2025-04-11",
      "locator": "page 3"
    }
  ],
  "confidence": {
    "faithfulness_score": 0.92,
    "conflict_flag": "none",
    "flagged_sections": []
  }
}
```

可輸出形式：

| 輸出格式            | 用途                |
| --------------- | ----------------- |
| JSON            | API 串接、儀表板渲染、系統保存 |
| Markdown        | 快速報告、人工編修         |
| PDF             | 主管閱讀、比賽展示、正式交付    |
| HTML            | Web dashboard     |
| JSONL audit log | 稽核、回溯、錯誤分析        |

## 3. 下游模組輸出契約

第三份文件建議對其他模組採「推論輸出＋metadata」雙欄位設計，讓 RL 排程、旁泊位建議、疫情追溯、儀表板與 LLM/RAG 能使用一致的來源與信心資訊。

通用契約：

```json
{
  "output": {
    "type": "policy_impact",
    "value": "碳定價政策可能提高高排放航線成本權重"
  },
  "metadata": {
    "source_ids": ["imo_official", "eu_ets"],
    "evidence_ids": ["ev_001", "ev_002"],
    "confidence": 0.86,
    "model_version": "rag-policy-v1",
    "generated_at": "2026-07-02T10:30:00+08:00",
    "policy_verdict": "allow_with_attribution"
  }
}
```

不同下游模組的輸出對接方式：

| 下游模組           | 數據接口層輸出                          |
| -------------- | -------------------------------- |
| UI / 儀表板       | 報告摘要、來源清單、風險標示、confidence        |
| RL 排程          | 政策衝擊參數、成本權重變化、來源依據               |
| EPP 旁泊位建議      | 法規限制、港口條件、作業建議依據                 |
| ConvoLSTM 氣象預測 | 標準化氣象資料、預測結果 metadata            |
| 疫情追溯與警報        | WHO / 航線 / 港口風險 evidence package |
| LLM 報告生成器      | Evidence Package                 |
| 決策層            | Decision Log、採納建議、回饋紀錄           |

## 4. 稽核與驗證輸出

第一份文件建議生成後執行 policy consistency checker，檢查報告中的「應、不得、須、可、建議」句是否有 evidence bundle 支撐，並標記 unsupported claim、low authority support、temporal conflict 等問題。

因此每次輸出應同步保存：

```json
{
  "query": "...",
  "retrieved_chunks": ["chunk_001", "chunk_002"],
  "rerank_result": ["chunk_002", "chunk_001"],
  "evidence_package_id": "EP-20260702-001",
  "prompt_version": "policy-report-prompt-v3",
  "model_version": "model-adapter-v1",
  "output_report_id": "RPT-20260702-001",
  "validation": {
    "citation_coverage": 0.94,
    "faithfulness_score": 0.91,
    "unsupported_claim_count": 0,
    "temporal_conflict_count": 0
  }
}
```
