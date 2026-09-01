from __future__ import annotations

from dataclasses import asdict
from typing import Any, Iterable

from aihot.models import Event
from aihot.score import ScoreResult


def event_to_dict(event: Event) -> dict[str, Any]:
    return asdict(event)


def _pending_verification(event: Event) -> str:
    if event.evidence_status == "discovery_only":
        return "需要补充可直接引用的一手来源，不能只依据聚合摘要。"
    if event.evidence_status == "primary":
        return "核对官方页面中的适用范围、地区、版本和已知限制。"
    return "补充一手来源或独立交叉来源后再进入写作。"


def candidate_to_dict(event: Event, score: ScoreResult, rank: int) -> dict[str, Any]:
    source_links = [
        link
        for item in event.items
        for link in item.source_links
        if isinstance(link, dict) and isinstance(link.get("url"), str) and link["url"]
    ]
    if not source_links:
        source_links = [
            {
                "url": item.url,
                "source_id": item.source_id,
                "source_class": item.source_class,
                "provenance": "direct_source",
            }
            for item in event.items
            if item.url
        ]
    source_links = sorted(
        {tuple(sorted(link.items())) for link in source_links},
        key=lambda link: (
            dict(link).get("url", ""),
            dict(link).get("source_id", ""),
            dict(link).get("source_class", ""),
            dict(link).get("provenance", ""),
        ),
    )
    serializable_source_links = [dict(link) for link in source_links]
    source_urls = sorted({link["url"] for link in serializable_source_links})
    return {
        "rank": rank,
        "event_id": event.event_id,
        "title": event.canonical_title,
        "total_score": score.total,
        "score_breakdown": score.components,
        "penalties": score.penalties,
        "penalty_reasons": score.penalty_reasons,
        "recommendation": score.recommendation,
        "evidence_status": event.evidence_status,
        "primary_source_url": event.primary_source_url,
        "source_urls": source_urls,
        "source_links": serializable_source_links,
        "pending_verification": _pending_verification(event),
    }


def render_markdown(run_date: str, candidates: Iterable[dict[str, Any]]) -> str:
    lines = [f"# Amesi 每日候选榜 — {run_date}", "", "本文件只列出待人工核验的候选，不代表已发布内容。", ""]
    for candidate in candidates:
        lines.extend(
            [
                f"## {candidate['rank']}. {candidate['title']} — {candidate['total_score']}/100",
                "",
                "- 分项分数：" + "；".join(f"{name} {value}" for name, value in candidate["score_breakdown"].items()),
                "- 惩罚：" + ("；".join(f"{name} {value}" for name, value in candidate["penalties"].items()) if candidate["penalties"] else "无"),
                "- 惩罚原因：" + ("；".join(candidate["penalty_reasons"]) if candidate["penalty_reasons"] else "无"),
                f"- 为什么适合 Amesi：{candidate['recommendation']}",
                f"- 证据状态：{candidate['evidence_status']}",
                f"- 主证据链接：{candidate['primary_source_url'] or '暂无'}",
                "- 所有来源链接：",
            ]
        )
        lines.extend(
            f"  - [{link['provenance']} / {link['source_id']}] {link['url']}" for link in candidate["source_links"]
        )
        lines.extend([f"- 尚待核验事项：{candidate['pending_verification']}", ""])
    return "\n".join(lines).rstrip() + "\n"
