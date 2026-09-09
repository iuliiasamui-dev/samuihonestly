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

# Step 1 — collector + raw layer ✅ LIVE AND VERIFIED

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

# Step 2 — make TikTok traffic attributable ✅ BUILT AND VERIFIED

TikTok gives a profile one bio link, so every video points at the same URL and no video
can be told from another. `/go/<slug>` fixes that.

**Verified live, 9 September 2026.** Four `bio_click` rows confirmed: an unregistered
slug falling through to the homepage, a registered slug routing to its destination, and
the same slug logged with `registered` flipping false → true either side of the INSERT —
which proves the lookup happens per request rather than being baked in at deploy.

**How the live bio link is wired.** The TikTok bio still says `samuihonestly.com/tt`. A
Cloudflare Redirect Rule sends `/tt` → `/go/tt`, and the Worker takes it from there. This
matters because **Cloudflare redirect rules run at the edge, before Workers execute** — an
earlier version of that rule pointed straight at `/weather` and every click bypassed the
collector entirely, invisibly. If bio clicks ever stop appearing, check that rule first.

Because the bio URL never changes, anyone who saved or screenshotted the old link is
still tracked.

**One extra table to create** — D1 → Console:

```sql
CREATE TABLE IF NOT EXISTS link_targets (
  slug         TEXT PRIMARY KEY,
  dest         TEXT NOT NULL DEFAULT '/',
  utm_campaign TEXT,
  note         TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
```

## How you use it

When you post a video, put this in your TikTok bio, changing the last part:

```
samuihonestly.com/go/nathon-sunset
```

That's it. The slug does not need registering first — an unknown slug still redirects to
the homepage carrying `utm_content=nathon-sunset`, so the link works the moment you
invent it. Register one only when you want it to land somewhere specific:

```sql
INSERT INTO link_targets (slug, dest, utm_campaign, note) VALUES
  ('nathon-sunset', '/where-to-stay', 'area-guides', 'Nathon sunset video, 9 Sep');
```

Changing where an existing slug lands is one statement, live on the next click — no
deploy, no upload, no code change:

```sql
UPDATE link_targets SET dest = '/' WHERE slug = 'tt';
```

That is exactly why this mapping lives in a table rather than in the Worker's source.

Slugs are lowercase letters, digits and hyphens.

**How often to change the slug.** Not every video. You post twice a day, and most of
those — the weather clips, the 6am series — send nobody to a website. Minting 730 slugs a
year, most with single-digit clicks, is noise rather than data, and it turns publishing
into an admin task, which breaks the rule this whole account runs on.

Keep `tt` as the standing link. Swap in a dated slug only when a video has a real call to
action — an area guide, a "where to stay", anything whose overlay tells people to check
the link. Two or three changes a week, and those are the only videos where the answer is
interesting anyway. Everything else lands in `tt`, which is an honest label for general
bio traffic.

## What it records

Every click writes a `bio_click` row **server-side** — no cookie, no visitor id, no IP,
no user-agent. Nothing touches the visitor's device, so it needs no consent and is
counted even for people who decline the banner. That makes it the one number in the whole
pipeline with no consent bias in it, and the honest denominator for everything else:

```sql
-- clicks per video, and how many turned into a consented session
SELECT utm_content AS video,
       SUM(event_name = 'bio_click') AS clicks,
       COUNT(DISTINCT CASE WHEN event_name = 'page_view' THEN session_id END) AS sessions
FROM raw_events
WHERE utm_source = 'tiktok'
GROUP BY 1 ORDER BY clicks DESC;
```

The gap between those two columns is your consent decline rate plus the people who bounce
before the page loads. Worth watching — it is the honest cost of the banner.

---

# Step 3 — TikTok data + orchestration 🔨 STARTED

**Use `pipeline/consolidate_tiktok.py`.** `pipeline/parse_tiktok_csv.py` is **superseded**
— it was written against guessed CSV column names before a real export had been seen, and
TikTok exports `.xlsx`, not `.csv`. Delete it; a plausible-looking script that has never
touched real data is worse than no script.

