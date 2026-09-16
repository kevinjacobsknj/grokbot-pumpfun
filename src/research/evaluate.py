"""Evaluation harness: run arms on the same observation set under paper fills."""

from __future__ import annotations

from typing import Any

from ..observation import Observation
from .baselines import BASELINE_ARMS, run_baseline_arm


def _pnl_lookup(paper_pnls: dict[str, float], oid: str) -> float:
    return float(paper_pnls.get(oid, 0.0))


def report_metrics(
    selected_ids: list[str],
    *,
    paper_pnls: dict[str, float] | None = None,
    untradeable: set[str] | None = None,
) -> dict[str, Any]:
    paper_pnls = paper_pnls or {}
    untradeable = untradeable or set()
    pnls = [_pnl_lookup(paper_pnls, oid) for oid in selected_ids if oid not in untradeable]
    n = len(selected_ids)
    n_ut = sum(1 for oid in selected_ids if oid in untradeable)
    n_traded = len(pnls)
    total = sum(pnls)
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    return {
        "n_selected": n,
        "n_traded": n_traded,
        "n_untradeable": n_ut,
        "total_net_pnl": total,
        "mean_net_pnl": (total / n_traded) if n_traded else 0.0,
        "win_rate": (wins / n_traded) if n_traded else 0.0,
        "n_wins": wins,
        "n_losses": losses,
    }


def evaluate_arms(
    observations: list[Observation],
    *,
    paper_pnls: dict[str, float] | None = None,
    untradeable: set[str] | None = None,
    arm_kwargs: dict[str, dict[str, Any]] | None = None,
    arms: tuple[str, ...] | None = None,
) -> dict[str, dict[str, Any]]:
    """Run each baseline arm on the same set; return comparative metrics.

    Does NOT claim agent value — callers must compare prospectively on OOS.
    """
    arm_kwargs = arm_kwargs or {}
    results = {}
    for arm in arms or BASELINE_ARMS:
        selected = run_baseline_arm(arm, observations, **arm_kwargs.get(arm, {}))
        metrics = report_metrics(selected, paper_pnls=paper_pnls, untradeable=untradeable)
        metrics["arm"] = arm
        metrics["selected_ids"] = selected
        results[arm] = metrics
    return results
