#!/usr/bin/env python3
"""
Build a Mac/Apple-Silicon-runnable subset of the owned-games list, sorted by
rating.

Sources:
  - AppleGamingWiki Cargo API (Compatibility_macOS): native / rosetta_2 status
  - Steam appdetails platforms.mac flag (fallback coverage)

Reuses games_ratings_cache.json for Metacritic / Steam review data and writes:
  games_silicon_cache.json   - per-title AGW + Steam mac data (incremental)
  games_arm.txt              - best owned games runnable on Apple Silicon
"""

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
LIBRARIES_FILE = SCRIPT_DIR / "games_libraries.json"
RATINGS_CACHE = SCRIPT_DIR / "games_ratings_cache.json"
SILICON_CACHE = SCRIPT_DIR / "games_silicon_cache.json"
OUT_TXT = SCRIPT_DIR / "games_arm.txt"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15")
AGW_API = "https://www.applegamingwiki.com/w/api.php"

PLAYABLE = {"perfect", "playable", "runs"}


def normalize(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def http_json(url, timeout=20):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def collect_games():
    libs = json.loads(LIBRARIES_FILE.read_text())
    by_norm = {}
    for store, items in libs.items():
        for it in items:
            if not isinstance(it, dict) or it.get("type") != "game":
                continue
            title = (it.get("title") or "").strip()
            if not title:
                continue
            key = normalize(title)
            if key not in by_norm:
                by_norm[key] = {"title": title, "stores": set()}
            by_norm[key]["stores"].add(store)
    return [{"key": k, "title": v["title"], "stores": sorted(v["stores"])}
            for k, v in by_norm.items()]


def agw_batch(titles, delay):
    """Query AGW Cargo API for up to ~40 titles at once via OR'd where clause."""
    where = " OR ".join(
        "_pageName=" + "'" + t.replace("'", "''") + "'" for t in titles
    )
    params = urllib.parse.urlencode({
        "action": "cargoquery",
        "tables": "Compatibility_macOS",
        "fields": "_pageName=Page,native,rosetta_2,crossover,wine,parallels",
        "where": where,
        "limit": "200",
        "format": "json",
    })
    url = f"{AGW_API}?{params}"
    try:
        data = http_json(url)
    except Exception as e:
        print(f"  agw_batch error: {e}", file=sys.stderr)
        return {}
    out = {}
    for row in data.get("cargoquery", []):
        r = row.get("title") or {}
        page = r.get("Page") or ""
        if not page:
            continue
        out[page] = {
            "native": r.get("native") or "",
            "rosetta_2": r.get("rosetta 2") or "",
            "crossover": r.get("crossover") or "",
            "wine": r.get("wine") or "",
            "parallels": r.get("parallels") or "",
        }
    time.sleep(delay)
    return out


def steam_mac_flag(appid, delay):
    """Hit appdetails to learn if the game has a macOS build."""
    url = ("https://store.steampowered.com/api/appdetails"
           f"?appids={appid}&filters=basic&cc=us&l=english")
    try:
        data = http_json(url)
    except Exception:
        time.sleep(delay)
        return None
    entry = data.get(str(appid)) or {}
    if not entry.get("success"):
        time.sleep(delay)
        return None
    plat = (entry.get("data") or {}).get("platforms") or {}
    time.sleep(delay)
    return bool(plat.get("mac"))


def load_cache(path):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def save_cache(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def fetch_agw(games, cache, delay, batch_size):
    todo = [g for g in games if "agw" not in (cache.get(g["key"]) or {})]
    if not todo:
        return
    print(f"AGW lookup: {len(todo)} titles in batches of {batch_size}")
    for i in range(0, len(todo), batch_size):
        chunk = todo[i:i + batch_size]
        result = agw_batch([g["title"] for g in chunk], delay)
        norm_result = {normalize(k): v for k, v in result.items()}
        for g in chunk:
            rec = cache.setdefault(g["key"], {"title": g["title"]})
            rec["agw"] = norm_result.get(g["key"]) or {}
        hits = sum(1 for g in chunk if cache[g["key"]].get("agw"))
        print(f"  [{i + len(chunk)}/{len(todo)}] batch hits: {hits}/{len(chunk)}")
        save_cache(SILICON_CACHE, cache)


def fetch_steam_mac(games, ratings, sil_cache, delay, limit):
    todo = []
    for g in games:
        rec = sil_cache.get(g["key"]) or {}
        if "steam_mac" in rec:
            continue
        rcache = ratings.get(g["key"]) or {}
        appid = rcache.get("steam_appid")
        if not appid:
            continue
        todo.append((g, appid))
    if limit > 0:
        todo = todo[:limit]
    if not todo:
        return
    print(f"Steam mac flag lookup: {len(todo)} titles")
    for i, (g, appid) in enumerate(todo, 1):
        flag = steam_mac_flag(appid, delay)
        rec = sil_cache.setdefault(g["key"], {"title": g["title"]})
        rec["steam_mac"] = flag
        if i % 50 == 0:
            print(f"  [{i}/{len(todo)}]")
            save_cache(SILICON_CACHE, sil_cache)
    save_cache(SILICON_CACHE, sil_cache)


def best_status(rec):
    agw = rec.get("agw") or {}
    nat = (agw.get("native") or "").lower()
    ros = (agw.get("rosetta_2") or "").lower()
    cx = (agw.get("crossover") or "").lower()
    if nat in PLAYABLE:
        return "ARM Native", nat, 0
    if ros in PLAYABLE:
        return "Rosetta 2", ros, 1
    if rec.get("steam_mac") is True:
        return "Steam macOS", "yes", 2
    if cx in PLAYABLE:
        return "CrossOver", cx, 3
    if rec.get("steam_mac") is False:
        return "", "no", 9
    return "", "unknown", 8


def write_table(games, ratings, sil_cache):
    rows = []
    for g in games:
        sil = sil_cache.get(g["key"]) or {}
        cat, detail, prio = best_status(sil)
        if not cat:
            continue
        r = ratings.get(g["key"]) or {}
        rows.append({
            "title": g["title"],
            "stores": ",".join(s[:1].upper() for s in g["stores"]),
            "category": cat,
            "detail": detail,
            "prio": prio,
            "mc": r.get("metacritic_score"),
            "steam_desc": r.get("steam_review_desc") or "",
            "steam_pct": r.get("steam_positive_pct"),
            "steam_n": r.get("steam_review_count") or 0,
        })

    rows.sort(key=lambda r: (
        r["prio"],
        -(r["mc"] if r["mc"] is not None else -1),
        -(r["steam_pct"] if r["steam_pct"] is not None else -1),
        -(r["steam_n"] or 0),
        r["title"].lower(),
    ))

    by_cat = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)

    title_w = 50
    lines = []
    lines.append("=" * 110)
    lines.append(f"BEST OWNED GAMES RUNNABLE ON APPLE SILICON")
    lines.append("=" * 110)
    lines.append(f"Total Mac-runnable: {len(rows)} (out of {len(games)} owned)")
    for cat in ["ARM Native", "Rosetta 2", "Steam macOS", "CrossOver"]:
        if cat in by_cat:
            lines.append(f"  {cat:<14} {len(by_cat[cat])}")
    lines.append("")

    for cat in ["ARM Native", "Rosetta 2", "Steam macOS", "CrossOver"]:
        if cat not in by_cat:
            continue
        items = by_cat[cat]
        lines.append("=" * 110)
        lines.append(f"{cat.upper()} ({len(items)})")
        lines.append("=" * 110)
        hdr = (f"{'Title':<{title_w}}  {'Stores':<6}  {'MC':>3}  "
               f"{'Status':<10}  {'Steam Reviews':<22}  {'%':>3}  {'N':>7}")
        lines.append(hdr)
        lines.append("-" * len(hdr))
        for r in items:
            t = r["title"]
            if len(t) > title_w:
                t = t[: title_w - 1] + "\u2026"
            mc = f"{r['mc']:>3}" if r["mc"] is not None else "  -"
            pct = f"{r['steam_pct']:>3}" if r["steam_pct"] is not None else "  -"
            n = f"{r['steam_n']:>7}" if r["steam_n"] else "      -"
            lines.append(
                f"{t:<{title_w}}  {r['stores']:<6}  {mc}  "
                f"{r['detail']:<10}  {r['steam_desc']:<22}  {pct}  {n}"
            )
        lines.append("")

    out = "\n".join(lines) + "\n"
    OUT_TXT.write_text(out)
    return out


def main():
    p = argparse.ArgumentParser(
        description="Find best owned games runnable on Apple Silicon")
    p.add_argument("--delay", type=float, default=0.5,
                   help="Seconds between HTTP requests")
    p.add_argument("--batch-size", type=int, default=40,
                   help="AGW titles per Cargo query")
    p.add_argument("--steam-limit", type=int, default=0,
                   help="Cap Steam mac-flag lookups (0 = all unknowns)")
    p.add_argument("--no-steam", action="store_true",
                   help="Skip Steam mac-flag fallback")
    p.add_argument("--write-only", action="store_true",
                   help="Skip fetching; regenerate the table from cache")
    args = p.parse_args()

    games = collect_games()
    print(f"Owned game titles: {len(games)}")

    sil_cache = load_cache(SILICON_CACHE)
    ratings = load_cache(RATINGS_CACHE)

    if not args.write_only:
        fetch_agw(games, sil_cache, args.delay, args.batch_size)
        if not args.no_steam:
            fetch_steam_mac(games, ratings, sil_cache, args.delay, args.steam_limit)

    out = write_table(games, ratings, sil_cache)
    print(f"\nWrote {OUT_TXT}")
    print("\n".join(out.split("\n")[:8]))


if __name__ == "__main__":
    main()
