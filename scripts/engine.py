#!/usr/bin/env python3
"""ValueBet Scout - rekenmotor.

Gebruik:
  python scripts/engine.py fetch            # data ophalen (odds + teamdata), rekenen, tracker afrekenen
  python scripts/engine.py apply [--notify] # onderzoek van Claude (research.json/promos.json) verwerken, opnieuw rekenen

Geen externe libraries nodig. Sleutels via omgevingsvariabelen:
  ODDS_API_KEY, API_FOOTBALL_KEY, NTFY_TOPIC (optioneel), SITE_URL (optioneel)
Testen zonder internet: MOCK_DIR=<map met voorbeeldantwoorden>.
"""
import json
import os
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "site" / "data"
CACHE = ROOT / "cache"

# ---------------------------------------------------------------- instellingen
LEAGUES = [
    # (naam, The Odds API key, API-Football league id)
    ("Premier League", "soccer_epl", 39),
    ("LaLiga", "soccer_spain_la_liga", 140),
    ("Serie A", "soccer_italy_serie_a", 135),
    ("Bundesliga", "soccer_germany_bundesliga", 78),
    ("Ligue 1", "soccer_france_ligue_one", 61),
    ("Eredivisie", "soccer_netherlands_eredivisie", 88),
    ("Primeira Liga", "soccer_portugal_primeira_liga", 94),
    ("Jupiler Pro League", "soccer_belgium_first_div", 144),
    ("Champions League", "soccer_uefa_champs_league", 2),
    ("Europa League", "soccer_uefa_europa_league", 3),
]
UNIBET_KEYS = ["unibet_nl", "unibet_fr", "unibet_se", "unibet_it"]  # geen aparte BE-feed; NL ligt het dichtst bij
SHARP_KEYS = {"pinnacle": 3.0, "betfair_ex_eu": 2.0, "matchbook": 1.5}
DAYS_AHEAD = 7
MIN_BOOKMAKERS = 4
MIN_VALUE = 0.03          # risicobets: minstens +3% value
SAFE_PROB = 0.55          # favorieten: minstens 55% winkans
SAFE_MIN_VALUE = -0.035   # favorieten: hoogstens 3,5% onder de eerlijke odds
MIN_PROB = 0.15           # geen gokken op grote outsiders
MAX_ODDS = 6.0
TRACK_SCORE = 7.0         # risicobets vanaf deze score volgen in de Tracker
TRACK_SAFE_TOP = 5        # de 5 beste favorieten van elke dag volgen
CAP_MODEL = 3.0           # max correctie (procentpunten) uit vaste regels
CAP_RESEARCH = 4.0        # max correctie uit Claude-onderzoek
CAP_TOTAL = 6.0           # max totale correctie
TZ = timezone(timedelta(hours=1))  # weergave; de app toont lokale tijd van de gsm


def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


# ---------------------------------------------------------------- HTTP
class Http:
    def __init__(self):
        self.mock = os.environ.get("MOCK_DIR")
        self.errors = []
        self.notes = []
        self.odds_remaining = None
        self.football_calls = 0

    def get(self, url, headers=None, mock_name=None):
        if self.mock:
            p = Path(self.mock) / (mock_name or "missing")
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
            return None
        req = urllib.request.Request(url, headers=headers or {})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    rem = r.headers.get("x-requests-remaining")
                    if rem is not None:
                        self.odds_remaining = rem
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")[:300]
                self.errors.append(f"{e.code} bij {redact(url)}: {body}")
                return None
            except Exception as e:  # netwerkfout: kort wachten en opnieuw
                if attempt == 2:
                    self.errors.append(f"Netwerkfout bij {redact(url)}: {e}")
                    return None
                time.sleep(3)


def redact(url):
    parts = urllib.parse.urlsplit(url)
    q = [(k, "***" if "key" in k.lower() else v) for k, v in urllib.parse.parse_qsl(parts.query)]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(q)))


# ---------------------------------------------------------------- ophalen
def fetch_odds(http):
    key = os.environ.get("ODDS_API_KEY", "")
    if not key and not http.mock:
        http.errors.append("ODDS_API_KEY ontbreekt")
        return {}
    active = http.get(f"https://api.the-odds-api.com/v4/sports/?apiKey={key}", mock_name="sports.json") or []
    active_keys = {s.get("key") for s in active if s.get("active")}
    out = {}
    t_from = iso(now_utc())
    t_to = iso(now_utc() + timedelta(days=DAYS_AHEAD))
    for name, okey, _ in LEAGUES:
        if active and okey not in active_keys:
            continue  # competitie ligt stil (geen kosten)
        q = urllib.parse.urlencode({
            "apiKey": key, "regions": "eu", "markets": "h2h", "oddsFormat": "decimal",
            "commenceTimeFrom": t_from, "commenceTimeTo": t_to,
        })
        res = http.get(f"https://api.the-odds-api.com/v4/sports/{okey}/odds?{q}", mock_name=f"odds_{okey}.json")
        if res is not None:
            out[okey] = res
    return out


def season_for(d):
    return d.year if d.month >= 7 else d.year - 1


