# OFAC SDN Directory

**The open reference index for U.S. sanctions data.** Every party on the OFAC SDN list — 19,254 designations, 39,604 names/aliases, 439 crypto wallet addresses — with stable permalinks, full-text search, and structured data.

→ **Live: https://ofac-sdn-directory.vercel.app**

[![Daily Refresh](https://github.com/kindrat86/ofac-sdn-directory/actions/workflows/daily-refresh.yml/badge.svg)](https://github.com/kindrat86/ofac-sdn-directory/actions/workflows/daily-refresh.yml)

---

## What this is

- **Entity pages** (`/sdn/<slug>-<uid>/`) — one permalink per designated party, with JSON-LD, sanctions programs, aliases, and related entities
- **Wallet pages** (`/wallet/<chain>/<address>/`) — one permalink per sanctioned crypto wallet with explorer links and API screening CTA
- **Program hubs** (`/program/<code>/`) — 73 sanctions program pages (e.g. SDGT, RUSSIA-EO14024)
- **Type hubs** (`/type/<individual|entity|vessel|aircraft>/`) — browse by designation type
- **Full-text search** — zero-dependency client-side index (19,694 records, sub-50ms)
- **Funnels to [SanctionsAI](https://sanctionsai.dev)** — every page has API CTAs

## Data source

First-hand from the **U.S. Treasury OFAC list service**, not a GitHub mirror:

| Source | What |
|---|---|
| [SDN.CSV](https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV) | Primary designations |
| [ALT.CSV](https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/ALT.CSV) | Alternate names / aliases |

Cryptocurrency wallet addresses are extracted from the `Remarks` field (col 11) where OFAC publishes them as `Digital Currency Address - <SYMBOL> <address>`.

**Last refreshed:** seen in the data file or on each page.

## Architecture

```
data/ofac-sdn.json    ←  fetched by fetch_data.py from Treasury
src/fetch_data.py     ←   fetcher + wallet/metadata extractor
src/build.py          ←   static site generator (19k+ HTML pages)
src/style.css         ←   design system (matches sanctionsai.dev)
src/search.js         ←   client-side full-text search
dist/                 ←   generated static site (→ deployed to Vercel)
```

**Zero runtime dependencies.** The directory is pure static HTML. The search is in-browser JavaScript (no server roundtrip). Deployment is a static file upload.

## Deploy

### One-time setup

```bash
# 1. Clone
git clone https://github.com/kindrat86/ofac-sdn-directory
cd ofac-sdn-directory

# 2. Install Vercel CLI & link project
npm i -g vercel
vercel login
vercel link                         # creates .vercel/project.json

# 3. Fetch data + build
python3 src/fetch_data.py           # pulls fresh Treasury data
SITE_BASE=https://your-domain.vercel.app python3 src/build.py

# 4. Deploy
vercel deploy dist --prod --yes --archive=tgz
```

### Daily auto-refresh (GitHub Actions)

The included `.github/workflows/daily-refresh.yml` runs at 09:00 UTC every day. It fetches fresh Treasury data, rebuilds, deploys to Vercel, and pings IndexNow.

You need to set these GitHub repository secrets:

| Secret | Where to get it |
|---|---|
| `VERCEL_TOKEN` | https://vercel.com/account/tokens → create token |
| `VERCEL_ORG_ID` | `vercel link` then cat .vercel/project.json |
| `VERCEL_PROJECT_ID` | Same file |

### Custom domain

1. Deploy to Vercel (above)
2. Add domain in Vercel project settings (`ofac-sdn-directory`)
3. Add `CNAME  directory  →  cname.vercel-dns.com` to your DNS
4. Set `SITE_BASE` in the workflow env to `https://directory.sanctionsai.dev`

## Search engine discovery

### Google
1. Verify ownership in [Google Search Console](https://search.google.com/search-console) (add the TXT record Vercel provides)
2. Submit `/sitemap.xml`
3. 19,789 URLs will begin crawling within hours

### Bing / Yandex / Seznam / Naver
Already submitted via **IndexNow**. Key file hosted at `/<key>.txt`. Re-pings on every deploy.

### AI crawlers
`llms.txt`, `robots.txt` explicitly allow GPTBot, ClaudeBot, PerplexityBot, Google-Extended, OAI-SearchBot.

## License

OFAC SDN data is **U.S. public domain** (not subject to domestic copyright). The directory structure, search, and markup are © OFAC SDN Directory.

Underlying data: [CC-PDM 1.0](https://creativecommons.org/publicdomain/mark/1.0/)

## Companion project

This directory answers **"who is on the list"**. To answer **"should this specific payment be blocked?"**, use the [SanctionsAI API](https://sanctionsai.dev) — free tier, no API key, under 100ms.

```bash
curl "https://sanctionsai.dev/sanctions?wallet=0x098B716B8Aaf21512996dC57EB0615e2383E2f96"
```
