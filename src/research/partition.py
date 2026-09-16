"""DEVELOPMENT / VALIDATION / LOCKED_OUT_OF_SAMPLE partitions.

Default rule: partition by creation_timestamp terciles of the observation set
sorted by time (first 60% DEV, next 20% VAL, last 20% OOS).

Alternative: by observation_id lexicographic hash buckets (documented).
Once OOS is locked, do not move observations across partitions.
"""

from __future__ import annotations

import hashlib
from enum import Enum
from typing import Iterable, Sequence

from ..observation import Observation


class Partition(str, Enum):
    DEVELOPMENT = "DEVELOPMENT"
    VALIDATION = "VALIDATION"
    LOCKED_OUT_OF_SAMPLE = "LOCKED_OUT_OF_SAMPLE"


PARTITION_RULE_DOC = """
Partition rule (time-based, default):
  1. Sort observations by creation_timestamp ascending (stable; missing ts -> 0).
  2. First 60% -> DEVELOPMENT
  3. Next 20%  -> VALIDATION
  4. Last 20%  -> LOCKED_OUT_OF_SAMPLE

Hash-based alternative (observation_id):
  sha256(observation_id) mod 10 -> {0-5: DEV, 6-7: VAL, 8-9: OOS}

Once a rule is selected for an experiment freeze, it must not change.
"""


def assign_partition(
    index: int,
    n: int,
    *,
    observation_id: str | None = None,
    method: str = "time",
) -> Partition:
    if n <= 0:
        return Partition.DEVELOPMENT
    if method == "hash":
        if not observation_id:
            raise ValueError("hash partition requires observation_id")
        digest = hashlib.sha256(observation_id.encode()).hexdigest()
        bucket = int(digest[:8], 16) % 10
        if bucket <= 5:
            return Partition.DEVELOPMENT
        if bucket <= 7:
            return Partition.VALIDATION
        return Partition.LOCKED_OUT_OF_SAMPLE
    # time-based by index in sorted list
    if index < int(n * 0.6):
        return Partition.DEVELOPMENT
    if index < int(n * 0.8):
        return Partition.VALIDATION
    return Partition.LOCKED_OUT_OF_SAMPLE


def partition_observations(
    observations: Sequence[Observation],
    *,
    method: str = "time",
) -> dict[Partition, list[Observation]]:
    if method == "time":
        ordered = sorted(
            observations,
            key=lambda o: (o.creation_timestamp or 0.0, o.observation_id),
        )
        n = len(ordered)
        out: dict[Partition, list[Observation]] = {
            Partition.DEVELOPMENT: [],
            Partition.VALIDATION: [],
            Partition.LOCKED_OUT_OF_SAMPLE: [],
        }
        for i, obs in enumerate(ordered):
            out[assign_partition(i, n, method="time")].append(obs)
        return out
    if method == "hash":
        out = {
            Partition.DEVELOPMENT: [],
            Partition.VALIDATION: [],
            Partition.LOCKED_OUT_OF_SAMPLE: [],
        }
        for obs in observations:
            part = assign_partition(0, 1, observation_id=obs.observation_id, method="hash")
            out[part].append(obs)
        return out
    raise ValueError(f"unknown partition method: {method}")


def iter_partition_labels(observations: Iterable[Observation], method: str = "time"):
    parts = partition_observations(list(observations), method=method)
    for part, items in parts.items():
        for obs in items:
            yield obs.observation_id, part
