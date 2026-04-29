"""Import creature cards from AtomicCards.json into a partitioned SQLite database.

Each CMC value gets its own table: creatures_cmc_0, creatures_cmc_1, etc.
Only non-funny Creature cards are imported.

Fields stored per card:
    name, mana_cost, cmc, text, types, subtypes, power, toughness
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path
from typing import Iterator

import ijson


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_INPUT = "AtomicCards.json"
DEFAULT_DB = "creatures.db"
DEFAULT_MAX_ENTRIES = 100
BATCH_SIZE = 500
LOG_PROGRESS_EVERY = 1000  # log a progress line every N cards examined


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

TABLE_DDL = """
CREATE TABLE IF NOT EXISTS {table} (
    name        TEXT NOT NULL,
    mana_cost   TEXT,
    cmc         INTEGER NOT NULL,
    text        TEXT,
    types       TEXT NOT NULL,
    subtypes    TEXT,
    power       TEXT,
    toughness   TEXT
);
"""

TABLE_INDEX_DDL = "CREATE INDEX IF NOT EXISTS idx_{table}_name ON {table} (name);"

METADATA_DDL = """
CREATE TABLE IF NOT EXISTS import_metadata (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file     TEXT NOT NULL,
    imported_at     TEXT NOT NULL,
    total_rows      INTEGER NOT NULL,
    rows_per_table  TEXT NOT NULL
);
"""

INSERT_SQL = """
INSERT INTO {table}
    (name, mana_cost, cmc, text, types, subtypes, power, toughness)
VALUES
    (:name, :mana_cost, :cmc, :text, :types, :subtypes, :power, :toughness);
