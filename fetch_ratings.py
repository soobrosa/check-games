#!/usr/bin/env python3
"""
Fetch ratings for owned games from Steam Store API, with Metacritic fallback.

Sources:
  - Steam storesearch -> appid match
  - Steam appdetails  -> Metacritic score
  - Steam appreviews  -> Steam user-review summary
  - Metacritic game page scrape (only for games missing a Steam Metacritic score)

Outputs:
  games_ratings_cache.json  - persistent cache (resumable)
  games_ratings.txt         - tabular view sorted by best available score
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
LIBRARIES_FILE = SCRIPT_DIR / "games_libraries.json"
CACHE_FILE = SCRIPT_DIR / "games_ratings_cache.json"
RATINGS_TXT = SCRIPT_DIR / "games_ratings.txt"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15")


def normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def metacritic_slug(title: str) -> str:
    s = title.lower()
    s = re.sub(r"&", " and ", s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def http_get(url: str, timeout: float = 15, accept_json: bool = False):
    headers = {"User-Agent": UA}
    if accept_json:
        headers["Accept"] = "application/json, text/plain, */*"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        return r.status, body


def http_json(url: str, timeout: float = 15):
    _, body = http_get(url, timeout=timeout, accept_json=True)
    return json.loads(body)


def http_text(url: str, timeout: float = 15):
    _, body = http_get(url, timeout=timeout)
    return body.decode("utf-8", errors="replace")


# ---------- Steam ----------

def steam_search(title: str):
    url = ("https://store.steampowered.com/api/storesearch/"
           f"?term={urllib.parse.quote(title)}&l=english&cc=us")
    try:
        data = http_json(url)
    except Exception as e:
        return None, f"search_error: {e}"
    items = data.get("items") or []
    if not items:
        return None, "no_results"
    target = normalize(title)
    for it in items:
        if normalize(it.get("name", "")) == target:
            return {"appid": it.get("id"), "name": it.get("name")}, "exact"
    first = items[0]
    return {"appid": first.get("id"), "name": first.get("name")}, "fuzzy"


def steam_appdetails(appid: int):
    url = ("https://store.steampowered.com/api/appdetails"
           f"?appids={appid}&filters=basic,metacritic&cc=us&l=english")
    try:
        data = http_json(url)
    except Exception:
        return None
    entry = data.get(str(appid))
    if not entry or not entry.get("success"):
        return None
    d = entry.get("data") or {}
    out = {
        "type": d.get("type"),
        "name": d.get("name"),
    }
    mc = d.get("metacritic")
    if isinstance(mc, dict) and "score" in mc:
        out["metacritic_score"] = mc.get("score")
        out["metacritic_url"] = mc.get("url")
    return out


def steam_reviews(appid: int):
    url = (f"https://store.steampowered.com/appreviews/{appid}"
           "?json=1&language=all&purchase_type=all&num_per_page=0")
    try:
        data = http_json(url)
    except Exception:
        return None
    if not data.get("success"):
        return None
    qs = data.get("query_summary") or {}
    if not qs:
        return None
    total = qs.get("total_reviews") or 0
    pos = qs.get("total_positive") or 0
    pct = round(100 * pos / total) if total else None
    return {
        "review_score_desc": qs.get("review_score_desc"),
        "total_reviews": total,
        "positive_pct": pct,
    }


# ---------- Metacritic fallback ----------

def metacritic_lookup(title: str):
    """Try direct slug; if 404, try search page."""
    slug = metacritic_slug(title)
    direct = f"https://www.metacritic.com/game/{slug}/"
    score, url = _metacritic_parse_page(direct)
    if score is not None:
        return {"score": score, "url": url or direct, "source": "direct"}
    # search fallback
    search_url = (f"https://www.metacritic.com/search/{urllib.parse.quote(title)}/"
                  "?page=1&category=13")
    try:
        html = http_text(search_url)
    except Exception:
        return None
    m = re.search(r'href="(/game/[^"#?]+)"', html)
    if not m:
        return None
    page = "https://www.metacritic.com" + m.group(1)
    score, url = _metacritic_parse_page(page)
    if score is not None:
        return {"score": score, "url": url or page, "source": "search"}
    return None


def _metacritic_parse_page(url: str):
    try:
        html = http_text(url)
    except urllib.error.HTTPError:
        return None, None
    except Exception:
        return None, None
    # The reliable signal is the schema.org JSON-LD aggregateRating block.
    # Anything else on Metacritic's Next.js page mostly contains internal IDs.
    m = re.search(
        r'"aggregateRating"\s*:\s*\{[^}]*?"name"\s*:\s*"Metascore"[^}]*?'
        r'"ratingValue"\s*:\s*(\d+)',
        html,
    )
    if not m:
        m = re.search(
            r'"aggregateRating"\s*:\s*\{[^}]*?"ratingValue"\s*:\s*(\d+)'
            r'[^}]*?"name"\s*:\s*"Metascore"',
            html,
        )
    if m:
        try:
            score = int(m.group(1))
            if 0 <= score <= 100:
                return score, url
        except ValueError:
            pass
    return None, None


# ---------- Cache + driver ----------

def load_cache():
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def save_cache(cache: dict):
    CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False))


def collect_games():
    libs = json.loads(LIBRARIES_FILE.read_text())
    by_norm = {}
    for store, items in libs.items():
        for it in items:
            if not isinstance(it, dict):
                continue
            if it.get("type") != "game":
                continue
            title = (it.get("title") or "").strip()
            if not title:
                continue
            key = normalize(title)
            if key not in by_norm:
                by_norm[key] = {"title": title, "stores": set()}
            by_norm[key]["stores"].add(store)
    games = []
    for key, info in by_norm.items():
        games.append({
            "key": key,
            "title": info["title"],
            "stores": sorted(info["stores"]),
        })
    games.sort(key=lambda g: g["title"].lower())
    return games


def fetch_one(title: str, do_metacritic: bool, delay: float):
    rec = {"title": title}
    match, status = steam_search(title)
    rec["steam_match_status"] = status
    if match and match.get("appid"):
        rec["steam_appid"] = match["appid"]
        rec["steam_name"] = match["name"]
        time.sleep(delay)
        details = steam_appdetails(match["appid"])
        if details:
            rec["steam_type"] = details.get("type")
            if "metacritic_score" in details:
                rec["metacritic_score"] = details["metacritic_score"]
                rec["metacritic_url"] = details.get("metacritic_url")
                rec["metacritic_source"] = "steam"
        time.sleep(delay)
        reviews = steam_reviews(match["appid"])
        if reviews:
            rec["steam_review_desc"] = reviews.get("review_score_desc")
            rec["steam_review_count"] = reviews.get("total_reviews")
            rec["steam_positive_pct"] = reviews.get("positive_pct")
    if do_metacritic and "metacritic_score" not in rec:
        time.sleep(delay)
        mc = metacritic_lookup(title)
        if mc:
            rec["metacritic_score"] = mc["score"]
            rec["metacritic_url"] = mc["url"]
            rec["metacritic_source"] = f"metacritic_{mc['source']}"
    return rec


def write_ratings_table(games, cache):
    rows = []
    for g in games:
        rec = cache.get(g["key"]) or {}
        rows.append({
            "title": g["title"],
            "stores": ",".join(s[:1].upper() for s in g["stores"]),
            "mc": rec.get("metacritic_score"),
            "mc_src": rec.get("metacritic_source") or "",
            "steam_desc": rec.get("steam_review_desc") or "",
            "steam_pct": rec.get("steam_positive_pct"),
            "steam_n": rec.get("steam_review_count") or 0,
        })
    rows.sort(key=lambda r: (
        -(r["mc"] if r["mc"] is not None else -1),
        -(r["steam_pct"] if r["steam_pct"] is not None else -1),
        -(r["steam_n"] or 0),
        r["title"].lower(),
    ))

    title_w = 50
    lines = []
    lines.append("=" * 110)
    lines.append(f"GAME RATINGS - {len(rows)} unique titles")
    lines.append("=" * 110)
    rated = [r for r in rows if r["mc"] is not None or r["steam_desc"]]
    lines.append(f"With any rating data: {len(rated)} / {len(rows)}")
    with_mc = [r for r in rows if r["mc"] is not None]
    if with_mc:
        avg = sum(r["mc"] for r in with_mc) / len(with_mc)
        lines.append(f"With Metacritic score: {len(with_mc)} (avg {avg:.1f})")
    lines.append("")
    hdr = (f"{'Title':<{title_w}}  {'Stores':<6}  {'MC':>3}  "
           f"{'Source':<10}  {'Steam Reviews':<22}  {'%':>3}  {'N':>7}")
    lines.append(hdr)
    lines.append("-" * len(hdr))
    for r in rows:
        title = r["title"]
        if len(title) > title_w:
            title = title[: title_w - 1] + "\u2026"
        mc = f"{r['mc']:>3}" if r["mc"] is not None else "  -"
        pct = f"{r['steam_pct']:>3}" if r["steam_pct"] is not None else "  -"
        n = f"{r['steam_n']:>7}" if r["steam_n"] else "      -"
        lines.append(
            f"{title:<{title_w}}  {r['stores']:<6}  {mc}  "
            f"{r['mc_src']:<10}  {r['steam_desc']:<22}  {pct}  {n}"
        )
    out = "\n".join(lines) + "\n"
    RATINGS_TXT.write_text(out)
    return out


def main():
    p = argparse.ArgumentParser(description="Fetch game ratings from Steam + Metacritic")
    p.add_argument("--limit", type=int, default=0,
                   help="Only process first N games (0 = all)")
    p.add_argument("--delay", type=float, default=0.4,
                   help="Seconds between HTTP requests (default 0.4)")
    p.add_argument("--no-metacritic", action="store_true",
                   help="Skip Metacritic fallback for Steam misses")
    p.add_argument("--refresh", action="store_true",
                   help="Re-fetch even if already cached")
    p.add_argument("--refresh-metacritic", action="store_true",
                   help="Re-run Metacritic lookup for cached entries whose "
                        "metacritic data did not come from Steam")
    p.add_argument("--write-only", action="store_true",
                   help="Skip fetching; just regenerate the table from cache")
    args = p.parse_args()

    games = collect_games()
    print(f"Found {len(games)} unique game titles")
    cache = load_cache()

    if args.refresh_metacritic and not args.write_only:
        targets = []
        for g in games:
            rec = cache.get(g["key"])
            if not rec:
                continue
            src = rec.get("metacritic_source") or ""
            if src.startswith("metacritic_"):
                targets.append(g)
        print(f"Refreshing Metacritic for {len(targets)} cached entries")
        for i, g in enumerate(targets, 1):
            rec = cache[g["key"]]
            for k in ("metacritic_score", "metacritic_url", "metacritic_source"):
                rec.pop(k, None)
            try:
                mc = metacritic_lookup(g["title"])
            except Exception:
                mc = None
            if mc:
                rec["metacritic_score"] = mc["score"]
                rec["metacritic_url"] = mc["url"]
                rec["metacritic_source"] = f"metacritic_{mc['source']}"
            cache[g["key"]] = rec
            sc = rec.get("metacritic_score")
            print(f"[{i}/{len(targets)}] {g['title'][:60]:<60}  MC={sc if sc is not None else '-':>3}")
            if i % 20 == 0:
                save_cache(cache)
            time.sleep(args.delay)
        save_cache(cache)
        out = write_ratings_table(games, cache)
        print(f"\nWrote {RATINGS_TXT}")
        print(out.split("\n\n", 1)[0])
        return

    if not args.write_only:
        todo = games if args.refresh else [g for g in games if g["key"] not in cache]
        if args.limit > 0:
            todo = todo[: args.limit]
        print(f"Need to fetch: {len(todo)} (cache hits: {len(games) - len(todo)})")

        for i, g in enumerate(todo, 1):
            try:
                rec = fetch_one(g["title"], not args.no_metacritic, args.delay)
            except KeyboardInterrupt:
                print("\nInterrupted; saving cache.")
                save_cache(cache)
                sys.exit(1)
            except Exception as e:
                rec = {"title": g["title"], "error": str(e)}
            cache[g["key"]] = rec
            mc = rec.get("metacritic_score")
            sd = rec.get("steam_review_desc") or ""
            print(f"[{i}/{len(todo)}] {g['title'][:60]:<60}  "
                  f"MC={mc if mc is not None else '-':>3}  Steam={sd}")
            if i % 20 == 0:
                save_cache(cache)
            time.sleep(args.delay)
        save_cache(cache)

    out = write_ratings_table(games, cache)
    print(f"\nWrote {RATINGS_TXT}")
    print(out.split("\n\n", 1)[0])


if __name__ == "__main__":
    main()
