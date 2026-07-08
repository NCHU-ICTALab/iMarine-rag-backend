"""iMarine 政策助理 — Streamlit 介面（對話 / 報告產出 / 知識庫管理）。"""

import asyncio
import concurrent.futures

import streamlit as st

st.set_page_config(page_title="iMarine 政策助理", page_icon="⚓", layout="wide")


# ── helpers ───────────────────────────────────────────────────────────────

def run_async(coro):
    """在獨立 thread 跑 asyncio.run()，避免與 Streamlit uvloop 衝突。
    NullPool 確保每次都是全新連線，不會有 loop 綁定問題。"""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result(timeout=600)


@st.cache_resource(show_spinner="載入 Embedding 模型…")
def load_embed():
    from src.rag_agent.indexing.embedding import warmup
    warmup()
    return True


# ── pipeline ─────────────────────────────────────────────────────────────

async def _retrieve_and_pack(query: str, top_k: int, task_type: str):
    from src.rag_agent.db.session import AsyncSessionLocal
    from src.rag_agent.evidence.packaging import build_package
    from src.rag_agent.generation.query_rewrite import rewrite_queries
    from src.rag_agent.indexing.retrieval import multi_retrieve
    queries = rewrite_queries(query)
    async with AsyncSessionLocal() as session:
        chunks = await multi_retrieve(session, queries, top_k=top_k)
    return build_package(query, chunks, task_type=task_type)


async def _retrieve_chunks(query, top_k):
    from src.rag_agent.db.session import AsyncSessionLocal
    from src.rag_agent.generation.query_rewrite import rewrite_queries
    from src.rag_agent.indexing.retrieval import multi_retrieve
    queries = rewrite_queries(query)
    async with AsyncSessionLocal() as session:
        return await multi_retrieve(session, queries, top_k=top_k)


def plan_chat_turn(history, user_msg, top_k):
    """代理規劃迴圈：模型自行決定要不要查、查什麼，回傳累積證據。"""
    load_embed()
    from src.rag_agent.generation.agent import plan_turn
    return plan_turn(history, user_msg, lambda q: run_async(_retrieve_chunks(q, top_k)))


def record_chat(user_msg, plan, answer):
    """把代理這一輪封裝成 Evidence Package 與結果並寫入稽核。"""
    from src.rag_agent.audit.recorder import record
    from src.rag_agent.generation.agent import finalize_turn

    pkg, result = finalize_turn(user_msg, plan, answer)
    record(user_msg, pkg, result)
    return pkg, result, result.cited_ids


def run_report(topic, top_k, max_tokens, temperature):
    load_embed()
    package = run_async(_retrieve_and_pack(topic, top_k, "report_generation"))
    from src.rag_agent.generation.report import generate_report
    report = generate_report(package, max_new_tokens=max_tokens, temperature=temperature)
    from src.rag_agent.audit.recorder import record_report
    record_report(topic, package, report)
    return package, report


# ── KB 管理 async 包裝 ────────────────────────────────────────────────────

async def _list_sources():
    from src.rag_agent.db.queries import list_sources
    from src.rag_agent.db.session import AsyncSessionLocal
    async with AsyncSessionLocal() as s:
        return await list_sources(s)


async def _toggle_source(sid, enabled):
    from src.rag_agent.db.queries import set_source_enabled
    from src.rag_agent.db.session import AsyncSessionLocal
    async with AsyncSessionLocal() as s:
        await set_source_enabled(s, sid, enabled)


async def _browse(src_type, search):
    from src.rag_agent.db.queries import browse_chunks
    from src.rag_agent.db.session import AsyncSessionLocal
    async with AsyncSessionLocal() as s:
        return await browse_chunks(s, src_type, search)


async def _run_ingest():
    from src.rag_agent.db.session import AsyncSessionLocal
    from src.rag_agent.ingestion.pipeline import run_full_ingest
    async with AsyncSessionLocal() as s:
        return await run_full_ingest(s)


