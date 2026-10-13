#!/usr/bin/env python3
"""
build_games.py — every scrimmage play of every game, from the public nflverse play-by-play, into the repo.

    python3 tools/build_games.py            # the current season
    python3 tools/build_games.py 2026 2025  # these seasons

Writes
    data/games.json                  the index: one row per game, with how many snaps each offense ran
    data/pbp/<season>/<game_id>.json one file per game: drives, each with its plays in order

Stdlib only. No install.

Diagnostics are always on (tools/_diag/, git-ignored):
    log.txt                 every run, every stage, counts and timing
    baselines/<season>.json the last known-good column list, row count and game count per season
    dumps/<stamp>-<stage>.json  what a failing stage received, next to the baseline it should have matched
"""
import csv, gzip, io, json, os, re, sys, time, traceback, urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
DIAG = os.path.join(ROOT, "tools", "_diag")
NV = "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.csv.gz"

NEED = ["game_id", "play_id", "week", "season", "game_date", "home_team", "away_team", "posteam", "defteam", "qtr", "time", "down", "ydstogo",
        "yardline_100", "desc", "play_type", "yards_gained", "shotgun", "no_huddle", "qb_scramble", "sack", "pass_length", "pass_location",
        "run_location", "run_gap", "touchdown", "interception", "fumble_lost", "first_down", "penalty", "posteam_score", "defteam_score",
        "fixed_drive", "fixed_drive_result", "drive_start_yard_line", "drive_quarter_start", "drive_game_clock_start", "home_score", "away_score"]
SCRIM = {"pass", "run", "qb_kneel", "qb_spike", "no_play"}

