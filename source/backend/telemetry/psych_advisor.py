"""Process recent teacher notes into a scholar learning-style profile matrix.

Expected Aiven tables:

* ``Teacher_Notes(student_id, note_text, created_at)``
* ``Scholar_Traits(student_id, kinesthetic_percentage,
  visual_percentage, analytical_percentage)``

The function intentionally uses parameterized SQL and a single transaction so
note reads and profile writes stay on the same MySQL connection.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.database import create_database_engine
from backend.models import (
    CurriculumDeck,
    PedagogicalStandardRule,
    SchoolTenant,
    TeacherNote,
    TenantScholarTrait,
)

TRAIT_CATEGORIES = (
    "analytical_logic",
    "spatial_visual",
    "kinesthetic_mastery",
)
CAPS_TRAIT_CATEGORIES = TRAIT_CATEGORIES

RECENT_NOTE_LIMIT = 40
RECENCY_DECAY = Decimal("0.85")

LEARNING_STYLE_MARKERS: dict[str, tuple[str, ...]] = {
    "kinesthetic": (
        "kinesthetic",
        "hands-on",
        "movement",
        "touch",
        "build",
        "role-play",
        "physical",
        "demonstrate",
    ),
    "visual": (
        "visual",
        "diagram",
        "picture",
        "colour",
        "color",
        "map",
        "draw",
        "chart",
        "see",
    ),
    "analytical": (
        "analytical",
        "logic",
        "pattern",
        "reason",
        "calculate",
        "sequence",
        "problem-solving",
        "evidence",
    ),
}

MARKER_PATTERNS = {
    style: tuple(
        re.compile(rf"\b{re.escape(marker)}\b", re.IGNORECASE)
        for marker in markers
    )
    for style, markers in LEARNING_STYLE_MARKERS.items()
}


def _neutral_caps_traits() -> dict[str, int]:
    return {category: 0 for category in CAPS_TRAIT_CATEGORIES}


def parse_caps_traits(
    educator_note: str,
    *,
    db: Session,
    curriculum_deck_id: int | None,
) -> dict[str, int]:
    """Count trait hits from validated rules assigned to a tenant's deck.

    Missing, empty, or malformed deck data produces neutral evidence. This
    avoids applying another school's curriculum or failing an observation due
    to incomplete deck setup.
    """
    note_text = educator_note if isinstance(educator_note, str) else ""
    if not isinstance(curriculum_deck_id, int) or curriculum_deck_id <= 0:
        return _neutral_caps_traits()

    rules = db.scalars(
        select(PedagogicalStandardRule).where(
            PedagogicalStandardRule.deck_id == curriculum_deck_id
        )
    ).all()
    if not rules or {rule.trait_category for rule in rules} != set(
        CAPS_TRAIT_CATEGORIES
    ):
        return _neutral_caps_traits()

    try:
        return parse_curriculum_traits(note_text, rules)
    except CurriculumConfigurationError:
        return _neutral_caps_traits()


class CurriculumConfigurationError(ValueError):
    """Raised when a school's curriculum rules cannot safely score an observation."""


class AssignedCurriculumRules(tuple):
    """Request-scoped rules carrying the identity actually used by the scorer."""

    def __new__(cls, rules, *, session, tenant_id, deck):
        instance = super().__new__(cls, rules)
        instance.session = session
        instance.tenant_id = tenant_id
        instance.deck_id = deck.id
        instance.deck_name = deck.name
        return instance


def validate_curriculum_rules(
    rules: Iterable[PedagogicalStandardRule],
) -> tuple[dict[str, re.Pattern[str]], list[dict[str, Any]]]:
    """Compile rules once and report all category-specific configuration errors."""
    patterns: dict[str, re.Pattern[str]] = {}
    errors: list[dict[str, Any]] = []
    seen: set[str] = set()

    def issue(category: str | None, code: str, message: str) -> None:
        errors.append({"category": category, "code": code, "message": message})

    for rule in rules:
        category = rule.trait_category
        regex_string = rule.keywords_regex_string
        if category not in TRAIT_CATEGORIES:
            issue(category, "unsupported_category",
                  f"Unsupported curriculum trait category: {category!r}. Use one of: {', '.join(TRAIT_CATEGORIES)}.")
            continue
        if category in seen:
            issue(category, "duplicate_rule",
                  f"Multiple curriculum rules are configured for '{category}'. Keep one rule per category.")
        seen.add(category)
        if not isinstance(regex_string, str) or not regex_string.strip():
            issue(category, "empty_rule", f"The curriculum rule for '{category}' is empty. Supply a keyword regex.")
            continue
        try:
            pattern = re.compile(regex_string, re.IGNORECASE)
        except (re.error, OverflowError, RecursionError) as error:
            issue(category, "malformed_regex",
                  f"The curriculum rule for '{category}' is not a valid regex: {error}. Correct its syntax.")
            continue
        if pattern.search("") is not None:
            issue(category, "empty_match",
                  f"The curriculum rule for '{category}' must not match empty text. Require a consuming keyword.")
            continue
        # Inspect minimum consumed width rather than testing an arbitrary phrase:
        # lookaheads such as (?=diagram) would otherwise evade validation.
        if re._parser.parse(regex_string, re.IGNORECASE).getwidth()[0] == 0:
            issue(category, "zero_width",
                  f"The curriculum rule for '{category}' must not use zero-width matches. Require at least one consumed character.")
            continue
        patterns[category] = pattern

    if not seen and not errors:
        issue(None, "empty_deck", "The curriculum deck has no rules. Configure at least one category rule.")
    return patterns, errors


