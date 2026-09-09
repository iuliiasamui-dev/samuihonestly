# Phase 3 — own the data pipeline

The goal: stop reading Cloudflare's aggregates and build the thing that produces them.
The portfolio piece is the pipeline, not the numbers in it.

Phase 2 is skipped — payment-provider registration is not workable right now — so the
funnel ends at **email signup**, not purchase. The schema is shaped so a `purchase`
event can slot in later without a rewrite.

```
Browser  ──► /e collector (Cloudflare Worker)  ──► D1  (raw, append-only)
                                                   │
EmailOctopus API ─┐                                │
TikTok CSV export ┴──► Python extractors ──────────┤
                          (GitHub Actions, nightly)│
                                                   ▼
                                       MotherDuck / DuckDB
                                                   │
                                            dbt Core (+ tests)
                                                   │
                                             Evidence.dev
```

**Deployment, as it actually works here:** `samuihonestly` is a **Worker** (not Pages),
its config lives in `wrangler.jsonc` in the GitHub repo, and Cloudflare builds and
deploys on push. So uploading files through the GitHub web UI *is* the deploy — no
terminal needed. The one thing no file can do is bring a database into existence, so
that part happens by clicking in the Cloudflare dashboard, once.

---

# Step 1 — collector + raw layer  ← you are here

## What is already written

| File | What it is |
|---|---|
| `worker/index.js` | **new** — the `/e` endpoint. Validates, adds country, writes to D1. Everything that isn't `/e` is handed straight back to the asset server, so the site behaves exactly as before. |
| `worker/schema.sql` | **new** — `raw_events` (append-only, `event_id` as the idempotency key) and `raw_event_rejects`. |
| `assets/track.js` | **new** — consent banner, visitor/session ids, UTM capture frozen at session start, event wiring. |
| `wrangler.jsonc` | **changed** — adds `main` and the D1 binding. |
| `.assetsignore` | **changed** — keeps `worker/` from being served as a public file. |
| `privacy.html` | **changed** — analytics section rewritten for consent, retention and withdrawal. |
| all 15 `*.html` | **changed** — `<script defer src="/assets/track.js">` before `</body>`. |

---

## A. Create the database — Cloudflare dashboard

1. **dash.cloudflare.com** → left sidebar → **Storage & Databases** → **D1 SQL Database**
2. **Create** → name it exactly `samuihonestly-events` → **Create**
3. On the database's page, copy the **Database ID** (a long hex string). You need it in step C.

## B. Create the tables — same dashboard, no files

4. Open that database → **Console** tab
5. Paste the whole block below and run it

```sql
CREATE TABLE IF NOT EXISTS raw_events (
  event_id      TEXT PRIMARY KEY,
  received_at   TEXT NOT NULL,
  occurred_at   TEXT,
  ingest_day    TEXT NOT NULL,
  event_name    TEXT NOT NULL,
  visitor_id    TEXT,
  session_id    TEXT,
  page_path     TEXT,
  landing_path  TEXT,
  referrer      TEXT,
  utm_source    TEXT,
  utm_medium    TEXT,
  utm_campaign  TEXT,
  utm_content   TEXT,
  utm_term      TEXT,
  country       TEXT,
  device_type   TEXT,
  props         TEXT
);
CREATE INDEX IF NOT EXISTS idx_raw_events_ingest_day ON raw_events (ingest_day);
CREATE INDEX IF NOT EXISTS idx_raw_events_session    ON raw_events (session_id);
CREATE INDEX IF NOT EXISTS idx_raw_events_name       ON raw_events (event_name);
CREATE TABLE IF NOT EXISTS raw_event_rejects (
  reject_id   INTEGER PRIMARY KEY AUTOINCREMENT,
  rejected_at TEXT NOT NULL,
  reason      TEXT NOT NULL,
  payload     TEXT
);
CREATE INDEX IF NOT EXISTS idx_rejects_at ON raw_event_rejects (rejected_at);
```

If the console refuses the whole block, run the statements one at a time in that order.

6. Open the **Tables** tab. You should see `raw_events` and `raw_event_rejects`, both empty.

*(`worker/schema.sql` is the same thing with the reasoning written out. It lives in the
repo as documentation — the console is what actually creates the tables.)*

