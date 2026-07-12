"""輔助報告情境設計測試（品質 eval，非單元測試）。

對一組代表性政策題打真後端 /api/report，量化每份報告的品質並標出弱點，
用來（a）驗證 RAG→報告整條在各情境都可用、（b）抓回歸與品質問題（如單一來源撐整段）。

用法（需後端在 :8100 且知識庫已 ingest）：
    uv run python scripts/eval_report.py
    uv run python scripts/eval_report.py --base http://127.0.0.1:8100 --only imo_nzf

LLM 輸出有隨機性，門檻採「軟性」——未達標記 ✗ 供人判讀，不是 CI 硬性 gate。
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx


@dataclass
class Scenario:
    id: str
    prompt: str
    template: str
    expect_types: set[str]        # source_list 中必須出現的來源類型
    min_coverage: float = 0.6     # citation_coverage 門檻
    min_sources: int = 4          # 不同 evidence 來源數門檻


SCENARIOS: list[Scenario] = [
    Scenario("green_methanol", "台灣港口綠色甲醇加注的政策現況與建議",
             "policy_brief", {"alt_energy"}, 0.6, 4),
    Scenario("imo_nzf", "IMO 淨零框架（NZF）對台灣航商與港口的影響與因應建議",
             "policy_brief", {"alt_energy"}, 0.6, 5),
    Scenario("shipping_port_act", "商港法對商港管理與港務作業的主要規範與法律責任",
             "policy_brief", {"regulation"}, 0.5, 3),
    Scenario("shore_power", "台灣港口岸電（岸基電力）推動的政策與實踐現況",
             "policy_brief", {"alt_energy"}, 0.55, 3),
    Scenario("news_digest", "近期國際海運與替代能源的重點動態彙整",
             "news_digest", {"alt_energy"}, 0.5, 3),
    Scenario("seafarer_training", "替代燃料時代的船員培訓與教育資源規劃",
             "policy_brief", {"alt_energy"}, 0.5, 3),
]


@dataclass
class Result:
    scenario: Scenario
    ok: bool = False
    coverage: float = 0.0
    n_sections: int = 0
    empty_sections: list[str] = field(default_factory=list)
    n_sources: int = 0
    present_types: set[str] = field(default_factory=set)
    single_source_sections: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    error: str = ""


def _is_empty(text: str) -> bool:
    t = (text or "").strip()
    return len(t) < 60 or "證據不足" in t or "無法產出" in t


def evaluate(base: str, sc: Scenario, type_by_source: dict[str, str]) -> Result:
    r = Result(scenario=sc)
    try:
        resp = httpx.post(
            base + "/api/report",
            json={"prompt": sc.prompt, "source_ids": None, "template": sc.template},
            timeout=150,
        )
        resp.raise_for_status()
        d = resp.json()
    except Exception as exc:  # noqa: BLE001
        r.error = f"{type(exc).__name__}: {exc}"
        return r

    r.coverage = float(d.get("citation_coverage", 0.0))
    sections = d.get("sections", [])
    r.n_sections = len(sections)
    for sec in sections:
        label = sec.get("label", sec.get("key", "?"))
        if _is_empty(sec.get("text", "")):
            r.empty_sections.append(label)
        if len(set(sec.get("citations", []))) <= 1:
            r.single_source_sections.append(label)

    source_list = d.get("source_list", [])
    r.n_sources = len({s.get("evidence_id") for s in source_list})
    r.present_types = {
        type_by_source.get(s.get("source_id", ""), "?") for s in source_list
    }

    # 軟性品質旗標
    if r.coverage < sc.min_coverage:
        r.flags.append(f"覆蓋率 {r.coverage:.0%} < 門檻 {sc.min_coverage:.0%}")
    if r.empty_sections:
        r.flags.append(f"空章節：{'、'.join(r.empty_sections)}")
    if r.n_sources < sc.min_sources:
        r.flags.append(f"來源數 {r.n_sources} < 門檻 {sc.min_sources}")
    missing = sc.expect_types - r.present_types
    if missing:
        r.flags.append(f"缺少期望來源類型：{'、'.join(missing)}")
    if r.single_source_sections:
        r.flags.append(f"單一來源撐整段：{'、'.join(r.single_source_sections)}")

    # pass = 無「硬性」問題（覆蓋率/空章節/期望類型）；單一來源與來源數列為軟性提醒不擋 pass
    hard_ok = (
        r.coverage >= sc.min_coverage
        and not r.empty_sections
        and not missing
    )
    r.ok = hard_ok
    return r


def main() -> int:
    # Windows 主控台預設 cp950，直接印 ✓/中文會 UnicodeEncodeError；統一走 UTF-8。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8100")
    ap.add_argument("--only", default="", help="只跑某情境 id")
    ap.add_argument("--out", default="", help="另存 markdown 結果路徑")
    args = ap.parse_args()

    try:
        srcs = httpx.get(args.base + "/api/sources", timeout=20).json()
    except Exception as exc:  # noqa: BLE001
        print(f"無法連線後端 {args.base}：{exc}", file=sys.stderr)
        return 2
    type_by_source = {s["source_id"]: s["source_type"] for s in srcs}
    total_chunks = sum(s["chunk_count"] for s in srcs)
    print(f"知識庫：{len(srcs)} 個來源、{total_chunks} chunks\n")

    scenarios = [s for s in SCENARIOS if not args.only or s.id == args.only]
    results = [evaluate(args.base, sc, type_by_source) for sc in scenarios]

    # 主控台表格
    print(f"{'情境':<18}{'pass':<6}{'覆蓋':<7}{'章節':<6}{'來源':<6}旗標")
    print("-" * 78)
    for r in results:
        if r.error:
            print(f"{r.scenario.id:<18}{'ERR':<6}{r.error}")
            continue
        mark = "✓" if r.ok else "✗"
        flags = "；".join(r.flags) if r.flags else "—"
        print(f"{r.scenario.id:<18}{mark:<6}{r.coverage:>4.0%}   "
              f"{r.n_sections:<6}{r.n_sources:<6}{flags}")

    passed = sum(1 for r in results if r.ok)
    print("-" * 78)
    print(f"通過 {passed}/{len(results)}（硬性：覆蓋率+無空章節+期望來源類型）")

    if args.out:
        lines = [f"# 報告情境測試結果", "",
                 f"知識庫 {len(srcs)} 來源 / {total_chunks} chunks · 通過 {passed}/{len(results)}", ""]
        for r in results:
            lines.append(f"## {r.scenario.id} — {'✓ PASS' if r.ok else '✗ FAIL'}")
            lines.append(f"- 題目：{r.scenario.prompt}")
            if r.error:
                lines.append(f"- 錯誤：{r.error}")
            else:
                lines.append(f"- 覆蓋率 {r.coverage:.0%}｜章節 {r.n_sections}"
                             f"（空 {len(r.empty_sections)}）｜來源 {r.n_sources}"
                             f"｜類型 {'、'.join(sorted(r.present_types))}")
                lines.append(f"- 旗標：{'；'.join(r.flags) if r.flags else '無'}")
            lines.append("")
        Path(args.out).write_text("\n".join(lines), encoding="utf-8")
        print(f"\n已另存：{args.out}")

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
