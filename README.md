# Poke Watcher

Watches eBay for newly listed Pokémon cards and pings a Discord webhook when a
Buy It Now listing looks like a deal against the TCGplayer market price.

Two alert rules per watched card:

| Rule | Fires when |
| --- | --- |
| `under_market` | Buy It Now total (price + shipping) is at or under *X%* of TCGplayer market (default 100%) |
| `offer_near_market` | Listing accepts Best Offers and its total is at or under *Y%* of market (default 115%) |

Each listing alerts once per watch. The web UI shows the watchlist, per-card
thresholds, and every alert that has fired.

## Sealed products

A watch is either a **single card** or a **sealed product** (booster boxes,
ETBs, packs, cases — tcgcsv lists them alongside singles). Sealed watches search
the sealed eBay categories with a new-condition filter and drop anything that
comes back from the singles category; grade targeting is disabled for them, and
the form pre-fills exclusions for opened/empty/damaged/proxy/repack/code-card
listings.

## Graded cards

A watch can target a grade (PSA / BGS / CGC / SGC / ACE / TAG plus a number, or
"raw only"). The grade is appended to the eBay query, and listing titles are
matched against it — `PSA 10`, `psa10` and `PSA-10` all count, `PSA 9` and raw
copies do not; a raw watch rejects anything that looks slabbed.

tcgcsv prices are for raw cards, so a graded watch multiplies the market price
by `grade_price_multiplier` (e.g. `4` if PSA 10 copies sell for ~4× raw) before
the percentage thresholds apply. A manual market price override is used as-is.

## Data sources

- **Listings** — eBay [Browse API](https://developer.ebay.com/api-docs/buy/browse/overview.html),
  filtered to fixed-price trading card singles, sorted newest first. Set
  `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET` from a production keyset at
  https://developer.ebay.com/my/keys. Without credentials the app falls back to
  parsing eBay search HTML, which eBay blocks from most datacenter IPs.
- **Prices** — TCGplayer market prices from the free daily dumps at
  [tcgcsv.com](https://tcgcsv.com) (TCGplayer has no public price API). A local
  product index of every Pokémon product powers card search; build it once from
  the Settings tab. Any watch can also use a manual market price override, set when adding it or
  changed/cleared later via "Market price" in the watch list.
- **Notifications** — a Discord webhook URL, set in the Settings tab (stored in
  the database) or via `DISCORD_WEBHOOK_URL`. Each alert carries direct action links:
  **Buy It Now** goes straight into eBay checkout (`/atc/binctr?item=…`) and
  **Make Offer** opens the listing with the Best Offer layer (`?boolp=1`), the
  same URLs eBay's own item page uses. Both prompt eBay sign-in if needed.

## Running

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env    # fill in eBay keys (optional but recommended)
.venv/bin/uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000, go to **Settings → Rebuild card index** (a few
minutes, ~20k cards), paste your Discord webhook, then add cards on the
**Watchlist** tab. A background sweep runs every `POLL_INTERVAL_SECONDS`
(default 300); "Check all now" runs one immediately.

## Database

Set `DATABASE_URL` to a Postgres connection string and the app keeps everything
there (watches, alerts, settings, the product/price index); the schema is
created on startup. With `DATABASE_URL` unset it falls back to a local SQLite
file at `DATABASE_PATH` (default `data/poke_watcher.db`), which is fine for
local development and tests.

For Supabase, copy **Project Settings → Database → Connection string → URI** and
substitute your database password. `postgres://`, `postgresql://` and
`postgresql+psycopg://` are all accepted. Prefer the pooler host (port 6543) on
platforms that open many short-lived connections.

Moving off an existing SQLite deployment means pointing `DATABASE_URL` at
Postgres and rebuilding the card index from the Settings tab; old watches are
not copied across automatically.

## eBay account deletion endpoint

eBay only enables production API keys once the application exposes a public
HTTPS [marketplace account deletion](https://developer.ebay.com/marketplace-account-deletion)
endpoint. This app serves it at `/ebay/notifications`:

- `GET  /ebay/notifications?challenge_code=…` returns
  `{"challengeResponse": sha256(challengeCode + verificationToken + endpoint)}`
- `POST /ebay/notifications` acks with 204. Forwarding a summary of each notice
  to Discord is opt-in ("Forward deletion notices to Discord" in Settings) —
  eBay sends one for every account it closes, so it is off by default.

Deploy the app somewhere public, then in **Settings → eBay account deletion
endpoint** paste that public URL, hit **Generate** for a token, save, and
register the same URL + token on https://developer.ebay.com/my/keys. The
endpoint URL must match byte for byte — it is part of the hash.

## Deploying

The app ships a `Dockerfile` plus ready-made `render.yaml` and `fly.toml`. State
lives in Postgres, so no volume is needed — just set `DATABASE_URL`:

```bash
# Render: New → Blueprint → point at this repo, then set DATABASE_URL,
# EBAY_CLIENT_ID, EBAY_CLIENT_SECRET, DISCORD_WEBHOOK_URL under Environment
# Fly:
fly launch --copy-config --no-deploy
fly secrets set DATABASE_URL=… EBAY_CLIENT_ID=… EBAY_CLIENT_SECRET=… DISCORD_WEBHOOK_URL=…
fly deploy
```

Then use the deployed `https://…/ebay/notifications` URL below.

## Layout

```
app/config.py     settings from .env
app/db.py         SQLAlchemy schema (Postgres or SQLite) + query helpers
app/tcg.py        tcgcsv product index and TCGplayer market prices
app/ebay.py       Browse API client, HTML scraper fallback, Listing model
app/rules.py      deal evaluation (pure, unit tested)
app/notifier.py   Discord webhook embeds
app/monitor.py    polling loop, dedupe, alert persistence
app/main.py       FastAPI routes
app/ebay_notifications.py  eBay account deletion challenge + notices
app/static/       single-page UI (no build step)
```

## Tests

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check app tests
```
