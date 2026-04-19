#!/usr/bin/env python3
"""
Cross-reference a game wishlist against Steam, GOG, Humble, and Epic libraries.
Also checks Apple Silicon compatibility via AppleGamingWiki.

Drives Safari via AppleScript (macOS only). Requirements:
  - Safari -> Develop -> Allow JavaScript from Apple Events: ON
  - Logged in to Steam, GOG, Humble, and Epic in Safari

Outputs:
  games_ownership.json  - full cross-reference results
  games_ownership.txt   - summary table
  games_owned.json      - filtered to owned games only
  games_libraries.json  - full library dumps with item types
  games_cache.json      - cache for incremental runs
"""

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR
CACHE_FILE = OUTPUT_DIR / "games_cache.json"

GAMES = [
    "Knotwords", "Mini Motorways", "Return to Monkey Island", "Unpacking",
    "Kami", "Gorogoa", "How To Say Goodbye", "The White Door",
    "World of Goo", "Dorfromantik", "Baba is You", "The Witness",
    "Fez", "Portal", "SpaceChem", "Braid", "The Swapper", "Firewatch",
    "Opus Magnum", "Return of the Obra Dinn", "Infinifactory",
    "Antichamber", "Ctrl Alt Ego", "The Incredible Machine 2",
]


ITEM_PATTERNS = [
    ("soundtrack",  re.compile(r"\b(soundtrack|ost|original score|original music|music of)\b", re.I)),
    ("dlc",         re.compile(r"\b(dlc|expansion pass|season pass|expansion pack|add-on|addon|bonus content|booster pack|character pack|map pack|skin pack|cosmetic pack|item pack|upgrade pack)\b", re.I)),
    ("comic",       re.compile(r"\b(comic|graphic novel|issue\s*#?\d+)\b", re.I)),
    ("artbook",     re.compile(r"\b(artbook|art\s*book|art\s*of|digital art|concept art collection)\b", re.I)),
    ("book",        re.compile(r"\b(ebook|e-book|\bpdf\b|guide book|strategy guide|novel\b|novella)\b", re.I)),
    ("demo",        re.compile(r"\b(demo|playtest|prologue)\b", re.I)),
    ("video",       re.compile(r"\b(movie|film|documentary|video series|making of)\b", re.I)),
    ("wallpaper",   re.compile(r"\b(wallpaper|avatar|profile|badge|emoticon|trading card)\b", re.I)),
    ("software",    re.compile(r"\b(rpg maker|game maker|editor|sdk|toolkit|engine)\b", re.I)),
    ("coupon",      re.compile(r"\b(\d+%\s*off|coupon|discount)\b", re.I)),
]


def classify_item(title: str) -> str:
    for item_type, pattern in ITEM_PATTERNS:
        if pattern.search(title):
            return item_type
    return "game"


def osa(script: str, timeout: float = 30) -> str:
    r = subprocess.run(
        ["osascript", "-"], input=script,
        capture_output=True, text=True, timeout=timeout,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())
    return r.stdout.strip()


def navigate(url: str):
    osa(f'''
tell application "Safari"
    activate
    if (count of documents) = 0 then make new document
    set URL of front document to "{url}"
end tell
''')
    deadline = time.time() + 30
    while time.time() < deadline:
        time.sleep(0.4)
        try:
            ready = osa('tell application "Safari" to return '
                        '(do JavaScript "document.readyState" in front document)')
        except RuntimeError:
            continue
        if ready == "complete":
            time.sleep(0.6)
            return
    print(f"  warning: load timeout for {url}", file=sys.stderr)


def run_js(js_code: str, timeout: float = 120) -> str:
    """Inject JS that sets window.__result; poll until set; return string."""
    full = "window.__result = null;\n" + js_code
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(full)
        path = f.name
    try:
        osa(f'''
set jsCode to (read POSIX file "{path}")
tell application "Safari" to do JavaScript jsCode in front document
''')
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(0.6)
            try:
                out = osa(
                    'tell application "Safari" to return '
                    '(do JavaScript "window.__result === null ? \\"\\" : window.__result" '
                    'in front document)'
                )
            except RuntimeError:
                continue
            if out:
                if out.startswith("ERR:"):
                    raise RuntimeError(out)
                return out
        raise TimeoutError("JS poll timed out")
    finally:
        Path(path).unlink(missing_ok=True)


def normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


# ---------- Steam ----------

STEAM_JS = """
(async () => {
  try {
    const ud = await (await fetch('/dynamicstore/userdata/', {credentials:'include'})).json();
    const ownedIds = ud.rgOwnedApps || [];
    const owned = new Set(ownedIds);
    const games = %s;
    const norm = s => s.toLowerCase().replace(/[^a-z0-9]+/g, '');
    const lookups = await Promise.all(games.map(g =>
      fetch('/api/storesearch/?term=' + encodeURIComponent(g) + '&l=english&cc=us',
            {credentials:'include'})
        .then(r => r.json()).catch(() => ({items:[]}))
    ));
    const result = {};
    for (let i = 0; i < games.length; i++) {
      const items = (lookups[i] && lookups[i].items) || [];
      const target = norm(games[i]);
      const exact = items.find(it => norm(it.name) === target);
      const match = exact || items[0] || null;
      result[games[i]] = match
        ? {id: match.id, name: match.name, owned: owned.has(match.id)}
        : {id: null, name: null, owned: false};
    }
    window.__result = JSON.stringify({totalOwned: owned.size, ownedIds: ownedIds, matches: result});
  } catch (e) {
    window.__result = 'ERR:' + e.message;
  }
})();
"""

STEAM_LIBRARY_JS = """
(async () => {
  try {
    const seen = {};
    const harvest = () => {
      document.querySelectorAll('a[href*="/app/"]').forEach(a => {
        const m = a.href.match(/\\/app\\/(\\d+)/);
        if (!m) return;
        const id = parseInt(m[1]);
        const name = a.textContent.trim();
        if (name && name !== 'Store Page' && name.length < 200 && !seen[id]) {
          seen[id] = name;
        }
      });
    };
    // Find the scrollable container (React virtual scroll)
    const containers = [...document.querySelectorAll('div')].filter(d => {
      const s = getComputedStyle(d);
      return (s.overflowY === 'auto' || s.overflowY === 'scroll') && d.scrollHeight > d.clientHeight + 100;
    });
    const scroller = containers.length > 0 ? containers[containers.length - 1] : null;
    const target = scroller || document.documentElement;
    const step = (scroller ? scroller.clientHeight : window.innerHeight) * 0.7;
    let pos = 0;
    let stale = 0;
    let prev = 0;
    for (let i = 0; i < 500 && stale < 15; i++) {
      pos += step;
      target.scrollTop = pos;
      await new Promise(r => setTimeout(r, 150));
      harvest();
      const cnt = Object.keys(seen).length;
      if (cnt === prev) stale++;
      else stale = 0;
      prev = cnt;
    }
    const games = Object.entries(seen).map(([id, name]) => ({id: parseInt(id), name}));
    window.__result = JSON.stringify(games);
  } catch (e) {
    window.__result = 'ERR:' + e.message;
  }
})();
"""

def check_steam():
    print("\n=== Steam ===")
    navigate("https://store.steampowered.com/")
    time.sleep(2)
    js = STEAM_JS % json.dumps(GAMES)
    raw = run_js(js, timeout=180)
    data = json.loads(raw)
    print(f"  Owned Steam apps (incl DLC/tools): {data['totalOwned']}")

    library = []
    try:
        print("  Fetching full library from community profile...")
        navigate("https://steamcommunity.com/my/games/?tab=all&sort=name")
        time.sleep(4)
        raw2 = run_js(STEAM_LIBRARY_JS, timeout=120)
        library_data = json.loads(raw2)
        library = sorted([{"title": g["name"], "type": "game"}
                           for g in library_data if g.get("name")],
                          key=lambda x: x["title"])
        print(f"  Library: {len(library)} games")
    except Exception as e:
        print(f"  Library fetch failed: {e}", file=sys.stderr)

    return data["matches"], library


