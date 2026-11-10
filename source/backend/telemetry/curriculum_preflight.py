"""Bounded, database-free administrative curriculum checks."""

from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace

from backend.telemetry.psych_advisor import TRAIT_CATEGORIES, validate_curriculum_rules

MAX_RULES = 32
MAX_REGEX_LENGTH = 2000
PREFLIGHT_TIMEOUT_SECONDS = 3


def evaluate_rules(rules: list[dict], sample_text: str | None) -> dict:
    """Pure worker operation; never loads learner data or opens a database."""
    errors = []
    if len(rules) > MAX_RULES:
        errors.append({
            "category": None, "code": "too_many_rules",
            "message": f"The deck exceeds {MAX_RULES} rules. Keep one rule per supported category.",
        })
    for rule in rules[:MAX_RULES]:
        if len(rule["keywords_regex_string"] or "") > MAX_REGEX_LENGTH:
            errors.append({
                "category": rule["trait_category"], "code": "rule_too_long",
                "message": f"Shorten this category's regex to {MAX_REGEX_LENGTH} characters or fewer.",
            })
    if errors:
        return {"valid": False, "errors": errors, "matches": None}
    patterns, errors = validate_curriculum_rules(SimpleNamespace(**rule) for rule in rules)
    matches = None
    if not errors and sample_text is not None:
        matches = {
            category: sum(1 for _ in patterns[category].finditer(sample_text))
            if category in patterns else 0
            for category in TRAIT_CATEGORIES
        }
    return {"valid": not errors, "errors": errors, "matches": matches}


def run_preflight(rules: list[dict], sample_text: str | None) -> dict:
    """Isolate arbitrary stored regexes so backtracking cannot hang an API worker."""
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "backend.telemetry.curriculum_preflight"],
            input=json.dumps({"rules": rules, "sample_text": sample_text}),
            text=True, capture_output=True, check=True,
            timeout=PREFLIGHT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {
            "valid": False,
            "errors": [{
                "category": None, "code": "evaluation_timeout",
                "message": "The deck check exceeded its time limit. Simplify nested repetitions or overlapping alternatives and retry.",
            }],
            "matches": None,
        }
    return json.loads(completed.stdout)


if __name__ == "__main__":
    payload = json.load(sys.stdin)
    print(json.dumps(evaluate_rules(payload["rules"], payload["sample_text"])))