def fetch_football(http):
    key = os.environ.get("API_FOOTBALL_KEY", "")
    if not key and not http.mock:
        http.errors.append("API_FOOTBALL_KEY ontbreekt: geen blessures/vorm/uitslagen")
        return {}
    headers = {"x-apisports-key": key}
    base = "https://v3.football.api-sports.io"
    today = now_utc().date()
    season = int(os.environ.get("SEASON", season_for(today)))
    frm, to = (today - timedelta(days=5)).isoformat(), (today + timedelta(days=DAYS_AHEAD)).isoformat()
    out = {}
    for name, okey, lid in LEAGUES:
        entry = {}
        for kind, path in [
            ("fixtures", f"/fixtures?league={lid}&season={season}&from={frm}&to={to}"),
            ("standings", f"/standings?league={lid}&season={season}"),
            ("injuries", f"/injuries?league={lid}&season={season}"),
        ]:
            res = http.get(base + path, headers=headers, mock_name=f"af_{kind}_{lid}.json")
            http.football_calls += 1
            if res is None:
                continue
            errs = res.get("errors")
            if errs and isinstance(errs, dict) and "plan" in errs:
                # gratis plan geeft geen toegang tot het huidige seizoen: stoppen, requests sparen
                http.notes.append("API-Football (gratis plan) geeft geen toegang tot dit seizoen: "
                                  "blessures, vorm en uitslagen komen uit het ochtendonderzoek van Claude.")
                return out
            if errs:
                http.errors.append(f"API-Football {kind} {name}: {errs}")
            entry[kind] = res.get("response", [])
        out[okey] = entry
    return out


# ---------------------------------------------------------------- namen koppelen
STOP = {"fc", "cf", "afc", "sc", "ac", "as", "ss", "sv", "rc", "cd", "ud", "sl", "club", "de", "the", "kv", "krc",
        "rsc", "kaa", "royal", "rcd", "ogc", "stade", "1", "fk", "bk", "vfb", "vfl", "tsg", "sk", "if", "us", "ssc",
        "calcio", "football", "futbol", "cfc", "ca", "rb", "rsca", "kvc", "sint", "st", "olympique", "real", "sporting", "cp"}
ALIASES = {
    "internazionale": "inter", "psg": "paris saint germain", "man united": "manchester united",
    "man city": "manchester city", "spurs": "tottenham", "wolves": "wolverhampton", "bayern": "bayern munchen",
    "gladbach": "monchengladbach", "brugge": "club brugge", "union sg": "union saint gilloise",
}


def norm(name):
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    for ch in ".-'&/()":
        s = s.replace(ch, " ")
    s = ALIASES.get(s.strip(), s)
    toks = [t for t in s.split() if t not in STOP]
    return " ".join(toks) or s.strip()


def sim(a, b):
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    if na in nb or nb in na:
        return 0.95
    ta, tb = set(na.split()), set(nb.split())
    overlap = len(ta & tb) / max(1, min(len(ta), len(tb)))
    return max(SequenceMatcher(None, na, nb).ratio(), overlap * 0.9)


def match_fixture(ev, fixtures):
    t = parse_dt(ev["commence_time"])
    best, best_s = None, 0.0
    for f in fixtures:
        try:
            ft = parse_dt(f["fixture"]["date"])
        except Exception:
            continue
        if abs((ft - t).total_seconds()) > 36 * 3600:
            continue
        s = (sim(ev["home_team"], f["teams"]["home"]["name"]) + sim(ev["away_team"], f["teams"]["away"]["name"])) / 2
        if s > best_s:
            best, best_s = f, s
    return best if best_s >= 0.6 else None


# ---------------------------------------------------------------- kansen
def devig(outcomes, home, away):
    prices = {}
    for o in outcomes:
        n = o.get("name")
        if n == home:
            prices["home"] = o["price"]
        elif n == away:
            prices["away"] = o["price"]
        elif n and n.lower() == "draw":
            prices["draw"] = o["price"]
    if len(prices) != 3 or min(prices.values()) <= 1.0:
        return None, None
    inv = {k: 1 / v for k, v in prices.items()}
    tot = sum(inv.values())
    return {k: v / tot for k, v in inv.items()}, prices


def shift(p, outcome, pp):
    """Verschuif `pp` procentpunten naar `outcome`, trek het proportioneel af van de andere twee."""
    d = pp / 100
    q = dict(p)
    others = [k for k in q if k != outcome]
    rest = sum(q[k] for k in others)
    q[outcome] = q[outcome] + d
    for k in others:
        q[k] = q[k] - d * (q[k] / rest if rest else 0.5)
    q = {k: clamp(v, 0.01, 0.98) for k, v in q.items()}
    tot = sum(q.values())
    return {k: v / tot for k, v in q.items()}


def form_ppg(form):
    if not form:
        return None
    f = [c for c in form.upper() if c in "WDL"][-5:]
    if not f:
        return None
    return sum({"W": 3, "D": 1, "L": 0}[c] for c in f) / len(f)


def rec_ppg(rec):
    try:
        pl = rec["played"]
        if pl < 3:
            return None
        return (3 * rec["win"] + rec["draw"]) / pl
    except Exception:
        return None


def standings_index(standings):
    idx = {}
    for block in standings or []:
        for group in block.get("league", {}).get("standings", []):
            for row in group:
                idx[row["team"]["id"]] = row
    return idx


