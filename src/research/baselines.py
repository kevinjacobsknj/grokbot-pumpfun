"""Baseline comparison arms for falsifiable research.

Arms share the same observation set and paper fill assumptions.
Do not claim agent value without prospective OOS improvement vs baselines.
"""

from __future__ import annotations

import random
from typing import Any, Callable

from ..observation import Observation

BASELINE_ARMS: tuple[str, ...] = (
    "random_eligible_launch",
    "basic_deterministic_filter_only",
    "monitor_quantitative_signals_only",
    "monitor_plus_auditor",
    "monitor_plus_auditor_plus_narrative",
    "full_system",
)


def _eligible(obs: Observation) -> bool:
    return obs.candidate_status in ("accepted", "pending") and not obs.missing_trade_stream


def arm_random_eligible_launch(
    observations: list[Observation],
    *,
    seed: int = 0,
    rate: float = 0.1,
) -> list[str]:
    rng = random.Random(seed)
    return [
        o.observation_id
        for o in observations
        if _eligible(o) and rng.random() < rate
    ]


def arm_basic_deterministic_filter_only(
    observations: list[Observation],
    *,
    min_buyers: int = 5,
    max_curve_progress: float = 0.4,
) -> list[str]:
    selected = []
    for o in observations:
        progress = float(o.bonding_curve_state.get("curve_progress") or 0.0)
        if o.unique_buyers >= min_buyers and progress < max_curve_progress:
            if o.candidate_status != "rejected" or o.reject_reason in ("too_young", "few_buyers"):
                selected.append(o.observation_id)
    return selected


def arm_monitor_quantitative_signals_only(
    observations: list[Observation],
    *,
    min_buyers: int = 8,
    max_top_concentration: float = 0.5,
) -> list[str]:
    selected = []
    for o in observations:
        if not _eligible(o):
            continue
        top5 = float(o.metadata.get("top5_share") or o.bonding_curve_state.get("top5_share") or 0.0)
        if o.unique_buyers >= min_buyers and top5 <= max_top_concentration:
            selected.append(o.observation_id)
    return selected


def arm_monitor_plus_auditor(
    observations: list[Observation],
    *,
    auditor_scores: dict[str, float] | None = None,
    min_audit: float = 0.5,
) -> list[str]:
    base = set(arm_monitor_quantitative_signals_only(observations))
    scores = auditor_scores or {}
    return [oid for oid in base if scores.get(oid, 0.0) >= min_audit]


def arm_monitor_plus_auditor_plus_narrative(
    observations: list[Observation],
    *,
    auditor_scores: dict[str, float] | None = None,
    narrative_scores: dict[str, float] | None = None,
    min_audit: float = 0.5,
    min_narrative: float = 0.4,
) -> list[str]:
    base = set(
        arm_monitor_plus_auditor(
            observations, auditor_scores=auditor_scores, min_audit=min_audit
        )
    )
    scores = narrative_scores or {}
    return [oid for oid in base if scores.get(oid, 0.0) >= min_narrative]


def arm_full_system(
    observations: list[Observation],
    *,
    auditor_scores: dict[str, float] | None = None,
    narrative_scores: dict[str, float] | None = None,
    timing_scores: dict[str, float] | None = None,
    checker_approve: dict[str, bool] | None = None,
    min_total: float = 0.65,
) -> list[str]:
    """Requires checker approval; scores must be provided from prior agent runs."""
    selected = []
    a = auditor_scores or {}
    n = narrative_scores or {}
    t = timing_scores or {}
    c = checker_approve or {}
    for o in observations:
        if not _eligible(o):
            continue
        total = 0.3 * a.get(o.observation_id, 0.0)
        total += 0.25 * n.get(o.observation_id, 0.0)
        total += 0.15 * t.get(o.observation_id, 0.0)
        # metrics placeholder from unique buyers normalized
        metrics = min(1.0, o.unique_buyers / 20.0)
        total += 0.30 * metrics
        if total >= min_total and c.get(o.observation_id, False):
            selected.append(o.observation_id)
    return selected


ARM_FN: dict[str, Callable[..., list[str]]] = {
    "random_eligible_launch": arm_random_eligible_launch,
    "basic_deterministic_filter_only": arm_basic_deterministic_filter_only,
    "monitor_quantitative_signals_only": arm_monitor_quantitative_signals_only,
    "monitor_plus_auditor": arm_monitor_plus_auditor,
    "monitor_plus_auditor_plus_narrative": arm_monitor_plus_auditor_plus_narrative,
    "full_system": arm_full_system,
}


def run_baseline_arm(arm: str, observations: list[Observation], **kwargs: Any) -> list[str]:
    if arm not in ARM_FN:
        raise ValueError(f"unknown baseline arm: {arm}; choose from {BASELINE_ARMS}")
    return ARM_FN[arm](observations, **kwargs)
