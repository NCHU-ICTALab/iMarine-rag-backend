"""報告模版：把「報告」抽象成一組章節 + 生成指引，可持續擴充不同模版。

NotebookLM 式產報告：使用者選來源 + 輸入需求 + 挑模版 → 依模版章節產出結構化報告。
每個模版定義章節（key/label/guide）；prompt 由 build_report_prompt 依模版動態組出，
要求 LLM 只輸出對應 key 的 JSON，report.generate_report 再依模版章節解析。
"""

from dataclasses import dataclass

from ..evidence.packaging import EvidencePackage
from .prompts import _fmt_locator


@dataclass
class ReportSection:
    key: str
    label: str
    guide: str


@dataclass
class ReportTemplate:
    id: str
    label: str
    description: str
    sections: list[ReportSection]
    instruction: str = ""          # 模版專屬開頭指引；空則用通用開頭
    honor_user_prompt: bool = False  # free 型：以使用者需求主導內容


_WRITING_RULES = """寫作規則：
- 以正式書面中文撰寫。
- 每個具體事實或法律主張，句尾標註來源，例如 [ev_001]；可標多個 [ev_001][ev_003]。
- 只能依據提供的證據，不可捏造條文、數字或來源；證據不足的章節如實說明。
- 只輸出一個 JSON 物件，不要任何說明或 markdown 標記。"""


POLICY_BRIEF = ReportTemplate(
    id="policy_brief",
    label="政策輔助報告（四章節）",
    description="背景 / 政策法源依據 / 國際案例 / 建議事項，適合完整政策評估。",
    sections=[
        ReportSection("background", "背景說明", "議題的脈絡與現況。"),
        ReportSection("policy_basis", "政策法源依據", "相關商港法條文與航港局規定。"),
        ReportSection("international_cases", "國際案例與動態",
                      "可參照的國際或他國作法；證據不足可簡述資料有限。"),
        ReportSection("recommendations", "建議事項", "具體可行的政策建議。"),
    ],
)

NEWS_DIGEST = ReportTemplate(
    id="news_digest",
    label="重點摘要（速報）",
    description="重點摘要 + 關鍵動態條列 + 影響研判，適合快速掌握議題。",
    sections=[
        ReportSection("summary", "重點摘要", "三到五句話濃縮議題核心。"),
        ReportSection("highlights", "關鍵動態", "以條列（每列一項）整理重要事實與數據。"),
        ReportSection("implications", "影響研判", "對臺灣航港的可能影響與後續觀察點。"),
    ],
)

# 仿交通部運研所《國際海運減碳趨勢與貨櫃運輸因應探討》委託研究報告結構
MARITIME_POLICY_RESEARCH = ReportTemplate(
    id="maritime_policy_research",
    label="航港政策研究報告（六段）",
    description="前言 / 國際規範趨勢 / 國際港口案例 / 我國現況 / 課題與挑戰 / 結論建議，仿委託研究報告，適合長官政策研究報告。",
    sections=[
        ReportSection("preface", "前言", "研究背景、動機與範圍。"),
        ReportSection("intl_regulation", "國際規範與趨勢",
                      "IMO 與國際組織的相關公約、規範及政策/減碳趨勢。"),
        ReportSection("intl_cases", "國際港口與產業案例",
                      "新加坡、歐盟等港口或先進國家的具體作法；證據不足可簡述資料有限。"),
        ReportSection("domestic_status", "我國現況",
                      "臺灣航商與港口目前的因應現況與推動進度。"),
        ReportSection("challenges", "課題與挑戰",
                      "我國面臨的關鍵課題、落差與待解問題。"),
        ReportSection("conclusion", "結論與建議", "綜整結論並提出具體可行建議。"),
    ],
)

# 仿航港局《國際海事公約及趨勢動態掌握與因應分析》情報動態結構
MARITIME_INTEL_BRIEF = ReportTemplate(
    id="maritime_intel_brief",
    label="國際海事動態分析（五段）",
    description="國際要聞 / 重點會議議題 / 對我國影響 / 建議事項 / 後續追蹤，仿航港局海事公約動態分析，適合定期情報彙整。",
    sections=[
        ReportSection("intl_news", "國際海事要聞",
                      "近期 IMO 與國際海事的重要動態與要聞。"),
        ReportSection("key_meetings", "重點會議與議題摘要",
                      "重要委員會會議或議題的重點與摘要。"),
        ReportSection("impact_tw", "對我國之影響",
                      "上述動態對臺灣航港政策、航商與港口的影響研判。"),
        ReportSection("recommendations", "建議事項", "因應上述動態的具體建議。"),
        ReportSection("followup", "後續追蹤", "值得持續關注的議題與後續期程。"),
    ],
)

FREE = ReportTemplate(
    id="free",
    label="自由格式（依需求）",
    description="不固定章節，完全依使用者輸入的需求與指定結構產出。",
    sections=[ReportSection("body", "報告內容", "依使用者需求自行組織內容與小標。")],
    honor_user_prompt=True,
)

TEMPLATES: dict[str, ReportTemplate] = {
    t.id: t for t in (
        POLICY_BRIEF, MARITIME_POLICY_RESEARCH, MARITIME_INTEL_BRIEF, NEWS_DIGEST, FREE,
    )
}
DEFAULT_TEMPLATE = "policy_brief"


def get_template(template_id: str | None) -> ReportTemplate:
    return TEMPLATES.get(template_id or DEFAULT_TEMPLATE, POLICY_BRIEF)


def list_templates() -> list[dict]:
    return [
        {"id": t.id, "label": t.label, "description": t.description}
        for t in TEMPLATES.values()
    ]


def build_report_prompt(
    package: EvidencePackage, template: ReportTemplate, user_prompt: str | None = None
) -> str:
    evidence_block = "\n\n".join(
        f"[{e.evidence_id}] {e.title}　{_fmt_locator(e.locator)}\n{e.text}"
        for e in package.evidence_items
    )
    section_lines = "\n".join(f"- {s.key}（{s.label}）：{s.guide}" for s in template.sections)
    json_keys = ", ".join(f'"{s.key}": "..."' for s in template.sections)
    header = template.instruction or (
        "你是 iMarine 的航港政策分析師，需依據提供的證據撰寫一份結構化報告。"
    )
    need = user_prompt.strip() if (template.honor_user_prompt and user_prompt) else package.query

    return f"""{header}

報告分為以下章節：
{section_lines}

{_WRITING_RULES}
輸出格式：{{{json_keys}}}

---

報告主題 / 需求：{need}

可用證據（共 {len(package.evidence_items)} 筆）：

{evidence_block}

---

請依上述章節輸出 JSON："""