def model_adjustments(fixture, st_idx, injuries):
    """Kleine, begrensde correcties op de thuiswinst (procentpunten) uit vaste regels."""
    steps = []
    if not fixture:
        return 0.0, steps, False
    hid, aid = fixture["teams"]["home"]["id"], fixture["teams"]["away"]["id"]
    hn, an = fixture["teams"]["home"]["name"], fixture["teams"]["away"]["name"]
    total = 0.0
    hs, as_ = st_idx.get(hid), st_idx.get(aid)
    if hs and as_:
        fh, fa = form_ppg(hs.get("form")), form_ppg(as_.get("form"))
        if fh is not None and fa is not None:
            a = clamp((fh - fa) * 0.5, -1.5, 1.5)
            if abs(a) >= 0.1:
                steps.append({"label": "Vorm (laatste 5)", "note": f"{hn} {hs.get('form','')[-5:]} tegen {an} {as_.get('form','')[-5:]}", "pp": round(a, 1)})
                total += a
        hh, ha = rec_ppg(hs.get("home", {})), rec_ppg(hs.get("all", {}))
        ah, aa = rec_ppg(as_.get("away", {})), rec_ppg(as_.get("all", {}))
        if None not in (hh, ha, ah, aa):
            spec = (hh - ha) - (ah - aa)
            a = clamp(spec * 0.75, -1.0, 1.0)
            if abs(a) >= 0.1:
                steps.append({"label": "Thuis/uit-record", "note": f"{hn} thuis {hh:.1f} ptn/match, {an} uit {ah:.1f}".replace(".", ","), "pp": round(a, 1)})
                total += a
    fid = fixture["fixture"]["id"]
    miss_h = [i["player"]["name"] for i in injuries if i.get("fixture", {}).get("id") == fid and i["team"]["id"] == hid]
    miss_a = [i["player"]["name"] for i in injuries if i.get("fixture", {}).get("id") == fid and i["team"]["id"] == aid]
    if miss_h or miss_a:
        a = clamp((len(miss_a) - len(miss_h)) * 0.4, -2.0, 2.0)
        note = f"{hn}: {len(miss_h)} afwezig, {an}: {len(miss_a)} afwezig"
        steps.append({"label": "Blessures/schorsingen", "note": note, "pp": round(a, 1),
                      "players": {"home": miss_h[:8], "away": miss_a[:8]}})
        total += a
    return clamp(total, -CAP_MODEL, CAP_MODEL), steps, True