# ---------- GOG ----------

GOG_JS = """
(async () => {
  try {
    const products = [];
    for (const mediaType of [1, 2]) {
      let page = 1, totalPages = 1;
      do {
        const r = await fetch(
          'https://embed.gog.com/account/getFilteredProducts?mediaType=' + mediaType + '&page=' + page,
          {credentials:'include'});
        const data = await r.json();
        totalPages = data.totalPages || 1;
        for (const p of (data.products || [])) {
          products.push({title: p.title, category: p.category || null, mediaType: mediaType});
        }
        page++;
      } while (page <= totalPages);
    }
    window.__result = JSON.stringify(products);
  } catch (e) {
    window.__result = 'ERR:' + e.message;
  }
})();
"""

GOG_MEDIA_MAP = {1: "game", 2: "video"}

def check_gog():
    print("\n=== GOG ===")
    navigate("https://embed.gog.com/account")
    time.sleep(2)
    raw = run_js(GOG_JS, timeout=120)
    products = json.loads(raw)
    print(f"  Owned GOG products: {len(products)}")
    titles = [p["title"] for p in products]
    norm_owned = {normalize(t): t for t in titles}
    out = {}
    for g in GAMES:
        n = normalize(g)
        if n in norm_owned:
            out[g] = {"owned": True, "match": norm_owned[n]}
        else:
            out[g] = {"owned": False, "match": None}
    library = []
    for p in sorted(products, key=lambda x: x["title"]):
        t = GOG_MEDIA_MAP.get(p["mediaType"], classify_item(p["title"]))
        library.append({"title": p["title"], "type": t})
    return out, library


# ---------- Humble ----------

HUMBLE_JS = """
(async () => {
  try {
    const orders = await (await fetch('/api/v1/user/order',
                                      {credentials:'include'})).json();
    const details = await Promise.all(orders.map(o =>
      fetch('/api/v1/order/' + o.gamekey + '?all_tpkds=true',
            {credentials:'include'}).then(r => r.json())
    ));
    const items = {};
    for (const d of details) {
      for (const sp of (d.subproducts || [])) {
        if (!sp.human_name) continue;
        const platforms = (sp.downloads || []).map(dl => dl.platform);
        if (!items[sp.human_name]) {
          items[sp.human_name] = {platforms: platforms, icon: sp.icon || null};
        }
      }
      const tpkd = d.tpkd_dict || {};
      for (const t of (tpkd.all_tpks || [])) {
        if (t.human_name && !items[t.human_name]) {
          items[t.human_name] = {platforms: [], key_type: t.key_type_human_name || t.key_type || null};
        }
      }
    }
    window.__result = JSON.stringify({orders: orders.length, items: items});
  } catch (e) {
    window.__result = 'ERR:' + e.message;
  }
})();
"""

HUMBLE_PLATFORM_MAP = {
    "ebook": "book",
    "audio": "audio",
    "video": "video",
    "comedy": "video",
    "asmr": "audio",
}

def humble_item_type(name: str, info: dict) -> str:
    platforms = set(info.get("platforms", []))
    game_platforms = platforms & {"windows", "mac", "linux", "android"}
    non_game = platforms - game_platforms
    if non_game and not game_platforms:
        for p in non_game:
            if p in HUMBLE_PLATFORM_MAP:
                return HUMBLE_PLATFORM_MAP[p]
    if game_platforms or not platforms:
        return classify_item(name)
    return classify_item(name)

