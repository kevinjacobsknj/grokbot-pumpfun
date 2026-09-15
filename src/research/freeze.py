"""Freeze experimental configurations with sha256 integrity.

Once a rule is selected for OOS: freeze feature definitions, thresholds,
entry/exit logic, paper fill assumptions, fees, slippage, max holding period.
Do NOT change frozen configs because results look bad.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ExperimentConfig(BaseModel):
    experiment_id: str
    version: str = "1"
    partition_method: str = "time"
    features: list[str] = Field(default_factory=list)
    thresholds: dict[str, float] = Field(default_factory=dict)
    entry_logic: str = ""
    exit_logic: str = ""
    paper_fill_assumptions: dict[str, Any] = Field(
        default_factory=lambda: {
            "fee_pct": 1.0,
            "max_impact_pct": 3.0,
            "invent_fills": False,
            "graduated_exit": "UNTRADEABLE",
        }
    )
    fees: dict[str, float] = Field(default_factory=lambda: {"trade_fee_pct": 1.0})
    slippage: dict[str, float] = Field(default_factory=lambda: {"max_price_impact_pct": 3.0})
    max_holding_period_seconds: float = 3600.0
    baseline_arm: str | None = None
    notes: str = ""
    frozen: bool = False
    config_sha256: str = ""


def canonical_bytes(cfg: ExperimentConfig) -> bytes:
    """Hash payload excludes config_sha256 itself and frozen flag churn."""
    data = cfg.model_dump(mode="json")
    data.pop("config_sha256", None)
    # Include frozen=True in hash once frozen so unfreezing changes digest.
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


def compute_sha256(cfg: ExperimentConfig) -> str:
    return hashlib.sha256(canonical_bytes(cfg)).hexdigest()


def freeze_config(cfg: ExperimentConfig, path: str | Path) -> ExperimentConfig:
    """Write experiments/<id>/config.yaml + sha256; mark frozen."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg.frozen = True
    cfg.config_sha256 = compute_sha256(cfg)
    # Write YAML human form + sidecar hash file
    payload = cfg.model_dump(mode="json")
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
    (path.parent / "config.sha256").write_text(cfg.config_sha256 + "\n", encoding="utf-8")
    (path.parent / "FROZEN").write_text(
        "This experiment config is FROZEN. Do not edit to chase results.\n"
        f"sha256={cfg.config_sha256}\n",
        encoding="utf-8",
    )
    return cfg


def load_experiment(path: str | Path) -> ExperimentConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return ExperimentConfig.model_validate(raw)


def verify_frozen(path: str | Path) -> tuple[bool, str]:
    """Return (ok, message). Fails if file edited after freeze."""
    path = Path(path)
    cfg = load_experiment(path)
    if not cfg.frozen:
        return False, "config is not marked frozen"
    expected = cfg.config_sha256
    actual = compute_sha256(cfg)
    if expected != actual:
        return False, f"hash mismatch: stored={expected} actual={actual}"
    sidecar = path.parent / "config.sha256"
    if sidecar.exists() and sidecar.read_text().strip() != expected:
        return False, "sidecar config.sha256 does not match"
    return True, expected


def assert_frozen_unchanged(path: str | Path) -> None:
    ok, msg = verify_frozen(path)
    if not ok:
        raise RuntimeError(f"frozen experiment config integrity failed: {msg}")
