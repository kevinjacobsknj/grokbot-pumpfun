"""Experimental design: partitions, freeze hashes, baselines, evaluation."""

from .baselines import BASELINE_ARMS, run_baseline_arm
from .evaluate import evaluate_arms, report_metrics
from .freeze import ExperimentConfig, freeze_config, verify_frozen
from .partition import Partition, assign_partition, partition_observations

__all__ = [
    "BASELINE_ARMS",
    "ExperimentConfig",
    "Partition",
    "assign_partition",
    "evaluate_arms",
    "freeze_config",
    "partition_observations",
    "report_metrics",
    "run_baseline_arm",
    "verify_frozen",
]
