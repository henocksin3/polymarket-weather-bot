# Claude Code Build Plan — Fase-for-fase prompts

## Slik bruker du dette dokumentet

Hver fase er ett Claude Code-prompt du kjører i én sesjon.
Start ny sesjon (/clear) mellom hver fase.
Test at alt fungerer før du går videre.
Kopier CLAUDE.md-filen inn i prosjektmappen FØR du starter.

---

## FASE 0: Prosjektoppsett (5 min)

```
Opprett en ny mappe kalt polymarket-weather-bot.
Les CLAUDE.md i prosjektroten for kontekst.

Lag denne filstrukturen med tomme placeholder-filer:
- .env.example med alle nødvendige miljøvariabler
- requirements.txt med avhengighetene fra CLAUDE.md
- config.py med konfigurasjonsverdiene fra CLAUDE.md
- main.py som bare printer "Bot starting..." og avslutter
- src/ med __init__.py
- src/weather.py, src/markets.py, src/signals.py, src/trader.py, src/risk.py, src/alerts.py — alle med bare en docstring
- tests/ med __init__.py
- railway.json for deployment

Verifiser at `pip install -r requirements.txt` fungerer uten feil.
Verifiser at `python main.py` printer meldingen og avslutter rent.
```

---

## FASE 1: Værdata (30 min)

```
Les CLAUDE.md for kontekst.
Les src/weather.py — den er tom.

Implementer src/weather.py med disse funksjonene:

1. fetch_ensemble_forecast(lat, lon, forecast_days=2)
   - Kaller Open-Meteo ensemble API
   - Returnerer en dataclass med 31 ensemble members' temperaturdata per time
   - Bruker httpx med timeout og retry
   
2. calculate_probability(ensemble_data, threshold_low, threshold_high, target_date, target_hour)
   - Tar ensemble-dataen og teller hvor mange av 31 members
     som faller innenfor temp-range [threshold_low, threshold_high]
   - Returnerer probability (float 0-1) og confidence (float 0-1)
   - Confidence = hvor enige medlemmene er (høy hvis 28/31, lav hvis 16/31)

3. get_forecasts_for_cities(cities_config)
   - Itererer over CITIES fra config.py
   - Kaller fetch_ensemble_forecast for hver by
   - Returnerer dict med by -> forecast data

Skriv tester i tests/test_weather.py som:
- Mocker HTTP-kall med en fixture av ekte Open-Meteo respons
- Tester at probability-kalkulasjonen er riktig (28/31 = 0.903)
- Tester at confidence-kalkulasjonen er riktig
- Tester feilhåndtering ved timeout

Kjør testene og verifiser at alle passerer.
Kjør også en ekte test mot Open-Meteo API for å bekrefte at formatet matcher.
```

---

## FASE 2: Markedsdata (30 min)

```
Les CLAUDE.md for kontekst.
Les src/weather.py for å forstå hva vi har.
Les src/markets.py — den er tom.

Implementer src/markets.py med disse funksjonene:

1. fetch_weather_markets()
   - Kaller Polymarket Gamma API: GET https://gamma-api.polymarket.com/markets
   - Filtrerer for aktive værmarkeder (tag=weather, eller "temperature" i question)
   - Returnerer liste av WeatherMarket dataclasses

2. parse_market_question(question_text) -> ParsedMarket
   - Parser spørsmål som "What will the high temperature be in New York City on March 10?"
   - Ekstraherer: city, date, temperature_ranges (buckets)
   - Returnerer ParsedMarket dataclass med disse feltene
   - Viktig: Polymarket-spørsmål har varierende format — vær robust

3. get_market_prices(condition_id)
   - Henter nåværende YES/NO-priser for et marked
   - Returnerer dict med token_id -> price for hver bucket

Skriv tester i tests/test_markets.py som:
- Tester parse_market_question med 5 ulike eksempler på ekte spørsmålsformater
- Mocker Gamma API-respons

Kjør testene og verifiser.
Kjør også en ekte test mot Gamma API for å se hvilke værmarkeder som finnes akkurat nå.
Print resultatene pent formatert.
```

---

## FASE 3: Signal-generering (20 min)

```
Les CLAUDE.md for kontekst.
Les src/weather.py og src/markets.py for å forstå hva vi har.
Les src/signals.py — den er tom.

Implementer src/signals.py:

1. generate_signals(forecasts, markets) -> list[Signal]
   - For hvert weather market:
     a. Match markedet til riktig by og dato i forecast-dataen
     b. Beregn edge = forecast_probability - market_price
     c. Hvis abs(edge) > MIN_EDGE og confidence > MIN_CONFIDENCE:
        opprett et Signal med all relevant info
   - Sorter signaler etter edge (høyest først)
   - Returner liste av Signal dataclasses

Signal dataclass:
  - market_id, condition_id, token_id
  - question (menneskelesbart)
  - city, date
  - forecast_prob, market_price, edge
  - confidence
  - recommended_side ("YES" eller "NO")
  - recommended_size (fra kelly)
  - timestamp

Skriv tester med hardkodede verdier:
- Forecast sier 90% sjanse, marked priser til 15% → edge = 75%, signal YES
- Forecast sier 10% sjanse, marked priser til 85% → edge = -75%, signal NO
- Forecast sier 50%, marked priser til 48% → edge 2%, under terskel → ingen signal

Kjør testene.
```

