# check-games

Cross-reference a game wishlist against your Steam, GOG, Humble Bundle, and Epic Games libraries. Then enrich the full owned-game list with Metacritic / Steam review ratings and Apple Silicon (ARM / Rosetta 2 / CrossOver) compatibility from [AppleGamingWiki](https://www.applegamingwiki.com/).

macOS only for the library-scraping step (drives Safari via AppleScript). The post-processing scripts (`group_by_type.py`, `reclassify.py`, `fetch_ratings.py`, `best_arm_games.py`) only need Python 3.10+ and an internet connection.

## Requirements

- macOS with Safari (only for `check_games.py`)
- Safari → Develop → Allow JavaScript from Apple Events: **ON**
- Logged in to Steam, GOG, Humble, and Epic in Safari
- Python 3.10+ (no external dependencies)

## Usage

### 1. Scrape libraries + cross-reference wishlist

```bash
# First run (fetches everything, takes a few minutes)
python3 check_games.py

# Subsequent runs use cached data (instant)
python3 check_games.py

# Force re-fetch everything
python3 check_games.py --force

# Force re-fetch only one store
python3 check_games.py --force-store steam

# Change cache TTL (default: 24 hours)
python3 check_games.py --max-age 48

# Custom output directory
python3 check_games.py --output-dir ~/Desktop
```

### 2. Re-classify items without re-scraping

If you tweak the title-pattern rules in `check_games.py`, re-run classification on the existing `games_libraries.json` in place. Only items currently typed `game`, `course`, `asset`, `demo`, `software`, or `wallpaper` are re-evaluated, so Humble's platform-derived `book` / `audio` / `video` types are preserved.

```bash
python3 reclassify.py
```

### 3. Tabular inventory grouped by type

```bash
python3 group_by_type.py
# writes games_by_type.txt
```

### 4. Fetch ratings (Metacritic + Steam reviews)

Uses Steam's public store API for Metacritic scores and review summaries, with a Metacritic page-scrape fallback (parses the schema.org `aggregateRating` JSON-LD block). Resumable via `games_ratings_cache.json`.

```bash
# Full run (1500-ish games at ~1.5s each = ~30 min)
python3 fetch_ratings.py

# Try a small sample first
python3 fetch_ratings.py --limit 20

# Skip the Metacritic fallback
python3 fetch_ratings.py --no-metacritic

# Re-run only the Metacritic-fallback entries (e.g. after a regex fix)
python3 fetch_ratings.py --refresh-metacritic

# Just rebuild the table from the cache without fetching
python3 fetch_ratings.py --write-only
```

### 5. Best owned games runnable on Apple Silicon

Queries AppleGamingWiki's Cargo API in batches for the full owned-games list, falls back to Steam's `platforms.mac` flag for coverage gaps, then joins with the ratings cache and writes a table sorted by best available rating per category (ARM Native, Rosetta 2, Steam macOS, CrossOver).

```bash
python3 best_arm_games.py
# writes games_arm.txt and games_silicon_cache.json

# Skip the Steam mac-flag fallback
python3 best_arm_games.py --no-steam

# Just rebuild the table from cache
python3 best_arm_games.py --write-only
```

## Output files

| File | Producer | Description |
|------|----------|-------------|
| `games_ownership.json` | `check_games.py` | Full cross-reference results for all wishlist games |
| `games_ownership.txt` | `check_games.py` | Human-readable wishlist summary table |
| `games_owned.json` | `check_games.py` | Wishlist items you own, with stores + Apple Silicon info |
| `games_libraries.json` | `check_games.py` | Complete library dumps from all stores, with item-type classification |
| `games_cache.json` | `check_games.py` | Library-scraping cache for incremental runs |
| `games_by_type.txt` | `group_by_type.py` | Tabular inventory grouped by item type |
| `games_ratings.txt` | `fetch_ratings.py` | All owned games sorted by Metacritic / Steam rating |
| `games_ratings_cache.json` | `fetch_ratings.py` | Per-title ratings cache (resumable) |
| `games_arm.txt` | `best_arm_games.py` | Owned games runnable on Apple Silicon, sorted by rating |
| `games_silicon_cache.json` | `best_arm_games.py` | AGW + Steam mac-flag cache |

All output files are gitignored.

## Item types

Items are classified using source metadata (Humble download platforms, GOG media types) with title-pattern fallback:

`game`, `book`, `audio`, `video`, `soundtrack`, `dlc`, `comic`, `artbook`, `demo`, `software`, `coupon`, `wallpaper`, `course`, `asset`

`course` covers technical courses / workshops / video tutorials common in Humble book-and-software bundles. `asset` covers game-development asset packs (tilesets, sprites, icons, sound packs, etc.).

## Customization

Edit the `GAMES` list at the top of `check_games.py` to set your own wishlist.

## Data sources

- **Steam**: Store API + community profile page (library scrape via Safari) + public storesearch / appdetails / appreviews APIs (ratings)
- **GOG**: embed.gog.com account API
- **Humble Bundle**: Order/library API
- **Epic Games**: Account transactions page (DOM scraping with pagination)
- **Metacritic**: Steam's appdetails.metacritic field + Metacritic.com schema.org JSON-LD `aggregateRating` block (page scrape)
- **Apple Silicon**: [AppleGamingWiki](https://www.applegamingwiki.com/) Cargo API (`Compatibility_macOS` table)