async def _reembed_all():
    from src.rag_agent.db.session import AsyncSessionLocal
    from src.rag_agent.indexing.embedding import reembed_all
    async with AsyncSessionLocal() as s:
        return await reembed_all(s)


# ── render helpers ────────────────────────────────────────────────────────

def _turn_badge(searched: bool, n: int) -> str:
    return f"🔍 檢索了 {n} 次" if searched else "💬 直接回答（未檢索）"


def render_evidence(pkg_dict: dict, result_dict: dict):
    cited = set(result_dict["cited_ids"])
    cols = st.columns(4)
    cols[0].metric("信心分數", f"{pkg_dict['confidence']:.0%}")
    cols[1].metric("引用覆蓋率", f"{result_dict['citation_coverage']:.0%}")
    cols[2].metric("引用 / 總計", f"{len(cited)} / {len(pkg_dict['evidence_items'])}")
    if result_dict.get("tokens_per_sec", 0) > 0:
        cols[3].metric("生成速度", f"{result_dict['tokens_per_sec']:.1f} tok/s")
    with st.expander("📄 參考來源", expanded=False):
        for ev in pkg_dict["evidence_items"]:
            eid = ev["evidence_id"]
            icon = "✅" if eid in cited else "⬜"
            loc = ev["locator"].get("article") or ev["locator"].get("section") or ""
            st.markdown(f"{icon} **[{eid}]** {ev['title']} {loc}")
            st.caption(ev["text"][:200].replace("\n", " ") + f"…\n\n🔗 {ev['source_url']}")


# ── 側邊欄 ────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("⚙️ 設定")
    from src.rag_agent.generation import provider
    _cfg = provider.current()
    st.caption(f"🧠 目前模型：**{_cfg.model}**\n\n{_cfg.provider}")
    st.divider()
    top_k = st.slider("檢索筆數", 3, 12, 6)
    max_tokens = st.slider("最大回答長度", 256, 1024, 512, step=128)
    temperature = st.slider("Temperature", 0.0, 1.0, 0.0, step=0.05,
                            help="0 = greedy（最快最確定）")
    st.divider()
    if st.button("🗑️ 清除對話"):
        st.session_state.messages = []
        st.rerun()
    st.divider()
    st.subheader("最近查詢")
    try:
        from src.rag_agent.audit.recorder import read_logs
        for lg in reversed(read_logs(limit=5)):
            tag = "📊" if lg.get("task_type") == "report_generation" else "🕐"
            st.caption(f"{tag} {lg['ts'][:16].replace('T', ' ')}\n\n_{lg['query'][:38]}_")
    except Exception:
        st.caption("（尚無紀錄）")


# ── 分頁 ──────────────────────────────────────────────────────────────────

tab_chat, tab_report, tab_kb, tab_settings = st.tabs(
    ["💬 助理對話", "📊 報告產出", "📚 知識庫管理", "⚙️ 模型設定"]
)


# ══ 對話頁 ════════════════════════════════════════════════════════════════

with tab_chat:
    st.markdown("#### ⚓ iMarine 政策助理")
    st.caption("會記得對話上下文；由助理自己判斷要不要查知識庫，回答政策事實時才標示來源。")

    if "messages" not in st.session_state:
        st.session_state.messages = []

    # 顯示歷史對話
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            if msg["role"] == "assistant" and "searched" in msg:
                st.caption(_turn_badge(msg["searched"], msg.get("n_search", 0)))
            st.markdown(msg["content"])
            if msg["role"] == "assistant" and "meta" in msg:
                render_evidence(msg["meta"]["package"], msg["meta"]["result"])

    prompt = st.chat_input("請輸入問題，例如：港區停泊有哪些規定？")

    if prompt:
        history = list(st.session_state.messages)      # 本輪之前的對話
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            from src.rag_agent.generation.agent import stream_answer

            try:
                with st.spinner("思考中（判斷是否需要查資料）…"):
                    plan = plan_chat_turn(history, prompt, top_k)
            except Exception as e:
                st.error(f"發生錯誤：{e}")
                st.stop()

            st.caption(_turn_badge(plan.searched, len(plan.search_queries)))

            try:
                answer = st.write_stream(
                    stream_answer(history, prompt, plan.evidence_items,
                                  max_tokens, temperature)
                )
            except Exception as e:
                st.error(f"發生錯誤：{e}")
                st.stop()

            pkg, result, _ = record_chat(prompt, plan, answer)

            new_msg = {
                "role": "assistant", "content": answer,
                "searched": plan.searched, "n_search": len(plan.search_queries),
            }
            if plan.evidence_items:
                meta = {
                    "package": pkg.to_dict(),
                    "result": {
                        "citation_coverage": result.citation_coverage,
                        "cited_ids": result.cited_ids,
                        "uncited_ids": result.uncited_ids,
                        "tokens_per_sec": 0.0,
                    },
                }
                render_evidence(meta["package"], meta["result"])
                new_msg["meta"] = meta

            st.session_state.messages.append(new_msg)
        st.rerun()