---

## FASE 4: Risk management (15 min)

```
Les CLAUDE.md.
Les src/risk.py — den er tom.

Implementer src/risk.py:

1. kelly_size(edge, market_price, bankroll, kelly_fraction=0.15)
   - Beregner Kelly criterion: f = (p*b - q) / b
     der p = forecast_prob, q = 1-p, b = (1/market_price) - 1
   - Multipliser med kelly_fraction (konservativ)
   - Cap til MAX_POSITION_USD
   - Returner størrelse i USDC

2. check_daily_limits(db_path) -> bool
   - Les dagens handler fra SQLite
   - Sjekk antall handler < MAX_DAILY_TRADES
   - Sjekk total daglig P&L > -MAX_DAILY_LOSS_PCT * BANKROLL
   - Returner True hvis vi kan handle, False hvis vi må stoppe

3. get_current_exposure(db_path) -> float
   - Returner total USDC i åpne posisjoner

Skriv tester. Kjør dem.
```

---

## FASE 5: Trading og alerts (30 min)

```
Les CLAUDE.md.
Les alle src/-filer for å forstå systemet.

Implementer src/trader.py:

1. initialize_client(api_key, api_secret, api_passphrase)
   - Oppretter ClobClient fra py-clob-client
   - Returnerer klient-instans

2. place_order(client, token_id, side, size, price)
   - Plasserer limit order via CLOB API
   - Logger ordren
   - Returnerer order response

3. get_open_positions(client)
   - Henter åpne posisjoner

Implementer src/alerts.py:

1. send_telegram_alert(signal, trade_result)
   - Formaterer en pen melding med:
     Marked, Edge, Størrelse, Pris, Resultat
   - Sender via Telegram Bot API

2. send_daily_summary(stats)
   - Dagens handler, P&L, åpne posisjoner

Implementer SQLite logging i en ny src/database.py:
- create_tables() — trades table med alle relevante felter
- log_trade(signal, order_result)
- get_today_trades()
- get_total_pnl()

IKKE skriv ekte trading-tester — bare verifiser at funksjonene
kan instansieres uten feil med mock-verdier.
```

---

## FASE 6: Main loop (20 min)

```
Les CLAUDE.md.
Les alle src/-filer.

Implementer main.py:

1. Parse kommandolinje-argumenter:
   --dry-run (monitor mode, ingen ekte handler)
   --once (kjør én gang og avslutt, for testing)

2. Main loop:
   while True:
     a. Hent værvarsler for alle byer
     b. Hent aktive værmarkeder fra Polymarket
     c. Generer signaler
     d. For hvert signal: sjekk risk limits, plasser ordre (eller logg i dry-run)
     e. Send Telegram-alerts
     f. Vent SCAN_INTERVAL_MINUTES
     g. Fang alle exceptions, logg dem, og fortsett

3. Graceful shutdown med SIGTERM/SIGINT

Test med: python main.py --dry-run --once
Verifiser at den:
- Henter ekte værdata
- Henter ekte markeder
- Finner (eller ikke finner) signaler
- Logger alt pent
- Avslutter rent
```

---

## FASE 7: Deploy til Railway (10 min)

```
Prosjektet skal deployes til Railway.

1. Verifiser at railway.json er korrekt
2. Verifiser at .env.example har alle nødvendige variabler
3. Lag en kort README.md med:
   - Hva boten gjør
   - Oppsett-instruksjoner
   - Miljøvariabler som trengs
   - Hvordan kjøre lokalt

Verifiser at alt fungerer med:
python main.py --dry-run --once
```

---

## Tips for hver sesjon

1. **Start ALLTID med**: "Les CLAUDE.md for kontekst"
2. **Én fase per sesjon** — /clear mellom faser
3. **Test underveis** — aldri gå videre uten grønne tester
4. **Dry-run først** — kjør --dry-run i minst 24 timer før ekte penger
5. **Start med $10-20** — skaler opp bare etter bevist profitt
6. **Sjekk output** — les loggene, forstå hva boten gjør

## Etter alle faser er ferdige

1. Kjør `python main.py --dry-run` i 48 timer
2. Les loggene — finner den reelle signaler? Ser edgen riktig ut?
3. Sammenlign botens signaler med hva som faktisk skjedde med været
4. Hvis resultatene ser bra ut: bytt til live med $10-20 startkapital
5. Overvåk daglig i minst en uke
6. Skaler gradvis opp posisjonsstørrelse basert på resultater