def check_humble():
    print("\n=== Humble Bundle ===")
    navigate("https://www.humblebundle.com/home/library")
    raw = run_js(HUMBLE_JS, timeout=300)
    data = json.loads(raw)
    items = data["items"]
    print(f"  Humble orders: {data['orders']}, distinct entitlements: {len(items)}")
    norm_owned = {normalize(t): t for t in items}
    out = {}
    for g in GAMES:
        n = normalize(g)
        if n in norm_owned:
            out[g] = {"owned": True, "match": norm_owned[n]}
        else:
            out[g] = {"owned": False, "match": None}
    library = []
    for name, info in sorted(items.items()):
        library.append({"title": name, "type": humble_item_type(name, info)})
    return out, library


# ---------- Epic ----------

EPIC_JS = """
(async () => {
  try {
    const allTitles = [];
    let pages = 0;

    const harvest = () => {
      const items = [];
      const seen = new Set();
      document.querySelectorAll('tr[data-orderid]').forEach(row => {
        const oid = row.getAttribute('data-orderid');
        if (seen.has(oid)) return;
        seen.add(oid);
        const cells = row.querySelectorAll('td');
        if (cells.length < 3) return;
        const desc = cells[2].textContent.trim();
        const title = desc.replace(/^(Purchased|Refunded|Free)\\s*/i, '').trim();
        if (title) items.push(title);
      });
      return items;
    };

    await new Promise(r => setTimeout(r, 2000));
    allTitles.push(...harvest());
    pages = 1;

    for (let i = 0; i < 200; i++) {
      const nextBtn = document.getElementById('next-btn');
      if (!nextBtn || nextBtn.disabled || nextBtn.hasAttribute('disabled')) break;
      nextBtn.click();
      await new Promise(r => setTimeout(r, 3000));
      const newItems = harvest();
      if (newItems.length === 0) break;
      allTitles.push(...newItems);
      pages++;
    }

    window.__result = JSON.stringify({pages: pages, titles: allTitles});
  } catch (e) {
    window.__result = 'ERR:' + e.message;
  }
})();
"""

def check_epic():
    print("\n=== Epic Games ===")
    navigate("https://www.epicgames.com/account/transactions/purchases?productName=epicgames")
    time.sleep(3)
    raw = run_js(EPIC_JS, timeout=600)
    data = json.loads(raw)
    titles = data["titles"]
    print(f"  Epic pages scraped: {data['pages']}, purchases found: {len(titles)}")
    norm_owned = {normalize(t): t for t in titles}
    out = {}
    for g in GAMES:
        n = normalize(g)
        if n in norm_owned:
            out[g] = {"owned": True, "match": norm_owned[n]}
        else:
            out[g] = {"owned": False, "match": None}
    library = sorted([{"title": t, "type": classify_item(t)}
                       for t in set(titles)], key=lambda x: x["title"])
    return out, library


# ---------- Apple Silicon (AppleGamingWiki Cargo API) ----------

AGWIKI_API = "https://www.applegamingwiki.com/w/api.php"

def check_apple_silicon():
    """Query AppleGamingWiki Cargo API for ARM/Rosetta compatibility."""
    print("\n=== Apple Silicon Compatibility (via AppleGamingWiki) ===")
    where_parts = [f"_pageName='{g}'" for g in GAMES]
    where = " OR ".join(where_parts)
    params = urllib.parse.urlencode({
        "action": "cargoquery",
        "tables": "Compatibility_macOS",
        "fields": "_pageName=Page,native,rosetta_2,crossover,wine,parallels",
        "where": where,
        "limit": "100",
        "format": "json",
    })
    url = f"{AGWIKI_API}?{params}"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "check_games/1.0 (game library checker)"
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print(f"  API request failed: {e}", file=sys.stderr)
        return {}

    rows = data.get("cargoquery", [])
    result = {}
    for row in rows:
        r = row.get("title", {})
        page = r.get("Page", "")
        result[page] = {
            "native": r.get("native", "unknown"),
            "rosetta_2": r.get("rosetta 2", "unknown"),
            "crossover": r.get("crossover", "unknown"),
            "wine": r.get("wine", "unknown"),
            "parallels": r.get("parallels", "unknown"),
        }

    norm_map = {normalize(k): k for k in result}
    out = {}
    for g in GAMES:
        n = normalize(g)
        if g in result:
            out[g] = result[g]
        elif n in norm_map:
            out[g] = result[norm_map[n]]
        else:
            out[g] = None

    found = sum(1 for v in out.values() if v)
    native = sum(1 for v in out.values()
                 if v and v["native"].lower() not in ("na", "unknown", "unplayable"))
    rosetta = sum(1 for v in out.values()
                  if v and v["rosetta_2"].lower() not in ("na", "unknown", "unplayable"))
    print(f"  Found {found}/{len(GAMES)} in wiki, {native} ARM-native, {rosetta} Rosetta 2")
    return out


