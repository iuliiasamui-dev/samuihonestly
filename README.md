# samuihonestly.com

A six-page static site for **Koh Samui, Honestly** — a location guide written by a European who has lived on Koh Samui for three years, published as the web home of the TikTok account [@ai.yu0278](https://www.tiktok.com/@ai.yu0278) (8K followers, 100K shares and saves).

**Live:** https://samuihonestly.com

---

## What this is

The account's audience is Western European trip-planners — 21% of TikTok traffic arrives via search, which is unusually high and signals people actively planning rather than passively scrolling. The site exists to serve that intent off-platform: it hands over a free guide, answers the questions those people are actually typing, and builds an audience on a channel that can't be taken away by an algorithm change.

## Architecture

Static HTML, CSS and vanilla JavaScript. **No backend, no database, no build step.**

```
index.html            Hub — links to the four topic pages
things-to-do.html     Beaches, temples, night markets, waterfalls, beach clubs
where-to-stay.html    The five areas compared — the decision that actually matters
where-to-eat.html     Thai kitchens, street food, breakfast, European food
what-to-bring.html    Snacks, tea, balms, crafts
weather.html          Live webcams + month-by-month reality vs the forecast
assets/               Images, PDF guide, shared stylesheet, stats.json
sitemap.xml
robots.txt
```

### Why no framework

At this size a framework would add a build step, a dependency tree and a deployment failure mode, in exchange for nothing a visitor would notice. Static files on a CDN are faster than anything a framework would produce here, cost nothing to host, and cannot break while unattended. The constraint is deliberate, not a shortcut.

### Notable implementation details

- **`assets/stats.json`** — follower and engagement figures are fetched at runtime rather than hardcoded, so the numbers can be updated without touching markup. The HTML carries the same values as a fallback if the fetch fails.
- **Webcams open on YouTube rather than embedding.** The stream owners disabled third-party embedding, and an iframe would have loaded Google cookies on page load — which, with a European audience, would have triggered a consent-banner obligation. Linking out avoids both problems.
- **Hero video** — 6 seconds, silent, 289 KB after compression from 4.8 MB. Falls back to a poster frame when `prefers-reduced-motion` is set.
- **No cookies, no consent banner.** Nothing on the page tracks anyone.

## SEO structure

Each page targets one search intent, because Google ranks pages, not sites.

| Page | Primary intent |
|---|---|
| `weather.html` | koh samui weather right now, live webcam |
| `things-to-do.html` | things to do in koh samui |
| `where-to-stay.html` | where to stay, lamai or chaweng |
| `where-to-eat.html` | where to eat, restaurants, cafés |
| `what-to-bring.html` | what to buy in koh samui |

Implemented: unique keyword-led titles and H1s, meta descriptions under 160 characters, canonical URLs, Open Graph and Twitter cards, JSON-LD (`Article`, `BreadcrumbList`, `FAQPage`, `Person`, `WebSite`), `sitemap.xml`, `robots.txt`, descriptive alt text on all 21 images, and next/previous links between pages.

The competitive reality is that head terms like "things to do in koh samui" belong to TripAdvisor and Lonely Planet and are not winnable by a new domain. The realistic targets are the long tail — live webcams, seasonal specifics, and area comparisons — where the answers online are weak and lived experience wins.

## Content principle

The guide's 300+ places live in the PDF. The pages deliberately carry judgement rather than lists: which beach works in which season, why the east coast goes choppy in winter while the west goes too shallow in summer, why January is busy *and* wet despite what every other guide claims. A list of names would not rank and would not be worth reading.

## Deployment

Cloudflare Pages, connected to this repository. Every push to `main` deploys automatically. No build command, no output directory — the repository root is the site.

Analytics is Cloudflare Web Analytics: cookieless, so no consent banner, and unaffected by the ad-blockers a privacy-conscious European audience tends to run.

## Roadmap

**Phase 2 — paid content.** A paid guide sold through a hosted checkout. Still no backend.

**Phase 3 — own the data pipeline.** Replace third-party aggregates with a first-party event pipeline: a Cloudflare Worker collecting events into R2, Python extractors against the email and payment APIs, orchestration on GitHub Actions, modelling in dbt, serving through a dashboard. The interesting problem is identity stitching — resolving an anonymous session to a subscriber to a buyer, so a single TikTok video can be attributed to revenue. That is the join no analytics tool will hand you.

---

Photography and writing by [@ai.yu0278](https://www.tiktok.com/@ai.yu0278). Webcam streams belong to [The Real Samui Webcam](https://www.youtube.com/@TheRealSamuiWebcam).