## C. Put the ID into the config — ✅ already done

`wrangler.jsonc` now carries the real database id:
`952fc342-38a5-427c-9390-e2623b734e72`. Nothing to edit — just make sure this file is
one of the ones you upload in step D.

**Do not** add the D1 binding through the Worker's Settings screen instead. Every deploy
rewrites the Worker's bindings from `wrangler.jsonc`, so a binding added by hand would
be wiped on the next upload and the collector would start failing silently.

## D. Upload to GitHub — the web UI, as you already do

Repo → **Add file** → **Upload files**, then drag these in. Same paths, so they replace
what's there.

**New:**

```
worker/index.js
worker/schema.sql
assets/track.js
PHASE3.md
```

**Changed:**

```
wrangler.jsonc      .assetsignore      privacy.html
index.html          guides.html        weather.html
things-to-do.html   where-to-stay.html where-to-eat.html
what-to-bring.html  when-to-come.html  area-guide.html
4-day-guide.html    thanks.html        thank-you.html
refund-policy.html  terms.html
```

Dragging the `worker` folder itself works in Chrome and keeps the folder structure.
Commit straight to `main`.

## E. Verify — this is the part not to skip

An analytics pipeline that silently collects nothing looks exactly like one that works.

1. **Cloudflare → Workers & Pages → samuihonestly → Deployments.** A new deployment
   should appear within a minute or two of the upload.
   *If nothing appears:* the Worker isn't connected to the repo. Settings → **Build** →
   connect it to `iuliiasamui-dev/samuihonestly`, branch `main`.
2. Open **https://samuihonestly.com/e** in a browser. It should print
   `{"ok":true,"service":"collector"}`. A 404 means the Worker deployed without `main` —
   check that `wrangler.jsonc` actually uploaded.
3. Open the site. The consent banner should appear at the bottom. Click **Allow**,
   click through two pages, and download the guide from `/thanks`.
4. Back in **D1 → Console**:

```sql
SELECT event_name, COUNT(*) AS n FROM raw_events GROUP BY 1 ORDER BY n DESC;
```

Expect `page_view`, `consent_granted`, `pdf_download`.

5. And check nothing is quietly being thrown away:

```sql
SELECT reason, COUNT(*) FROM raw_event_rejects GROUP BY 1;
```

Zero rows is the pass condition.

6. Reload the site once more and confirm the banner does **not** come back — that means
   the consent decision is being remembered rather than re-asked on every page.

---

# Step 2 — make TikTok traffic attributable

Right now every visitor arrives on the same bio link, so no video can be distinguished
from another. Until this is fixed, "which video produced subscribers" has no answer no
matter how good the warehouse is.

- **Cheap:** change the bio link each time you post —
  `samuihonestly.com/?utm_source=tiktok&utm_medium=bio&utm_content=2026-09-09-nathon`
- **Better:** add a `/go/<slug>` route to the Worker that redirects to the site with the
  UTMs attached and logs the click. Then the bio link stays short and the mapping lives
  in code rather than in your memory.

# Step 3 — extractors + orchestration

- `pipeline/extract_events.py` — pull `raw_events` from D1 over the API, `WHERE ingest_day >= watermark`, write Parquet
- `pipeline/extract_emailoctopus.py` — subscribers and status, paginated, incremental
- `pipeline/extract_tiktok.py` — parse the CSV export TikTok gives you
- GitHub Actions on a nightly cron, with secrets and a failure notification

The parts worth writing up honestly: idempotency (re-running yesterday must not
double-count), late-arriving TikTok stats (they settle over ~48h), and subscriber status
as a slowly-changing dimension rather than an overwrite.

# Step 4 — dbt Core on MotherDuck

`staging` → `intermediate` → `marts`, with tests and freshness checks. The centrepiece
model is the identity graph: anonymous `session_id` → `visitor_id` → subscriber,
resolved backwards so a session can be credited to a TikTok video.

# Step 5 — Evidence.dev dashboard

Funnel health, TikTok → site attribution, email list health. Deployed next to the site.

---

## Done when

You can answer *"which video produced subscribers"* from your own warehouse, and your
numbers agree with Cloudflare's within about 20%.