# ---------------------------------------------------------------- hoofdberekening
def build(odds_raw, fb_raw, research, promos):
    matches, bets = [], []
    adj_research = research.get("adjustments", {}) if research else {}
    notes_research = research.get("match_notes", {}) if research else {}
    for name, okey, _ in LEAGUES:
        events = odds_raw.get(okey) or []
        fb = fb_raw.get(okey, {})
        fixtures = fb.get("fixtures", [])
        st_idx = standings_index(fb.get("standings"))
        injuries = fb.get("injuries", [])
        for ev in events:
            home, away = ev["home_team"], ev["away_team"]
            books, unibet = [], None
            for bm in ev.get("bookmakers", []):
                mk = next((m for m in bm.get("markets", []) if m["key"] == "h2h"), None)
                if not mk:
                    continue
                p, prices = devig(mk["outcomes"], home, away)
                if not p:
                    continue
                if bm["key"] in UNIBET_KEYS:
                    if unibet is None or UNIBET_KEYS.index(bm["key"]) < UNIBET_KEYS.index(unibet["key"]):
                        unibet = {"key": bm["key"], "title": bm.get("title", bm["key"]), "prices": prices, "p": p}
                    continue
                books.append((bm["key"], p, prices))
            if len(books) < MIN_BOOKMAKERS or not unibet:
                continue
            wsum = sum(SHARP_KEYS.get(k, 1.0) for k, _, _ in books)
            pm = {o: sum(SHARP_KEYS.get(k, 1.0) * p[o] for k, p, _ in books) / wsum for o in ("home", "draw", "away")}
            has_sharp = any(k in SHARP_KEYS for k, _, _ in books)
            best = {o: max(pr[o] for _, _, pr in books) for o in ("home", "draw", "away")}

            fx = match_fixture(ev, fixtures) if fixtures else None
            madj, msteps, matched = model_adjustments(fx, st_idx, injuries)
            p_model = shift(pm, "home", madj) if madj else dict(pm)

            names = {"home": home, "draw": "Gelijkspel", "away": away}
            match_id = ev["id"]
            m = {
                "id": match_id, "league": name, "kickoff": ev["commence_time"],
                "home": home, "away": away, "n_books": len(books), "sharp": has_sharp,
                "unibet_source": unibet["title"], "unibet": unibet["prices"],
                "p_market": {k: round(v, 4) for k, v in pm.items()},
                "fixture_id": fx["fixture"]["id"] if fx else None,
                "note": notes_research.get(match_id, ""),
            }
            matches.append(m)

            for o in ("home", "draw", "away"):
                odds = unibet["prices"][o]
                r = adj_research.get(f"{match_id}|{o}")
                rpp = clamp(float(r.get("pp", 0)), -CAP_RESEARCH, CAP_RESEARCH) if r else 0.0
                p_fin = shift(p_model, o, rpp) if rpp else dict(p_model)
                # totale correctie begrenzen
                diff = (p_fin[o] - pm[o]) * 100
                if abs(diff) > CAP_TOTAL:
                    p_fin = shift(pm, o, CAP_TOTAL if diff > 0 else -CAP_TOTAL)
                p = p_fin[o]
                ev_val = p * odds - 1
                ev_mkt = pm[o] * odds - 1
                if p >= SAFE_PROB and ev_val >= SAFE_MIN_VALUE and odds >= 1.15:
                    cat = "safe"
                elif ev_val >= MIN_VALUE and p >= MIN_PROB and odds <= MAX_ODDS and p < SAFE_PROB:
                    cat = "risky"
                else:
                    continue
                steps = [
                    {"label": f"Unibet-odds {fmt(odds)}", "note": f"Bron: {unibet['title']} (zelfde platform als unibet.be)", "val": pct(1 / odds)},
                    {"label": "Marktconsensus", "note": f"{len(books)} bookmakers, marge eruit" + (", incl. Pinnacle" if has_sharp else ""), "val": pct(pm[o])},
                ]
                for s in msteps:
                    sign = s["pp"] if o == "home" else (-s["pp"] * (pm[o] / (1 - pm["home"])) if pm["home"] < 1 else 0)
                    if abs(sign) >= 0.1:
                        steps.append({"label": s["label"], "note": s["note"], "val": ppfmt(sign)})
                if r:
                    steps.append({"label": "Onderzoek Claude", "note": r.get("note", ""), "val": ppfmt(rpp),
                                  "sources": r.get("sources", [])[:4]})
                steps.append({"label": "Eigen kans", "note": "Na alle correcties (begrensd)", "val": pct(p), "final": True})

                rel_s = clamp(len(books) / 15 * 6 + (2 if has_sharp else 0) + (2 if matched or r else 0), 0, 10)
                news_s = clamp(float(r.get("confidence", 6)), 0, 10) if r else (6.0 if matched else 4.0)
                if cat == "safe":
                    # vooral: hoe groot is de winkans, en betaalt Unibet (bijna) eerlijk?
                    win_s = clamp((p - 0.5) / 0.35 * 10, 0, 10)
                    value_s = clamp((ev_val + 0.04) / 0.08 * 10, 0, 10)
                    score = 0.45 * win_s + 0.3 * value_s + 0.15 * rel_s + 0.1 * news_s
                    parts = [["Winkans", round(win_s, 1)], ["Value t.o.v. eerlijke odds", round(value_s, 1)],
                             ["Betrouwbaarheid analyse", round(rel_s, 1)], ["Nieuws bevestigd", round(news_s, 1)]]
                else:
                    value_s = clamp(ev_val / 0.12 * 10, 0, 10)
                    lag_s = clamp(ev_mkt / 0.06 * 10, 0, 10)
                    score = 0.4 * value_s + 0.25 * rel_s + 0.2 * lag_s + 0.15 * news_s
                    parts = [["X-factor (value)", round(value_s, 1)], ["Betrouwbaarheid analyse", round(rel_s, 1)],
                             ["Odds-achterstand Unibet", round(lag_s, 1)], ["Nieuws bevestigd", round(news_s, 1)]]
                bets.append({
                    "key": f"{match_id}|{o}", "match_id": match_id, "league": name, "kickoff": ev["commence_time"],
                    "match": f"{home} – {away}", "outcome": o, "pick": names[o], "odds": odds,
                    "best_odds": best[o], "p": round(p, 4), "p_market": round(pm[o], 4),
                    "ev": round(ev_val, 4), "ev_market": round(ev_mkt, 4), "score": round(score, 1),
                    "category": cat, "parts": parts, "steps": steps, "promo": None,
                    "why": explain(cat, names[o], o, home, away, p, odds, ev_val, len(books), has_sharp, r,
                                   notes_research.get(match_id)),
                })
    bets.sort(key=lambda b: (-b["score"], -b["ev"]))
    # niet meer dan 1 tip per wedstrijd per lijst
    seen, top = set(), []
    for b in bets:
        k = (b["match_id"], b["category"])
        if k in seen:
            continue
        seen.add(k)
        top.append(b)
    apply_odds_boosts(top, matches, promos)
    top.sort(key=lambda b: (-b["score"], -b["ev"]))
    return matches, top


def dec(x):
    return f"{x:.1f}".replace(".", ",")


def euro(x):
    return "€" + f"{x:.2f}".replace(".", ",")