def load_assigned_curriculum_rules(
    session: Session,
    school_tenant_id: int | None,
) -> tuple[PedagogicalStandardRule, ...]:
    """Load the active school's rules or fail before observation mutation."""
    if school_tenant_id is None:
        raise CurriculumConfigurationError("The active school is unavailable.")

    school = session.scalar(
        select(SchoolTenant)
        .where(SchoolTenant.id == school_tenant_id)
        .options(
            selectinload(SchoolTenant.curriculum_deck).selectinload(
                CurriculumDeck.rules
            )
        )
    )
    if school is None or not school.is_active:
        raise CurriculumConfigurationError("The active school is unavailable.")
    if school.curriculum_deck is None:
        raise CurriculumConfigurationError(
            f"The school '{school.name}' does not have a curriculum deck."
        )
    if not school.curriculum_deck.rules:
        raise CurriculumConfigurationError(
            f"The curriculum deck '{school.curriculum_deck.name}' has no rules."
        )
    return AssignedCurriculumRules(
        school.curriculum_deck.rules,
        session=session,
        tenant_id=school_tenant_id,
        deck=school.curriculum_deck,
    )


def parse_curriculum_traits(
    educator_note: str,
    rules: Iterable[PedagogicalStandardRule],
) -> dict[str, int]:
    """Count validated assigned-curriculum regex matches by trait category."""
    note_text = educator_note if isinstance(educator_note, str) else ""
    patterns_by_category, errors = validate_curriculum_rules(rules)
    if errors:
        raise CurriculumConfigurationError(errors[0]["message"])

    hits = {
        category: (
            len(patterns_by_category[category].findall(note_text))
            if category in patterns_by_category
            else 0
        )
        for category in TRAIT_CATEGORIES
    }
    if isinstance(rules, AssignedCurriculumRules):
        # The insert listener consumes this exact result, without re-running
        # regexes or resolving a possibly changed tenant deck a second time.
        rules.session.info.setdefault("observation_curriculum_contexts", {})[
            (rules.tenant_id, note_text)
        ] = {
            "deck_id": rules.deck_id,
            "deck_name": rules.deck_name,
            "category_hits": dict(hits),
        }
    return hits


def _bounded_score(value: object) -> float:
    """Convert a supplied score to the normalized telemetry score range."""
    if isinstance(value, bool):
        return 0.0
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return 0.0
    if normalized != normalized or normalized in (float("inf"), float("-inf")):
        return 0.0
    return min(100.0, max(0.0, normalized))


def _nonnegative_hit_count(value: object) -> int:
    """Convert supplied trait evidence to a safe whole keyword count."""
    if isinstance(value, bool):
        return 0
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, normalized)


def compute_profile_mutation(
    current_profile: dict,
    trait_hits: dict,
    fidget_score: float,
    grit_score: float,
) -> dict:
    """Return a new telemetry profile with trait evidence included."""
    updated_profile = dict(current_profile or {})
    safe_hits = trait_hits or {}
    for category in TRAIT_CATEGORIES:
        current_score = _bounded_score(updated_profile.get(category, 0.0))
        hit_count = _nonnegative_hit_count(safe_hits.get(category, 0))
        updated_profile[category] = min(100.0, current_score + hit_count * 5.0)

    updated_profile["fidget_index"] = _bounded_score(fidget_score)
    updated_profile["conative_grit_score"] = _bounded_score(grit_score)
    return updated_profile


def _marker_hits(note_text: str, style: str) -> int:
    """Count recognized markers once per occurrence, capped per note."""
    hits = sum(
        len(pattern.findall(note_text)) for pattern in MARKER_PATTERNS[style]
    )
    return min(hits, 4)


