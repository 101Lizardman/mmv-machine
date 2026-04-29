"""Look up a random creature by CMC from creatures.db."""

from __future__ import annotations

import random
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path("creatures.db")


def random_creature(cmc: int, db_path: Path = DB_PATH) -> dict | None:
    """Return a random creature dict for the given CMC, or None if none found.

    Uses Python's random.randrange() (Mersenne Twister, period 2^19937) to pick
    a uniformly random row offset, then fetches that single row with LIMIT 1
    OFFSET N.  This avoids SQLite's ORDER BY RANDOM() which scores every row
    with C's rand() before sorting — slower and lower quality.
    """
    table = f"creatures_cmc_{cmc}"
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            count_row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            count = count_row[0] if count_row else 0
            if count == 0:
                return None
            offset = random.randrange(count)
            cur = conn.execute(
                f"SELECT name, mana_cost, cmc, types, subtypes, power, toughness, text"
                f" FROM {table} LIMIT 1 OFFSET ?",
                (offset,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    except sqlite3.OperationalError:
        return None  # table doesn't exist (e.g. CMC > 16)


if __name__ == "__main__":
    cmc = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    result = random_creature(cmc)
    if result:
        name = result["name"]
        pt   = f"{result.get('power') or '?'}/{result.get('toughness') or '?'}"
        cost = result.get("mana_cost") or ""
        print(f"CMC {cmc}: {name}  {pt}  {cost}")
    else:
        print(f"No creatures found for CMC {cmc}")