def explain(cat, pick, o, home, away, p, odds, ev, n_books, sharp, research, match_note):
    """Uitleg in gewone taal: waarom deze bet, wat kan je verwachten."""
    fair = 1 / p
    wins = "het gelijk eindigt" if o == "draw" else f"{pick} wint"
    src = f"{n_books} bookmakers" + (" (waaronder Pinnacle, de scherpste)" if sharp else "")
    out = []
    if cat == "safe":
        out.append(f"{pick} is de favoriet. Volgens {src} is de kans dat {wins} ongeveer {round(p * 100)}%: "
                   f"zo'n {round(p * 10)} op 10 keer.")
        if ev >= 0:
            out.append(f"Unibet betaalt {fmt(odds)}, terwijl een eerlijke odds voor die kans {fmt(fair)} is. "
                       f"Unibet betaalt dus iets meer dan eerlijk: dat is value, zeldzaam bij een favoriet.")
        else:
            out.append(f"Unibet betaalt {fmt(odds)}; een eerlijke odds voor die kans is {fmt(fair)}. Het verschil is "
                       f"de marge van de bookmaker: op lange termijn kost dit ongeveer {dec(abs(ev) * 100)}% van je inzet. "
                       f"Je wint deze bet vaak, maar niet gratis. Dit is een van de favorieten met de kleinste marge.")
        out.append(f"Met €10 krijg je {euro(10 * odds)} terug als het lukt (winst {euro(10 * (odds - 1))}). "
                   f"In ongeveer {round((1 - p) * 100)}% van de gevallen verlies je je inzet.")
    else:
        out.append(f"Dit is een risicobet. De kans dat {wins} is volgens {src} maar ongeveer {round(p * 100)}%: "
                   f"zo'n 1 op {max(2, round(1 / p))} keer.")
        out.append(f"Unibet betaalt wel {fmt(odds)}, terwijl een eerlijke odds {fmt(fair)} zou zijn. Daardoor is de "
                   f"x-factor +{dec(ev * 100)}%: wie zulke bets vaak speelt, wint op lange termijn gemiddeld zoveel "
                   f"bovenop zijn inzet terug.")
        out.append(f"Je verliest de meeste van deze bets. Met €10 krijg je {euro(10 * odds)} terug als het lukt. "
                   f"Speel ze alleen met een klein bedrag en als reeks, nooit om verlies goed te maken.")
    if research and research.get("note"):
        out.append("Nieuws: " + research["note"])
    elif match_note:
        out.append("Nieuws: " + match_note)
    else:
        out.append("Nog geen ploegnieuws verwerkt: dat gebeurt elke ochtend om 7u35.")
    return out


def apply_odds_boosts(top, matches, promos):
    for pr in (promos or {}).get("promos", []):
        if pr.get("type") != "odds_boost" or not pr.get("boosted_odds"):
            continue
        txt = pr.get("match", "")
        best_m, best_s = None, 0
        for m in matches:
            s = sim(txt, f"{m['home']} {m['away']}")
            if s > best_s:
                best_m, best_s = m, s
        if not best_m or best_s < 0.55:
            continue
        pick = pr.get("pick", "").lower()
        o = "draw" if "gelijk" in pick or "draw" in pick else ("away" if sim(pick, best_m["away"]) > sim(pick, best_m["home"]) else "home")
        p = best_m["p_market"][o]
        odds = float(pr["boosted_odds"])
        ev_val = p * odds - 1
        if ev_val <= 0:
            continue
        names = {"home": best_m["home"], "draw": "Gelijkspel", "away": best_m["away"]}
        top.append({
            "key": f"{best_m['id']}|{o}|boost", "match_id": best_m["id"], "league": best_m["league"],
            "kickoff": best_m["kickoff"], "match": f"{best_m['home']} – {best_m['away']}", "outcome": o,
            "pick": names[o], "odds": odds, "best_odds": odds, "p": round(p, 4), "p_market": round(p, 4),
            "ev": round(ev_val, 4), "ev_market": round(ev_val, 4), "score": round(clamp(5 + ev_val * 40, 0, 10), 1),
            "parts": [["Value", round(clamp(ev_val / 0.12 * 10, 0, 10), 1)]],
            "steps": [{"label": f"Odds-boost {fmt(odds)}", "note": pr.get("name", "Unibet-promo"), "val": pct(1 / odds)},
                      {"label": "Marktconsensus", "note": "Kans volgens de markt", "val": pct(p), "final": True}],
            "promo": pr.get("name", "Odds-boost"),
            "category": "safe" if p >= SAFE_PROB else "risky",
            "why": [f"Promo van Unibet: {pr.get('name', 'odds-boost')}. De verhoogde odds {fmt(odds)} liggen boven de "
                    f"eerlijke odds {fmt(1 / p)}, dus deze promo heeft value (+{dec(ev_val * 100)}%)."]
                   + ([pr["note"]] if pr.get("note") else []),
        })


def build_combis(top, promos):
    # combi's uit favorieten: grote kans per selectie; een combi-boost kan de marge goedmaken
    legs = [b for b in top if b["category"] == "safe" and not b.get("promo")][:7]
    boost = None
    for pr in (promos or {}).get("promos", []):
        if pr.get("type") == "combi_boost" and pr.get("value"):
            boost = pr
    out = []
    for n in (2, 3):
        for combo in combinations(legs, n):
            if len({c["match_id"] for c in combo}) < n:
                continue
            odds, p = 1.0, 1.0
            for c in combo:
                odds *= c["odds"]
                p *= c["p"]
            eff = odds
            if boost and n >= int(boost.get("min_legs", 2)):
                eff = 1 + (odds - 1) * (1 + float(boost["value"]))
            out.append({
                "legs": [{"match": c["match"], "pick": c["pick"], "odds": c["odds"], "key": c["key"]} for c in combo],
                "odds": round(odds, 2), "odds_boosted": round(eff, 2) if eff != odds else None,
                "boost_name": boost.get("name") if boost and eff != odds else None,
                "p": round(p, 4), "ev": round(p * eff - 1, 4),
            })
    out = [c for c in out if c["p"] >= 0.30]
    out.sort(key=lambda c: (-c["ev"], -c["p"]))
    return out[:4]