# ══ 報告產出頁 ════════════════════════════════════════════════════════════

with tab_report:
    st.markdown("#### 📊 政策輔助報告產出")
    st.caption("輸入政策議題，依商港法規與航港局動態產出四章節結構化報告，每段皆標示來源。")

    topic = st.text_input(
        "報告議題",
        placeholder="例如：航港局如何因應船舶碳排相關規範？",
        key="report_topic",
    )
    report_tokens = st.slider("報告長度上限", 512, 2048, 1024, step=256, key="report_len")

    if st.button("🚀 產出報告", type="primary", disabled=not topic.strip()):
        with st.spinner("🔍 檢索知識庫 → 🤖 撰寫四章節報告…"):
            try:
                package, report = run_report(topic.strip(), top_k, report_tokens, temperature)
                st.session_state["last_report"] = (package.to_dict(), report)
            except Exception as e:
                st.error(f"發生錯誤：{e}")
                st.stop()

    if "last_report" in st.session_state:
        pkg_dict, report = st.session_state["last_report"]
        st.divider()

        c = st.columns(3)
        c[0].metric("報告編號", report.report_id)
        c[1].metric("引用覆蓋率", f"{report.citation_coverage:.0%}")
        c[2].metric("引用來源數", len(report.source_list))

        from src.rag_agent.generation.report import SECTIONS, SECTION_LABELS
        for name in SECTIONS:
            sec = report.sections.get(name, {})
            st.markdown(f"##### {SECTION_LABELS[name]}")
            st.markdown(sec.get("text", "（無內容）"))
            if sec.get("citations"):
                st.caption("引用：" + " ".join(f"[{c}]" for c in sec["citations"]))
            st.write("")

        with st.expander("📄 參考來源清單", expanded=False):
            for s in report.source_list:
                loc = f"　{s['locator']}" if s.get("locator") else ""
                st.markdown(f"**[{s['evidence_id']}]** {s['source_name']}{loc}")
                st.caption(f"🔗 {s.get('url', '')}")

        st.download_button(
            "⬇️ 下載 Markdown",
            report.to_markdown(),
            file_name=f"{report.report_id}.md",
            mime="text/markdown",
        )


# ══ 知識庫管理頁 ══════════════════════════════════════════════════════════