csv.field_size_limit(1 << 24)
os.makedirs(os.path.join(DIAG, "baselines"), exist_ok=True)
os.makedirs(os.path.join(DIAG, "dumps"), exist_ok=True)
T0 = time.time()


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(stage, msg, **kv):
    line = f"{now()} +{time.time() - T0:6.1f}s {stage:<14} {msg}" + (("  " + json.dumps(kv, separators=(",", ":"))) if kv else "")
    print(line)
    with open(os.path.join(DIAG, "log.txt"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def dump(stage, expected, got, payload):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = os.path.join(DIAG, "dumps", f"{stamp}-{stage}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"stage": stage, "expected": expected, "got": got, "at": now(), "payload": payload}, f, indent=1, default=str)
    return path


class Fail(Exception):
    def __init__(self, stage, expected, got, payload=None):
        path = dump(stage, expected, got, payload)
        super().__init__(f"{stage}: expected {expected}, got {got}. Dump: {path}")


def baseline(season):
    p = os.path.join(DIAG, "baselines", f"{season}.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return None


def save_baseline(season, obj):
    with open(os.path.join(DIAG, "baselines", f"{season}.json"), "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1)


def fetch(season):
    url = NV.format(season=season)
    log("fetch", url)
    req = urllib.request.Request(url, headers={"User-Agent": "playbook build_games"})
    with urllib.request.urlopen(req, timeout=120) as r:
        status, raw = r.status, r.read()
    if status != 200 or len(raw) < 10_000:
        raise Fail("fetch", "200 and a real file", f"{status}, {len(raw)} bytes", {"url": url, "head": raw[:200].decode("latin1")})
    text = gzip.decompress(raw).decode("utf-8")
    log("fetch", "ok", gz_mb=round(len(raw) / 1e6, 2), csv_mb=round(len(text) / 1e6, 1))
    return text


def rows_of(text, season):
    rd = csv.DictReader(io.StringIO(text))
    cols = rd.fieldnames or []
    missing = [c for c in NEED if c not in cols]
    if missing:
        raise Fail("columns", f"all of {NEED}", f"missing {missing}", {"columns": cols})
    rows = list(rd)
    if not rows:
        raise Fail("rows", "some rows", "0 rows", {"columns": cols})
    base = baseline(season)
    if base and len(rows) < base["rows"] * 0.5:
        raise Fail("rows", f"about {base['rows']} rows (last good)", f"{len(rows)} rows", {"baseline": base})
    log("rows", "parsed", rows=len(rows), columns=len(cols), new_columns=[c for c in cols if base and c not in base["columns"]][:10])
    return rows, cols


def num(v, f=int):
    if v in ("", None, "NA"):
        return None
    try:
        return f(float(v))
    except ValueError:
        return None


def flag(v):
    return v not in ("", None, "NA", "0", "0.0")


def res_of(r):
    pt = r["play_type"]
    ptype = "pass" if pt in ("pass", "qb_spike") else "run" if pt in ("run", "qb_kneel") else None
    d = r["desc"].lower()
    if pt == "no_play":
        ptype = "pass" if " pass " in d or "sacked" in d or "scrambles" in d else "run" if " up the middle" in d or " left end" in d or " right end" in d or " left tackle" in d or " right tackle" in d or " left guard" in d or " right guard" in d or " rushes " in d else None
    if not ptype:
        return None
    yds = num(r["yards_gained"])
    scr = flag(r["qb_scramble"])
    out = {"ptype": ptype, "loc": (r["pass_location"] if ptype == "pass" else r["run_location"]) or None,
           "gap": (r["run_gap"] or None) if ptype == "run" and not scr else None,
           "len": (r["pass_length"] or None) if ptype == "pass" else None,
           "yds": yds, "sack": flag(r["sack"]), "scr": scr, "td": flag(r["touchdown"]), "int": flag(r["interception"]),
           "fum": flag(r["fumble_lost"]), "fd": flag(r["first_down"]),
           "expl": yds is not None and (yds >= 10 if ptype == "run" else yds >= 20), "from": "nflverse"}
    if pt == "no_play":   # the feed leaves direction blank on a nullified play; the words still say where it went
        m = re.search(r"\b(short|deep) (left|middle|right)\b", d)
        if m and ptype == "pass":
            out["len"], out["loc"] = m.group(1), m.group(2)
        m = re.search(r"\b(left|right) (end|tackle|guard)\b", d)
        if m and ptype == "run":
            out["loc"], out["gap"] = m.group(1), m.group(2)
        if "up the middle" in d and ptype == "run":
            out["loc"], out["gap"] = "middle", "middle"
        m = re.search(r"for (-?\d+) yards?", d)
        out["yds"] = int(m.group(1)) if m else (0 if "incomplete" in d or "no gain" in d else None)
    if pt == "qb_kneel":
        out["kneel"] = True
    if pt == "qb_spike":
        out["spike"] = True
    return out


def build(season, rows):
    games = {}
    for r in rows:
        if r["play_type"] not in SCRIM or not r["posteam"] or r["posteam"] == "NA":
            continue
        res = res_of(r)
        if not res:
            continue
        gid = r["game_id"]
        g = games.get(gid)
        if not g:
            g = games[gid] = {"id": gid, "season": season, "week": num(r["week"]), "date": r["game_date"], "away": r["away_team"], "home": r["home_team"],
                              "score": {r["away_team"]: num(r["away_score"]), r["home_team"]: num(r["home_score"])}, "drives": {}}
        dn = num(r["fixed_drive"]) or 0
        d = g["drives"].get(dn)
        if not d:
            d = g["drives"][dn] = {"n": dn, "pos": r["posteam"], "def": r["defteam"], "q": num(r["drive_quarter_start"]), "start": r["drive_game_clock_start"] or None,
                                   "from": r["drive_start_yard_line"] or None, "result": r["fixed_drive_result"] or None, "plays": []}
        pid = num(r["play_id"])
        d["plays"].append({"key": f"{gid}:{pid}", "pid": pid, "q": num(r["qtr"]), "clock": r["time"] or None, "down": num(r["down"]), "dist": num(r["ydstogo"]),
                           "yard": num(r["yardline_100"]), "pos": r["posteam"], "def": r["defteam"], "desc": r["desc"],
                           "gun": flag(r["shotgun"]), "nh": flag(r["no_huddle"]), "pen": flag(r["penalty"]), "np": r["play_type"] == "no_play",
                           "sc": [num(r["posteam_score"]), num(r["defteam_score"])], "res": res})
    out = []
    for g in games.values():
        drives = [g["drives"][k] for k in sorted(g["drives"])]
        for d in drives:
            d["plays"].sort(key=lambda p: p["pid"])
        g["drives"] = drives
        counts = {}
        for d in drives:
            c = counts.setdefault(d["pos"], {"plays": 0, "drives": 0})
            c["plays"] += len(d["plays"])
            c["drives"] += 1
        g["teams"] = counts
        if len(drives) < 10 or sum(len(d["plays"]) for d in drives) < 60:
            raise Fail("game", "10+ drives and 60+ snaps in a game", f"{len(drives)} drives, {sum(len(d['plays']) for d in drives)} snaps in {g['id']}", g)
        out.append(g)
    out.sort(key=lambda g: (g["week"], g["date"], g["id"]))
    return out


def write_season(season, games):
    folder = os.path.join(DATA, "pbp", str(season))
    os.makedirs(folder, exist_ok=True)
    keep = set()
    for g in games:
        path = os.path.join(folder, g["id"] + ".json")
        keep.add(g["id"] + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(dict(g, v=1, built=now()), f, separators=(",", ":"), ensure_ascii=False)
    stale = [n for n in os.listdir(folder) if n.endswith(".json") and n not in keep]
    for n in stale:
        os.remove(os.path.join(folder, n))
    size = sum(os.path.getsize(os.path.join(folder, n)) for n in keep)
    log("write", f"{season}: {len(games)} game files", mb=round(size / 1e6, 2), removed_stale=stale)


def write_index(all_games):
    path = os.path.join(DATA, "games.json")
    old = {"games": []}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
    seasons_built = {g["season"] for g in all_games}
    kept = [g for g in old.get("games", []) if g.get("season") not in seasons_built]
    rows = kept + [{"id": g["id"], "season": g["season"], "week": g["week"], "date": g["date"], "away": g["away"], "home": g["home"], "score": g["score"], "teams": g["teams"]} for g in all_games]
    rows.sort(key=lambda g: (-g["season"], -(g["week"] or 0), g["date"], g["id"]))
    seasons = {}
    for g in rows:
        s = seasons.setdefault(str(g["season"]), {"games": 0, "weeks": []})
        s["games"] += 1
        if g["week"] not in s["weeks"]:
            s["weeks"].append(g["week"])
    for s in seasons.values():
        s["weeks"].sort()
    idx = {"v": 1, "built": now(), "source": "nflverse play_by_play (github.com/nflverse/nflverse-data)", "seasons": seasons, "games": rows}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(idx, f, separators=(",", ":"), ensure_ascii=False)
    log("index", f"{len(rows)} games", seasons={k: v["games"] for k, v in seasons.items()}, kb=round(os.path.getsize(path) / 1e3))


def main(seasons):
    log("start", f"seasons {seasons}")
    done = []
    for season in seasons:
        text = fetch(season)
        rows, cols = rows_of(text, season)
        games = build(season, rows)
        weeks = sorted({g["week"] for g in games})
        if not games:
            raise Fail("build", "games with scrimmage plays", "0 games", {"rows": len(rows)})
        log("build", f"{season}: {len(games)} games, weeks {weeks[0]}–{weeks[-1]}", snaps=sum(sum(len(d["plays"]) for d in g["drives"]) for g in games))
        write_season(season, games)
        save_baseline(season, {"at": now(), "rows": len(rows), "columns": cols, "games": len(games), "weeks": weeks})
        done.extend(games)
    write_index(done)
    log("done", "ok", seconds=round(time.time() - T0, 1))


if __name__ == "__main__":
    args = [int(a) for a in sys.argv[1:]] or [datetime.now().year if datetime.now().month >= 9 else datetime.now().year - 1]
    try:
        main(args)
    except Fail as e:
        log("FAIL", str(e))
        sys.exit(1)
    except Exception as e:
        path = dump("crash", "no exception", repr(e), {"trace": traceback.format_exc()})
        log("FAIL", f"{e!r}. Dump: {path}")
        sys.exit(1)
