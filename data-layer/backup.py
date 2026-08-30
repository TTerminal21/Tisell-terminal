"""Copy the DuckDB file to a backup destination. Run manually, monthly-ish.

    python data-layer/backup.py                      # -> BACKUP_DIR or ./backups
    python data-layer/backup.py --dest /Volumes/usb  # explicit destination
    python data-layer/backup.py --keep 6             # prune older copies

DuckDB holds an exclusive lock while a connection is open, so this checkpoints
through a real connection rather than copying the file blind - that flushes the
write-ahead log and guarantees the copy is consistent.
"""
from __future__ import annotations

import argparse
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from config import DUCKDB_PATH, REPO_ROOT


def default_destination() -> Path:
    raw = (os.getenv("BACKUP_DIR") or "").strip()
    return Path(raw).expanduser() if raw else REPO_ROOT / "backups"


def checkpoint(source: Path) -> None:
    """Flush the WAL so the copied file is self-contained."""
    con = duckdb.connect(str(source))
    try:
        con.execute("CHECKPOINT")
    finally:
        con.close()


def backup(dest_dir: Path, keep: int | None = None) -> Path:
    source = DUCKDB_PATH
    if not source.exists():
        raise FileNotFoundError(f"no database at {source}")

    checkpoint(source)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = dest_dir / f"{source.stem}-{stamp}{source.suffix}"

    shutil.copy2(source, target)

    if keep is not None and keep > 0:
        existing = sorted(
            dest_dir.glob(f"{source.stem}-*{source.suffix}"),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        for stale in existing[keep:]:
            stale.unlink()

    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", help="destination directory (default: $BACKUP_DIR or ./backups)")
    parser.add_argument("--keep", type=int, help="keep only the N most recent backups")
    args = parser.parse_args()

    dest = Path(args.dest).expanduser() if args.dest else default_destination()
    try:
        target = backup(dest, args.keep)
    except (OSError, duckdb.Error) as exc:
        print(f"error: {exc}")
        return 1

    size_mb = target.stat().st_size / 1e6
    print(f"backed up {DUCKDB_PATH.name} -> {target} ({size_mb:,.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
