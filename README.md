# check-games

Cross-reference a game wishlist against your Steam, GOG, Humble Bundle, and Epic Games libraries. Also checks Apple Silicon (ARM/Rosetta 2) compatibility via [AppleGamingWiki](https://www.applegamingwiki.com/).

macOS only — drives Safari via AppleScript.

## Requirements

- macOS with Safari
- Safari → Develop → Allow JavaScript from Apple Events: **ON**
- Logged in to Steam, GOG, Humble, and Epic in Safari
- Python 3.10+ (no external dependencies)

## Usage

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

## Output files

| File | Description |
|------|-------------|
| `games_ownership.json` | Full cross-reference results for all games in the wishlist |
| `games_ownership.txt` | Human-readable summary table |
| `games_owned.json` | Filtered to only games you own, with store and Apple Silicon info |
| `games_libraries.json` | Complete library dumps from all stores, with item type classification |
| `games_cache.json` | Cache file for incremental runs |

## Item types

Items are classified using source metadata (Humble download platforms, GOG media types) with title-pattern fallback:

`game`, `book`, `audio`, `video`, `soundtrack`, `dlc`, `comic`, `artbook`, `demo`, `software`, `coupon`, `wallpaper`

## Customization

Edit the `GAMES` list at the top of `check_games.py` to set your own wishlist.

## Data sources

- **Steam**: Store API + community profile page
- **GOG**: embed.gog.com account API
- **Humble Bundle**: Order/library API
- **Epic Games**: Account transactions page (DOM scraping with pagination)
- **Apple Silicon**: [AppleGamingWiki](https://www.applegamingwiki.com/) Cargo API