def _weighted_evidence(notes: Iterable[Mapping[str, Any]]) -> dict[str, Decimal]:
    """Convert newest-first notes into normalized recency-weighted percentages."""
    scores = {style: Decimal("0") for style in LEARNING_STYLE_MARKERS}
    total_score = Decimal("0")

    for note_index, note in enumerate(notes):
        note_text = str(note.get("note_text") or "")
        recency_weight = RECENCY_DECAY**note_index
        for style in scores:
            score = recency_weight * Decimal(_marker_hits(note_text, style))
            scores[style] += score
            total_score += score

    if not total_score:
        return {style: Decimal("0") for style in scores}

    return {
        style: (score / total_score * Decimal("100")).quantize(Decimal("0.01"))
        for style, score in scores.items()
    }


def _normalized_profile(profile: Mapping[str, Any]) -> dict[str, Decimal]:
    """Normalize three percentages and keep their total at exactly 100."""
    values = {
        style: Decimal(str(profile.get(f"{style}_percentage") or 0))
        for style in LEARNING_STYLE_MARKERS
    }
    total = sum(values.values(), Decimal("0"))
    if total <= 0:
        return {style: Decimal("0") for style in values}

    normalized = {
        style: (value / total * Decimal("100")).quantize(Decimal("0.01"))
        for style, value in values.items()
    }
    remainder = Decimal("100.00") - sum(normalized.values(), Decimal("0"))
    largest_style = max(normalized, key=normalized.get)
    normalized[largest_style] += remainder
    return normalized


def _normalized_student_id(student_id: int | str) -> int:
    if student_id is None or not str(student_id).strip():
        raise ValueError("student_id is required.")
    try:
        normalized = int(str(student_id).strip())
    except ValueError as error:
        raise ValueError("student_id must be a positive integer.") from error
    if normalized <= 0:
        raise ValueError("student_id must be a positive integer.")
    return normalized


def _normalized_school_tenant_id(school_tenant_id: int) -> int:
    if isinstance(school_tenant_id, bool):
        raise ValueError("school_tenant_id must be a positive integer.")
    try:
        normalized = int(school_tenant_id)
    except (TypeError, ValueError) as error:
        raise ValueError("school_tenant_id must be a positive integer.") from error
    if normalized <= 0:
        raise ValueError("school_tenant_id must be a positive integer.")
    return normalized


def _process_teacher_observations(
    student_id: int | str,
    session: Session,
    school_tenant_id: int,
) -> dict[str, Any]:
    normalized_id = _normalized_student_id(student_id)
    normalized_tenant_id = _normalized_school_tenant_id(school_tenant_id)
    notes_query = (
        select(TeacherNote.note_text, TeacherNote.created_at)
        .where(
            TeacherNote.student_id == normalized_id,
            TeacherNote.school_tenant_id == normalized_tenant_id,
        )
        .order_by(TeacherNote.created_at.desc(), TeacherNote.id.desc())
        .limit(RECENT_NOTE_LIMIT)
    )
    notes = session.execute(notes_query).mappings().all()

    if not notes:
        raise LookupError(
            f"No recent teacher notes found for student_id={normalized_id}."
        )

    trait = session.get(
        TenantScholarTrait,
        {
            "school_tenant_id": normalized_tenant_id,
            "student_id": normalized_id,
        },
    )
    evidence = _weighted_evidence(notes)
    profile = _normalized_profile(
        {
            f"{style}_percentage": evidence[style]
            for style in evidence
        }
    )

    if trait is None:
        trait = TenantScholarTrait(
            school_tenant_id=normalized_tenant_id,
            student_id=normalized_id,
        )
        session.add(trait)
    trait.kinesthetic_percentage = profile["kinesthetic"]
    trait.visual_percentage = profile["visual"]
    trait.analytical_percentage = profile["analytical"]
    session.flush()

    return {
        "student_id": normalized_id,
        "school_tenant_id": normalized_tenant_id,
        "notes_considered": len(notes),
        "profile": {
            style: float(value) for style, value in profile.items()
        },
    }


def process_teacher_observations(
    student_id: int | str,
    *,
    school_tenant_id: int,
    session: Session | None = None,
) -> dict[str, Any]:
    """Update a tenant-scoped matrix from that tenant's recent observations.

    Notes are weighted newest-first using ``RECENCY_DECAY``. Recognized
    Kinesthetic, Visual, and Analytical markers are converted to percentages.
    The profile is recomputed deterministically from the latest notes, so
    rerunning without new evidence produces the same matrix.
    """
    if session is not None:
        return _process_teacher_observations(
            student_id,
            session,
            school_tenant_id,
        )

    engine = create_database_engine()
    try:
        with Session(engine) as owned_session:
            with owned_session.begin():
                return _process_teacher_observations(
                    student_id,
                    owned_session,
                    school_tenant_id,
                )
    finally:
        engine.dispose()