```
python pipeline/consolidate_tiktok.py "Tiktok downloads" --out "Tiktok downloads/consolidated"
```

## What TikTok actually exports

Studio has a Download button on every tab, but what comes out is narrower than it looks:

| Tab | Grain | Rows in one export |
|---|---|---|
| Overview | one row per day | 364 |
| Viewers | one row per day | 365 |
| FollowerHistory | one row per day | 364 |
| FollowerActivity | day × hour | 168 (7 days only) |
| FollowerGender / TopTerritories | current distribution | 3 / 11 |
| **Content** | one row per video | **only the page of top posts on screen** |

That last row is the catch. There is no "export all videos" — you page through the top
posts table and export each page, which produces many heavily overlapping files. The
first real run took **16 files, 240 rows, and 58 distinct videos**.

## Three outputs, not one

| File | Grain |
|---|---|
| `tiktok_daily.csv` | one row per date — Overview + Viewers + FollowerHistory joined |
| `tiktok_videos.csv` | one row per video, deduplicated |
| `tiktok_snapshots.csv` | gender, territories, hourly activity — long format |

Three because there are three grains. Overview, Viewers and FollowerHistory *do* share a
grain, so joining them into one daily table is a real consolidation. Forcing the video
rows and the distributions in beside them would not be.

## The two problems the script exists to solve

**No year on any date.** Every date reads `September 9` or `March 30`. Parsing that
naively collapses a year of history into one ambiguous year and silently destroys the
ordering. The script walks each series in order, rolls the year at the December → January
boundary, and anchors the result so the series ends on or before the export date.
Video post dates get the same treatment: the most recent occurrence at or before today.

**Duplicate rows that disagree.** The same video appears in up to 16 files, and in the
first run 16 rows disagreed — always by one or two views (`86226` vs `86225`), because
counts were still ticking up while the files were being downloaded. These are monotonic
lifetime counters, so the largest reading is the freshest: max wins. Every disagreement
is printed rather than quietly resolved, because a silent tie-break is how a number you
trust turns out to be arbitrary.

## Known data-quality notes

- **The last day or two are provisional.** TikTok's numbers settle over roughly 48 hours,
  and the tabs do not settle together — the first run had a Viewers row for 8 September
  with no matching Overview or FollowerHistory row.
- **A video older than twelve months will be misdated.** Post dates carry no year, so the
  script assumes the most recent match. Nothing in the export can disambiguate it.
- **Content coverage is only as complete as your paging.** 58 videos is what was on
  screen, not the full archive.

**Where the data lives:** `data/` is in `.gitignore`. This repo is public and the daily
view and follower numbers are not — the follower count on the profile is public, the
day-by-day curve behind it is a different thing. The code is the portfolio artefact; the
numbers do not have to be.

**Consequence, stated plainly:** the GitHub Action in
`.github/workflows/tiktok-csv.yml` cannot work in this repo, because the file it would
parse is never committed. Do not create that workflow here — it would sit green and do
nothing, which is worse than not existing. Two ways to get the automation back:

- **A private data repo.** `samuihonestly-data`, private, holding `data/` plus a copy of
  the parser and the workflow. Uploads and runs work exactly as designed, and the public
  repo keeps the code. Costs you one duplicated Python file.
- **Run it locally instead.** Keep the CSVs in `Social media/data/tiktok/raw/` and parse
  them there. No automation, but nothing to maintain either — reasonable while this is
  one download a week.

Either way the script itself is unchanged. Only where it runs differs.

## Still to do in Step 3

- `pipeline/extract_events.py` — pull `raw_events` from D1 over the API,
  `WHERE ingest_day >= watermark`, write Parquet
- `pipeline/extract_emailoctopus.py` — subscribers and status, paginated, incremental
- Load all three sources into MotherDuck
- A nightly schedule once there is enough moving to justify one

The parts worth writing up honestly when you do: idempotency (re-running yesterday must
not double-count), late-arriving TikTok stats, and subscriber status as a slowly-changing
dimension rather than an overwrite.

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
