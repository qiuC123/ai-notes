from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterable

import yaml

from aihot.cluster import title_similarity
from aihot.models import Event


@dataclass(slots=True)
class ScoreResult:
    total: int
    components: dict[str, int]
    penalties: dict[str, int]
    penalty_reasons: list[str]
    recommendation: str


def load_scoring_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict) or not isinstance(config.get("components"), dict):
        raise ValueError(f"Invalid scoring configuration: {path}")
    return config


def _weight(config: dict[str, Any], name: str) -> int:
    return int(config["components"][name])


def _event_text(event: Event) -> str:
    return " ".join(
        [event.canonical_title, *[item.summary for item in event.items], *[category for item in event.items for category in item.categories]]
    ).lower()


def _title_text(event: Event) -> str:
    return event.canonical_title.lower()


def _has_term(text: str, terms: Iterable[str]) -> bool:
    return any(term.lower() in text for term in terms)


def _latest_published_at(event: Event) -> datetime | None:
    values: list[datetime] = []
    for item in event.items:
        if not item.published_at:
            continue
        try:
            parsed = datetime.fromisoformat(item.published_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        values.append(parsed.astimezone(UTC))
    return max(values) if values else None


def _as_of_datetime(as_of: str) -> datetime:
    try:
        return datetime.fromisoformat(as_of.replace("Z", "+00:00")).replace(tzinfo=UTC)
    except ValueError:
        return datetime.combine(date.fromisoformat(as_of), datetime.min.time(), tzinfo=UTC)


def _timeliness_score(event: Event, config: dict[str, Any], as_of: str) -> int:
    weight = _weight(config, "timeliness")
    latest = _latest_published_at(event)
    if latest is None:
        return 0
    age_days = max(0, (_as_of_datetime(as_of) - latest).total_seconds() / 86400)
    if age_days <= 1:
        return weight
    if age_days <= 3:
        return round(weight * 0.75)
    if age_days <= 7:
        return round(weight * 0.4)
    return 0


def _evidence_score(event: Event, config: dict[str, Any]) -> int:
    weight = _weight(config, "evidence")
    return {
        "primary": weight,
        "multi_source": round(weight * 0.7),
        "insufficient": round(weight * 0.35),
        "discovery_only": 0,
    }.get(event.evidence_status, 0)


def _recommendation(event: Event, result_total: int) -> str:
    if event.evidence_status == "discovery_only":
        return f"仅作为发现信号：{event.canonical_title} 还缺少可直接引用的一手证据，补齐后再判断是否适合 Amesi。"
    if event.evidence_status == "primary":
        return f"有一手证据：{event.canonical_title} 可清楚说明变化、使用场景和限制，适合作为 Amesi 候选（{result_total} 分）。"
    return f"{event.canonical_title} 有可追溯来源，但仍需补充交叉核验后再进入 Amesi 选题池（{result_total} 分）。"


def score_event(
    event: Event,
    config: dict[str, Any],
    *,
    as_of: str,
    recent_decision_titles: Iterable[str] = (),
) -> ScoreResult:
    """Score an event from YAML-configured, inspectable rules only."""
    rules = config.get("rules", {})
    text = _event_text(event)
    title_text = _title_text(event)
    workflow_terms = rules.get("workflow_keywords", [])
    workflow_match = _has_term(text, workflow_terms)
    title_workflow_match = _has_term(title_text, workflow_terms)
    components = {
        "audience_value": _weight(config, "audience_value") if workflow_match else round(_weight(config, "audience_value") * 0.4),
        "evidence": _evidence_score(event, config),
        "novelty": min(_weight(config, "novelty"), 4 + len(event.source_ids) * 4),
        "timeliness": _timeliness_score(event, config, as_of),
        "amesi_fit": _weight(config, "amesi_fit") if workflow_match else round(_weight(config, "amesi_fit") * 0.3),
        "explainability": _weight(config, "explainability") if event.evidence_status == "primary" else round(_weight(config, "explainability") * 0.5),
        "production_cost_fit": _weight(config, "production_cost_fit") if event.evidence_status != "discovery_only" else round(_weight(config, "production_cost_fit") * 0.6),
    }
    penalties: dict[str, int] = {}
    reasons: list[str] = []
    configured_penalties = config.get("penalties", {})
    if event.evidence_status == "discovery_only":
        penalties["discovery_only"] = int(configured_penalties["discovery_only"])
        reasons.append("仅有聚合发现信号，缺少可直接引用的主证据。")
    if any(title_similarity(event.canonical_title, prior) >= float(rules.get("recent_similarity_threshold", 0.85)) for prior in recent_decision_titles):
        penalties["recent_decision_similarity"] = int(configured_penalties["recent_decision_similarity"])
        reasons.append("与最近 14 天已决选题过于相似。")
    if _has_term(text, rules.get("weak_evidence_terms", [])):
        penalties["weak_evidence_language"] = int(configured_penalties["weak_evidence_language"])
        reasons.append("标题或摘要含有传闻性质的弱证据词。")
    if _has_term(title_text, rules.get("non_workflow_noise_terms", [])) and not title_workflow_match:
        penalties["non_workflow_noise"] = int(configured_penalties["non_workflow_noise"])
        reasons.append("内容偏融资、股价或名人争议，未体现普通读者工作流价值。")
    total = max(0, min(100, round(sum(components.values()) + sum(penalties.values()))))
    return ScoreResult(
        total=total,
        components=components,
        penalties=penalties,
        penalty_reasons=reasons,
        recommendation=_recommendation(event, total),
    )