# ---------------------------------------------------------------- tracker
def update_history(history, top, matches):
    hist = history.setdefault("bets", [])
    known = {h["key"] for h in hist}
    by_match = {m["id"]: m for m in matches}
    now = now_utc()
    by_key = {h["key"]: h for h in hist}
    safe_top = {b["key"] for b in [x for x in top if x["category"] == "safe"][:TRACK_SAFE_TOP]}
    for b in top:
        if parse_dt(b["kickoff"]) <= now:
            continue
        tracked = b["key"] in safe_top or (b["category"] == "risky" and b["score"] >= TRACK_SCORE)
        if not tracked and b["score"] < 5.0:
            continue
        h = by_key.get(b["key"])
        if h is None:
            hist.append({
                "key": b["key"], "match_id": b["match_id"], "league": b["league"], "kickoff": b["kickoff"],
                "match": b["match"], "pick": b["pick"], "outcome": b["outcome"], "odds": b["odds"],
                "p": b["p"], "ev": b["ev"], "score": b["score"], "tipped_at": iso(now), "status": "open",
                "tracked": tracked, "category": b["category"], "fixture_id": by_match.get(b["match_id"], {}).get("fixture_id"),
                "promo": b.get("promo"),
            })
        elif h["status"] == "open" and tracked and not h.get("tracked"):
            # pas vandaag sterk genoeg: volgen tegen de odds van nu
            h.update({"odds": b["odds"], "p": b["p"], "ev": b["ev"], "score": b["score"],
                      "tipped_at": iso(now), "tracked": True})
    # laatste marktprijs voor aftrap bijhouden (benadering van de slotkoers)
    for h in hist:
        m = by_match.get(h["match_id"])
        if h["status"] == "open" and m and parse_dt(m["kickoff"]) > now:
            fair = 1 / m["p_market"][h["outcome"]]
            h["last_fair_odds"] = round(fair, 3)
            h["clv"] = round(h["odds"] / fair - 1, 4)
            if not h.get("fixture_id"):
                h["fixture_id"] = m.get("fixture_id")


def settle(history, fb_raw, research):
    fixtures = {}
    for entry in fb_raw.values():
        for f in entry.get("fixtures", []):
            fixtures[f["fixture"]["id"]] = f
    manual = (research or {}).get("results", {})
    now = now_utc()
    for h in history.get("bets", []):
        if h["status"] != "open":
            continue
        gh = ga = None
        f = fixtures.get(h.get("fixture_id"))
        if f and f["fixture"]["status"]["short"] in ("FT", "AET", "PEN"):
            ft = f.get("score", {}).get("fulltime", {})
            gh, ga = ft.get("home"), ft.get("away")
        elif h["match_id"] in manual:
            gh, ga = manual[h["match_id"]].get("home"), manual[h["match_id"]].get("away")
        elif f and f["fixture"]["status"]["short"] in ("PST", "CANC", "ABD"):
            h["status"] = "void"
            continue
        if gh is None or ga is None:
            if parse_dt(h["kickoff"]) < now - timedelta(days=6):
                h["status"] = "void"
                h["result"] = "geen uitslag gevonden"
            continue
        res = "home" if gh > ga else ("away" if ga > gh else "draw")
        h["status"] = "won" if res == h["outcome"] else "lost"
        h["result"] = f"{gh}-{ga}"
        h["settled_at"] = iso(now)


# ---------------------------------------------------------------- uitvoer
def fmt(x):
    return f"{x:.2f}".replace(".", ",")


def pct(x):
    return f"{x * 100:.1f}%".replace(".", ",")


def ppfmt(x):
    return ("+" if x >= 0 else "−") + f"{abs(x):.1f}".replace(".", ",")


# ---------------------------------------------------------------- weekportefeuille van Claude
PICKS_PER_WEEK = 10
PICK_STAKE = 10.0
MAX_RISKY_PICKS = 3       # hoogstens 3 risicobets per week


def local_now():
    return now_utc().astimezone(TZ)


def week_key(dt):
    y, w, _ = dt.astimezone(TZ).isocalendar()
    return f"{y}-W{w:02d}"


def week_label(key):
    y, w = key.split("-W")
    mon = datetime.fromisocalendar(int(y), int(w), 1)
    sun = mon + timedelta(days=6)
    return f"{mon.day}/{mon.month} – {sun.day}/{sun.month}"


