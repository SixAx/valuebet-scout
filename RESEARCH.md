# Ochtendonderzoek (voor de geplande Claude-taak)

Elke ochtend rond 8u voegt Claude nieuws, promo's en uitslagen toe. De odds staan er dan al: die haalt
GitHub Actions om ±06:15 op. Claude wijzigt **alleen** `site/data/research.json` en `site/data/promos.json`.
De push van die bestanden start de workflow `research.yml`, die alles herberekent en de pushmelding stuurt.

## Werkwijze

Let op: het gratis plan van API-Football geeft geen toegang tot het huidige seizoen. Blessures, vorm én
uitslagen komen dus volledig uit dit onderzoek. Kijk bij elke bet naar `category`: `safe` = favoriet (grote
winkans), `risky` = outsider met hoge x-factor. Zoek vooral nieuws dat een favoriet kan doen struikelen of
dat een favoriet nog sterker maakt.

1. `git pull`. Lees `site/data/latest.json` (huidige bets), `cache/matches.json` (alle wedstrijden met
   Unibet-odds en marktkans) en `site/data/history.json` (gevolgde bets).
2. Kies maximaal 15 wedstrijden: alle wedstrijden uit `latest.json` → `bets`, aangevuld met topwedstrijden
   uit `cache/matches.json` van de komende 3 dagen.
3. Zoek per wedstrijd het **recente** ploegnieuws op het web: blessures, schorsingen, verwachte opstelling,
   coachwissel, rotatie voor een Europese match. Goede bronnen: officiële clubsites, Sporza, HLN, Het Nieuwsblad,
   BBC Sport, L'Équipe, Gazzetta, Marca/AS, Kicker, Voetbal International, Record/A Bola.
   Neem alleen op wat je in een bron vond. Verzin nooit nieuws.
4. Bepaal per uitkomst een correctie in procentpunten (`pp`, tussen −4 en +4). Wees zuinig: de markt verwerkt
   bekend nieuws snel. Een correctie is alleen terecht als het nieuws vers is (minder dan ±24 uur) en over een
   basisspeler gaat. Geef `confidence` 0–10 (hoe zeker het nieuws en het effect is). Twijfel = geen correctie.
5. Zoek de Unibet België-promo's van vandaag voor voetbal (combi-boost, odds-boost, geld terug). Neem alleen
   promo's op die je in een bron bevestigd ziet.
6. Zoek voor bets in `history.json` met `"status": "open"` waarvan de aftrap voorbij is de eindstand na 90 minuten
   (zonder verlengingen) op en zet die in `results`.
7. Maak maximaal 5 `alerts`: belangrijk nieuws waarbij Unibet de odds nog niet lijkt te hebben aangepast, of een
   promo met duidelijke value.
8. Schrijf beide bestanden. Controleer met `python3 scripts/engine.py apply` dat er geen fout is. Zet daarna
   `site/data/latest.json`, `site/data/history.json` en `cache/` terug met `git checkout -- site/data/latest.json
   site/data/history.json cache`, en commit en push alleen de twee bestanden.

## research.json

```json
{
  "date": "2026-10-29",
  "summary": "2–3 zinnen in het Nederlands over de dag.",
  "adjustments": {
    "<match_id>|home": {"pp": 2.0, "confidence": 8, "note": "Topschutter Anderlecht geschorst (rode kaart zondag).",
                        "sources": ["https://..."]}
  },
  "match_notes": {"<match_id>": "Korte notitie"},
  "alerts": [
    {"tag": "BLESSURE", "time": "07:52", "title": "Kort, concreet", "body": "Wat er is en welke bet het raakt.",
     "url": "https://..."}
  ],
  "results": {"<match_id>": {"home": 2, "away": 1}}
}
```

- `date` = vandaag in Belgische tijd (YYYY-MM-DD). Onderzoek van een andere dag wordt genegeerd.
- Uitkomst-sleutels: `home`, `draw` of `away`. `match_id` komt uit `cache/matches.json` (`id`).
- Tags: `BLESSURE`, `SCHORSING`, `ODDS-ACHTERSTAND`, `PROMO`, `NIEUWS`.
- `results` blijft staan tot de bet afgerekend is; oude uitslagen mag je laten staan.

## promos.json

```json
{
  "date": "2026-10-29",
  "promos": [
    {"name": "Combi-boost", "type": "combi_boost", "value": 0.10, "min_legs": 3,
     "note": "+10% op de winst vanaf 3 selecties", "url": "https://..."},
    {"name": "Odds-boost Club Brugge", "type": "odds_boost", "match": "Club Brugge Anderlecht",
     "pick": "Club Brugge", "boosted_odds": 2.30, "note": "Normaal 1,95", "url": "https://..."},
    {"name": "Geld terug", "type": "other", "note": "Inzet terug als free bet als 1 selectie mist"}
  ]
}
```

`value` bij een combi-boost = extra fractie op de winst (0.10 = +10%).