with tab_kb:
    st.markdown("#### 📚 知識庫管理")

    # ── 來源管理 ──
    st.markdown("##### 資料來源")
    col_a, col_b = st.columns([3, 1])
    with col_b:
        if st.button("🔄 重新抓取 / 更新", help="抓取商港法與航港局新聞稿並建立索引"):
            with st.spinner("抓取 → 切段 → 向量化…（首次含模型載入）"):
                try:
                    load_embed()
                    stats = run_async(_run_ingest())
                    st.success(
                        f"新增文件 {stats['docs_fetched']}、chunk {stats['chunks_added']}、"
                        f"向量化 {stats['chunks_embedded']}"
                    )
                    st.session_state.pop("kb_cache_key", None)
                except Exception as e:
                    st.error(f"抓取失敗：{e}")

    try:
        sources = run_async(_list_sources())
    except Exception as e:
        st.error(f"讀取來源失敗：{e}")
        sources = []

    type_label = {"regulation": "🔵 法規", "news": "🟡 新聞稿", "alt_energy": "🟢 替代能源"}
    for src in sources:
        c1, c2, c3 = st.columns([4, 2, 1])
        with c1:
            st.markdown(f"**{src['source_name']}**")
            st.caption(f"{src['publisher']}｜{type_label.get(src['source_type'], src['source_type'])}"
                       f"｜可信度 {src['trust_score']}")
        with c2:
            st.markdown(f"chunk 數：**{src['chunk_count']}**")
            st.caption(f"階段：{src['phase']}")
        with c3:
            new_val = st.toggle("啟用", value=src["enabled"], key=f"tg_{src['source_id']}",
                                help="停用後檢索不再納入此來源")
            if new_val != src["enabled"]:
                run_async(_toggle_source(src["source_id"], new_val))
                st.rerun()

    st.divider()

    # ── chunk 瀏覽 ──
    st.markdown("##### 內容瀏覽")
    col_f1, col_f2 = st.columns([2, 3])
    with col_f1:
        src_filter = st.selectbox(
            "來源類型",
            ["全部", "regulation（法規）", "news（新聞稿）", "alt_energy（替代能源）"],
            key="kb_src")
    with col_f2:
        search_kw = st.text_input("🔍 搜尋關鍵字", key="kb_search",
                                  placeholder="標題或內文關鍵字")

    src_type = None
    if "regulation" in src_filter:
        src_type = "regulation"
    elif "news" in src_filter:
        src_type = "news"
    elif "alt_energy" in src_filter:
        src_type = "alt_energy"

    kb_cache_key = f"kb_{src_type}_{search_kw}"
    if st.session_state.get("kb_cache_key") != kb_cache_key:
        with st.spinner("查詢知識庫…"):
            try:
                st.session_state["kb_rows"] = run_async(_browse(src_type, search_kw.strip()))
                st.session_state["kb_cache_key"] = kb_cache_key
            except Exception as e:
                st.error(f"資料庫查詢失敗：{e}")
                st.session_state["kb_rows"] = []

    rows = st.session_state.get("kb_rows", [])
    st.caption(f"共 {len(rows)} 筆 chunk")

    type_icon = {"regulation": "🔵", "news": "🟡", "alt_energy": "🟢"}
    for row in rows:
        icon = type_icon.get(row["source_type"], "⚪")
        loc = row["section_path"] or ""
        with st.expander(f"{icon} **{row['title']}**　{loc}", expanded=False):
            c1, c2 = st.columns([3, 1])
            with c1:
                st.markdown(row["text"])          # 完整內容（DB 全文，非截斷）
                st.caption(f"🔗 {row['original_url']}")
            with c2:
                st.markdown(f"**類型：** {row['source_type']}")
                st.markdown(f"**Tokens：** {row['token_count']}")
                st.markdown(f"**可信度：** {row['credibility_score']}")
                st.markdown(f"**已向量化：** {'✅' if row['has_embedding'] else '❌'}")
                if row["published_at"]:
                    st.markdown(f"**發布日：** {str(row['published_at'])[:10]}")


# ══ 模型設定頁 ════════════════════════════════════════════════════════════