def add_picks(history, top, research, autofill, matches=()):
    """Leg de keuzes van Claude vast (research.json -> picks) en vul eventueel aan tot 10 per week."""
    hist = history.setdefault("bets", [])
    by_key = {h["key"]: h for h in hist}
    bets = {b["key"]: b for b in top}
    now = now_utc()
    wk = week_key(now)
    in_week = [h for h in hist if h.get("claude_pick") and h.get("pick_week") == wk]
    n_risky = sum(1 for h in in_week if h.get("category") == "risky")
    added = []

    def take(b, reason, auto=False):
        nonlocal n_risky
        if len(in_week) >= PICKS_PER_WEEK or parse_dt(b["kickoff"]) <= now:
            return False
        if b["category"] == "risky" and n_risky >= MAX_RISKY_PICKS:
            return False
        h = by_key.get(b["key"])
        if h and h.get("claude_pick"):
            return False
        if h is None:
            h = {"key": b["key"], "match_id": b["match_id"], "league": b["league"], "kickoff": b["kickoff"],
                 "match": b["match"], "pick": b["pick"], "outcome": b["outcome"], "odds": b["odds"],
                 "p": b["p"], "ev": b["ev"], "score": b["score"], "tipped_at": iso(now), "status": "open",
                 "tracked": True, "category": b["category"], "fixture_id": None, "promo": b.get("promo")}
            hist.append(h)
            by_key[b["key"]] = h
        h.update({"claude_pick": True, "pick_week": wk, "pick_odds": b["odds"], "pick_stake": PICK_STAKE,
                  "pick_p": b["p"], "pick_ev": b["ev"], "pick_at": iso(now), "pick_reason": reason,
                  "pick_auto": auto, "category": b["category"]})
        in_week.append(h)
        if b["category"] == "risky":
            n_risky += 1
        added.append(h)
        return True

    by_match = {m["id"]: m for m in matches}
    for pk in (research or {}).get("picks", []):
        b = bets.get(pk.get("key"))
        if b is None and "|" in pk.get("key", ""):
            # ook een uitkomst die niet in de lijst staat mag gekozen worden (bv. na eigen onderzoek)
            mid, o = pk["key"].split("|", 1)
            m = by_match.get(mid)
            if m and o in ("home", "draw", "away"):
                p = m["p_market"][o]
                b = {"key": pk["key"], "match_id": mid, "league": m["league"], "kickoff": m["kickoff"],
                     "match": f"{m['home']} – {m['away']}", "outcome": o,
                     "pick": {"home": m["home"], "draw": "Gelijkspel", "away": m["away"]}[o],
                     "odds": m["unibet"][o], "p": p, "ev": round(p * m["unibet"][o] - 1, 4), "score": 0,
                     "category": "safe" if p >= SAFE_PROB else "risky"}
        if b:
            take(b, pk.get("reason", "").strip() or "Gekozen na het ochtendonderzoek.")

    # zaterdag en zondag: aanvullen tot 10 met de sterkste bets die nog dit weekend gespeeld worden
    if autofill and local_now().weekday() >= 5 and len(in_week) < PICKS_PER_WEEK:
        end = (local_now().replace(hour=23, minute=59) + timedelta(days=6 - local_now().weekday())).astimezone(timezone.utc)
        cands = [b for b in top if parse_dt(b["kickoff"]) <= end and not b.get("promo")]
        cands.sort(key=lambda b: (b["ev"] < 0, -(b["ev"] if b["category"] == "risky" else b["score"] / 100 + b["ev"])))
        for b in cands:
            if len(in_week) >= PICKS_PER_WEEK:
                break
            why = (f"Automatisch aangevuld: {round(b['p'] * 100)}% winkans, "
                   f"value {('+' if b['ev'] >= 0 else '−')}{dec(abs(b['ev']) * 100)}% bij odds {fmt(b['odds'])}.")
            take(b, why, auto=True)
    return added


def weekly_summary(history):
    weeks = {}
    for h in history.get("bets", []):
        if not h.get("claude_pick"):
            continue
        w = weeks.setdefault(h["pick_week"], {"week": h["pick_week"], "label": week_label(h["pick_week"]),
                                              "n": 0, "won": 0, "lost": 0, "open": 0, "void": 0,
                                              "staked": 0.0, "returned": 0.0, "pnl": 0.0})
        st = h.get("pick_stake", PICK_STAKE)
        w["n"] += 1
        if h["status"] == "won":
            w["won"] += 1; w["staked"] += st; w["returned"] += st * h["pick_odds"]; w["pnl"] += st * (h["pick_odds"] - 1)
        elif h["status"] == "lost":
            w["lost"] += 1; w["staked"] += st; w["pnl"] -= st
        elif h["status"] == "void":
            w["void"] += 1
        else:
            w["open"] += 1
    out = sorted(weeks.values(), key=lambda w: w["week"], reverse=True)
    for w in out:
        for k in ("staked", "returned", "pnl"):
            w[k] = round(w[k], 2)
    total = {"staked": round(sum(w["staked"] for w in out), 2), "pnl": round(sum(w["pnl"] for w in out), 2),
             "weeks": len(out), "won": sum(w["won"] for w in out),
             "settled": sum(w["won"] + w["lost"] for w in out),
             "weeks_positive": sum(1 for w in out if w["pnl"] > 0 and w["open"] == 0)}
    total["roi"] = round(total["pnl"] / total["staked"], 4) if total["staked"] else 0.0
    return {"weeks": out, "total": total, "per_week": PICKS_PER_WEEK, "stake": PICK_STAKE}


