#!/usr/bin/env python3
"""Generate a human-readable tabular view of all owned items, grouped by type."""

import json
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
LIBRARIES_FILE = SCRIPT_DIR / "games_libraries.json"
OUTPUT_FILE = SCRIPT_DIR / "games_by_type.txt"

TYPE_ORDER = [
    "game", "dlc", "soundtrack", "book", "comic", "audio", "video",
    "artbook", "demo", "software", "wallpaper", "coupon",
]

STORE_LABEL = {"steam": "Steam", "gog": "GOG", "humble": "Humble", "epic": "Epic"}


def main():
    libraries = json.loads(LIBRARIES_FILE.read_text())

    grouped = defaultdict(list)
    for store, items in libraries.items():
        for item in items:
            if not isinstance(item, dict):
                continue
            title = item.get("title", "").strip()
            t = item.get("type", "game")
            if title:
                grouped[t].append((title, STORE_LABEL.get(store, store)))

    ordered_types = [t for t in TYPE_ORDER if t in grouped] + \
                    sorted(t for t in grouped if t not in TYPE_ORDER)

    total = sum(len(v) for v in grouped.values())

    lines = []
    lines.append("=" * 80)
    lines.append(f"GAME LIBRARY INVENTORY - {total} items across {len(grouped)} types")
    lines.append("=" * 80)
    lines.append("")

    lines.append(f"{'Type':<14}  {'Count':>6}")
    lines.append(f"{'-' * 14}  {'-' * 6}")
    for t in ordered_types:
        lines.append(f"{t:<14}  {len(grouped[t]):>6}")
    lines.append(f"{'-' * 14}  {'-' * 6}")
    lines.append(f"{'TOTAL':<14}  {total:>6}")
    lines.append("")

    title_w = 70
    store_w = 8
    for t in ordered_types:
        items = sorted(grouped[t], key=lambda x: x[0].lower())
        header = f"{t.upper()} ({len(items)})"
        lines.append("")
        lines.append("=" * 80)
        lines.append(header)
        lines.append("=" * 80)
        lines.append(f"{'Title':<{title_w}}  {'Store':<{store_w}}")
        lines.append(f"{'-' * title_w}  {'-' * store_w}")
        for title, store in items:
            display = title if len(title) <= title_w else title[: title_w - 1] + "…"
            lines.append(f"{display:<{title_w}}  {store:<{store_w}}")

    output = "\n".join(lines) + "\n"
    OUTPUT_FILE.write_text(output)
    print(output)
    print(f"\nWritten to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
