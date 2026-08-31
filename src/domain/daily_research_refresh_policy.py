"""Fail-closed five-section snapshot refresh policy for Phase 17."""

from __future__ import annotations

from typing import Any, Mapping


REQUIRED_ANALYSIS_SECTIONS = (
    "valuation",
    "liquidity",
    "technical_support",
    "screening",
    "target_confluence",
)

_DISALLOWED_AVAILABLE_FLAGS = (
    "quality_warning", "partial", "unknown", "blocked", "stale", "pending", "error"
)


def _explicit_not_applicable(section: Mapping[str, Any]) -> bool:
    applicability = section.get("applicability")
    return bool(
        isinstance(applicability, Mapping)
        and applicability.get("applicable") is False
        and isinstance(applicability.get("applicability_reason"), str)
        and applicability["applicability_reason"].strip()
        and isinstance(applicability.get("method_policy_version"), str)
        and applicability["method_policy_version"].strip()
    )


def evaluate_required_analysis_sections(
    analysis: Mapping[str, Any],
) -> dict[str, Any]:
    for name in REQUIRED_ANALYSIS_SECTIONS:
        section = analysis.get(name)
        if not isinstance(section, Mapping):
            return {
                "eligible": False,
                "error": "required_analysis_section_missing",
                "section": name,
            }
        status = section.get("status")
        if status == "not_applicable":
            if not _explicit_not_applicable(section):
                return {
                    "eligible": False,
                    "error": "required_analysis_section_applicability_unresolved",
                    "section": name,
                }
            continue
        if status != "available":
            return {
                "eligible": False,
                "error": "required_analysis_section_unavailable",
                "section": name,
            }
        if any(section.get(flag) for flag in _DISALLOWED_AVAILABLE_FLAGS):
            return {
                "eligible": False,
                "error": "required_analysis_section_unavailable",
                "section": name,
            }
        quality = section.get("quality")
        if isinstance(quality, Mapping) and quality.get("status") not in {None, "available"}:
            return {
                "eligible": False,
                "error": "required_analysis_section_unavailable",
                "section": name,
            }
    return {"eligible": True, "error": None, "section": None}


__all__ = ["REQUIRED_ANALYSIS_SECTIONS", "evaluate_required_analysis_sections"]
