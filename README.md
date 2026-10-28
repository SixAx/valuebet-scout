# ValueBet Scout

Zoekt elke ochtend voetbalbets waarvoor Unibet een hogere odds geeft dan de geschatte kans rechtvaardigt.

- `scripts/engine.py`: haalt odds (The Odds API) en teamdata (API-Football) op en rekent value en score uit.
- `site/`: de app voor je gsm (wordt via Netlify online gezet).
- `.github/workflows/`: `daily.yml` haalt om ±06:15 de data op, `notify.yml` stuurt om 08:00 de melding,
  `research.yml` verwerkt het ochtendonderzoek van Claude.
- `RESEARCH.md`: werkwijze voor het ochtendonderzoek van Claude.

Geheimen (GitHub → Settings → Secrets and variables → Actions): `ODDS_API_KEY`, `API_FOOTBALL_KEY`.
Variabele (zelfde scherm, tab Variables): `SITE_URL` = adres van je Netlify-site.

Gratis limieten: The Odds API 500 credits/maand (deze app gebruikt er ±10 per dag), API-Football 100 requests/dag
(deze app gebruikt er ±30 per dag).
