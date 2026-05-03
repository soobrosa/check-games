#!/usr/bin/env python3
"""
Re-run item-type classification on the existing games_libraries.json without
re-fetching from the stores.

Only items currently typed "game" are eligible for re-typing: this preserves
Humble's platform-derived types (book / audio / video) which can't be recovered
from the title alone.
"""

import json
from pathlib import Path

from check_games import classify_item

SCRIPT_DIR = Path(__file__).resolve().parent
LIBRARIES_FILE = SCRIPT_DIR / "games_libraries.json"

# Types that can only be set by title-pattern classify_item (never from
# source-side metadata). Safe to re-evaluate from the title.
PATTERN_ONLY_TYPES = {"game", "course", "asset", "demo", "software", "wallpaper"}


def main():
    libs = json.loads(LIBRARIES_FILE.read_text())
    changed = 0
    by_change = {}
    for store, items in libs.items():
        for it in items:
            if not isinstance(it, dict):
                continue
            old = it.get("type", "game")
            if old not in PATTERN_ONLY_TYPES:
                continue
            new_t = classify_item(it.get("title", ""))
            if new_t != old:
                it["type"] = new_t
                changed += 1
                by_change[(old, new_t)] = by_change.get((old, new_t), 0) + 1

    LIBRARIES_FILE.write_text(json.dumps(libs, indent=2, ensure_ascii=False))
    print(f"Reclassified {changed} items.")
    for (a, b), n in sorted(by_change.items(), key=lambda x: -x[1]):
        print(f"  {a:>6} -> {b:<10}  {n}")


if __name__ == "__main__":
    main()