# ---------- Main ----------

def load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_cache(cache: dict):
    CACHE_FILE.write_text(json.dumps(cache, indent=2))


def cache_age_hours(cache: dict, key: str) -> float | None:
    ts = cache.get("timestamps", {}).get(key)
    if not ts:
        return None
    fetched = datetime.fromisoformat(ts)
    return (datetime.now(timezone.utc) - fetched).total_seconds() / 3600


def cell(label: str, max_w: int) -> str:
    return (label[:max_w-1] + "…") if len(label) > max_w else label.ljust(max_w)

def main():
    parser = argparse.ArgumentParser(description="Cross-reference game libraries")
    parser.add_argument("--max-age", type=float, default=24,
                        help="Max cache age in hours before re-fetching (default: 24)")
    parser.add_argument("--force", action="store_true",
                        help="Ignore cache and re-fetch everything")
    parser.add_argument("--force-store", action="append", default=[],
                        metavar="STORE",
                        help="Force re-fetch for specific store (steam/gog/humble/epic/silicon)")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="Directory for output files (default: script directory)")
    args = parser.parse_args()

    global OUTPUT_DIR, CACHE_FILE
    if args.output_dir:
        OUTPUT_DIR = args.output_dir.resolve()
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE = OUTPUT_DIR / "games_cache.json"

    cache = load_cache()
    results = cache.get("results", {"steam": {}, "gog": {}, "humble": {}, "epic": {}})
    libraries = cache.get("libraries", {"steam": [], "gog": [], "humble": [], "epic": []})
    silicon = cache.get("silicon", {})
    if "timestamps" not in cache:
        cache["timestamps"] = {}

    def needs_fetch(key):
        if args.force or key in args.force_store:
            return True
        age = cache_age_hours(cache, key)
        if age is None:
            return True
        return age > args.max_age

    sources = [("steam", check_steam), ("gog", check_gog),
               ("humble", check_humble), ("epic", check_epic)]
    stale = [k for k, _ in sources if needs_fetch(k)] + (["silicon"] if needs_fetch("silicon") else [])

    if not stale:
        ages = {k: cache_age_hours(cache, k) for k in ["steam", "gog", "humble", "epic", "silicon"]}
        print("All data is cached and fresh:")
        for k, h in ages.items():
            print(f"  {k}: {h:.1f}h ago")
        print(f"Use --force to re-fetch, or --max-age N to change threshold (current: {args.max_age}h)")
    else:
        print("Pre-flight checklist:")
        print("  1. Safari -> Develop -> Allow JavaScript from Apple Events: ON")
        print("  2. Logged in to Steam, GOG, Humble, Epic in Safari")
        print("  3. Safari is the default browser or front app (script will activate it)")
        print(f"\nWill fetch: {', '.join(stale)}")
        cached = [k for k, _ in sources if k not in stale]
        if "silicon" not in stale:
            cached.append("silicon")
        if cached:
            print(f"Using cache: {', '.join(cached)}")
        input("Press Return to start...")

        now = datetime.now(timezone.utc).isoformat()
        for name, fn in sources:
            if not needs_fetch(name):
                age = cache_age_hours(cache, name)
                print(f"\n=== {name.title()} === (cached, {age:.1f}h old)")
                continue
            try:
                rv = fn()
                if rv:
                    results[name], libraries[name] = rv
                    cache["timestamps"][name] = now
            except Exception as e:
                print(f"  {name} failed: {e}", file=sys.stderr)

        if needs_fetch("silicon"):
            try:
                silicon = check_apple_silicon()
                cache["timestamps"]["silicon"] = now
            except Exception as e:
                print(f"  Apple Silicon check failed: {e}", file=sys.stderr)

        cache["results"] = results
        cache["libraries"] = libraries
        cache["silicon"] = silicon
        save_cache(cache)

    print("\n=== Summary ===")
    hdr = f"{'Game':<28}  {'Steam':<7}  {'GOG':<7}  {'Humble':<7}  {'Epic':<7}  {'ARM':<10}  {'Rosetta':<10}"
    sep = "-" * len(hdr)
    print(hdr)
    print(sep)
    lines = [hdr, sep]

    playable = {"perfect", "playable", "runs"}

    for g in GAMES:
        s = results["steam"].get(g)
        sm = "OWNED" if (s and s.get("owned")) else ("—" if s else "?")
        gg = results["gog"].get(g)
        gm = "OWNED" if (gg and gg.get("owned")) else ("~" if gg and gg.get("match") else "—")
        h = results["humble"].get(g)
        hm = "OWNED" if (h and h.get("owned")) else ("~" if h and h.get("match") else "—")
        e = results["epic"].get(g)
        em = "OWNED" if (e and e.get("owned")) else ("~" if e and e.get("match") else "—")

        si = silicon.get(g)
        if si:
            nat = si["native"].lower()
            ros = si["rosetta_2"].lower()
            am = nat.capitalize() if nat in playable else ("N/A" if nat == "na" else "?")
            rm = ros.capitalize() if ros in playable else ("N/A" if ros == "na" else "?")
        else:
            am = "?"
            rm = "?"

        line = f"{cell(g, 28)}  {sm:<7}  {gm:<7}  {hm:<7}  {em:<7}  {am:<10}  {rm:<10}"
        print(line)
        lines.append(line)

    legend = [
        "",
        "Legend: OWNED = in library | ~ = fuzzy match | — = not found",
        "ARM/Rosetta from AppleGamingWiki: Perfect/Playable/Runs = works, N/A = no port, ? = unknown",
    ]
    for l in legend:
        print(l)
        lines.append(l)

    out_json = OUTPUT_DIR / "games_ownership.json"
    results["apple_silicon"] = silicon
    out_json.write_text(json.dumps(results, indent=2))

    out_txt = OUTPUT_DIR / "games_ownership.txt"
    out_txt.write_text("\n".join(lines) + "\n")

    stores = ["steam", "gog", "humble", "epic"]
    owned = {}
    for g in GAMES:
        platforms = [st for st in stores
                     if results.get(st, {}).get(g, {}).get("owned")]
        if platforms:
            owned[g] = {
                "owned_on": platforms,
                "apple_silicon": silicon.get(g),
            }
    out_owned = OUTPUT_DIR / "games_owned.json"
    out_owned.write_text(json.dumps(owned, indent=2))

    all_types = {}
    for store, items in libraries.items():
        for item in items:
            t = item["type"] if isinstance(item, dict) else "game"
            all_types[t] = all_types.get(t, 0) + 1

    out_libs = OUTPUT_DIR / "games_libraries.json"
    out_libs.write_text(json.dumps(libraries, indent=2))

    total = sum(len(v) for v in libraries.values())
    print(f"\nFull results: {out_json}")
    print(f"Summary table: {out_txt}")
    print(f"Owned only: {out_owned} ({len(owned)} games from checklist)")
    print(f"All libraries: {out_libs} ({total} items: "
          f"{len(libraries['steam'])} Steam, {len(libraries['gog'])} GOG, "
          f"{len(libraries['humble'])} Humble, {len(libraries['epic'])} Epic)")
    breakdown = ", ".join(f"{v} {k}" for k, v in
                          sorted(all_types.items(), key=lambda x: -x[1]))
    print(f"By type: {breakdown}")


if __name__ == "__main__":
    main()
