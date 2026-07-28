# FoodBot

A personal food- and fitness-tracking system fronted by a Telegram bot and backed by a FastAPI service and PostgreSQL. You log meals by typing a name, sending a free-text description, or photographing a barcode; the system resolves nutrition through a tiered lookup (local database → OpenFoodFacts → LLM estimation), computes per-entry and daily macros, and answers questions about today, this week, and progress against your goals.

The project is deliberately dual-purpose: a tool that is genuinely useful day to day, and a vehicle for practicing production-shaped data engineering — schema design, migrations, an OLTP/analytics split, role-based permission separation, and (next) a dbt transformation layer with dashboards.

---

## Table of contents

- [What it does](#what-it-does)
- [Commands](#commands)
- [Architecture overview](#architecture-overview)
- [Data flow](#data-flow)
- [Data model](#data-model)
- [The tiered food lookup](#the-tiered-food-lookup)
- [The weight-trend model](#the-weight-trend-model)
- [Layered code structure](#layered-code-structure)
- [Key design decisions](#key-design-decisions)
- [Repository layout](#repository-layout)
- [Running it locally](#running-it-locally)
- [Configuration](#configuration)
- [Database administration](#database-administration)
- [Deployment](#deployment)
- [Development workflow and conventions](#development-workflow-and-conventions)
- [Known gotchas](#known-gotchas)
- [Roadmap](#roadmap)

---

## What it does

FoodBot turns meal logging into a few seconds of chat interaction. There is no app to install beyond Telegram, no account to create, and no password to manage — Telegram itself supplies a verified, stable user identity with every message.

Three ways to log a food:

1. **By name.** `/log chicken` runs a typo-tolerant fuzzy search over the foods table and presents matches as tappable buttons.
2. **By description.** If the name search misses, the text is handed to an LLM that extracts per-100g nutrition into a validated schema, which you confirm before it is saved.
3. **By barcode.** Send a photo of a product barcode; it is decoded locally, looked up against the local cache and then OpenFoodFacts, and (on a miss) falls through to the same describe-and-estimate flow.

Whatever path identifies the food, every route converges on the same "how many grams?" → "confirm the macros" → "logged" tail. Once a food is resolved through any path, it becomes a permanent local row, so the expensive paths (LLM, network lookup) are only ever paid once per product.

On the read side you can see today's entries and totals, today's macros against your targets, the current week so far, and your recent body-weight history.

---

## Commands

| Command | What it does |
|---|---|
| `/start` | Greeting plus a backend connectivity check. |
| `/whoami` | Shows your stored profile; creates it on first use. Surfaces approval status. |
| `/log <food>` | Fuzzy-search foods, pick one, enter grams, confirm. |
| `/log <description>` | Free-text; an LLM estimates per-100g macros if there is no database match. |
| *(photo)* | Send a barcode photo to resolve a packaged product. |
| `/today` | Today's logged meals and macro totals. |
| `/status` | Today's macros against your daily targets. |
| `/week` | This calendar week so far (Monday through today). |
| `/logmeal <description>` | Estimate a whole meal in one shot (takeout/restaurant food). |
| `/weight <kg>` | Log body weight, e.g. `/weight 80.5`. |
| `/weight <kg> seed` | Log a remembered (not measured) weight; excluded from trend fits. |
| `/weight` | Show recent weight entries. |
| `/weight_model` | Weight-trend chart: OLS fit, ±1σ band, slope CI, step-down trigger. |
| `/weight_model <months>` | Same, with the projection extended N months out. |
| `/weight_model goal <kg>` | Set your target weight; target lines rescale to it. Persists. |
| `/goal` | Show current daily targets. |
| `/goal <field> <value>` | Set one target (`kcal`, `protein`, `fat`, `carbs`). |
| `/help` | Full command reference. |
| `/cancel` | Abort a multi-step flow (during `/log`). |

The active set is also registered with Telegram via `setMyCommands`, so the `/` autocomplete and the menu button list them natively.

---

## Architecture overview

```
        ┌──────────────┐
        │   Telegram    │   user types / taps / sends a photo
        └──────┬───────┘
               │  long-poll (outbound; no public endpoint needed)
        ┌──────▼───────┐
        │   Bot service │   python-telegram-bot
        │   (handlers,  │   - decodes barcodes locally (pyzbar)
        │    API client)│   - holds no business logic
        └──────┬───────┘
               │  HTTP + JSON  (httpx)
        ┌──────▼───────┐
        │  FastAPI app  │   all business logic lives here
        │  (routes →    │   - validation at the boundary (Pydantic)
        │   services →  │   - tiered food lookup
        │   repositories)│  - macro computation, day bucketing
        └──┬────────┬──┘
           │        │  async (asyncpg)        │ httpx
   ┌───────▼──┐  ┌──▼──────────┐      ┌───────▼────────┐
   │ Postgres │  │  Gemini API  │      │ OpenFoodFacts  │
   │ app +    │  │ (LLM parse)  │      │ (barcode → food)│
   │ analytics│  └─────────────┘      └────────────────┘
   └──────────┘
```

The bot is a **thin client**. It owns presentation and conversation state only; every fact about food, every macro calculation, every database write happens behind the FastAPI service's HTTP boundary. This is the single most important structural choice in the project: it means a future web dashboard, mobile app, or scheduled job is just another API client and costs nothing architecturally to add.

The API is **async end to end** because it fans out to slow external services — an LLM call can take seconds, and a synchronous worker would stall every other request for its duration. Async lets one process serve other requests while waiting on Gemini or OpenFoodFacts.

PostgreSQL is the **single source of truth**, holding both the operational (`app`) tables and, in the same instance, the analytics tables that dbt will build.

---

## Data flow

### Logging a meal (the write path)

```
/log "greek yogurt"
        │
        ▼
bot handler  ──HTTP──►  GET /foods/search?q=greek+yogurt
        │                       │
        │                       ▼  trigram similarity over foods.name
        │               ┌── hit ──►  return matches
        ▼               │
  show buttons ◄────────┘
        │  (user taps one)
        ▼
  "how many grams?"  ──►  user types 200
        │
        ▼
  compute macros locally for the preview, then on Confirm:
        │
        ▼
  POST /meal-entries  {user_id, source_type:"food", food_id, weight_g}
        │
        ▼
  service sets consumed_at = now(UTC) if unset, writes row, commits
        │
        ▼
  "Logged 200g of Greek yogurt."
```

On a **search miss**, the same `/log` flow silently routes to `POST /foods/parse`, which calls Gemini with a Pydantic-derived response schema, validates the result, and shows it for confirmation. On **Save**, the parsed food is persisted (`source = ai_estimated`) and the flow merges back into the grams-and-confirm tail.

On a **barcode photo**, the bot decodes the EAN locally and calls `GET /foods/by-barcode/{ean}`, which checks the local cache, then OpenFoodFacts, saving any hit as a public food row. A miss prompts for a description and joins the LLM path — carrying the barcode through so the eventual saved food gets tagged with the real EAN and warms the cache for next time.

### Reading totals (the read path)

```
/status
   │
   ▼
GET /meal-entries/status?user_id=…
   │
   ▼
service: resolve user → compute today's UTC bounds from the user's timezone
   │      → fetch entries in [start, end)  (index-friendly range scan)
   │      → sum macros in Decimal, eager-loading each entry's food
   │      → attach the user's daily targets
   ▼
{ day, totals:{kcal,protein,fat,carbs}, targets:{…} }
   │
   ▼
bot renders "1820 / 2300 kcal", "no goal set" where a target is null
```

`/week` follows the same shape but anchors to the local Monday, runs a single bounded range query for the whole week, buckets entries by **local** date (so a 00:30 entry files under the correct day rather than the previous UTC day), and fills missing days with zeros up to today — never padding future days.

---

## Data model

All operational tables live in the `app` schema. Macros are stored as `NUMERIC` (exact decimal) rather than floating point, because accumulating floats across a day's entries drifts.

```
users
  id, telegram_id (unique), display_name, timezone,
  is_active, is_admin,
  daily_kcal_target, daily_protein_target_g,
  daily_fat_target_g, daily_carbs_target_g,
  goal_weight_kg,
  created_at, updated_at

foods
  id, name, brand, barcode,
  kcal_100g, protein_100g, fat_100g, carbs_100g,
  fiber_100g, sugar_100g, sat_fat_100g,
  source (system | openFoodFacts | user | ai_estimated),
  visibility (public | private),
  created_by_user_id → users.id,
  openfoodfacts_id, created_at, updated_at

recipes
  id, user_id → users.id, name,
  override_total_weight_g, notes, created_at, updated_at

recipe_ingredients
  id, recipe_id → recipes.id, food_id → foods.id,
  weight_g, position

meal_entries
  id, user_id → users.id, consumed_at,
  source_type (food | recipe),
  food_id → foods.id, recipe_id → recipes.id,
  weight_g, notes, created_at

body_metrics
  id, user_id → users.id, recorded_at,
  weight_kg, body_fat_pct, notes, is_seed, created_at
```

`body_metrics.is_seed` marks a weight that was remembered or estimated rather
than stepped on a scale. It is the **only** mechanism that excludes a point from
a trend fit — never a date cutoff, never a value threshold — because a single
unmeasured anchor visibly drags the slope (−0.72 kg/wk instead of −0.82 on the
reference series). The flag is persistent, so the fit is reproducible.

Integrity is enforced **in the database**, not only in application code:

- **Partial unique index** on `foods.barcode` where the barcode is not null — multiple null barcodes are allowed, but any actual barcode is unique.
- **GIN trigram index** on `foods.name` for fuzzy search.
- **XOR check constraint** on `meal_entries`: a row is either a food entry (`food_id` set, `recipe_id` null) or a recipe entry (the reverse), never both or neither. This same rule is duplicated as a Pydantic validator at the API boundary so malformed input gets a clean 422 instead of a database error surfacing as a 500.
- **Range checks** on macros (non-negative), weight (`> 0`), and body-fat percentage (0–70).
- A check that a `private` food must have an owner.

Every macro-bearing column and every constraint name follows a fixed naming convention (`ix_`, `uq_`, `ck_`, `fk_`, `pk_`), which makes Alembic's autogenerated migration diffs deterministic.

### Two schemas: `app` and `analytics`

The database is split into two schemas backed by two roles:

- **`app_user`** owns the `app` schema and has read-only access to `analytics`.
- **`dbt_user`** has read-only access to `app` and owns `analytics`.

Default privileges are configured so that when `app_user` creates a table in `app`, `dbt_user` automatically gains `SELECT` on it, and vice versa. The effect is that the architecture is **self-enforcing**: the API physically cannot write to analytics, and dbt physically cannot corrupt operational data — enforced by grants, not by convention.

This is the standard production ELT separation (operational store → warehouse → transformation layer) collapsed onto a single Postgres instance. At single-VPS scale, schemas-within-one-database give the logical separation without the cost of a second database: cross-schema joins are free and transactional, one backup covers everything, and one instance fits the memory budget.

---

## The tiered food lookup

Resolving "what is this food and what are its macros" runs through three tiers, cheapest first:

```
        ┌─────────────────────────┐
        │  Tier 1: local DB        │  trigram search (by name)
        │                          │  or exact match (by barcode)
        └───────────┬─────────────┘
                    │ miss
        ┌───────────▼─────────────┐
        │  Tier 2: OpenFoodFacts   │  barcode → product nutrition
        │  (barcode path only)     │  free, open data, no API key
        └───────────┬─────────────┘
                    │ miss
        ┌───────────▼─────────────┐
        │  Tier 3: Gemini LLM      │  free-text → estimated macros
        │  (constrained parser)    │  user confirms before saving
        └─────────────────────────┘
```

The crucial property is **cache-warming**: every successful resolution at tier 2 or 3 is written back as a local food row. After a month of real use, your common groceries all live in tier 1 and the network/LLM call rate approaches zero. OpenFoodFacts hits are saved as public foods (everyone benefits); LLM estimates are saved private to the user and tagged `ai_estimated` so they remain auditable.

The LLM is treated as an **untrusted parser, not an oracle**: its output is constrained by a JSON schema derived from a Pydantic model, validated on receipt, retried once with the validation error fed back into the prompt, and always confirmed by the user before anything is persisted. OpenFoodFacts data — which is community-contributed and varies in quality — is similarly never trusted blindly: products missing any required macro are rejected as a clean miss rather than saved with misleading zeros.

---

## The weight-trend model

`/weight_model` fits an ordinary least-squares line through every measured
weigh-in and renders it as a chart: the observed points, the fit, a ±1σ
residual band, a slope-CI cone, a dashed projection, target lines with crossing
dates, and a residual panel beneath.

### Target lines scale to the user

The reference figure shipped with a hardcoded 100/95/90/86 ladder, which only
reads correctly for someone starting near 105 kg. Targets are now generated from
two numbers the user owns — their goal (`users.goal_weight_kg`, set with
`/weight_model goal 86`) and their highest measured weight — so the band, and
therefore the chart's vertical framing, centres on whatever range that person
actually occupies. Change the goal and the chart re-centres.

`build_targets()` picks a step off a round-number ladder (0.5, 1, 2, 2.5, 5, 10,
20, 25, 50) — the smallest one that keeps the line count at five or under — so
the step grows with the span instead of the lines multiplying into clutter. The
goal line always appears and keeps its exact value; intermediate lines snap to
the grid so they read as 90/95/100 rather than 88.4/93.4/98.4, and any grid line
close enough to crowd the goal's label is dropped. It works in both directions:
a goal above current weight produces the same ladder running upward.

No goal set means no target lines and no crossing dates — the fit, the band, and
the trigger still render, and the caption says how to set one. On the original
reference series a goal of 86 reproduces the old hardcoded ladder, which is
pinned by a test.

The statistics live in `api/app/services/weight_model.py`, which imports nothing
but numpy — no FastAPI, no SQLAlchemy, no config. That is deliberate: it is the
one module in the project that is pure computation, so it can be tested against
known-good numbers without a database or an event loop. `api/tests/test_weight_model.py`
pins it to a reference series (slope −0.822 kg/wk, SE 0.063, r² 0.890, n=23) and
to four crossing dates. If those move, the change is wrong, not the test.

Everything that knows about the database lives in `services/weight_trend.py` —
the entire integration surface is one function mapping `body_metrics` rows to
`WeighIn` objects. Timestamps are converted to the user's local wall-clock and
stripped of tzinfo before fitting, for the same reason `/week` buckets on local
dates: a 00:30 Vienna weigh-in is the previous day in UTC, and both the day-index
arithmetic and the chart's date labels would file it under the wrong date.

Three framing rules are structural, not stylistic:

- **The projection is a counterfactual, not a forecast.** It answers "where does
  today's rate lead if nothing changes" — and something always changes, because
  real loss decelerates as maintenance falls with mass. True dates land *later*.
  The API ships a `projection_disclaimer` string with every response and the bot
  prints it under every chart; the chart's own legend labels the dashed line
  "upper bound". Do not let a caption rewrite drop this.
- **The CI cone is parameter uncertainty, not a prediction interval.** It is how
  precisely the slope is known, not where tomorrow's weigh-in will fall.
- **Exclusion is by flag only.** See `is_seed` above.

The trigger is rate-based: it fires when the trailing 14-day slope goes
*shallower* than −0.30 kg/wk, i.e. loss has stalled and a calorie step-down is
due. A window needs ≥3 points to report at all, so one heavy-dinner morning
barely moves it — which is the point.

The chart is rendered server-side (matplotlib, Agg) and returned as a PNG from
`GET /body-metrics/weight-model/chart.png`, keeping the bot a thin client and
letting a future dashboard pull the same image from the same place. Renders run
in a worker thread behind a lock: matplotlib's pyplot carries global figure
state and isn't thread-safe, and a ~1s blocking render on the event loop would
stall every other request.

---

## Layered code structure

The API follows a strict four-layer pattern, repeated for every domain (users, foods, meal entries, body metrics):

```
route       HTTP shape: parse request, translate domain
            exceptions → HTTP status codes, nothing else
   │
service     business logic + transaction control:
            decides when to commit/rollback, owns defaults
            (e.g. consumed_at = now), composes repositories
   │
repository  data access only: builds and runs queries,
            returns ORM objects; never commits
   │
schema      Pydantic models: validation at the trust boundary,
            request/response contracts, OpenAPI documentation
```

The discipline that makes this work: **repositories flush, services commit.** A repository's `create()` flushes the INSERT so the caller gets a populated object with its generated id, but does not end the transaction. The service decides the transaction boundary, which lets it perform several repository operations atomically (e.g. a recipe and its ingredients in one transaction) without the repository holding any opinion about it.

Validation lives **at the boundary**. Anything arriving from outside the application — HTTP bodies, environment variables, LLM output — is untyped and possibly malformed. A Pydantic model at each such boundary converts "garbage somewhere deep in the call stack" into "a clean 422 at the front door." Because FastAPI derives its OpenAPI docs from the same models, the validation rules and the API documentation cannot drift apart.

---

## Key design decisions

A condensed version of the reasoning behind the stack. The throughline is that almost no choice is about a tool being "best" in the abstract — each is about fit to *this* project's constraints (a single small VPS, one developer, a data-engineering learning goal, multi-user-but-small scale, Python fixed).

| Area | Choice | Why, in one line |
|---|---|---|
| Frontend | Telegram bot | Lowest friction per entry; supplies auth for free; long-polls outward so no public endpoint is needed. |
| API framework | FastAPI + Uvicorn | Async-native (slow LLM calls don't block other users); type hints drive validation, serialization, and docs from one source. |
| Validation | Pydantic v2 | One validation model covers HTTP input, config, and LLM output; Rust core makes per-request validation cheap. |
| Database | PostgreSQL 16 | Relational, multi-writer, accumulates for years → needs MVCC, real constraints, transactional DDL; plus schemas/roles/trigram for the analytics split. |
| OLTP/analytics split | Two schemas, two roles | Production ELT separation on one box; grants make it self-enforcing. |
| ORM | SQLAlchemy 2.0 (async) | Spans ORM → Core → raw SQL so you never fight it at either extreme; models are the canonical schema Alembic diffs. |
| Drivers | asyncpg (runtime) + psycopg (migrations) | Async app needs a non-blocking driver; Alembic is a 2-second sync CLI that gets the simpler one. |
| Migrations | Alembic | Schema changes are reviewed, versioned, ordered code; autogenerate diffs the same metadata the app uses. |
| Search | pg_trgm | Typo-tolerant search on the hottest path, GIN-indexed, ships inside Postgres — no extra service. |
| Packaging | Docker + Compose | Dev/prod parity as a property of the image, not a hope; three-file layering keeps dev conveniences out of prod. |
| LLM | Gemini, structured output | Constrained parser pattern; existing account; free tier covers the cache-cold tail. |
| Barcode data | OpenFoodFacts | Free, open, no key, strong European coverage; commercial APIs are US-centric with capped free tiers. |
| Task runner | Make | The repo's operational interface — every verb self-documented, including the dangerous-to-mistype prod invocations. |
| Dependencies | pip + venv (pyproject.toml) | Standards-based manifest is the portable asset; migrating to uv/Poetry later is an afternoon. |

Two recurring patterns worth naming explicitly, because they show up across many of these:

- **Constraints decide, not rankings.** Memory budget ruled out heavier BI tools and local LLMs; single-host topology ruled out Kubernetes; the async architecture ruled out sync drivers and `requests`; the multi-user requirement plus role separation ruled out SQLite. The strongest answer to "why not X" names the constraint X violates and the conditions under which X would become correct.
- **Decide for now, design for later.** Several choices are explicitly staged: pip now with a uv/Poetry path; Telegram now with a dashboard path (the API boundary makes frontends additive); `user_id` in the request body now, resolved from an auth context later; cron-triggered dbt now, an orchestrator only if pipelines multiply.

---

## Repository layout

```
.
├── api/                          FastAPI service
│   ├── app/
│   │   ├── main.py               app construction, lifespan, /health
│   │   ├── config.py             pydantic-settings; sync/async DB URLs
│   │   ├── db/
│   │   │   ├── base.py           DeclarativeBase + naming convention
│   │   │   ├── enums.py          FoodSource, Visibility, EntrySource
│   │   │   ├── models.py         ORM table definitions
│   │   │   └── session.py        async engine + session dependency
│   │   ├── schemas/              Pydantic request/response models
│   │   ├── repositories/         data access (queries, no commits)
│   │   ├── services/             business logic + transaction control
│   │   │   ├── llm_parser.py     Gemini structured-output parser
│   │   │   ├── openfoodfacts.py  OFF client with defensive parsing
│   │   │   ├── weight_model.py   OLS trend stats (numpy only, no framework)
│   │   │   ├── weight_plot.py    matplotlib figure — the visual spec
│   │   │   └── weight_trend.py   DB adapter: body_metrics rows -> WeighIn
│   │   ├── routes/               HTTP endpoints
│   │   └── scripts/seed_foods.py idempotent pantry-staples seeder
│   ├── alembic/                  migrations (env.py, versions/)
│   ├── alembic.ini
│   ├── tests/                    pytest — weight-model reference check
│   └── Dockerfile
├── bot/                          Telegram bot service
│   └── bot/
│       ├── main.py               PTB Application, handler registration,
│       │                         setMyCommands
│       ├── config.py             bot settings (token, API URL)
│       ├── api_client.py         httpx wrapper, one method per endpoint
│       ├── barcode.py            pyzbar decode from photo bytes
│       └── handlers/             one module per command/flow
│           ├── start.py  whoami.py  log.py  logmeal.py
│           ├── today.py  weight.py  status.py
│           ├── weightmodel.py  goal.py  help.py
│   └── Dockerfile                installs libzbar0 for pyzbar
├── infra/
│   └── postgres/init/            runs once on first DB boot
│       ├── 01_extensions.sql     pg_trgm
│       ├── 02_schemas.sql        app, analytics
│       └── 03_roles.sh           app_user, dbt_user, default privileges
├── docker-compose.yml            base topology
├── docker-compose.override.yml   dev: source mounts, hot reload, ports
├── docker-compose.prod.yml       prod: log rotation, no dev conveniences
├── Makefile                      operational verbs
├── pyproject.toml                single dependency manifest (both services)
├── .env.example                  template; real .env is gitignored
└── README.md
```

The bot's package is named `bot` (inside `bot/bot/`) rather than `app` so that both services can be marked as source roots without a namespace collision with the API's `app` package.

---

## Running it locally

### Prerequisites

- Docker Desktop (or Docker Engine + Compose v2)
- A Telegram bot token from [@BotFather](https://t.me/BotFather) — use a dedicated **dev** bot, since a token can only be long-polled by one process at a time
- A Gemini API key from Google AI Studio
- `make` (preinstalled on Linux/macOS; on Windows it runs from Git Bash)

### First-time setup

```bash
# 1. Create your env file from the template
make bootstrap          # creates .env from .env.example on first run, then exits

# 2. Fill in .env — at minimum:
#    TELEGRAM_BOT_TOKEN, GEMINI_API_KEY
#    (the DB passwords come pre-populated in the example; change them for prod)

# 3. Run bootstrap again to bring up Postgres and verify schemas/roles
make bootstrap
```

`make bootstrap` brings up Postgres, waits for it to become healthy, and runs `make verify`, which confirms the `app`/`analytics` schemas exist, the `app_user`/`dbt_user` roles exist, and the `pg_trgm` extension is installed.

### Applying migrations and seeding

```bash
make up                 # start the full stack (API, bot, Postgres)

# apply the schema (run inside the api container, as app_user)
docker compose exec api alembic upgrade head

# seed common pantry foods (idempotent — skips names that already exist)
docker compose exec api python -m scripts.seed_foods
```

Migrations are applied by `app_user` (not the Postgres superuser) so that the default-privilege grants take effect on the tables it creates.

### Day-to-day

```bash
make up            # start everything (dev compose auto-applies override)
make down          # stop
make logs          # tail all service logs
make ps            # container status
make psql          # open a psql shell as the superuser
make restart-api   # restart just the API (escape hatch for flaky reload)
make restart-bot   # restart just the bot
make reset         # nuclear: down + drop the Postgres volume (fresh DB)
make migrate       # alembic upgrade head (inside the api container, as app_user)
make test          # pytest on the host venv (weight-model reference check)
```

`make test` runs on the host rather than in a container — the weight-model tests
are pure numpy, with no database and no event loop, so there is nothing to
containerize. It needs the dev extra once: `pip install -e ".[dev]"`.

With the dev compose override active, the API and bot source directories are bind-mounted and the API runs with `--reload`, so most code changes take effect without a rebuild. (See [Known gotchas](#known-gotchas) for the Windows reload caveat.)

Test API endpoints directly via the interactive docs at `http://localhost:8000/docs` — this is the recommended way to exercise endpoints, especially POSTs, without fighting shell quoting.

### Activating yourself (before the admin flow exists)

New users default to `is_active = false` (the multi-user approval gate). Until the admin-approval command lands, flip your own flag manually:

```bash
docker compose exec -T postgres psql -U postgres -d foodbot \
  -c "UPDATE app.users SET is_active = true WHERE telegram_id = <your_telegram_id>;"
```

---

## Configuration

All configuration is loaded once at startup from environment variables (and a `.env` file) into typed `Settings` objects — one for the API, one for the bot, each ignoring the other's keys. A missing required variable crashes the relevant service immediately at boot with a precise error naming the field, rather than surfacing as a 500 deep in a request days later.

`.env` keys (see `.env.example` for the full template):

| Key | Used by | Notes |
|---|---|---|
| `POSTGRES_SUPERUSER`, `POSTGRES_SUPERUSER_PASSWORD` | Postgres only | Admin; never referenced by app code. |
| `APP_USER_PASSWORD` | API, Postgres init | The application database role. |
| `DBT_USER_PASSWORD` | dbt (future), Postgres init | The analytics role. |
| `TELEGRAM_BOT_TOKEN` | bot | From @BotFather. Dev token locally; prod token only on the server. |
| `GEMINI_API_KEY` | API | From Google AI Studio. |
| `LOG_LEVEL` | both | `DEBUG` locally, `INFO` in production. |

Note that environment variables consumed inside containers must be **declared explicitly** in the relevant compose service's `environment` block — a value present in the host `.env` is not automatically passed through to a container.

---

## Database administration

Direct psql access for admin tasks:

```bash
# superuser shell
make psql

# one-off query as superuser
docker compose exec -T postgres psql -U postgres -d foodbot -c "SELECT …;"
```

Useful inspections:

```sql
-- who's registered and approved
SELECT id, telegram_id, display_name, is_active FROM app.users;

-- foods by source (how the tiered lookup is performing)
SELECT source, count(*) FROM app.foods GROUP BY source;

-- recent entries with local-time interpretation (timezone sanity check)
SELECT id, consumed_at,
       consumed_at AT TIME ZONE 'Europe/Vienna' AS local
FROM app.meal_entries
ORDER BY consumed_at DESC LIMIT 10;
```

Migrations:

```bash
# generate a draft after changing models (review it before applying!)
docker compose exec api alembic revision --autogenerate -m "describe change"

# apply
docker compose exec api alembic upgrade head

# check current version
docker compose exec api alembic current
```

---

## Deployment

Production runs on a single VPS using the explicit compose-file combination (no dev override):

```bash
make up-prod      # docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
make logs-prod
```

The three-file Compose layering is the mechanism that keeps dev and prod cleanly separated:

- `docker-compose.yml` — the base topology (Postgres tuning, healthchecks, the private network, service definitions).
- `docker-compose.override.yml` — applied **automatically** by `docker compose up`, this carries dev-only conveniences: source bind-mounts, `--reload`, exposed localhost ports, `DEBUG` logging.
- `docker-compose.prod.yml` — applied only via the explicit `-f` flags in `make up-prod`, carrying production concerns like log rotation.

Because the override is auto-applied and prod requires explicit flags, the dangerous failure mode (accidentally running prod with dev settings) is avoided by construction — and the `up-prod` invocation is encoded in the Makefile precisely so it is never typed from memory.

The production bot uses a separate Telegram token from the dev bot, present only in the server's `.env`. Both bots long-poll outward, so the server needs no inbound HTTPS endpoint, domain, or TLS certificate.

---

## Development workflow and conventions

The project is built in **phases**, each broken into **chunks** that close at commit-ready points. Work proceeds one logical unit at a time; bugs are resolved before advancing.

Conventions that hold throughout:

- **Repositories flush, services commit.** Transaction boundaries live in the service layer.
- **Validate at the boundary.** Every trust boundary (HTTP, config, LLM output) gets a Pydantic model.
- **Enforce at the lowest capable layer.** Rules that can be database constraints are database constraints, often *also* mirrored as Pydantic validators for clean error messages.
- **Static routes before parameterized routes.** `/foods/search` and `/foods/by-barcode/{x}` must be declared before `/foods/{food_id}`, since FastAPI matches in declaration order and a catch-all `{food_id}` would otherwise swallow them.
- **Plain text over Markdown in bot replies that interpolate data.** A food name containing `_` or `*` breaks Telegram's Markdown parser; static help text is the exception.
- **One dependency manifest.** `pyproject.toml` is shared by both services; container builds install the same file the host venv does.

The `Makefile` is the operational interface — read it to discover every supported verb.

---

## Known gotchas

Hard-won, environment-specific, and easy to lose an hour to:

- **Windows + Docker bind-mount hot reload is unreliable.** File-change events get dropped crossing the Windows FS → WSL2 → container boundary, so `watchfiles`/`--reload` sometimes doesn't fire. Use `make restart-api` / `make restart-bot` as the escape hatch.
- **PowerShell writes UTF-16 by default.** `>` redirection and `Out-File` default to UTF-16-LE, which injects null bytes into Python source files and produces `SyntaxError: source code string cannot contain null bytes`. Create/edit files in the editor, or use explicit UTF-8 output.
- **Pydantic v2 field names must not shadow imported types.** A field like `date: date` collides with the `datetime.date` import; under Python 3.14's lazy (PEP 649) annotations this surfaces late as a cryptic "not fully defined" error during OpenAPI generation rather than an immediate `NameError`. Name the field `day` (or alias it), and keep the type imported.
- **Stale images don't auto-rebuild.** Changing `pyproject.toml` or a `Dockerfile` requires an explicit `--build`; otherwise a healthcheck fails with `ModuleNotFoundError` because the new dependency isn't in the running image.
- **Git Bash mangles container paths.** `/app/...` gets rewritten to `C:/Program Files/Git/app/...`. Prefix with `//` (e.g. `//app/...`) or set `MSYS_NO_PATHCONV=1` for the command.
- **Compose env vars must be declared per service.** A key in the host `.env` is not passed into a container unless it appears in that service's `environment` block.

---

## Roadmap

**Done (Phases 0.5–8):** infrastructure bootstrap; schema-in-code with migrations; the FastAPI service and four-layer pattern; the bot skeleton and `/whoami`; the `/log` flow with fuzzy search; LLM free-text parsing; OpenFoodFacts barcode lookup with photo decoding; the read-side commands (`/today`, `/status`, `/week`); body metrics (`/weight`); goals (`/goal`); and the help/command-menu surface.

**Next — Phase 9: analytics.** A dbt-core transformation layer building the `analytics` schema from `app` sources (staging → intermediate → facts → marts), with declarative data tests (null checks, accepted ranges, kcal-vs-macro consistency) and generated lineage, scheduled via cron. Then Grafana dashboards over the analytics schema for kcal history, macro splits, and weight-vs-intake overlays. The operational schema is now stable, which is exactly the precondition that makes the transformation layer worth building — the models won't churn underneath it.

**Later — Phase 10+: production hardening.** A real admin-approval flow (an `/approve` command gated on `is_admin`, replacing the manual psql `is_active` flip); moving `user_id` out of request bodies and resolving it from an auth context; optionally Postgres row-level security as defense in depth; structured logging (structlog); a reverse proxy if webhooks are ever wanted; CI; and migrating dependency management from pip to uv or Poetry.

**Polish, as friction surfaces:** `/yesterday`, `/bodyfat`, normalizing the per-service repository attribute naming, and wiring external-client shutdown (`off_client.aclose()`, `engine.dispose()`) into the FastAPI lifespan.

---