with tab_settings:
    from src.rag_agent.generation import provider

    st.markdown("#### ⚙️ 模型設定")
    st.caption("透過 OpenAI 相容端點串接任意模型：本地 Ollama 或各家 API 皆可。")

    cfg = provider.current()
    st.info(f"目前使用：**{cfg.provider}** ｜ 模型 **{cfg.model}** ｜ `{cfg.base_url}`")

    preset_names = list(provider.PRESETS.keys())
    preset = st.selectbox(
        "供應商", preset_names,
        index=preset_names.index(cfg.provider) if cfg.provider in preset_names else 0,
    )

    default_url = provider.PRESETS.get(preset) or cfg.base_url
    base_url = st.text_input("Base URL", value=default_url or cfg.base_url,
                             placeholder="https://api.openai.com/v1")
    api_key = st.text_input("API Key", value=cfg.api_key, type="password",
                            help="Ollama 本地可隨意填；API 供應商填實際金鑰")

    # Ollama：自動列出本機已安裝模型
    is_ollama = "11434" in base_url or "ollama" in preset.lower()
    if is_ollama:
        installed = provider.list_ollama_models(base_url)
        if installed:
            idx = installed.index(cfg.model) if cfg.model in installed else 0
            model = st.selectbox("模型（本機 Ollama 已安裝）", installed, index=idx)
        else:
            model = st.text_input("模型", value=cfg.model,
                                  help="偵測不到 Ollama，請手動輸入；或先 `ollama pull <model>`")
    else:
        model = st.text_input("模型", value=cfg.model,
                              placeholder="gpt-4o-mini / meta-llama/... / gemma2-9b-it")

    col1, col2 = st.columns(2)
    if col1.button("💾 儲存設定", type="primary"):
        provider.configure(preset, base_url, api_key, model, persist=True)
        st.success("已儲存，立即生效。")
        st.rerun()
    if col2.button("🔌 測試連線"):
        provider.configure(preset, base_url, api_key, model, persist=False)
        with st.spinner("測試中…"):
            ok, msg = provider.test_connection()
        (st.success if ok else st.error)(msg)

    # ── Embedding 設定 ──
    st.divider()
    from src.rag_agent.indexing import embedding as emb

    st.markdown("##### 🔡 Embedding 模型")
    ecfg = emb.current()
    st.info(f"目前：**{ecfg.backend}** ｜ 模型 **{ecfg.model}**"
            + (f" ｜ `{ecfg.base_url}`" if ecfg.backend == "api" else ""))

    e_backend = st.radio("後端", ["local", "api"],
                         index=0 if ecfg.backend == "local" else 1,
                         format_func=lambda x: "本地 sentence-transformers" if x == "local"
                         else "API（OpenAI 相容，如 Ollama embeddings）",
                         horizontal=True)
    if e_backend == "local":
        e_model = st.text_input("模型名稱", value=ecfg.model,
                                help="HuggingFace 模型 id，例如 google/EmbeddingGemma-300m、"
                                     "BAAI/bge-m3、intfloat/multilingual-e5-large")
        e_base_url, e_api_key = "", ""
    else:
        e_base_url = st.text_input("Base URL", value=ecfg.base_url or "http://localhost:11434/v1",
                                   key="emb_url")
        e_api_key = st.text_input("API Key", value=ecfg.api_key, type="password", key="emb_key")
        e_model = st.text_input("模型名稱", value=ecfg.model,
                                placeholder="nomic-embed-text / text-embedding-3-small")

    st.warning("⚠️ 更換 embedding 模型後，**必須重新向量化全部知識庫**，"
               "否則查詢向量與庫內向量維度/語意不符，檢索會失敗或失準。")

    ec1, ec2 = st.columns(2)
    if ec1.button("💾 儲存 Embedding 設定"):
        emb.configure(e_backend, e_model, e_base_url, e_api_key, persist=True)
        st.success("已儲存。請接著點右側「重新向量化」。")
        st.rerun()
    if ec2.button("🔁 套用並重新向量化全部", type="primary"):
        emb.configure(e_backend, e_model, e_base_url, e_api_key, persist=True)
        with st.spinner("重新編碼全部 chunk 中…（依模型與資料量而定）"):
            try:
                n, dim = run_async(_reembed_all())
                st.success(f"完成：重新向量化 {n} 筆，向量維度 {dim}。")
                st.session_state.pop("kb_cache_key", None)
            except Exception as e:
                st.error(f"重新向量化失敗：{e}")

    st.divider()
    st.caption("提示：LLM 與 Embedding 設定分別存於 `data/llm_config.json`、"
               "`data/embed_config.json`（含金鑰，注意本機檔案安全）。")
