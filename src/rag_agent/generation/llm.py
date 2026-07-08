"""生成層：透過可設定的 OpenAI 相容供應商（Ollama / API 模型）產生文字。

raw_generate / stream_generate / generate 為對外介面，agent、report、FastAPI
generate route 皆透過這裡呼叫供應商，實際後端由 generation.provider 決定。
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass

from ..evidence.packaging import EvidencePackage
from . import provider
from .prompts import build_prompt


@dataclass
class GenerationResult:
    answer: str
    evidence_package_id: str
    cited_ids: list[str]
    uncited_ids: list[str]
    citation_coverage: float
    tokens_per_sec: float = 0.0


def raw_generate(
    prompt: str,
    max_new_tokens: int = 512,
    temperature: float = 0.0,
) -> tuple[str, float]:
    """非串流生成：回傳 (文字, tok/s)。tok/s 由 API 端不易取得，統一回 0。"""
    text = provider.complete(prompt, max_tokens=max_new_tokens, temperature=temperature)
    return text, 0.0


def stream_generate(
    prompt: str,
    max_new_tokens: int = 512,
    temperature: float = 0.0,
) -> Iterator[str]:
    """串流生成：逐段 yield 文字。"""
    yield from provider.stream(prompt, max_tokens=max_new_tokens, temperature=temperature)


def generate(
    package: EvidencePackage,
    max_new_tokens: int = 512,
    temperature: float = 0.0,
) -> GenerationResult:
    prompt = build_prompt(package)
    answer, _ = raw_generate(prompt, max_new_tokens, temperature)
    cited, uncited, coverage = _check_citations(answer, package)
    return GenerationResult(
        answer=answer,
        evidence_package_id=package.evidence_package_id,
        cited_ids=cited,
        uncited_ids=uncited,
        citation_coverage=coverage,
    )


def _check_citations(
    answer: str, package: EvidencePackage
) -> tuple[list[str], list[str], float]:
    all_ids = [e.evidence_id for e in package.evidence_items]
    cited = [eid for eid in all_ids if re.search(rf"\[{eid}\]", answer)]
    uncited = [eid for eid in all_ids if eid not in cited]
    coverage = len(cited) / len(all_ids) if all_ids else 0.0
    return cited, uncited, coverage