def notify(top, research, errors, new_picks=(), portfolio=None):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return
    lines = []
    safe = [b for b in top if b["category"] == "safe"][:3]
    risky = [b for b in top if b["category"] == "risky"][:1]
    for b in safe:
        lines.append(f"{round(b['p']*100)}% · {b['pick']} @ {fmt(b['odds'])} ({b['match']})")
    for b in risky:
        lines.append(f"🔥 {b['pick']} @ {fmt(b['odds'])} ({b['match']}) x-factor {b['ev']*100:+.0f}%")
    if new_picks:
        lines.append(f"Claude zet vandaag in ({len(new_picks)}× €{PICK_STAKE:.0f}):")
        for h in new_picks:
            lines.append(f"✓ {h['pick']} @ {fmt(h['pick_odds'])} ({h['match']})")
    if portfolio and local_now().weekday() == 0:
        done = [w for w in portfolio["weeks"] if w["week"] != week_key(now_utc())]
        if done:
            w = done[0]
            lines.insert(0, f"Vorige week ({w['label']}): {w['won']}/{w['won'] + w['lost']} raak, "
                            f"{'+' if w['pnl'] >= 0 else '−'}€{abs(w['pnl']):.2f}".replace(".", ","))
    for a in (research or {}).get("alerts", [])[:2]:
        lines.append(f"! {a.get('title','')}")
    if not lines:
        lines = ["Vandaag geen bets boven de drempel."]
    if errors:
        lines.append(f"({len(errors)} datafout(en), zie Meldingen)")
    body = "\n".join(lines).encode("utf-8")
    req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=body, method="POST")
    req.add_header("Title", "ValueBet Scout: bets van vandaag")
    req.add_header("Tags", "soccer")
    if os.environ.get("SITE_URL"):
        req.add_header("Click", os.environ["SITE_URL"])
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as e:
        print("ntfy mislukt:", e)


def shortlist(top):
    safe = [b for b in top if b["category"] == "safe"]
    risky = [b for b in top if b["category"] == "risky"]
    keep = safe[:12] + risky[:8]
    keep += [b for b in top if b.get("claude_pick") and b not in keep]
    return keep


def run(mode, do_notify=False):
    http = Http()
    research = load(DATA / "research.json", {})
    promos = load(DATA / "promos.json", {"promos": []})
    history = load(DATA / "history.json", {"bets": []})
    meta = load(CACHE / "meta.json", {})

    if mode == "fetch":
        odds_raw = fetch_odds(http)
        fb_raw = fetch_football(http)
        save(CACHE / "odds.json", odds_raw)
        save(CACHE / "football.json", fb_raw)
        meta = {"fetched_at": iso(now_utc()), "odds_remaining": http.odds_remaining,
                "football_calls": http.football_calls, "errors": http.errors, "notes": http.notes}
        save(CACHE / "meta.json", meta)
    else:
        odds_raw = load(CACHE / "odds.json", {})
        fb_raw = load(CACHE / "football.json", {})

    # onderzoek van een vorige dag niet meer gebruiken
    today = now_utc().astimezone(TZ).date().isoformat()
    if research.get("date") and research["date"] != today:
        research = {"results": research.get("results", {})}
    if promos.get("date") and promos["date"] != today:
        promos = {"promos": []}

    matches, top = build(odds_raw, fb_raw, research, promos)
    combis = build_combis(top, promos)
    settle(history, fb_raw, research)
    update_history(history, top, matches)
    new_picks = add_picks(history, top, research, autofill=do_notify, matches=matches)
    portfolio = weekly_summary(history)
    picked = {h["key"]: h for h in history["bets"] if h.get("claude_pick") and h["status"] == "open"}
    for b in top:
        if b["key"] in picked:
            b["claude_pick"] = True
            b["pick_reason"] = picked[b["key"]].get("pick_reason", "")

    latest = {
        "generated_at": iso(now_utc()), "fetched_at": meta.get("fetched_at"),
        "research_date": research.get("date"), "odds_remaining": meta.get("odds_remaining"),
        "errors": meta.get("errors", []), "notes": meta.get("notes", []), "n_matches": len(matches),
        "bets": shortlist(top), "combis": combis, "promos": promos.get("promos", []),
        "alerts": research.get("alerts", []), "summary": research.get("summary", ""), "portfolio": portfolio,
        "settings": {"min_value": MIN_VALUE, "track_score": TRACK_SCORE, "safe_prob": SAFE_PROB},
    }
    save(DATA / "latest.json", latest)
    save(DATA / "history.json", history)
    # compacte lijst van wedstrijden voor het onderzoek van Claude
    save(CACHE / "matches.json", [
        {"id": m["id"], "league": m["league"], "kickoff": m["kickoff"], "home": m["home"], "away": m["away"],
         "unibet": m["unibet"], "p_market": m["p_market"]} for m in matches])
    if do_notify:
        today_picks = [h for h in history["bets"] if h.get("claude_pick") and h.get("pick_at")
                       and parse_dt(h["pick_at"]).astimezone(TZ).date() == local_now().date()]
        notify(top, research, meta.get("errors", []), today_picks, portfolio)
    print(f"{len(matches)} wedstrijden, {len(top)} value bets, {len(combis)} combi's, fouten: {len(meta.get('errors', []))}")
    for e in meta.get("errors", []):
        print("  -", e)


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("fetch", "apply"):
        print(__doc__)
        sys.exit(1)
    run(sys.argv[1], "--notify" in sys.argv)
