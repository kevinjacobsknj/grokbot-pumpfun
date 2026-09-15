"""Durable storage for the observation universe (JSONL + optional SQLite).

Never delete losing observations. Never silently exclude missing outcomes.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterator

from .observation import IntegrityEvent, Observation

log = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path("data")


class ObservationStore:
    """Append-only JSONL writers + SQLite index for research queries."""

    def __init__(self, root: str | Path = DEFAULT_DATA_DIR) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.observations_path = self.root / "observations.jsonl"
        self.rejects_path = self.root / "rejects.jsonl"
        self.integrity_path = self.root / "integrity.jsonl"
        self.paper_trades_path = self.root / "paper_trades.jsonl"
        self.db_path = self.root / "research.sqlite3"
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS observations (
                    observation_id TEXT PRIMARY KEY,
                    mint TEXT,
                    creator TEXT,
                    creation_timestamp REAL,
                    first_seen_timestamp REAL,
                    candidate_status TEXT,
                    reject_reason TEXT,
                    missing_trade_stream INTEGER,
                    incomplete INTEGER,
                    migration_state TEXT,
                    json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS integrity_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL,
                    kind TEXT,
                    observation_id TEXT,
                    mint TEXT,
                    detail TEXT,
                    json TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def _append_jsonl(self, path: Path, obj: dict[str, Any]) -> None:
        line = json.dumps(obj, ensure_ascii=False, default=str)
        with self._lock:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def write_observation(self, obs: Observation) -> None:
        payload = obs.model_dump(mode="json")
        self._append_jsonl(self.observations_path, payload)
        if obs.candidate_status == "rejected":
            self._append_jsonl(self.rejects_path, payload)
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO observations
                    (observation_id, mint, creator, creation_timestamp,
                     first_seen_timestamp, candidate_status, reject_reason,
                     missing_trade_stream, incomplete, migration_state, json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        obs.observation_id,
                        obs.mint,
                        obs.creator,
                        obs.creation_timestamp,
                        obs.first_seen_timestamp,
                        obs.candidate_status,
                        obs.reject_reason,
                        int(obs.missing_trade_stream),
                        int(obs.incomplete),
                        obs.migration_state,
                        json.dumps(payload, ensure_ascii=False, default=str),
                    ),
                )
                conn.commit()
        log.debug("stored observation %s mint=%s status=%s",
                  obs.observation_id, obs.mint[:8] if obs.mint else "", obs.candidate_status)

    def write_integrity(self, event: IntegrityEvent) -> None:
        payload = event.model_dump(mode="json")
        self._append_jsonl(self.integrity_path, payload)
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO integrity_events
                    (timestamp, kind, observation_id, mint, detail, json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.timestamp,
                        event.kind,
                        event.observation_id,
                        event.mint,
                        event.detail,
                        json.dumps(payload, ensure_ascii=False, default=str),
                    ),
                )
                conn.commit()
        log.info("integrity[%s] obs=%s %s", event.kind, event.observation_id[:8], event.detail)

    def write_paper_trade(self, attempt: dict[str, Any]) -> None:
        self._append_jsonl(self.paper_trades_path, attempt)

    def iter_observations(self) -> Iterator[Observation]:
        if not self.observations_path.exists():
            return
            yield  # pragma: no cover
        with self.observations_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                yield Observation.model_validate(json.loads(line))

    def get(self, observation_id: str) -> Observation | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT json FROM observations WHERE observation_id = ?",
                (observation_id,),
            ).fetchone()
        if not row:
            return None
        return Observation.model_validate(json.loads(row[0]))

    def count(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            return int(conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0])

    def log_ws_disconnect(self, detail: str = "") -> None:
        self.write_integrity(
            IntegrityEvent(kind="ws_disconnect", detail=detail or "websocket disconnected")
        )

    def log_api_failure(self, mint: str = "", detail: str = "", observation_id: str = "") -> None:
        self.write_integrity(
            IntegrityEvent(
                kind="api_failure",
                mint=mint,
                observation_id=observation_id,
                detail=detail,
            )
        )
