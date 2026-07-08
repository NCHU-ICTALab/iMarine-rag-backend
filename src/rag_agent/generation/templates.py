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

FREE = ReportTemplate(
    id="free",
    label="自由格式（依需求）",
    description="不固定章節，完全依使用者輸入的需求與指定結構產出。",
    sections=[ReportSection("body", "報告內容", "依使用者需求自行組織內容與小標。")],
    honor_user_prompt=True,
)

TEMPLATES: dict[str, ReportTemplate] = {t.id: t for t in (POLICY_BRIEF, NEWS_DIGEST, FREE)}
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
