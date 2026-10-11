"""Command line entry point for the Telecom DWH ETL."""
from __future__ import annotations

import argparse
import logging
import shutil
import sqlite3
import time
from pathlib import Path

from .pipeline import initialize, process_file


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Load telecom transaction CSV files into a local SQLite DWH.")
    p.add_argument("--root", type=Path, default=Path.cwd(), help="Project root (default: current directory)")
    p.add_argument("--watch", action="store_true", help="Poll the inbox every 5 minutes")
    p.add_argument("--interval", type=int, default=300, help="Watch interval in seconds (default: 300)")
    p.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return p


def run_once(root: Path) -> int:
    inbox = root / "data" / "inbox"
    processed = root / "data" / "archive" / "processed"
    failed = root / "data" / "archive" / "failed"
    db_path = root / "data" / "telecom_dwh.sqlite3"
    for folder in (inbox, processed, failed):
        folder.mkdir(parents=True, exist_ok=True)
    initialize(db_path, root / "sql" / "seed_imsi_reference.sql")
    files = sorted(p for p in inbox.glob("*.csv") if p.is_file())
    if not files:
        logging.info("No CSV files found in %s", inbox)
        return 0
    failed_count = 0
    for source in files:
        try:
            with sqlite3.connect(db_path) as conn:
                result = process_file(conn, source)
            destination = processed / source.name
            if destination.exists():
                destination = processed / f"{source.stem}_{int(time.time())}{source.suffix}"
            shutil.move(str(source), str(destination))
            logging.info("Loaded %s: %d valid, %d rejected", source.name, result.valid, result.rejected)
        except Exception:
            failed_count += 1
            logging.exception("Could not process %s; leaving it in the inbox for retry", source.name)
    return 1 if failed_count else 0


def main() -> int:
    args = parser().parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(asctime)s %(levelname)s %(message)s")
    root = args.root.resolve()
    if args.interval < 1:
        raise SystemExit("--interval must be a positive number")
    while True:
        status = run_once(root)
        if not args.watch:
            return status
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
