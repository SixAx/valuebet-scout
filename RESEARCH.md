# Ochtendonderzoek (voor de geplande Claude-taak)

Elke ochtend rond 8u voegt Claude nieuws, promo's en uitslagen toe. De odds staan er dan al: die haalt
GitHub Actions om ±06:15 op. Claude wijzigt **alleen** `site/data/research.json` en `site/data/promos.json`.
De push van die bestanden start de workflow `research.yml`, die alles herberekent en de pushmelding stuurt.

## Werkwijze

Let op: ClubElo (onafhankelijk Elo-model) weegt 15% mee in de kans; je ziet het als `p_elo` per bet. Het gratis plan van API-Football geeft geen toegang tot het huidige seizoen. Blessures, vorm én
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
3b. **Verplichte check vóór elke pick (ook elke selectie in een combi), voor BEIDE ploegen** (op vraag van de
   gebruiker; API-Football levert geen vorm, dus dit komt volledig uit je webonderzoek):
   - **Vorm:** uitslagen van de laatste 5 wedstrijden (alle competities), apart thuis-/uitvorm als die afwijkt.
     Bronnen: Flashscore, Soccerway, ESPN, Sofascore, BBC Sport, Sporza.
   - **Blessures en schorsingen** van de favoriet én van de tegenstander (wie valt weg, is het een basisspeler).
   - **Context:** rotatie door Europese match of beker, coachwissel, motivatie (titelstrijd, degradatie, niets meer
     te spelen).
   - Schrijf dit kort in `match_notes` (bv. "Vorm PSG WWWDW, tegenstander LLDLW; Hakimi geschorst bij PSG; bij Lens
     ontbreekt de vaste doelman."), en verwijs ernaar in de `reason` van de pick.
   - **Geen pick** als de favoriet in slechte vorm is (2 of meer nederlagen in de laatste 5), als er bij de
     favoriet meerdere basisspelers ontbreken, of als je de vorm/het nieuws niet in een bron kon vinden.
     Sterke vorm van de favoriet plus zwakke vorm of afwezigen bij de tegenstander is een plus, maar geen reden
     om een bet met duidelijk te lage odds toch te nemen: de markt houdt al grotendeels rekening met vorm.
4. Bepaal per uitkomst een correctie in procentpunten (`pp`, tussen −4 en +4). Wees zuinig: de markt verwerkt
   bekend nieuws snel. Een correctie is alleen terecht als het nieuws vers is (minder dan ±24 uur) en over een
   basisspeler gaat. Geef `confidence` 0–10 (hoe zeker het nieuws en het effect is). Twijfel = geen correctie.
5. Zoek de Unibet België-promo's van vandaag voor voetbal (combi-boost, odds-boost, geld terug). Neem alleen
   promo's op die je in een bron bevestigd ziet.
6. Zoek voor bets in `history.json` met `"status": "open"` waarvan de aftrap voorbij is de eindstand na 90 minuten
   (zonder verlengingen) op en zet die in `results`.
6b. **Tipsters en fora.** Zoek voor de belangrijkste wedstrijden (de favorieten bovenaan `latest.json` en je
   eigen keuzes) wat tipsters en fora aanraden: Reddit (r/SoccerBetting, r/sportsbook, r/BelgianFootball),
   Sporting Life, Covers, voetbalwedden.net, bettingexpert. Zet per wedstrijd hoogstens 2 tips in `community`:
   `{"<match_id>": [{"outcome": "home", "note": "Wat ze aanraden en waarom, 1 zin", "source": "Reddit r/SoccerBetting", "url": "https://..."}]}`.
   Alleen echte, gevonden tips met link. Ze tellen niet mee in de kans; ze worden enkel getoond.
7. Maak maximaal 5 `alerts`: belangrijk nieuws waarbij Unibet de odds nog niet lijkt te hebben aangepast, of een
   promo met duidelijke value.
8. **Weekportefeuille: kies de bets waarop jij zelf zou inzetten.** Elke week (maandag–zondag) zet Claude
   zogezegd tot €100 in, op hoogstens 10 bets of combi's; de gebruiker beslist op basis van dit resultaat of hij echt geld inzet. Wees dus
   streng en eerlijk:
   - Lees in `site/data/history.json` hoeveel bets met `"claude_pick": true` en `"pick_week"` = deze week er al zijn.
     Kies er vandaag hoogstens zoveel dat het totaal 10 niet overschrijdt; spreid ze over de week (meestal 2–4 per
     dag, de meeste voor het weekend). Elke bet moet nog moeten beginnen.
   - **Strategie (sinds W46, op vraag van de gebruiker): veilig spelen, weinig risico, combi's van favorieten.**
     De cijfers tot nu: favorieten (≥55%) +9% rendement over 72 bets, risicobets −34% over 37 (eigen keuzes 0 op 6).
     - **Kern = favorieten** (eigen kans ≥ 60%, odds ±1,25–2,00, eigen kans × odds ≥ 1): ongeveer 70–80% van het
       weekbudget, samen enkel en in combi.
     - **Combi's van veilige bets:** 2–3 per week, elk van 2–3 favorieten (elke selectie ≥ 60% winkans, andere
       wedstrijden; 4 selecties alleen met een combi-boost; de engine weigert legs onder 60% en meer dan 4 legs).
       Kies alleen selecties waarvan je eigen kans × odds ≥ ±0,98 is: de marge van Unibet stapelt zich op in een
       combi, dus een combi van slechte bets is nog slechter, een combi van goede bets versterkt de winst. Bedrag €5–15.
     - **Risicobets:** hoogstens 1 per week, hoogstens €5 (de engine dwingt dit af), en alleen bij vers nieuws dat
       duidelijk nog niet in de odds zit (eigen value ≥ +5%). Een dunne marge van 2–3% op een outsider is geen reden.
     - Het evenwicht: zet het meest (€15–30) op de 2–3 sterkste favorieten van de week, en verdeel de rest over
       veilige combi's. Liever een week met €60 ingezet op goede bets dan €100 met opvulling.
   - Hoogstens 10 bets of combi's per week; liever minder dan 10 dan een slechte bet. Wat op zaterdag/zondag nog
     ontbreekt, vult de engine automatisch aan met de sterkste **favorieten** van het weekend (nooit risicobets).
   - **Bedrag:** je hebt per week €100. Kies per bet zelf een bedrag tussen €2 en €30 (`"stake"`).
   - **Combi-notatie:** `{"combi": ["<key1>", "<key2>", ...], "stake": 10, "reason": "..."}`. Een combi telt als één
     bet. Bekijk ook `combis` in `latest.json` (de "zekerste" combi's) en of er een combi-boost in `promos.json` staat.
   - Zet ze in `research.json` → `picks`: `{"key": "<match_id>|<home|draw|away>", "stake": 15, "reason": "2–3 zinnen: waarom"}`.
     De sleutel staat als `key` bij elke bet in `latest.json`; een andere uitkomst van een wedstrijd uit
     `cache/matches.json` mag ook. De odds worden vastgelegd op het moment van kiezen; later wijzigen kan niet.
8b. **Maandag: weekrapport.** Schrijf op maandag in `weekly_report` 4–6 zinnen over de vorige week (maandag–zondag):
   hoeveel van je keuzes raak waren, wat werkte en wat niet (favorieten of risico, welke competities; zie de
   bets met `"claude_pick": true` en die `pick_week`), en wat je deze week anders doet. Eerlijk, ook bij verlies.
   Zet `report_week` op die week (bv. `"2026-W44"`).
8c. **Weekendronde (zaterdag en zondag rond 10u47).** Er is dan een tweede, korte ronde. Lees eerst het bestaande
   `research.json` van vandaag en vul het aan in plaats van het te vervangen. Kijk alleen naar de wedstrijden van
   vandaag: officiële of verwachte opstellingen, last-minute blessures, en of Unibet een odds-achterstand heeft.
   Voeg alerts toe met het uur erbij en zet `"urgent": true` als er iets belangrijks is; nieuwe keuzes voor de
   weekportefeuille mag je toevoegen zolang de week onder 10 blijft. Er gaat dan automatisch een melding uit.
9. Schrijf beide bestanden. Controleer met `python3 scripts/engine.py apply` dat er geen fout is. Zet daarna
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
  "community": {"<match_id>": [{"outcome": "away", "note": "Tip in 1 zin", "source": "Reddit r/SoccerBetting", "url": "https://..."}]},
  "alerts": [
    {"tag": "BLESSURE", "time": "07:52", "title": "Kort, concreet", "body": "Wat er is en welke bet het raakt.",
     "url": "https://..."}
  ],
  "results": {"<match_id>": {"home": 2, "away": 1}},
  "picks": [{"key": "<match_id>|home", "stake": 15, "reason": "Waarom je hierop inzet."},
            {"combi": ["<match_id>|home", "<match_id>|away"], "stake": 10, "reason": "Waarom deze combi."}],
  "weekly_report": "Alleen op maandag: 4–6 zinnen over vorige week.",
  "report_week": "2026-W44",
  "urgent": false
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
