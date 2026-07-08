from ..evidence.packaging import EvidencePackage

SYSTEM_INSTRUCTION = """你是 iMarine 的航港政策助理，專門協助解讀商港法規與航港局最新動態。

**回答方式**：
- 用清楚、口語化的中文直接回答使用者的問題
- 可以綜合多筆證據來解釋、歸納，也可以補充背景脈絡讓答案更好理解
- 每個具體的法律主張或事實，在句尾加上來源標記，例如 [ev_001]
- 如果問題超出提供的證據範圍，說明哪些部分可以回答、哪些資料不足
- 條文數字、機構名稱、日期等具體事實必須來自證據，不可自行捏造"""


def build_prompt(package: EvidencePackage) -> str:
    evidence_block = "\n\n".join(
        f"[{e.evidence_id}] {e.title}　{_fmt_locator(e.locator)}\n{e.text}"
        for e in package.evidence_items
    )

    return f"""{SYSTEM_INSTRUCTION}

---

以下是從知識庫檢索到的相關資料（共 {len(package.evidence_items)} 筆）：

{evidence_block}

---

使用者問題：{package.query}

請根據上述資料，直接回答使用者的問題。"""


def _fmt_locator(locator: dict) -> str:
    if "article" in locator:
        return f"（{locator['article']}）"
    if "section" in locator:
        return f"（{locator['section']}）"
    return ""


# ── 意圖判斷（是否需要檢索知識庫）─────────────────────────────────────────

INTENT_INSTRUCTION = """你是 iMarine 航港政策助理的「意圖路由器」。判斷使用者這句話該歸為哪一類，決定是否需要檢索法規/新聞知識庫。

分類：
- "smalltalk"：招呼、閒聊、感謝、詢問你的身份或功能（如「你好」「你能做什麼」「謝謝」）。**不需要檢索**。
- "chat_qa"：詢問商港法規、航港政策、港務、航港局動態等具體問題。**需要檢索**。
- "report_generation"：要求產出一份政策報告、彙整、書面分析（如「幫我生成一份關於…的報告」）。**需要檢索**。

只輸出一個 JSON 物件，不要加任何說明文字或 markdown 標記：
{"intent": "smalltalk|chat_qa|report_generation", "reply": "若 intent 為 smalltalk，以『iMarine 航港政策助理』的身分用一兩句親切口語中文回覆使用者（不要提到分類、意圖或路由）；否則此欄留空字串"}"""


def build_intent_prompt(query: str) -> str:
    return f"""{INTENT_INSTRUCTION}

使用者輸入：{query}

JSON："""


# ── 報告生成（四章節結構化）────────────────────────────────────────────────

REPORT_INSTRUCTION = """你是 iMarine 的航港政策分析師，需依據提供的證據撰寫一份結構化政策輔助報告。

報告固定分為四個章節：
- background（背景說明）：議題的脈絡與現況。
- policy_basis（政策法源依據）：相關商港法條文與航港局規定。
- international_cases（國際案例與動態）：可參照的國際或他國作法（若證據不足可簡述資料有限）。
- recommendations（建議事項）：具體可行的政策建議。

寫作規則：
- 每個章節都用正式書面中文撰寫。
- 每個具體事實或法律主張，句尾標註來源，例如 [ev_001]；可標多個 [ev_001][ev_003]。
- 只能依據提供的證據，不可捏造條文、數字或來源；證據不足的章節如實說明。

只輸出一個 JSON 物件，不要加任何說明或 markdown 標記，格式如下：
{"background": "...", "policy_basis": "...", "international_cases": "...", "recommendations": "..."}"""


# ── 對話代理：規劃器（決定是否呼叫檢索工具）─────────────────────────────

PLANNER_INSTRUCTION = """你是 iMarine 航港政策助理的決策核心。你有一個工具可以查詢知識庫（收錄商港法規與航港局新聞稿）。

根據對話歷史與使用者最新訊息，決定下一步。只輸出一個 JSON 物件，不要任何其他文字或說明：
- 使用者明確要求「產生／寫／彙整一份報告」等產出結構化報告的意圖時：
  {"action": "report", "topic": "把需求補成完整的報告主題", "template": "policy_brief 或 news_digest 或 free"}
- 需要查資料才能回答商港法規或航港政策的具體事實時：
  {"action": "search", "query": "把使用者的問題（含追問）補成完整、可獨立檢索的問題"}
- 已有足夠資料、或這只是打招呼／閒聊／釐清／一般常識，可以直接回答時：
  {"action": "answer"}

規則：
- 追問（如「那 2027 年呢？」）要用對話歷史補成完整問題再查。
- 若「已查到的資料」已足夠回答，就選 answer，不要重複查同樣的東西。
- 打招呼、感謝、問你是誰/會做什麼，一律選 answer。
- report 只在使用者明確要「報告／報表／彙整成文件」時才選；一般問答用 search/answer。
  模版：完整政策評估用 policy_brief、快速摘要用 news_digest、指定特殊結構用 free。"""


def build_planner_prompt(history_text, user_msg, evidence_items, done_queries):
    ev = "\n".join(
        f"[{e.evidence_id}] {e.title}{_fmt_locator(e.locator)}：{e.text[:70]}…"
        for e in evidence_items
    ) or "（尚未查到資料）"
    dq = "、".join(done_queries) or "（無）"
    return f"""{PLANNER_INSTRUCTION}

對話歷史：
{history_text}

使用者最新訊息：{user_msg}

已查到的資料：
{ev}

已查過的關鍵字（不要重複）：{dq}

JSON："""


# ── 對話代理：最終回答（事實才引用）──────────────────────────────────────

CHAT_ANSWER_INSTRUCTION = """你是 iMarine 航港政策助理。請自然、口語地跟使用者對話，並記得前面聊過的內容。

引用規則：
- 若你在回答商港法規或航港政策的「具體事實」（條號、規定、日期、機構職權等），必須根據下方資料，並在該句尾標註來源，例如 [ev_001]，可標多個。
- 不可捏造條文、數字或來源；若資料不足以支撐某個事實，就如實說「目前資料查不到」。
- 若只是打招呼、閒聊、追問釐清、給方向，就自然回話，不需要硬加引用。"""


def build_chat_answer_prompt(history_text, user_msg, evidence_items):
    ev = "\n\n".join(
        f"[{e.evidence_id}] {e.title}{_fmt_locator(e.locator)}\n{e.text}"
        for e in evidence_items
    ) or "（本輪未檢索任何資料）"
    return f"""{CHAT_ANSWER_INSTRUCTION}

對話歷史：
{history_text}

可用資料：
{ev}

使用者最新訊息：{user_msg}

請直接回覆使用者（繁體中文）："""


def format_history(messages, max_turns: int = 6) -> str:
    """把對話訊息格式化成 prompt 用的歷史文字（只取最近幾輪）。"""
    turns = messages[-max_turns:]
    lines = []
    for m in turns:
        role = "使用者" if m.get("role") == "user" else "助理"
        lines.append(f"{role}：{m.get('content', '')}")
    return "\n".join(lines) if lines else "（無，這是第一句）"


def build_report_prompt(package: EvidencePackage) -> str:
    evidence_block = "\n\n".join(
        f"[{e.evidence_id}] {e.title}　{_fmt_locator(e.locator)}\n{e.text}"
        for e in package.evidence_items
    )
    return f"""{REPORT_INSTRUCTION}

---

報告主題：{package.query}

可用證據（共 {len(package.evidence_items)} 筆）：

{evidence_block}

---

請輸出四章節 JSON："""
