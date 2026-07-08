# MVP 速覽：LLM + RAG 政策輔助報告管線

> 本文件是 `docs/spec.md` 的 MVP 濃縮版，只保留「兩天內要做出來的東西」。完整設計理由、比較表、Phase 2/3 規劃請回頭查 `docs/spec.md`（本文件各節都會標註對應章節）。
> 對應 Issue：`docs/issues.md` Issue #1

---

## 一句話說明 MVP

**建立一條可展示、可稽核、可回放的 Evidence Pipeline**：法規/新聞進來 → 治理與切段 → embedding → 混合檢索 → 封裝成 Evidence Package → LLM 只根據這份證據回答，且每句話都能回指出處。不做知識庫平台、不做完整對話系統，只驗證這條管線走得通。（對應 spec.md 10.1）

---

## 1. 要接入哪些資料

| 來源 | 內容 | 索引方式 |
| --- | --- | --- |
| 全國法規資料庫 | 《商港法》**本文**（不含施行細則等子法規） | 全文索引，依條/項/款切分 |
| 交通部航港局新聞稿 RSS | `Information/RSS?SiteId=1&NodeId=15` | 全文索引（**主要新聞來源**） |
| MARAD Press Releases RSS | `maritime.dot.gov/taxonomy/term/36/feed` | 全文索引（國際脈絡，次要） |
| IMO Press Briefings RSS | `imo.org/en/pages/pressbriefingsrss.aspx` | **僅 metadata**（標題/摘要/URL/時間），不擷取全文——IMO 授權條款限制商用 |

**MVP 底線**：只要商港法 + 航港局新聞稿這兩項做完整，就算達標；MARAD/IMO 是加分項，來不及做不影響 MVP 成立。（對應 spec.md 3.1、10.2）

⚠️ 實作前待辦：MARAD feed URL 需在團隊環境用 `curl`/`feedparser` 實測一次（查證時被目標網站的反爬蟲擋下，但 URL 命名規則正確）。

---

## 2. 資料處理怎麼做

```text
RawDocument 建立（含 source_id / fetched_at / checksum）
  → checksum 去重
  → 格式正規化（UTF-8、ISO 8601 時間、法規條號格式）
  → 法規：依「條」為原子單位切分，不可跨條
  → 新聞：依段落切分，250–500 tokens，10–20% overlap
  → Metadata Enrichment（source_id、locator/section_path、published_at、credibility_score...）
  → 基礎 Policy Gate（來源是否登錄、授權是否相容、新鮮度）
  → content_pointer 寫入 MinIO（原始內容不進資料庫）
```

**最重要的一件事：法規 chunking 不能切錯條。** 這是整條管線準確度影響最大的環節，需要人工抽樣驗證切段結果。（對應 spec.md 5.5、10.2 第2項）

---

## 3. 技術棧（已定案，不要臨時換）

| 項目 | 選型 |
| --- | --- |
| 向量資料庫 | PostgreSQL + pgvector（不用 Qdrant/Milvus） |
| Embedding 模型 | `google/embeddinggemma-300m`，輸出維度 **768** |
| 生成模型 | `google/gemma-4-E4B-it`（直接用，不走「先上小模型」路線） |
| 推論框架 | Ollama（開發）／vLLM（正式） |
| 全文檢索 | PostgreSQL 內建 `tsvector`（**不是**真正 BM25，用詞上要注意） |
| 程式語言/框架 | Python 3.11+ + FastAPI + Pydantic + SQLAlchemy/SQLModel + Alembic |
| 物件儲存 | MinIO（S3 相容） |
| GPU | 單張 24GB 級顯卡（RTX 3090/4090） |
| 稽核紀錄 | 同一個 PostgreSQL，獨立 `audit` schema |

（對應 spec.md 第七、八節）

⚠️ 待辦：Day 1 上午盡早跑一次 `gemma-4-E4B-it` 基本推論，確認 24GB 顯存是否夠用（含 EmbeddingGemma 同時常駐），不夠再切官方量化版（`gemma-4-E4B-it-qat-*`）。

---

## 4. 檢索怎麼做

```text
Query → Metadata Filter → Hybrid Retrieval（dense + lexical）→ RRF 融合排序 → Top-K
  → Deduplicate
  → Conflict Check（MVP 先誠實標記 not_evaluated，不用假裝 none）
  → Policy Gate（查詢時）
  → Evidence Package
```

**MVP 明確不做**：cross-encoder reranker、query decomposition、multi-hop retrieval、真正 BM25、OpenSearch、Qdrant/Milvus、ACL-aware retrieval。（對應 spec.md 5.7、10.2 第4項）

---

## 5. Evidence Package 長什麼樣（最小可用欄位）

```json
{
  "evidence_package_id": "EP-20260704-0001",
  "task_type": "chat_qa",
  "query": "商港法對港區管理的依據是什麼？",
  "evidence_items": [
    {
      "evidence_id": "ev_001",
      "chunk_id": "LAW-K0080001-art12",
      "source_id": "law_moj_shipping_port_act",
      "source_type": "regulation",
      "title": "商港法",
      "text": "...",
      "locator": { "article": "第12條" },
      "retrieval_score": 0.82,
      "credibility_score": 96
    }
  ],
  "conflict_flag": "not_evaluated",
  "policy_verdict": "allow",
  "generation_instruction": {
    "answer_only_from_evidence": true,
    "must_cite": true
  }
}
```

（完整格式對應 spec.md 6.2）

---

## 6. 生成規則（不可退讓）

- 只能根據 Evidence Package 回答，不可用模型內部知識補法規內容。
- 每個具體主張都要對應一個 `evidence_id`。
- 證據不足時要老實說「資料不足」，不能自己推測。
- 不得產生不存在的條文或來源。

生成完成後跑**規則式** citation coverage 檢查（正則比對 evidence_id 是否出現）——這是 MVP 唯一要做的 Faithfulness 驗證，語意層級驗證留到 Phase 2。（對應 spec.md 7.3、7.4）

---

## 7. 對外要交付的 API

**必要（低階，其他模組/除錯都靠這幾支）：**

```text
POST /api/v1/ingest/law
POST /api/v1/ingest/news
POST /api/v1/retrieve
POST /api/v1/evidence-packages
POST /api/v1/generate
GET  /api/v1/audit/{run_id}
```

**可選（封裝層，把上面幾支串起來）：**

```text
POST /api/v1/chat
```

`/chat` 只能是 `retrieve → evidence-packages → generate` 的組合呼叫，**不可以把邏輯寫死在 chat endpoint 裡**——低階端點要能被獨立呼叫與測試。（對應 spec.md 9.3）

---

## 8. MVP 完成的判斷標準（10 條，全過才算過）

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

（對應 spec.md 10.3）

---

## 9. 明確不做的事（避免範圍蔓延）

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

（對應 spec.md 10.4）

---

## 10. 如果時間不夠，怎麼降級

優先保住這兩項，其他都可以砍：

1. **商港法的完整 End-to-End 管線**（ingest → chunk → embed → retrieve → generate，全程跑得通）
2. **Evidence Package 含 `evidence_items` 與 `locator`**（沒有這個，Grounding 就無從驗證）

Chat 多輪對話、報告模版四章節、MARAD/IMO 來源都可以先簡化或延後；就算新聞來源只做出航港局新聞稿這一項，也算達到 MVP 底線。（對應 spec.md 10.2 降級策略）