"""


def table_name(cmc: int) -> str:
    return f"creatures_cmc_{cmc}"


def ensure_table(conn: sqlite3.Connection, cmc: int, created: set[int]) -> None:
    if cmc in created:
        return
    tbl = table_name(cmc)
    conn.execute(TABLE_DDL.format(table=tbl))
    created.add(cmc)


# ---------------------------------------------------------------------------
# Streaming parser
# ---------------------------------------------------------------------------

def stream_all_faces(path: Path) -> Iterator[dict]:
    """Yield every card face from AtomicCards.json without filtering."""
    with open(path, "rb") as fh:
        for _name, faces in ijson.kvitems(fh, "data"):
            yield from faces


def classify_skip_reason(card: dict) -> str | None:
    """Return a human-readable reason if this card should be skipped, else None."""
    if card.get("isFunny"):
        return "funny"
    if "Creature" not in card.get("types", []):
        return "not_creature"
    return None


def extract_row(card: dict) -> dict:
    # ijson returns JSON numbers as Decimal; go through float to get a plain int
    raw_cmc = card.get("manaValue", card.get("convertedManaCost", 0))
    cmc = int(float(raw_cmc))
    return {
        "name":      card["name"],
        "mana_cost": card.get("manaCost"),
        "cmc":       cmc,
        "text":      card.get("text"),
        "types":     json.dumps(card.get("types", [])),
        "subtypes":  json.dumps(card.get("subtypes", [])),
        "power":     card.get("power"),
        "toughness": card.get("toughness"),
    }


# ---------------------------------------------------------------------------
# Import driver
# ---------------------------------------------------------------------------

def run_import(
    input_path: Path,
    db_path: Path,
    *,
    max_entries: int | None = None,
) -> None:
    limit_note = f" (limit: {max_entries})" if max_entries is not None else ""
    print(f"[import] Source  : {input_path}")
    print(f"[import] Database: {db_path}{limit_note}")
    print()

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute(METADATA_DDL)
    conn.commit()

    created_tables: set[int] = set()
    batches: dict[int, list[dict]] = {}
    total_examined = 0
    total_imported = 0
    skip_counts: dict[str, int] = {}
    rows_per_table: dict[str, int] = {}

    start = time.monotonic()

    for card in stream_all_faces(input_path):
        total_examined += 1

        reason = classify_skip_reason(card)
        if reason is not None:
            skip_counts[reason] = skip_counts.get(reason, 0) + 1
            if total_examined % LOG_PROGRESS_EVERY == 0:
                elapsed = time.monotonic() - start
                print(
                    f"[progress] examined={total_examined:,}  "
                    f"imported={total_imported:,}  "
                    f"skipped={sum(skip_counts.values()):,}  "
                    f"elapsed={elapsed:.1f}s"
                )
            continue

        row = extract_row(card)
        cmc = row["cmc"]

        ensure_table(conn, cmc, created_tables)
        batches.setdefault(cmc, []).append(row)
        total_imported += 1

        if total_examined % LOG_PROGRESS_EVERY == 0:
            elapsed = time.monotonic() - start
            print(
                f"[progress] examined={total_examined:,}  "
                f"imported={total_imported:,}  "
                f"skipped={sum(skip_counts.values()):,}  "
                f"elapsed={elapsed:.1f}s"
            )

        # Flush a bucket when it hits BATCH_SIZE
        if len(batches[cmc]) >= BATCH_SIZE:
            tbl = table_name(cmc)
            conn.executemany(INSERT_SQL.format(table=tbl), batches[cmc])
            conn.commit()
            rows_per_table[tbl] = rows_per_table.get(tbl, 0) + len(batches[cmc])
            batches[cmc] = []

        if max_entries is not None and total_imported >= max_entries:
            print(f"[import] Reached entry limit of {max_entries}. Stopping early.")
            break

    # Flush remaining partial batches
    for cmc, rows in batches.items():
        if rows:
            tbl = table_name(cmc)
            conn.executemany(INSERT_SQL.format(table=tbl), rows)
            conn.commit()
            rows_per_table[tbl] = rows_per_table.get(tbl, 0) + len(rows)

    # Add indexes after all data is in (faster than indexing during insert)
    print("\n[import] Building indexes...")
    for cmc in sorted(created_tables):
        tbl = table_name(cmc)
        conn.execute(TABLE_INDEX_DDL.format(table=tbl))
    conn.commit()

    # Record import metadata
    conn.execute(
        "INSERT INTO import_metadata (source_file, imported_at, total_rows, rows_per_table) "
        "VALUES (?, datetime('now'), ?, ?)",
        (str(input_path), total_imported, json.dumps(rows_per_table)),
    )
    conn.commit()

    elapsed = time.monotonic() - start

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print()
    print("=" * 50)
    print("IMPORT SUMMARY")
    print("=" * 50)
    print(f"  Total faces examined : {total_examined:,}")
    print(f"  Imported             : {total_imported:,}")
    print(f"  Skipped (not_creature): {skip_counts.get('not_creature', 0):,}")
    print(f"  Skipped (funny)      : {skip_counts.get('funny', 0):,}")
    print(f"  Time elapsed         : {elapsed:.1f}s")
    if max_entries is not None and total_imported < max_entries:
        print(f"  NOTE: fewer entries than limit ({total_imported} < {max_entries}) — source exhausted")
    print()

    # ------------------------------------------------------------------
    # Post-import database verification
    # ------------------------------------------------------------------
    print("=" * 50)
    print("DATABASE VERIFICATION")
    print("=" * 50)

    all_checks_passed = True

    for tbl, expected in sorted(rows_per_table.items(), key=lambda x: int(x[0].split("_")[-1])):
        actual = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]  # noqa: S608
        status = "PASS" if actual == expected else "FAIL"
        if status == "FAIL":
            all_checks_passed = False
        print(f"  [{status}] {tbl}: expected={expected:,}  actual={actual:,}")

    # Verify total across all tables matches recorded total
    db_total = sum(
        conn.execute(f"SELECT COUNT(*) FROM {table_name(cmc)}").fetchone()[0]
        for cmc in created_tables
    )
    total_status = "PASS" if db_total == total_imported else "FAIL"
    if total_status == "FAIL":
        all_checks_passed = False
    print()
    print(f"  [{total_status}] Grand total: expected={total_imported:,}  actual={db_total:,}")

    # Spot-check: every table must have at least one row with a non-empty name
    for cmc in sorted(created_tables):
        tbl = table_name(cmc)
        sample = conn.execute(
            f"SELECT name, mana_cost, cmc FROM {tbl} LIMIT 1"  # noqa: S608
        ).fetchone()
        if sample:
            print(f"  [SAMPLE] {tbl}: name={sample[0]!r}  mana_cost={sample[1]!r}  cmc={sample[2]}")
        else:
            print(f"  [WARN] {tbl}: no rows found during spot-check")
            all_checks_passed = False

    print()
    if all_checks_passed:
        print("[verify] All checks passed.")
    else:
        print("[verify] One or more checks FAILED — review output above.")

    conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import creature cards from AtomicCards.json into a partitioned SQLite database."
    )
    parser.add_argument(
        "--input",
        default=DEFAULT_INPUT,
        help=f"Path to AtomicCards.json (default: {DEFAULT_INPUT})",
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB,
        help=f"Output SQLite database path (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--clear",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Clear (delete) the database before importing. Default: true",
    )
    parser.add_argument(
        "--max-entries",
        type=int,
        default=DEFAULT_MAX_ENTRIES,
        metavar="N",
        help=(
            f"Maximum number of creature entries to import. "
            f"Set to 0 for no limit. Default: {DEFAULT_MAX_ENTRIES}"
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    db_path = Path(args.db)
    max_entries: int | None = args.max_entries if args.max_entries > 0 else None

    if not input_path.exists():
        print(f"[error] Input file not found: {input_path}")
        return 1

    if db_path.exists():
        if args.clear:
            db_path.unlink()
            print(f"[setup] Cleared existing database: {db_path}")
        else:
            print(f"[setup] Appending to existing database: {db_path}")

    run_import(input_path, db_path, max_entries=max_entries)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
