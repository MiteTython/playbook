# Playbook

The iPhone play logger. Open the GitHub Pages link in Safari, then Share → Add to Home Screen.

Data lives on the phone and in one private Gist (`chalk-playbook sync v1`) shared with chalk on the iPad. Nothing personal is in this repo.

`base-book.json` is the NFL base library (43 sets, 32 fronts) both apps build from — formations only, no play data.
Inside chalk on the iPad, Playbook runs in a frame (`window.PB_EMBED`): no sync, no storage, saves go back to chalk as messages.

`data/` is every scrimmage play of every game, from the public nflverse data: `data/games.json` is the index, `data/pbp/<season>/<game_id>.json` one game, drive by drive. Each play carries the play-by-play (situation, clock, result), FTN's charting when it's out (QB spot, backs, box, rushers, blitzers, motion, play action, RPO, screen, hash — FTN lags a day or two after a game) and NGS participation when nflverse publishes it (personnel, package, coverage — after the season). Rebuild with `python3 tools/build_games.py` (stdlib only; logs, baselines and dumps land in `tools/_diag/`, git-ignored). In season, personnel comes from the iPad: chalk writes NFL's on-field 22 for each play it shows into the shared Gist.
