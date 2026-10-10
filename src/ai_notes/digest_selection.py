"""Explainable editorial reviews, kept separate from evidence and publication.

No model calls or network requests are made here. Prepared original-source
material is untrusted data. Scores are supplied by an identified editor/model;
Python validates the record and computes policy effects without guessing scores.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import inspect
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

from . import digest
from .digest_claims import validate_claims

DIMENSIONS = ("value", "novelty", "evidence", "usability", "interest")
ASSESSMENT_FIELDS = ("precheck", "scores", "flags", "reason")
SCOPED_CONTRACT = "scoped-source.v1"
SOURCE_REFS_PROJECTION = "source-refs.v1"
READER_CONTEXT_CONTRACT = "digest-reader-context.v1"
EDITORIAL_POSITION_CONTRACT = "digest-editorial-position.v1"
UNDERSTANDING_CONTRACT = "project-reading.v1"
UNDERSTANDING_PROMPT_MARKER = "<!-- understanding_contract: project-reading.v1 -->"
INTRODUCTION_CONTRACT = "discovery.v1"
INTRODUCTION_PROMPT_MARKER = "<!-- introduction_contract: discovery.v1 -->"
SOURCE_READING_CONTRACT = "discovery-reading.v1"
PUBLIC_REVIEW_SCOPE = "public-introduction.v1"
SELECTION_FIX_PROMPT_MARKER = "<!-- editorial_scope: public-introduction.v1 -->"
SELECTION_REFINEMENT_CONTRACT = "evidence-focus.v1"
SELECTION_REFINEMENT_PROMPT_MARKER = "<!-- selection_refinement_contract: evidence-focus.v1 -->"
SCORE_INPUT_CONTRACT = "compact-schema.v1"
SCORE_INPUT_PROMPT_MARKER = "<!-- score_input_contract: compact-schema.v1 -->"
LICENSE_SCOPE_EXCLUDED = "excluded.v1"
SOURCE_SUPPORT_CONTRACT = "source-support.v1"
SOURCE_SUPPORT_PROMPT_MARKER = "<!-- source_support_contract: source-support.v1 -->"
LICENSE_SCOPE_GUIDANCE = '''
When license_review_scope is excluded.v1, follow this explicit editorial scope instead of any broader licensing-review instructions above. Do not investigate licence terms, component-specific licence coverage or commercial-use permissions. These are not ranking, score-cap or publication gates. Do not put licence names, licence scope or commercial-use conclusions in introductions, highlights, score explanations, flags or overall recommendation reasons. Rank the candidate on its documented purpose, features, supported systems and reader value. Repository open-source identification may retain its already documented licence name, without auditing its legal scope. Continue checking concrete non-licensing facts, including functionality, systems, versions, events and actually stated free/paid availability. Do not use the exclusion to forgive an unrelated unsupported claim in the same sentence. This editorial scope is not a statement about what any licence permits.'''
FLAG_BASIS_KINDS = {
    "routine_update": "limited_increment", "unsupported_promotion": "unsupported_effect_claim",
    "unfulfilled_announcement": "availability_limit", "unclear_usage": "usage_path_gap",
    "reader_mismatch": "reader_requirement", "insufficient_usage_evidence": "usage_evidence_gap",
}
# These types describe a missing support for the claimed recommendation. Neither
# lack of our own installation nor popularity is a type of missing evidence.
USAGE_EVIDENCE_GAPS = {
    "daily": {"actionable_steps"},
    "weekly": {"actionable_steps", "usage_record", "long_term_support"},
    "monthly": {"actionable_steps", "usage_record", "long_term_support"},
}
MAX_ASSESSMENT_BYTES = 65536
DB_PATH = Path("data/weekly_digest/selection.sqlite3")
POLICY_PATH = Path("config/digest_selection.json")
PROMPT_PATH = Path("docs/prompts/digest-selection.md")


class SelectionError(ValueError):
    """Malformed or conflicting review input."""


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise SelectionError("input must be finite JSON") from exc


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _object(value: Any, keys: tuple | set, label: str, optional: tuple = ()) -> dict:
    if not isinstance(value, dict) or set(value) - (set(keys) | set(optional)) or set(keys) - set(value):
        raise SelectionError(f"{label} requires exactly {', '.join(sorted(keys))}" + (f"; optional {optional}" if optional else ""))
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SelectionError(f"{label} must be nonempty text")
    return value


def _integer(value: Any, lo: int, hi: int, label: str) -> int:
    if type(value) is not int or not lo <= value <= hi:
        raise SelectionError(f"{label} must be an integer in {lo}..{hi}")
    return value


def _strings(value: Any, label: str, *, nonempty: bool = True) -> list:
    if not isinstance(value, list) or (nonempty and not value):
        raise SelectionError(f"{label} must be a {'nonempty ' if nonempty else ''}list")
    return [_text(item, label) for item in value]


def load_policy(root: Path, policy_path: Path | None = None) -> dict:
    policy = json.loads((policy_path or root / POLICY_PATH).read_text(encoding="utf-8"))
    return validate_policy(policy)


def validate_policy(policy: dict) -> dict:
    """Validate an in-memory policy and return an independent, unmodified copy."""
    policy = copy.deepcopy(policy)
    _object(policy, ("schema_version", "version", "calibration_status", "dimensions", "profiles", "category_profiles", "thresholds", "flag_caps", "defer_flags"), "policy", optional=("kind_profiles", "flag_kinds", "flag_basis", "usage_evidence_gaps", "assessment_contract", "editorial_review_contract", "scoring_projection", "reader_context", "editorial_position", "understanding_contract", "introduction_contract", "source_reading_contract", "editorial_scope", "selection_refinement_contract", "score_input_contract", "reason_review_contract", "license_review_scope", "source_support_contract"))
    _check_license_review_scope(policy)
    if "assessment_contract" in policy and policy["assessment_contract"] != SCOPED_CONTRACT:
        raise SelectionError("unsupported assessment contract")
    if "editorial_review_contract" in policy and policy["editorial_review_contract"] != "source-score.v1":
        raise SelectionError("unsupported editorial review contract")
    _check_scoring_projection(policy)
    _check_reader_context(policy)
    _check_editorial_position(policy)
    _check_understanding_contract(policy)
    _check_introduction_contract(policy)
    _check_discovery_selection_contracts(policy)
    _check_selection_refinement(policy)
    _check_score_input(policy)
    _check_reason_review(policy)
    _check_source_support(policy)
    if policy["schema_version"] != "digest-selection.policy.v1" or policy["dimensions"] != list(DIMENSIONS):
        raise SelectionError("unsupported policy schema/dimensions")
    _text(policy["version"], "policy.version")
    _text(policy["calibration_status"], "policy.calibration_status")
    if not isinstance(policy["category_profiles"], dict) or not isinstance(policy["profiles"], dict) or not policy["profiles"]:
        raise SelectionError("policy profiles must be nonempty objects")
    if set(policy["category_profiles"]) != set(digest.CATEGORIES):
        raise SelectionError("policy must cover exactly eight categories")
    for weights in policy["profiles"].values():
        _object(weights, DIMENSIONS, "weights")
        if sum(_integer(value, 0, 10, "weight") for value in weights.values()) != 10:
            raise SelectionError("profile weights must sum to 10")
    if any(profile not in policy["profiles"] for profile in policy["category_profiles"].values()):
        raise SelectionError("unknown category profile")
    kind_profiles = policy.get("kind_profiles", {})
    if not isinstance(kind_profiles, dict) or set(kind_profiles) - set(digest.KINDS):
        raise SelectionError("kind_profiles must map known candidate kinds")
    if any(not isinstance(profile, str) or profile not in policy["profiles"] for profile in kind_profiles.values()):
        raise SelectionError("unknown kind profile")
    thresholds = _object(policy["thresholds"], ("select", "reject_below"), "thresholds")
    if _integer(thresholds["reject_below"], 0, 100, "reject_below") > _integer(thresholds["select"], 0, 100, "select"):
        raise SelectionError("reject threshold exceeds select threshold")
    if not isinstance(policy["flag_caps"], dict):
        raise SelectionError("flag_caps must be an object")
    for caps in policy["flag_caps"].values():
        if not isinstance(caps, dict) or not caps or set(caps) - set(DIMENSIONS):
            raise SelectionError("unknown cap dimension")
        for value in caps.values():
            _integer(value, 0, 10, "cap")
    if set(_strings(policy["defer_flags"], "defer_flags", nonempty=False)) - set(policy["flag_caps"]):
        raise SelectionError("unknown defer flag")
    flag_kinds = policy.get("flag_kinds", {})
    if not isinstance(flag_kinds, dict) or set(flag_kinds) - set(policy["flag_caps"]):
        raise SelectionError("flag_kinds must map known flags")
    for kinds in flag_kinds.values():
        checked = _strings(kinds, "flag_kinds")
        if len(checked) != len(set(checked)) or set(checked) - set(digest.KINDS):
            raise SelectionError("flag_kinds must contain distinct known candidate kinds")
    flag_basis = policy.get("flag_basis", {})
    if not isinstance(flag_basis, dict) or set(flag_basis) - (set(FLAG_BASIS_KINDS) & set(policy["flag_caps"])):
        raise SelectionError("flag_basis must map supported flags")
    if any(basis != FLAG_BASIS_KINDS[code] for code, basis in flag_basis.items()):
        raise SelectionError("unknown flag basis kind")
    if "usage_evidence_gaps" in policy:
        gaps = _object(policy["usage_evidence_gaps"], tuple(USAGE_EVIDENCE_GAPS), "usage_evidence_gaps")
        if set(flag_basis) != set(policy["flag_caps"]) or "insufficient_usage_evidence" not in flag_basis:
            raise SelectionError("usage_evidence_gaps requires source basis for every cap flag")
        for ranking_type, allowed in gaps.items():
            checked = _strings(allowed, "usage_evidence_gaps." + ranking_type)
            if len(checked) != len(set(checked)) or set(checked) - USAGE_EVIDENCE_GAPS[ranking_type]:
                raise SelectionError("unsupported evidence gap for ranking type: " + ranking_type)
    return policy


def _connect(root: Path) -> sqlite3.Connection:
    path = root / DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript("""
      CREATE TABLE IF NOT EXISTS preparations (prepare_id TEXT PRIMARY KEY,payload TEXT NOT NULL,created_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS reviews (id INTEGER PRIMARY KEY,review_hash TEXT UNIQUE NOT NULL,prepare_id TEXT NOT NULL REFERENCES preparations(prepare_id),candidate_id TEXT NOT NULL,input_hash TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS review_lookup ON reviews(prepare_id,candidate_id,id);
      CREATE TABLE IF NOT EXISTS label_splits (canonical_url TEXT PRIMARY KEY,split TEXT NOT NULL CHECK(split IN ('dev','holdout')));
      CREATE TABLE IF NOT EXISTS labels (id INTEGER PRIMARY KEY,prepare_id TEXT NOT NULL REFERENCES preparations(prepare_id),candidate_id TEXT NOT NULL,canonical_url TEXT NOT NULL REFERENCES label_splits(canonical_url),payload TEXT NOT NULL,created_at TEXT NOT NULL);
    """)
    return connection


def _load(connection: sqlite3.Connection, prepare_id: str) -> dict:
    row = connection.execute("SELECT payload FROM preparations WHERE prepare_id=?", (_text(prepare_id, "prepare_id"),)).fetchone()
    if row is None:
        raise SelectionError("unknown prepare_id; prepare the frozen material first")
    return json.loads(row["payload"])


def load_preparation(root: Path, prepare_id: str) -> dict:
    """Read the exact persisted input; no fresh material or policy is substituted."""
    connection = _connect(Path(root))
    try:
        return _load(connection, prepare_id)
    finally:
        connection.close()


def load_prompt(root: Path, policy: dict) -> str:
    """Select optional instructions before freezing; never upgrade a snapshot."""
    _check_understanding_contract(policy)
    _check_introduction_contract(policy)
    _check_discovery_selection_contracts(policy)
    _check_selection_refinement(policy)
    _check_score_input(policy)
    _check_license_review_scope(policy)
    _check_source_support(policy)
    legacy_prompt, support_marker, support_guidance = (Path(root) / PROMPT_PATH).read_text(encoding="utf-8").partition(SOURCE_SUPPORT_PROMPT_MARKER)
    prior_text, score_marker, score_guidance = legacy_prompt.partition(SCORE_INPUT_PROMPT_MARKER)
    legacy_text, refinement_marker, refinement = prior_text.partition(SELECTION_REFINEMENT_PROMPT_MARKER)
    text, fix_marker, fix = legacy_text.partition(SELECTION_FIX_PROMPT_MARKER)
    existing, discovery_marker, discovery = text.partition(INTRODUCTION_PROMPT_MARKER)
    base, marker, _ = existing.partition(UNDERSTANDING_PROMPT_MARKER)
    if policy.get("understanding_contract") == UNDERSTANDING_CONTRACT:
        if not marker:
            raise SelectionError("project-reading prompt supplement is unavailable")
        base = existing
    if policy.get("introduction_contract") == INTRODUCTION_CONTRACT:
        if not discovery_marker:
            raise SelectionError("discovery prompt supplement is unavailable")
        base += discovery_marker + discovery
    if policy.get("editorial_scope") == PUBLIC_REVIEW_SCOPE:
        if not fix_marker:
            raise SelectionError("public-introduction prompt supplement is unavailable")
        base += fix_marker + fix
    if policy.get("selection_refinement_contract") == SELECTION_REFINEMENT_CONTRACT:
        if not refinement_marker:
            raise SelectionError("evidence-focus prompt supplement is unavailable")
        base += refinement_marker + refinement
    if policy.get("score_input_contract") == SCORE_INPUT_CONTRACT:
        if not score_marker:
            raise SelectionError("compact-schema prompt supplement is unavailable")
        base += score_marker + score_guidance
    if policy.get("source_support_contract") == SOURCE_SUPPORT_CONTRACT:
        if not support_marker:
            raise SelectionError("source-support prompt supplement is unavailable")
        base += support_marker + support_guidance
    if policy.get("license_review_scope") == LICENSE_SCOPE_EXCLUDED:
        base += LICENSE_SCOPE_GUIDANCE
    return base


def get_prompt(root: Path, prepared: dict) -> str:
    """Return the system prompt belonging to the preparation, not a later edit."""
    text = prepared.get("prompt_text")
    if text is None:  # Preparations made before prompt text persistence.
        text = load_prompt(root, prepared["policy"])
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != prepared["prompt_hash"]:
        raise SelectionError("prepared prompt is unavailable or its hash does not match")
    return text


def _card(prepared: dict, candidate_id: str) -> dict:
    found = [item for item in prepared["cards"] if item["candidate_id"] == candidate_id]
    if not found:
        raise SelectionError("candidate_id not present in this preparation")
    return found[0]


def evaluation_target(material: dict) -> dict:
    """Choose the editorial unit from the candidate kind, not its latest source.

    This does not prove that the upstream kind or the model's reasoning is right.
    A release page in a project card does not turn that card into an update.
    """
    units = {"project": "whole_project", "update": "event_increment",
             "news": "event_increment", "reading": "content_and_method"}
    return {"kind": material["kind"], "unit": units[material["kind"]]}


def _check_scoring_projection(policy: dict) -> None:
    if "scoring_projection" not in policy:
        return
    if policy["scoring_projection"] != SOURCE_REFS_PROJECTION:
        raise SelectionError("unsupported scoring projection")
    if policy.get("assessment_contract") != SCOPED_CONTRACT:
        raise SelectionError("source-refs projection requires scoped-source assessment contract")


def _check_reader_context(policy: dict) -> None:
    """Validate optional reader facts without filling in missing personal context."""
    if "reader_context" not in policy:
        return
    context = _object(policy["reader_context"], ("schema_version", "background", "exploration_interests"), "reader_context")
    if context["schema_version"] != READER_CONTEXT_CONTRACT:
        raise SelectionError("unsupported reader context contract")
    for key in ("background", "exploration_interests"):
        _strings(context[key], "reader_context." + key, nonempty=False)


def _check_editorial_position(policy: dict) -> None:
    """Validate explicit public editorial priorities without upgrading old policies."""
    if "editorial_position" not in policy:
        return
    position = _object(policy["editorial_position"], ("schema_version", "audience", "priorities"), "editorial_position")
    if position["schema_version"] != EDITORIAL_POSITION_CONTRACT:
        raise SelectionError("unsupported editorial position contract")
    for key in ("audience", "priorities"):
        _strings(position[key], "editorial_position." + key)


def _check_understanding_contract(policy: dict) -> None:
    if "understanding_contract" in policy and policy["understanding_contract"] != UNDERSTANDING_CONTRACT:
        raise SelectionError("unsupported understanding contract")


def _check_introduction_contract(policy: dict) -> None:
    """Only an explicit policy marker enables the discovery introduction."""
    if "introduction_contract" in policy and policy["introduction_contract"] != INTRODUCTION_CONTRACT:
        raise SelectionError("unsupported introduction contract")


def _supported_systems(value):
    """Validate the optional fact, without extracting platforms from other prose."""
    return None if value is None else _text(value, "supported_systems")


def _check_discovery_selection_contracts(policy: dict) -> None:
    """New routing/review semantics require explicit, frozen opt-in markers."""
    for field, expected in (("source_reading_contract", SOURCE_READING_CONTRACT),
                            ("editorial_scope", PUBLIC_REVIEW_SCOPE)):
        if field not in policy:
            continue
        if policy[field] != expected:
            raise SelectionError("unsupported " + field)
        if policy.get("introduction_contract") != INTRODUCTION_CONTRACT or policy.get("understanding_contract") != UNDERSTANDING_CONTRACT:
            raise SelectionError(field + " requires discovery and project-reading contracts")
    if "editorial_scope" in policy and policy.get("editorial_review_contract") != "source-score.v1":
        raise SelectionError("public introduction review requires source-score contract")


def _check_selection_refinement(policy: dict) -> None:
    """Opt in to new guidance without altering frozen, unmarked inputs."""
    if "selection_refinement_contract" not in policy:
        return
    if policy["selection_refinement_contract"] != SELECTION_REFINEMENT_CONTRACT:
        raise SelectionError("unsupported selection refinement contract")
    required = {
        "understanding_contract": UNDERSTANDING_CONTRACT,
        "introduction_contract": INTRODUCTION_CONTRACT,
        "source_reading_contract": SOURCE_READING_CONTRACT,
        "editorial_scope": PUBLIC_REVIEW_SCOPE,
        "assessment_contract": SCOPED_CONTRACT,
        "editorial_review_contract": "source-score.v1",
    }
    if any(policy.get(key) != value for key, value in required.items()):
        raise SelectionError("evidence-focus requires explicit discovery reading and public review contracts")


def _check_score_input(policy: dict) -> None:
    if "score_input_contract" not in policy:
        return
    if policy["score_input_contract"] != SCORE_INPUT_CONTRACT:
        raise SelectionError("unsupported score input contract")
    if (policy.get("assessment_contract") != SCOPED_CONTRACT
            or policy.get("scoring_projection") != SOURCE_REFS_PROJECTION
            or policy.get("selection_refinement_contract") != SELECTION_REFINEMENT_CONTRACT):
        raise SelectionError("compact-schema requires scoped-source, source-refs and evidence-focus contracts")
    _check_selection_refinement(policy)


def _check_license_review_scope(policy: dict) -> None:
    if "license_review_scope" not in policy:
        return
    if policy["license_review_scope"] != LICENSE_SCOPE_EXCLUDED:
        raise SelectionError("unsupported license review scope")
    if (policy.get("editorial_scope") != PUBLIC_REVIEW_SCOPE
            or policy.get("introduction_contract") != INTRODUCTION_CONTRACT):
        raise SelectionError("license review exclusion requires public discovery scope")


def _check_reason_review(policy: dict) -> None:
    """Explicit isolation gate; never upgrade old frozen policies."""
    if "reason_review_contract" not in policy:
        return
    from .digest_reason_review import CONTRACT
    from .digest_reason_statements import CONTRACT as STATEMENTS_CONTRACT
    if policy["reason_review_contract"] not in (CONTRACT, STATEMENTS_CONTRACT):
        raise SelectionError("unsupported reason review contract")
    if policy.get("score_input_contract") != SCORE_INPUT_CONTRACT:
        raise SelectionError("own-refs requires compact-schema and its dependencies")
    _check_score_input(policy)


def _check_source_support(policy: dict) -> None:
    if "source_support_contract" not in policy:
        return
    if policy["source_support_contract"] != SOURCE_SUPPORT_CONTRACT:
        raise SelectionError("unsupported source support contract")
    if (policy.get("license_review_scope") != LICENSE_SCOPE_EXCLUDED
            or policy.get("score_input_contract") != SCORE_INPUT_CONTRACT
            or policy.get("understanding_contract") != UNDERSTANDING_CONTRACT):
        raise SelectionError("source-support requires excluded licensing, compact scoring and project reading")


def _validated_understanding(value: dict, contexts: list, *, allow_unknown_conditions=False) -> dict:
    """Check frozen bindings against these sources, without claiming entailment."""
    from .digest_passages import build_passages
    from .digest_understanding import source_documents, validate_understanding
    try:
        card = validate_understanding(value, allow_unknown_conditions=allow_unknown_conditions)
        documents = source_documents(contexts)
        if card["source_documents"] != documents:
            raise ValueError("understanding source documents differ from supplied contexts")
        actual = {item["id"]: {key: item[key] for key in ("evidence_url", "quote", "heading_path")}
                  for item in build_passages(contexts)}
        if any(actual.get(pid) != proof for pid, proof in card["proof_map"].items()):
            raise ValueError("understanding proof does not match the supplied original passage")
        return card
    except ValueError as exc:
        raise SelectionError(str(exc)) from exc


def _understanding_projection(value: dict, contexts: list, *, source_spans: bool, allow_unknown_conditions=False) -> dict:
    """Keep one original text copy; source IDs refer to passages or exact spans."""
    card = _validated_understanding(value, contexts, allow_unknown_conditions=allow_unknown_conditions)
    proofs = card.pop("proof_map")
    card.pop("source_documents")  # The enclosing input carries these once.
    if source_spans:
        from .digest_passages import build_passages
        indices = {item["url"]: index for index, item in enumerate(contexts)}
        cursors, spans = {}, {}
        for passage in build_passages(contexts):
            index = indices[passage["evidence_url"]]
            start = contexts[index]["text"].index(passage["quote"], cursors.get(index, 0))
            end = start + len(passage["quote"])
            spans[passage["id"]] = {"context_index": index, "start": start, "end": end}
            cursors[index] = end
        for pid, proof in proofs.items():
            proof.pop("quote")
            proof["source_span"] = spans[pid]
        card["proof_map"] = proofs
    return card


def _source_claim_refs(claims: list, contexts: list) -> list:
    """Replace repeated quotations with reversible positions, leaving originals intact.

    The last context for a URL matches validate_claims' existing binding. Within
    that context the first exact occurrence is deterministic, even if the text
    repeats. Offsets index Unicode code points in the unescaped source string;
    the end is exclusive. Frozen claims and output quote validation stay raw.
    """
    if claims == []:
        return []
    try:
        projected = validate_claims(claims, contexts)
    except ValueError as exc:
        raise SelectionError(str(exc)) from exc
    indices = {item["url"]: index for index, item in enumerate(contexts)}
    for claim in projected:
        quote = claim.pop("quote")
        index = indices[claim["evidence_url"]]
        start = contexts[index]["text"].index(quote)
        claim["source_span"] = {"context_index": index, "start": start, "end": start + len(quote)}
    return projected


def build_scoring_input(prepared: dict, candidate_id: str) -> dict:
    """Build model-visible material without ledger eligibility or prior decisions.

    The full preparation remains unchanged and is still used by build_review.
    Whitelists also prevent newly added ledger metadata from leaking by default.
    This is a pure projection: no database, filesystem or network operations.
    """
    _check_scoring_projection(prepared["policy"])
    _check_reader_context(prepared["policy"])
    _check_editorial_position(prepared["policy"])
    _check_understanding_contract(prepared["policy"])
    _check_introduction_contract(prepared["policy"])
    _check_discovery_selection_contracts(prepared["policy"])
    _check_selection_refinement(prepared["policy"])
    _check_score_input(prepared["policy"])
    _check_reason_review(prepared["policy"])
    _check_license_review_scope(prepared["policy"])
    _check_source_support(prepared["policy"])
    card = _card(prepared, candidate_id)
    fields = ("url", "title", "category", "kind", "summary", "source_urls", "evidence_urls", "published_at", "change_note")
    material = {key: copy.deepcopy(card["material"][key]) for key in fields if key in card["material"]}
    if prepared["policy"].get("introduction_contract") == INTRODUCTION_CONTRACT:
        material["supported_systems"] = _supported_systems(card["material"].get("supported_systems"))
    event = card["material"].get("event")
    if event:
        material["event"] = {key: copy.deepcopy(event[key]) for key in
                             ("url", "occurred_at", "occurred_on", "date_precision", "timezone", "type") if key in event}
    policy_fields = ("version", "dimensions", "profiles", "category_profiles", "kind_profiles", "flag_caps", "flag_kinds", "flag_basis", "usage_evidence_gaps", "assessment_contract", "scoring_projection", "reader_context", "editorial_position", "understanding_contract", "introduction_contract", "source_reading_contract", "editorial_scope", "selection_refinement_contract", "score_input_contract", "license_review_scope", "source_support_contract")
    result = {
        "card": {"ranking_type": card.get("ranking_type", prepared["ranking_type"]), "profile": card["profile"],
                 "material": material,
                 "evidence_context": [{"url": item["url"], "text": item["text"]} for item in card["evidence_context"]]},
        "policy": {key: copy.deepcopy(prepared["policy"][key]) for key in policy_fields if key in prepared["policy"]},
    }
    if prepared["policy"].get("assessment_contract") == SCOPED_CONTRACT:
        result["card"]["evaluation_target"] = evaluation_target(material)
        if prepared["policy"].get("scoring_projection") == SOURCE_REFS_PROJECTION:
            result["card"]["source_claims"] = _source_claim_refs(card.get("source_claims", []), card["evidence_context"])
        else:
            result["card"]["source_claims"] = copy.deepcopy(card.get("source_claims", []))
    if prepared["policy"].get("understanding_contract") == UNDERSTANDING_CONTRACT:
        from .digest_understanding import source_documents
        result["card"]["understanding"] = _understanding_projection(card.get("understanding"), card["evidence_context"], source_spans=True,
            allow_unknown_conditions=prepared['policy'].get('source_support_contract') == SOURCE_SUPPORT_CONTRACT)
        result["card"]["source_documents"] = source_documents(card["evidence_context"])
    if prepared["policy"].get("selection_refinement_contract") == SELECTION_REFINEMENT_CONTRACT:
        result["output_example"] = {
            "purpose": "Output structure only; this is not a judgment or evidence about the candidate. PASS requires all five score dimensions with their own source-supported reasons and evidence_refs.",
            "unknown_example": {
                "precheck": {"status": "UNKNOWN", "reasons": ["结构示例：此处应写本候选实际缺少的原文依据。"], "evidence_refs": []},
                "scores": None, "flags": [], "reason": "结构示例，不是对本候选的判断。",
            },
        }
    if prepared["policy"].get("score_input_contract") == SCORE_INPUT_CONTRACT:
        result["output_guidance"] = {
            "structure_only_not_candidate_evidence": True,
            "required_fields": {"top_level": list(ASSESSMENT_FIELDS),
                                "precheck": ["status", "reasons", "evidence_refs"],
                                "scores": list(DIMENSIONS), "each_score": ["score", "reason", "evidence_refs"]},
            "pass_shape_example": {
                "precheck": {"status": "PASS", "reasons": ["结构示例，须替换为本候选实际依据。"],
                             "evidence_refs": [card["evidence_context"][0]["url"]] if card["evidence_context"] else []},
                "scores": {dimension: {"score": 0, "reason": "结构示例，分数与理由须独立判断。",
                                       "evidence_refs": [card["evidence_context"][0]["url"]] if card["evidence_context"] else []}
                           for dimension in DIMENSIONS},
                "flags": [], "reason": "仅展示四字段结构，不表示本候选应 PASS 或得零分。",
            } if card["evidence_context"] else None,
            "citation_check": ["每个理由只引用支持其中全部实质事实的 URL；可使用多个 URL。",
                               "来源导航仅用于定位；仍须读对应 evidence_context 原文，不把合法 URL 等同事实有据。",
                               "删除无关且缺据的枝节；不要自动补引用或把包内别处支持当成当前引用支持。"],
            "forbidden_extra_fields": ["reason_note", "decision", "total_score"],
        }
        claims = result["card"].get("source_claims", [])
        proofs = result["card"].get("understanding", {}).get("proof_map", {})
        result["source_navigation"] = [{
            "url": context["url"],
            "source_claim_indexes": [index for index, claim in enumerate(claims) if claim["evidence_url"] == context["url"]],
            "understanding_passage_ids": sorted(pid for pid, proof in proofs.items() if proof["evidence_url"] == context["url"]),
        } for context in card["evidence_context"]]
    return result


def candidate_id(record: dict) -> str:
    identity = record.get("canonical_url") or digest.canonical_url(record["url"], record["kind"])
    event = record.get("event")
    # Renaming a release ID at the same canonical event URL is not a new review
    # identity. The complete event still participates in the frozen input hash.
    event_url = digest._event_url(event["url"]) if event else (digest._event_url(record["url"]) if record["kind"] == "news" else "")
    return _hash({"canonical_url": identity, "kind": record["kind"], "event_url": event_url})[:24]


def _eligibility(record: dict, has_context: bool) -> dict:
    reasons = []
    if record.get("selection_block") or record.get("selection_status") == "blocked":
        return {"state": "blocked", "reasons": [record.get("selection_block") or "same-level or period eligibility blocked"]}
    if record.get("evidence_status") != "verified":
        reasons.append("original-source verification has not been recorded in the digest ledger")
    if not has_context:
        reasons.append("no readable original-source context was supplied for scoring")
    if record.get("selection_status") == "needs_evidence" and not reasons:
        reasons.append("digest candidate requires further evidence")
    return {"state": "needs_evidence" if reasons else "available", "reasons": reasons}


def _query(root: Path, ranking_type: str, period: str, limit: int, offset: int = 0) -> dict:
    args = dict(ranking_type=ranking_type, period=period, limit=limit)
    # Backward compatibility with the pre-pagination digest CLI.
    if "offset" in inspect.signature(digest.candidates).parameters:
        args.update(offset=offset, include_history=False)
        return digest.candidates(root, **args)
    if offset:
        args["limit"] = min(1000, offset + limit)
    result = digest.candidates(root, **args)
    result["candidates"] = result["candidates"][offset:offset + limit]
    return result


def _event_records(query: dict) -> list[dict]:
    records = []
    for project in query["candidates"]:
        for event in project.get("event_candidates") or [project]:
            records.append({**event, "canonical_url": project["canonical_url"],
                            "first_discovered_at": project.get("first_discovered_at"),
                            "period_label": project.get("period_label")})
    return records


def _exact_candidates(root: Path, ranking_type: str, period: str, requested: list[str]) -> tuple[dict, list[dict]]:
    """Find exact event identities after verification may have reordered cards."""
    wanted = set(requested)
    found = {}
    scanned = 0
    offset = 0
    while True:
        query = _query(root, ranking_type, period, 100, offset)
        scanned += len(query["candidates"])
        for record in _event_records(query):
            cid = candidate_id(record)
            if cid in wanted:
                if record.get("selection_block") or record.get("selection_status") == "blocked":
                    raise SelectionError("requested candidate event is blocked by current history: " + cid)
                found[cid] = record
        next_offset = query.get("next_offset")
        if wanted.issubset(found) or next_offset is None:
            break
        if type(next_offset) is not int or next_offset <= offset:
            raise SelectionError("candidate pagination made no progress")
        offset = next_offset
    missing = wanted - found.keys()
    if missing:
        raise SelectionError("requested candidate is absent, changed event or blocked; query current candidates again: " + ", ".join(sorted(missing)))
    query = {**query, "returned_count": len(requested), "truncated": False, "next_offset": None,
             "exact_scan_count": scanned}
    return query, [found[cid] for cid in requested]


def prepare(root: Path, ranking_type: str, period: str, limit: int = 30, *,
            evidence_context: list | None = None, policy_path: Path | None = None, offset: int = 0,
            candidate_ids: list[str] | None = None, source_claims: dict | None = None,
            source_understanding: dict | None = None, candidate_contexts: dict | None = None,
            source_supported_systems: dict | None = None,
            policy_snapshot: dict | None = None, prompt_snapshot: str | None = None) -> dict:
    """Freeze candidate input and source text, without scoring.

    Supplied snapshots take precedence over the corresponding on-disk policy
    (including policy_path) or prompt. Omitted snapshots retain the file path.
    The optional project-reading candidate_contexts mapping binds each candidate
    to its entire source set, avoiding cross-candidate joins on shared event URLs.
    Discovery supported systems may be supplied by exact candidate ID because
    this source fact need not be a field in the production candidate ledger.
    """
    root = Path(root)
    digest.period_window(ranking_type, period)
    _integer(limit, 1, 1000, "limit")
    _integer(offset, 0, 1000000, "offset")
    if candidate_ids is not None:
        _strings(candidate_ids, "candidate_ids", nonempty=False)
        if len(candidate_ids) != len(set(candidate_ids)):
            raise SelectionError("candidate_ids must not contain duplicates")
        if len(candidate_ids) > limit:
            raise SelectionError("exact candidate set exceeds the review limit")
        if offset:
            raise SelectionError("candidate_ids cannot be combined with offset")
    policy = load_policy(root, policy_path) if policy_snapshot is None else validate_policy(policy_snapshot)
    prompt = (load_prompt(root, policy) if prompt_snapshot is None
              else _text(prompt_snapshot, "prompt_snapshot"))
    contexts = [] if evidence_context is None else evidence_context
    claims_by_id = {} if source_claims is None else source_claims
    understands = policy.get("understanding_contract") == UNDERSTANDING_CONTRACT
    discovery = policy.get("introduction_contract") == INTRODUCTION_CONTRACT
    if source_supported_systems is not None:
        if not discovery:
            raise SelectionError("source_supported_systems requires discovery introduction contract")
        if not isinstance(source_supported_systems, dict) or any(
                not isinstance(key, str) or not key.strip() for key in source_supported_systems):
            raise SelectionError("source_supported_systems must map candidate IDs to string or null")
        for value in source_supported_systems.values():
            _supported_systems(value)
    if candidate_contexts is not None:
        if not understands:
            raise SelectionError("candidate_contexts requires project-reading understanding contract")
        if not isinstance(candidate_contexts, dict) or any(not isinstance(key, str) or not key.strip() for key in candidate_contexts):
            raise SelectionError("candidate_contexts must map candidate IDs to source context lists")
    understanding_by_id = {} if source_understanding is None else source_understanding
    if understands and (not isinstance(understanding_by_id, dict) or any(not isinstance(key, str) for key in understanding_by_id)):
        raise SelectionError("source_understanding must map candidate IDs to understanding cards")
    if not isinstance(claims_by_id, dict) or any(not isinstance(key, str) for key in claims_by_id):
        raise SelectionError("source_claims must map candidate IDs to quoted claim lists")
    def validate_contexts(items, label):
        if not isinstance(items, list):
            raise SelectionError(label + " must be a list")
        for context in items:
            _object(context, ("url", "text", "fetched_at"), "context", optional=("source_scope",) if understands else ())
            digest._url(context["url"])
            _text(context["text"], "context.text")
            if digest._timestamp(context["fetched_at"], "context.fetched_at") > digest._now():
                raise SelectionError("context fetched_at cannot be in the future")

    validate_contexts(contexts, "evidence_context")
    if candidate_ids is None:
        query = _query(root, ranking_type, period, limit, offset)
        records = query["candidates"]
    else:
        query, records = _exact_candidates(root, ranking_type, period, candidate_ids)
    if source_supported_systems is not None and set(source_supported_systems) != {candidate_id(record) for record in records}:
        raise SelectionError("source_supported_systems keys must match exactly the prepared candidate IDs")
    if candidate_contexts is not None:
        if set(candidate_contexts) != {candidate_id(record) for record in records}:
            raise SelectionError("candidate_contexts keys must match exactly the prepared candidate IDs")
        for cid, items in candidate_contexts.items():
            validate_contexts(items, "candidate_contexts." + cid)
    cards = []
    seen = set()
    keys = ("url", "title", "category", "kind", "summary", "reason", "source_urls", "evidence_urls", "evidence_status", "verification_level", "verified_at", "published_at", "discovered_at", "change_note", "event")
    # Core blocked cards deliberately contain no source material. They are an
    # audit list, not extra scoring requests outside the caller's budget.
    for record in records:
        cid = candidate_id(record)
        if cid in seen:
            continue
        seen.add(cid)
        material = {key: record.get(key) for key in keys}
        if discovery:
            material["supported_systems"] = _supported_systems(
                record.get("supported_systems") if source_supported_systems is None else source_supported_systems[cid])
        if candidate_contexts is not None:
            relevant = copy.deepcopy(candidate_contexts[cid])
        else:
            allowed = set(record.get("evidence_urls", []))
            relevant = [copy.deepcopy(context) for context in contexts if context["url"] in allowed]
            # Discovered source text may be read but does not promote verification.
            if not relevant:
                relevant = [copy.deepcopy(context) for context in contexts if context["url"] in record.get("source_urls", [])]
        card = {"candidate_id": cid, "canonical_url": record["canonical_url"], "ranking_type": ranking_type,
                "profile": policy.get("kind_profiles", {}).get(record["kind"], policy["category_profiles"][record["category"]]),
                "material": material, "evidence_context": relevant,
                "observation": {key: record.get(key) for key in ("run_id", "collected_at", "verified_at", "first_discovered_at", "period_label")},
                "eligibility": _eligibility(record, bool(relevant))}
        if policy.get("assessment_contract") == SCOPED_CONTRACT:
            card["evaluation_target"] = evaluation_target(material)
            try:
                card["source_claims"] = validate_claims(claims_by_id[cid], relevant) if cid in claims_by_id else []
            except ValueError as exc:
                raise SelectionError(str(exc)) from exc
        if discovery:
            claims = card.get("source_claims", [])
            system_claims = [claim for claim in claims if claim["field"] == "supported_systems"]
            systems = material["supported_systems"]
            if systems is None and system_claims:
                raise SelectionError("null supported_systems cannot have source claims")
            if systems is not None and (not system_claims or any(claim["text"] != systems for claim in system_claims)):
                raise SelectionError("known supported_systems requires matching original source claims")
        if understands:
            card["understanding"] = _validated_understanding(understanding_by_id.get(cid), relevant,
                allow_unknown_conditions=policy.get('source_support_contract') == SOURCE_SUPPORT_CONTRACT)
        card["input_hash"] = _hash(card)
        cards.append(card)
    if understands and set(understanding_by_id) - seen:
        raise SelectionError("source_understanding includes unknown candidate IDs")
    prepared = {"schema_version": "digest-selection.prepare.v1", "ranking_type": ranking_type, "period": period,
                "policy": policy, "policy_hash": _hash(policy), "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(), "prompt_text": prompt,
                "cards": cards, "blocked_candidates": query.get("blocked_candidates", []),
                "coverage": {key: query.get(key) for key in ("total_candidates", "eligible_count", "returned_count", "blocked_count", "truncated", "next_offset", "exact_scan_count")},
                "offset": offset, "limit": limit, "requested_candidate_ids": candidate_ids}
    prepared["prepare_id"] = _hash(prepared)
    prepared["prepared_at"] = digest._now().isoformat()
    connection = _connect(root)
    try:
        with connection:
            connection.execute("INSERT OR IGNORE INTO preparations VALUES (?,?,?)", (prepared["prepare_id"], _json(prepared), prepared["prepared_at"]))
        return _load(connection, prepared["prepare_id"])
    finally:
        connection.close()


def _refs(value: Any, allowed: set, label: str, *, nonempty: bool = True) -> None:
    if set(_strings(value, label, nonempty=nonempty)) - allowed:
        raise SelectionError(f"{label} includes a URL absent from the prepared material")


def _calculate(policy: dict, card: dict, review: dict) -> dict:
    scores = review["scores"]
    raw = {key: scores[key]["score"] for key in DIMENSIONS} if scores is not None else None
    effective = copy.deepcopy(raw)
    caps = []
    if effective is not None:
        for flag in review["flags"]:
            for dimension, ceiling in policy["flag_caps"][flag["code"]].items():
                if effective[dimension] > ceiling:
                    caps.append({"flag": flag["code"], "dimension": dimension, "from": effective[dimension], "to": ceiling})
                    effective[dimension] = ceiling
    weights = policy["profiles"][card["profile"]]
    raw_total = sum(raw[key] * weights[key] for key in DIMENSIONS) if raw is not None else None
    total = sum(effective[key] * weights[key] for key in DIMENSIONS) if effective is not None else None
    flags = {flag["code"] for flag in review["flags"]}
    if review["precheck"]["status"] == "BLOCK":
        suggested = "reject"
    elif review["precheck"]["status"] == "UNKNOWN" or total is None or card["eligibility"]["state"] != "available" or flags.intersection(policy["defer_flags"]):
        suggested = "defer"
    elif total >= policy["thresholds"]["select"]:
        suggested = "select"
    elif total < policy["thresholds"]["reject_below"]:
        suggested = "reject"
    else:
        suggested = "defer"
    return dict(raw_score=raw_total, effective_scores=effective, total_score=total, applied_caps=caps, suggested_decision=suggested)


def _validate_assessment(policy: dict, card: dict, review: dict) -> dict:
    _object(review, ASSESSMENT_FIELDS, "assessment")
    _json(review)
    allowed = {context["url"] for context in card["evidence_context"]}
    precheck = _object(review["precheck"], ("status", "reasons", "evidence_refs"), "precheck")
    if precheck["status"] not in ("PASS", "UNKNOWN", "BLOCK"):
        raise SelectionError("precheck status must be PASS/UNKNOWN/BLOCK")
    _strings(precheck["reasons"], "precheck.reasons")
    _refs(precheck["evidence_refs"], allowed, "precheck.evidence_refs", nonempty=precheck["status"] != "UNKNOWN")
    if review["scores"] is not None:
        if not card["evidence_context"]:
            raise SelectionError("readable original material required for scores; use UNKNOWN and null scores")
        _object(review["scores"], DIMENSIONS, "scores")
        for dimension in DIMENSIONS:
            score = _object(review["scores"][dimension], ("score", "reason", "evidence_refs"), dimension)
            _integer(score["score"], 0, 10, dimension + ".score")
            _text(score["reason"], dimension + ".reason")
            _refs(score["evidence_refs"], {context["url"] for context in card["evidence_context"]}, dimension + ".evidence_refs")
    elif precheck["status"] == "PASS":
        raise SelectionError("PASS needs scores; missing/failed material must be UNKNOWN")
    if not isinstance(review["flags"], list):
        raise SelectionError("flags must be a list")
    seen_flags = set()
    for flag in review["flags"]:
        _object(flag, ("code", "reason", "evidence_refs"), "flag", optional=("basis", "gap"))
        _text(flag["code"], "flag.code")
        if flag["code"] not in policy["flag_caps"] or flag["code"] in seen_flags:
            raise SelectionError("unknown or repeated flag")
        basis_kind = policy.get("flag_basis", {}).get(flag["code"])
        gap_policy = policy.get("usage_evidence_gaps") if flag["code"] == "insufficient_usage_evidence" else None
        _object(flag, ("code", "reason", "evidence_refs") + (("basis",) if basis_kind else ()) + (("gap",) if gap_policy is not None else ()), "flag")
        allowed_kinds = policy.get("flag_kinds", {}).get(flag["code"])
        if allowed_kinds is not None and card["material"]["kind"] not in allowed_kinds:
            raise SelectionError("flag does not apply to candidate kind: " + flag["code"])
        if gap_policy is not None:
            gap = _text(flag["gap"], "flag.gap")
            if gap not in gap_policy.get(card.get("ranking_type"), []):
                raise SelectionError("flag evidence gap does not apply to ranking type: " + gap)
        seen_flags.add(flag["code"])
        _text(flag["reason"], "flag.reason")
        _refs(flag["evidence_refs"], allowed, "flag.evidence_refs")
        if basis_kind:
            basis = _object(flag["basis"], ("kind", "claim", "quote", "evidence_url"), "flag.basis")
            if basis["kind"] != basis_kind:
                raise SelectionError("flag basis kind does not match its policy")
            for key in ("claim", "quote", "evidence_url"):
                _text(basis[key], "flag.basis." + key)
            if basis["evidence_url"] not in flag["evidence_refs"]:
                raise SelectionError("flag basis must cite a flag evidence reference")
            if not any(context["url"] == basis["evidence_url"] and basis["quote"] in context["text"]
                       for context in card["evidence_context"]):
                raise SelectionError("flag basis quote not found in supplied original")
            # Quote presence proves provenance, not that the quote entails the
            # claim. Semantic consistency remains an explicit editorial check;
            # never delete a flag or rewrite its scores as an automatic repair.
    _text(review["reason"], "reason")
    return _calculate(policy, card, review)


def adapt_assessment(output: Any, *, policy: dict, card: dict) -> dict:
    """Audit one bounded, lossless shape conversion; never infer editorial values.

    Only a nonempty precheck.reasons string may become a one-element array.
    Metadata, wrappers, aliases, missing judgments and authority fields are
    rejected, not silently discarded. Callers persist this result alongside the
    provider receipt and bind accepted assessments with the full frozen card.
    """
    result = {"status": "rejected", "raw_output": copy.deepcopy(output), "assessment": None,
              "transformations": [], "error": None}
    try:
        if len(_json(output).encode("utf-8")) > MAX_ASSESSMENT_BYTES:
            raise SelectionError("assessment exceeds the 65536-byte limit")
        _object(output, ASSESSMENT_FIELDS, "assessment")
        assessment = copy.deepcopy(output)
        precheck = _object(assessment["precheck"], ("status", "reasons", "evidence_refs"), "precheck")
        if isinstance(precheck["reasons"], str):
            _text(precheck["reasons"], "precheck.reasons")
            precheck["reasons"] = [precheck["reasons"]]
            result["transformations"].append({"path": "precheck.reasons", "operation": "wrap_nonempty_string_in_array"})
        _validate_assessment(policy, card, assessment)
        result.update(status="accepted", assessment=assessment)
    except SelectionError as exc:
        result["error"] = str(exc)
    return result


def build_review(root: Path, prepare_id: str, candidate_id: str, assessment: dict, reviewer: dict) -> dict:
    """Bind model judgments and let Python decide, without recording them yet.

    assessment contains only precheck, scores, flags and reason. Persist the raw
    provider response separately; this helper never asks a model to do arithmetic.
    """
    prepared = load_preparation(root, prepare_id)
    card = _card(prepared, candidate_id)
    _object(reviewer, ("kind", "name", "model"), "reviewer")
    if reviewer["kind"] not in ("human", "model"):
        raise SelectionError("reviewer.kind must be human/model")
    _text(reviewer["name"], "reviewer.name")
    if reviewer["kind"] == "model":
        _text(reviewer["model"], "reviewer.model")
    elif reviewer["model"] is not None:
        raise SelectionError("human reviewer.model must be null")
    computed = _validate_assessment(prepared["policy"], card, assessment)
    return {"schema_version": "digest-selection.review.v1", "prepare_id": prepare_id,
            "candidate_id": candidate_id, "input_hash": card["input_hash"],
            "reviewer": copy.deepcopy(reviewer), **copy.deepcopy(assessment),
            "decision": computed["suggested_decision"], "override_reason": None}


def record(root: Path, review: dict) -> dict:
    _object(review, ("schema_version", "prepare_id", "candidate_id", "input_hash", "reviewer", "precheck", "scores", "flags", "decision", "reason", "override_reason"), "review")
    if review["schema_version"] != "digest-selection.review.v1":
        raise SelectionError("unsupported review schema")
    _json(review)
    connection = _connect(Path(root))
    try:
        prepared = _load(connection, review["prepare_id"])
        card = _card(prepared, review["candidate_id"])
        if review["input_hash"] != card["input_hash"]:
            raise SelectionError("input_hash differs from frozen preparation")
        policy = prepared["policy"]
        reviewer = _object(review["reviewer"], ("kind", "name", "model"), "reviewer")
        if reviewer["kind"] not in ("human", "model"):
            raise SelectionError("reviewer.kind must be human/model")
        _text(reviewer["name"], "reviewer.name")
        if reviewer["kind"] == "model":
            _text(reviewer["model"], "reviewer.model")
        elif reviewer["model"] is not None:
            raise SelectionError("human reviewer.model must be null")
        computed = _validate_assessment(policy, card, {key: review[key] for key in ("precheck", "scores", "flags", "reason")})
        if review["decision"] not in ("select", "defer", "reject"):
            raise SelectionError("unknown decision")
        if review["decision"] == "select" and card["eligibility"]["state"] != "available":
            raise SelectionError("editorial overrides cannot bypass evidence, period or duplicate eligibility")
        _text(review["reason"], "reason")
        if review["override_reason"] is not None:
            _text(review["override_reason"], "override_reason")
            if reviewer["kind"] != "human":
                raise SelectionError("models cannot override computed decisions")
        if review["decision"] != computed["suggested_decision"] and not review["override_reason"]:
            raise SelectionError("decision differs from policy; a human override reason is required")
        result = {**copy.deepcopy(review), **computed, "canonical_url": card["canonical_url"], "profile": card["profile"],
                  "policy_hash": prepared["policy_hash"], "prompt_hash": prepared["prompt_hash"], "policy_version": policy["version"],
                  "recorded_at": digest._now().isoformat()}
        review_hash = _hash(review)
        previous = connection.execute("SELECT payload FROM reviews WHERE review_hash=?", (review_hash,)).fetchone()
        if previous:
            return {"status": "unchanged", **json.loads(previous["payload"])}
        with connection:
            connection.execute("INSERT INTO reviews(review_hash,prepare_id,candidate_id,input_hash,payload,created_at) VALUES (?,?,?,?,?,?)",
                (review_hash, review["prepare_id"], review["candidate_id"], review["input_hash"], _json(result), result["recorded_at"]))
        return {"status": "recorded", **result}
    finally:
        connection.close()


def _reviews(connection: sqlite3.Connection, prepare_id: str) -> dict:
    # Append-only history; the latest review within exactly this frozen input wins.
    result = {}
    for row in connection.execute("SELECT candidate_id,payload FROM reviews WHERE prepare_id=? ORDER BY id", (prepare_id,)):
        result[row["candidate_id"]] = json.loads(row["payload"])
    return result


def rank(root: Path, prepare_id: str) -> dict:
    connection = _connect(Path(root))
    try:
        prepared = _load(connection, prepare_id)
        reviews = _reviews(connection, prepare_id)
        # Refresh hard gates: scoring does not make a later duplicate publishable.
        current = _query(Path(root), prepared["ranking_type"], prepared["period"], 1000)
        current_by_id = {candidate_id(item): item for item in _event_records(current)}
        blocked_by_project = {item["canonical_url"]: item for item in current.get("blocked_candidates", [])}
        needed = {card["candidate_id"] for card in prepared["cards"]}
        while current.get("next_offset") is not None and not needed.issubset(current_by_id):
            current = _query(Path(root), prepared["ranking_type"], prepared["period"], 1000, current["next_offset"])
            current_by_id.update({candidate_id(item): item for item in _event_records(current)})
        result = {"prepare_id": prepare_id, "policy_version": prepared["policy"]["version"], "available": [], "needs_evidence": [], "blocked": [], "deferred": [], "rejected": [], "unreviewed": [],
                  "publication_note": "Editorial order only. digest render/archive must recheck current evidence, freshness, period and all-level history."}
        for card in prepared["cards"]:
            review = reviews.get(card["candidate_id"])
            current_record = current_by_id.get(card["candidate_id"]) or blocked_by_project.get(card["canonical_url"])
            eligibility = _eligibility(current_record, bool(card["evidence_context"])) if current_record else {"state": "blocked", "reasons": ["prepared event is no longer present in current eligible candidates; prepare again"]}
            row = {"candidate_id": card["candidate_id"], "canonical_url": card["canonical_url"], "title": card["material"]["title"], "event": card["material"].get("event"), "input_hash": card["input_hash"], "eligibility": eligibility, "review": review}
            if eligibility["state"] == "blocked":
                group = "blocked"
            elif not review:
                group = "unreviewed"
            elif eligibility["state"] == "needs_evidence":
                group = "needs_evidence"
            elif review["decision"] == "defer":
                group = "deferred"
            elif review["decision"] == "reject":
                group = "rejected"
            else:
                group = "available"
            result[group].append(row)
        for group in ("available", "needs_evidence", "blocked", "deferred", "rejected", "unreviewed"):
            result[group].sort(key=lambda item: (-((item["review"] or {}).get("total_score") or 0), item["canonical_url"], item["candidate_id"]))
        return result
    finally:
        connection.close()


def label(root: Path, document: dict) -> dict:
    _object(document, ("schema_version", "prepare_id", "candidate_id", "split", "label", "editor", "reason"), "label")
    if document["schema_version"] != "digest-selection.label.v1" or document["split"] not in ("dev", "holdout") or document["label"] not in ("select", "reject", "either"):
        raise SelectionError("invalid label schema/split/label")
    _text(document["editor"], "human editor")
    _text(document["reason"], "label reason")
    connection = _connect(Path(root))
    try:
        prepared = _load(connection, document["prepare_id"])
        card = _card(prepared, document["candidate_id"])
        canonical = card["canonical_url"]
        result = {**copy.deepcopy(document), "canonical_url": canonical, "input_hash": card["input_hash"], "labelled_at": digest._now().isoformat(), "provenance": "human_asserted"}
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute("SELECT split FROM label_splits WHERE canonical_url=?", (canonical,)).fetchone()
            if previous and previous["split"] != document["split"]:
                raise SelectionError("a canonical project cannot appear in both dev and holdout, including other events")
            connection.execute("INSERT OR IGNORE INTO label_splits VALUES (?,?)", (canonical, document["split"]))
            connection.execute("INSERT INTO labels(prepare_id,candidate_id,canonical_url,payload,created_at) VALUES (?,?,?,?,?)",
                (document["prepare_id"], document["candidate_id"], canonical, _json(result), result["labelled_at"]))
        return result
    finally:
        connection.close()


def _metrics(rows: list, threshold: int | None = None) -> dict:
    tp = fp = fn = tn = 0
    false_positives, false_negatives = [], []
    for row in rows:
        review, gold, card = row["review"], row["label"], row["card"]
        predicted = review["decision"] == "select" if threshold is None else (review["total_score"] is not None and review["total_score"] >= threshold and review["precheck"]["status"] == "PASS" and card["eligibility"]["state"] == "available" and not row["defer_flag"])
        actual = gold["label"] == "select"
        case = {"candidate_id": card["candidate_id"], "canonical_url": card["canonical_url"], "score": review["total_score"], "decision": review["decision"], "reason": review["reason"], "gold_reason": gold["reason"]}
        if predicted and actual:
            tp += 1
        elif predicted:
            fp += 1
            false_positives.append(case)
        elif actual:
            fn += 1
            false_negatives.append(case)
        else:
            tn += 1
    return {"true_positive": tp, "false_positive": fp, "false_negative": fn, "true_negative": tn,
            "precision": tp / (tp + fp) if tp + fp else None, "recall": tp / (tp + fn) if tp + fn else None,
            "false_positives": false_positives, "false_negatives": false_negatives}


def evaluate(root: Path, prepare_id: str, *, split: str = "dev") -> dict:
    if split not in ("dev", "holdout"):
        raise SelectionError("split must be dev/holdout")
    connection = _connect(Path(root))
    try:
        prepared = _load(connection, prepare_id)
        reviews = _reviews(connection, prepare_id)
        labels = {}
        for row in connection.execute("SELECT candidate_id,payload FROM labels WHERE prepare_id=? ORDER BY id", (prepare_id,)):
            labels[row["candidate_id"]] = json.loads(row["payload"])
        rows = []
        excluded = dict(unlabelled=0, either=0, other_split=0, unreviewed=0)
        for card in prepared["cards"]:
            gold = labels.get(card["candidate_id"])
            review = reviews.get(card["candidate_id"])
            if not gold:
                excluded["unlabelled"] += 1
            elif gold["split"] != split:
                excluded["other_split"] += 1
            elif gold["label"] == "either":
                excluded["either"] += 1
            elif not review:
                excluded["unreviewed"] += 1
            else:
                rows.append(dict(card=card, review=review, label=gold, defer_flag=bool({flag["code"] for flag in review["flags"]}.intersection(prepared["policy"]["defer_flags"]))))
        scan = []
        if split == "dev":
            for threshold in range(40, 91, 5):
                metrics = _metrics(rows, threshold)
                scan.append({"threshold": threshold, **{key: value for key, value in metrics.items() if key not in ("false_positives", "false_negatives")}})
        return {"prepare_id": prepare_id, "split": split, "evaluated_count": len(rows), "excluded": excluded,
                **_metrics(rows), "threshold_scan": scan,
                "quality_claim": "No validated quality claim without representative human gold; synthetic tests are not human labels. Holdout is not used for threshold search."}
    finally:
        connection.close()


def export_labels(root: Path, prepare_id: str) -> dict:
    connection = _connect(Path(root))
    try:
        prepared = _load(connection, prepare_id)
        return {"schema_version": "digest-selection.label-queue.v1", "prepare_id": prepare_id,
                "instruction": "A human must choose label and split and supply a reason. null values are not accepted by label. Do not auto-label with a model.",
                "items": [{"candidate_id": card["candidate_id"], "canonical_url": card["canonical_url"], "title": card["material"]["title"], "material": card["material"], "evidence_context": card["evidence_context"], "label": None, "split": None, "reason": None} for card in prepared["cards"]]}
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("prepare", "record", "rank", "label", "evaluate", "export-labels"):
        item = sub.add_parser(command)
        item.add_argument("--root", type=Path, default=Path("."))
        item.add_argument("--output", type=Path)
        if command == "prepare":
            item.add_argument("--ranking-type", choices=digest.RANKINGS, required=True)
            item.add_argument("--period", required=True)
            item.add_argument("--limit", type=int, default=30)
            item.add_argument("--offset", type=int, default=0)
            item.add_argument("--candidate-id", action="append", dest="candidate_ids", help="Repeat to freeze an exact verified event set; cannot combine with offset")
            item.add_argument("--context", type=Path, help="JSON array of {url,text,fetched_at} source extracts")
            item.add_argument("--claims", type=Path, help="JSON mapping of candidate IDs to quoted source claims; never inferred for legacy input")
            item.add_argument("--policy", type=Path)
        elif command in ("record", "label"):
            item.add_argument("--input", type=Path, required=True)
        else:
            item.add_argument("--prepare-id", required=True)
            if command == "evaluate":
                item.add_argument("--split", choices=("dev", "holdout"), default="dev")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            contexts = json.loads(args.context.read_text(encoding="utf-8")) if args.context else None
            claims = json.loads(args.claims.read_text(encoding="utf-8")) if args.claims else None
            result = prepare(args.root, args.ranking_type, args.period, args.limit, evidence_context=contexts, policy_path=args.policy, offset=args.offset, candidate_ids=args.candidate_ids, source_claims=claims)
        elif args.command in ("record", "label"):
            result = globals()[args.command](args.root, json.loads(args.input.read_text(encoding="utf-8")))
        elif args.command == "evaluate":
            result = evaluate(args.root, args.prepare_id, split=args.split)
        elif args.command == "export-labels":
            result = export_labels(args.root, args.prepare_id)
        else:
            result = rank(args.root, args.prepare_id)
        output = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            digest._atomic_write(args.output, output)
        else:
            print(output, end="")
        return 0
    except (SelectionError, digest.DigestError, OSError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
