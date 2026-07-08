"""四章節結構化政策報告生成（spec 6.3）。

依 Evidence Package 產出 background / policy_basis / international_cases /
recommendations 四章節，每章節帶引用，並回傳來源清單與規則式引用覆蓋率。
"""

import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from ..evidence.packaging import EvidencePackage
from .llm import raw_generate
from .templates import POLICY_BRIEF, ReportTemplate, build_report_prompt

logger = logging.getLogger(__name__)

# 向後相容：Streamlit 報告頁沿用四章節預設常數
SECTIONS = tuple(s.key for s in POLICY_BRIEF.sections)
SECTION_LABELS = {s.key: s.label for s in POLICY_BRIEF.sections}


@dataclass
class ReportResult:
    report_id: str
    topic: str
    generated_at: str
    sections: dict            # {name: {"text": str, "citations": [ev_id]}}
    source_list: list[dict]
    citation_coverage: float
    cited_ids: list[str]
    template_id: str = "policy_brief"
    section_order: list[str] | None = None     # 章節顯示順序（依模版）
    section_labels: dict | None = None         # {key: 中文標題}
    tokens_per_sec: float = 0.0

    def to_markdown(self) -> str:
        order = self.section_order or list(self.sections.keys())
        labels = self.section_labels or {}
        lines = [f"# 政策輔助報告：{self.topic}", "", f"_產出時間：{self.generated_at}_", ""]
        for name in order:
            sec = self.sections.get(name, {})
            lines += [f"## {labels.get(name, name)}", "", sec.get("text", "（無內容）"), ""]
        lines += ["## 參考來源", ""]
        for s in self.source_list:
            loc = f"　{s['locator']}" if s.get("locator") else ""
            lines.append(f"- **[{s['evidence_id']}]** {s['source_name']}{loc} — {s.get('url', '')}")
        return "\n".join(lines)


def generate_report(
    package: EvidencePackage,
    template: ReportTemplate | None = None,
    user_prompt: str | None = None,
    max_new_tokens: int = 1024,
    temperature: float = 0.0,
) -> ReportResult:
    tmpl = template or POLICY_BRIEF
    keys = [s.key for s in tmpl.sections]
    prompt = build_report_prompt(package, tmpl, user_prompt)
    raw, tps = raw_generate(prompt, max_new_tokens=max_new_tokens, temperature=temperature)

    section_texts = _parse_sections(raw, keys)
    all_ids = [e.evidence_id for e in package.evidence_items]

    sections: dict = {}
    cited: set[str] = set()
    for name in keys:
        text = section_texts.get(name, "").strip() or "（本章節證據不足，無法產出內容）"
        ids = [eid for eid in all_ids if re.search(rf"\[{eid}\]", text)]
        cited.update(ids)
        sections[name] = {"text": text, "citations": ids}

    source_list = [
        {
            "evidence_id": e.evidence_id,
            "source_id": e.source_id,
            "source_name": e.title,
            "url": e.source_url,
            "date": e.published_at,
            "locator": e.locator.get("article") or e.locator.get("section") or "",
        }
        for e in package.evidence_items
        if e.evidence_id in cited
    ]

    coverage = len(cited) / len(all_ids) if all_ids else 0.0
    date_str = datetime.utcnow().strftime("%Y%m%d")
    return ReportResult(
        report_id=f"RPT-{date_str}-{uuid.uuid4().hex[:4].upper()}",
        topic=user_prompt or package.query,
        generated_at=datetime.utcnow().isoformat(timespec="seconds") + "Z",
        sections=sections,
        source_list=source_list,
        citation_coverage=coverage,
        cited_ids=sorted(cited),
        template_id=tmpl.id,
        section_order=keys,
        section_labels={s.key: s.label for s in tmpl.sections},
        tokens_per_sec=tps,
    )


def _parse_sections(raw: str, keys: list[str]) -> dict:
    """解析報告章節：先嚴格 JSON；失敗（如被截斷）改逐鍵寬鬆擷取，避免全塞第一節。"""
    cleaned = re.sub(r"```(?:json)?", "", raw).strip()   # 去 markdown 圍欄
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            obj = json.loads(match.group(0))
            return {k: str(obj.get(k, "")) for k in keys}
        except json.JSONDecodeError:
            logger.warning("Report JSON parse failed, falling back to per-key extraction")

    # 寬鬆逐鍵擷取：先抓完整值 "key": "...", ；抓不到再抓被截斷、無結尾引號的尾段
    out: dict[str, str] = {}
    for k in keys:
        m = re.search(rf'"{k}"\s*:\s*"(.*?)"\s*(?:,|\}}|$)', cleaned, re.DOTALL)
        if not m:
            m = re.search(rf'"{k}"\s*:\s*"(.*)$', cleaned, re.DOTALL)
        if m:
            out[k] = m.group(1).strip().rstrip('"').rstrip(',').strip()
    if out:
        return out
    return {keys[0]: cleaned} if keys else {}
