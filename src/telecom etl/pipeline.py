"""Validation, transformation, and transactional loading for pipe-delimited files."""
from __future__ import annotations

import csv
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)
EXPECTED = ("id", "imsi", "imei", "cell", "lac", "event_type", "event_ts")
TIMESTAMP_FORMATS = (
    "%d/%m/%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M",
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
)


@dataclass(frozen=True)
class Result:
    valid: int
    rejected: int


def initialize(db_path: Path, seed_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
        PRAGMA foreign_keys = ON;
        CREATE TABLE IF NOT EXISTS dim_imsi_reference (
            imsi TEXT PRIMARY KEY, subscriber_id INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS fact_transaction (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id INTEGER NOT NULL,
            imsi TEXT NOT NULL,
            subscriber_id INTEGER NOT NULL,
            tac TEXT NOT NULL,
            snr TEXT NOT NULL,
            imei TEXT NOT NULL,
            cell INTEGER NOT NULL,
            lac INTEGER NOT NULL,
            event_type TEXT,
            event_ts TEXT NOT NULL,
            source_file TEXT NOT NULL,
            source_row INTEGER NOT NULL,
            loaded_at TEXT NOT NULL,
            UNIQUE(source_file, source_row)
        );
        CREATE TABLE IF NOT EXISTS rejected_record (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file TEXT NOT NULL,
            source_row INTEGER NOT NULL,
            raw_record TEXT NOT NULL,
            rejection_reason TEXT NOT NULL,
            rejected_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS etl_batch (
            source_file TEXT PRIMARY KEY,
            valid_count INTEGER NOT NULL,
            rejected_count INTEGER NOT NULL,
            completed_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_rejected_source ON rejected_record(source_file);
        CREATE INDEX IF NOT EXISTS ix_fact_event_ts ON fact_transaction(event_ts);
        """)
        if seed_path.exists():
            # Idempotent seed insert; the seed file uses SQLite-compatible statements.
            conn.executescript(seed_path.read_text(encoding="utf-8"))


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _integer(value: Any, field: str) -> int:
    text = _text(value)
    if not text:
        raise ValueError(f"{field} is null or empty")
    try:
        return int(text)
    except ValueError as exc:
        raise ValueError(f"{field} is not a valid integer") from exc


def _timestamp(value: Any) -> str:
    text = _text(value)
    if not text:
        raise ValueError("event_ts is null or empty")
    for fmt in TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(text, fmt).isoformat(sep=" ")
        except ValueError:
            continue
    raise ValueError("event_ts has an unsupported datetime format")


def _convert(raw: dict[str, Any], source_file: str, row_num: int,
             references: dict[str, int]) -> dict[str, Any]:
    missing = [name for name in EXPECTED if name not in raw]
    if missing:
        raise ValueError(f"missing required column(s): {', '.join(missing)}")
    transaction_id = _integer(raw.get("id"), "id")
    imsi = _text(raw.get("imsi"))
    if not imsi:
        raise ValueError("imsi is null or empty")
    cell = _integer(raw.get("cell"), "cell")
    lac = _integer(raw.get("lac"), "lac")
    event_ts = _timestamp(raw.get("event_ts"))
    imei = _text(raw.get("imei"))
    if not imei or len(imei) < 14:
        tac = snr = "-99999"
    else:
        tac, snr = imei[:8], imei[-6:]
    # The case defines a nine-character reference IMSI; source batches contain full IMSIs.
    ref_key = imsi[:9] if len(imsi) >= 9 else imsi
    subscriber_id = references.get(ref_key, -99999)
    event_type = _text(raw.get("event_type")) or None
    if event_type is not None and len(event_type) > 1:
        raise ValueError("event_type must be a single character")
    return {
        "transaction_id": transaction_id, "imsi": imsi, "subscriber_id": subscriber_id,
        "tac": tac, "snr": snr, "imei": imei or "-99999", "cell": cell, "lac": lac,
        "event_type": event_type, "event_ts": event_ts, "source_file": source_file,
        "source_row": row_num,
    }


def process_file(conn: sqlite3.Connection, source: Path) -> Result:
    """Load one file atomically. Caller archives it only after this function commits."""
    source_file = source.name
    if conn.execute("SELECT 1 FROM etl_batch WHERE source_file = ?", (source_file,)).fetchone():
        LOG.info("Skipping already loaded file %s", source_file)
        return Result(0, 0)
    conn.row_factory = sqlite3.Row
    references = {row["imsi"]: row["subscriber_id"] for row in conn.execute(
        "SELECT imsi, subscriber_id FROM dim_imsi_reference")}
    good: list[tuple[Any, ...]] = []
    rejected: list[tuple[str, int, str, str, str]] = []
    loaded_at = datetime.now(timezone.utc).isoformat()
    with source.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="|")
        if reader.fieldnames is None:
            raise ValueError("file is empty or has no header")
        reader.fieldnames = [name.strip().lower() for name in reader.fieldnames]
        if len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError("file contains duplicate column names")
        for line_num, raw in enumerate(reader, start=2):
            if None in raw:
                reason = "row has more fields than the header"
                rejected.append((source_file, line_num, json.dumps(raw, ensure_ascii=False), reason, loaded_at))
                continue
            try:
                item = _convert(raw, source_file, line_num, references)
                good.append(tuple(item[key] for key in (
                    "transaction_id", "imsi", "subscriber_id", "tac", "snr", "imei", "cell",
                    "lac", "event_type", "event_ts", "source_file", "source_row")) + (loaded_at,))
            except ValueError as exc:
                rejected.append((source_file, line_num, json.dumps(raw, ensure_ascii=False), str(exc), loaded_at))
    # Caller wraps this connection in a transaction. Both destinations and batch audit commit together.
    conn.executemany("""INSERT INTO fact_transaction
        (transaction_id, imsi, subscriber_id, tac, snr, imei, cell, lac, event_type, event_ts,
         source_file, source_row, loaded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", good)
    conn.executemany("""INSERT INTO rejected_record
        (source_file, source_row, raw_record, rejection_reason, rejected_at)
        VALUES (?, ?, ?, ?, ?)""", rejected)
    conn.execute("INSERT INTO etl_batch VALUES (?, ?, ?, ?)",
                 (source_file, len(good), len(rejected), loaded_at))
    return Result(len(good), len(rejected))
