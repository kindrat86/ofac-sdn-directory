#!/usr/bin/env python3
"""
build.py — generate a static OFAC SDN directory.

Produces, from data/ofac-sdn.json:
  /                              landing + live search
  /sdn/<slug>-<uid>/             one page per designated entity   (~19k)
  /program/<code>/               one hub per sanctions program     (~70)
  /type/<individual|entity|...>/ type hubs                          (4)
  /search/                       full-text search page
  /about/                        methodology + data provenance
  sitemaps (chunked to 50k), robots.txt, llms.txt, humans.txt

Design tokens mirror sanctionsai.dev (dark, --bg #08090b) so this reads as
a first-party extension of that brand, not a third-party scraper.
"""
import json, os, re, html, math, datetime, hashlib, urllib.parse
from collections import Counter, defaultdict

ROOT     = os.path.dirname(os.path.abspath(__file__))
DATA     = os.path.join(ROOT, "..", "data", "ofac-sdn.json")
DIST     = os.path.join(ROOT, "..", "dist")
# Canonical base — overridable via env so the deployed site matches its real URL.
# Default points at the directory subdomain; set SITE_BASE at deploy time to override.
BASE     = os.environ.get("SITE_BASE", "https://directory.sanctionsai.dev")
SOURCE   = "U.S. Treasury OFAC"

# ── design tokens (mirror sanctionsai.dev) ──────────────────────────────
BG="#08090b"; BG1="#0e1014"; BG2="#14171c"
LINE="#1e222a"; LINE2="#2b313b"
FG="#e9ecf1"; FG2="#b6bec9"; FG3="#7d8693"
ACCENT="#5b8cff"; ACCENT2="#7aa2ff"; GREEN="#3fb950"; RED="#f85149"; AMBER="#d29922"

# ── program code → human label (the ones people actually search) ─────────
PROGRAM_NAMES = {
    "RUSSIA-EO14024": "Russia Sanctioins (EO 14024)",
    "SDGT": "Global Terrorism — Specially Designated Global Terrorists",
    "IFSR": "Iran Financial Sanctions Regulations",
    "SDNTK": "Narcotics Trafficking — Kingpin Act",
    "NPWMD": "Non-Proliferation of Weapons of Mass Destruction",
    "IRAN-EO13902": "Iran — EO 13902",
    "GLOMAG": "Global Magnitsky Act",
    "ILLICIT-DRUGS-EO14059": "Illicit Drug Trafficking (EO 14059)",
    "IRAN": "Iran Transactions Regulations",
    "UKRAINE-EO13662": "Ukraine — EO 13662",
    "IRAN-EO13846": "Iran — EO 13846",
    "TCO": "Transnational Criminal Organizations",
    "IRGC": "Islamic Revolutionary Guard Corps (IRGC)",
    "CYBER2": "Significant Malicious Cyber-Enabled Activities",
    "IRAN-HR": "Iran Human Rights Abuses",
    "UKRAINE-EO13661": "Ukraine — EO 13661",
    "DPRK4": "North Korea — DPRK4",
    "IRAQ2": "Iraq Stabilization and Insurgency",
    "BELARUS-EO14038O": "Belarus — EO 14038O",
    "VENEZUELA-EO13850": "Venezuela — EO 13850",
    "UKRAINE-EO13660": "Ukraine — EO 13660",
    "BALKANS": "Western Balkans Stabilization",
    "DPRK3": "North Korea — DPRK3 (Weapons of Mass Destruction Proliferators)",
    "BURMA-EO14014": "Burma — EO 14014",
    "SDNT": "Narcotics Trafficking (SDNT)",
    "DPRK2": "North Korea — DPRK2",
    "IRAN-EO13876": "Iran — EO 13876",
    "ELECTION-EO13848": "Foreign Interference in U.S. Elections (EO 13848)",
    "FTO": "Foreign Terrorist Organizations",
    "UKRAINE-EO13685": "Ukraine — EO 13685",
    "DRCONGO": "Democratic Republic of the Congo",
    "CYBER4": "Cyber-Related Sanctions (CYBER4)",
    "CAATSA - RUSSIA": "CAATSA — Russia",
    "NICARAGUA": "Nicaragua",
    "CUBA": "Cuba Sanctions",
    "BELARUS": "Belarus Sanctions",
    "DPRK": "North Korea — DPRK (Korea, Democratic People's Republic of)",
    "VENEZUELA-EO13884": "Venezuela — EO 13884",
    "VENEZUELA": "Venezuela Sanctions",
    "SUDAN": "Sudan Sanctions",
    "SYRIA": "Syria Sanctions",
    "ZIMBABWE": "Zimbabwe Sanctions",
    "MAGNITSKY": "Global Magnitsky Act (Stand-alone)",
    "UNITED-STATES-EO13888": "Immigration Sanctions (EO 13888)",
    "LEBANON": "Lebanon Sanctions",
    "SOUTH-SUDAN": "South Sudan Stabilization",
    "LIBYA3": "Libya Sanctions",
    "LIBYA2": "Libya Sanctions",
    "YEMEN": "Yemen-Related Sanctions",
    "MEDI": "MidEast Economic Sanctions",
    "HRIT-EO13818": "Human Rights Abuses & Corruption (EO 13818)",
    "NSS": "National Security Sanctions",
    "FOREIGHTERRORISM-EO13224": "Terrorism — EO 13224",
    "UKRAINE-EO13685": "Ukraine — EO 13685",
    "PAARSSR-EO13894": "Syria — Protecting Allies (EO 13894)",
    "MBS-EO14023": "Ethiopia — EO 14023",
}
TYPE_LABEL = {
    "individual": "Designated Individual",
    "entity":     "Designated Entity / Organization",
    "vessel":     "Designated Vessel",
    "aircraft":   "Designated Aircraft",
}

def program_label(code):
    return PROGRAM_NAMES.get(code, code.replace("-", " ").replace("_", " ").title())

# ── slug helpers ────────────────────────────────────────────────────────
_slug_strip = re.compile(r"[^a-z0-9]+")
def slugify(s):
    s = (s or "").lower().replace("&", " and ")
    s = _slug_strip.sub("-", s).strip("-")
    # collapse runs but keep it short
    return (s[:70]).rstrip("-")

def entity_path(e):
    return f"/sdn/{slugify(e['name'])}-{e['uid']}/"

def wallet_path(w):
    # Wallet URLs: /wallet/<chain-slug>/<address>/
    # The chain is part of the path to disambiguate (same address can exist
    # on TRX and ETH when it's a Tron wrapped token).
    return f"/wallet/{slugify(w['chain'])}/{w['address']}/"

def wallet_chain_path(sym):
    return f"/wallets/{slugify(sym)}/"

def program_path(code):
    return f"/program/{slugify(code)}/"

def type_path(t):
    return f"/type/{t}/"

# ── html scaffolding ────────────────────────────────────────────────────
def esc(s):
    return html.escape(s or "", quote=True)

def page(title, desc, canonical, body, extra_head="", jsonld=None, body_class=""):
    """Full HTML document. Lean, fast, indexable."""
    ld = ""
    if jsonld:
        blocks = jsonld if isinstance(jsonld, list) else [jsonld]
        ld = "\n".join(
            '<script type="application/ld+json">' + json.dumps(b, ensure_ascii=False) + '</script>'
            for b in blocks
        )
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="{BG}">
<meta name="color-scheme" content="dark">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{canonical}">
<link rel="preconnect" href="https://sanctionsai.dev">
<link rel="alternate" type="application/rss+xml" title="OFAC SDN additions feed" href="{BASE}/feed.xml">
<meta property="og:type" content="website">
<meta property="og:site_name" content="OFAC SDN Directory">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{canonical}">
<meta property="og:image" content="{BASE}/og.png">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{esc(title)}">
<meta name="twitter:description" content="{esc(desc)}">
<meta name="twitter:image" content="{BASE}/og.png">
{ld}
{extra_head}
<style>{CSS}</style>
</head><body class="{body_class}">
<header class="nav">
  <a href="/" class="brand"><span class="brand-dot"></span> OFAC SDN Directory</a>
  <nav>
    <a href="/search/">Search</a>
    <a href="/type/individual/">Individuals</a>
    <a href="/type/entity/">Entities</a>
    <a href="/type/vessel/">Vessels</a>
    <a href="/wallets/">Wallets</a>
    <a href="/about/">About</a>
  </nav>
  <a class="nav-cta" href="https://sanctionsai.dev/sanctions?wallet=0x098B716B8Aaf21512996dC57EB0615e2383E2f96">Screen a wallet →</a>
</header>
<main>
{body}
</main>
<footer class="site-foot">
  <div class="foot-inner">
    <div>
      <strong>OFAC SDN Directory</strong>
      <p>A free, machine-readable reference index of the U.S. Treasury OFAC Specially Designated Nationals list. {TOTAL_ENTRIES:,} designated parties, refreshed daily from the primary Treasury source.</p>
      <p class="muted">Data source: <a href="https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV">U.S. Treasury OFAC SDN.CSV</a>. Not legal advice. Verify any match against the <a href="https://sanctionssearch.ofac.treas.gov/">official OFAC search</a>.</p>
    </div>
    <div class="foot-links">
      <a href="https://sanctionsai.dev">← back to SanctionsAI</a>
      <a href="https://sanctionsai.dev/data/ofac-sdn-list/">Bulk JSON/CSV</a>
      <a href="/llms.txt">llms.txt</a>
      <a href="/about/">Methodology</a>
    </div>
  </div>
</footer>
<script>{JS}</script>
</body></html>
"""

# ── shared styles + script (written below to keep this readable) ─────────
CSS = ""
JS  = ""

# ── build entry, program, type pages ────────────────────────────────────
TOTAL_ENTRIES = 0
LIST_DATE     = ""

def entity_jsonld(e, canonical):
    """Schema.org structured data so Google understands this is a reference
    entry about a sanctioned party."""
    name = e["name"]
    alt  = e.get("alternateNames", [])
    desc = (f"{name} is designated on the U.S. OFAC Specially Designated "
            f"Nationals (SDN) list under program(s): "
            + ", ".join(program_label(p) for p in e["programs"]) + ". "
            f"Type: {TYPE_LABEL.get(e['type'], e['type'])}. OFAC UID {e['uid']}.")
    return [{
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": f"{name} — OFAC SDN designated party",
        "description": desc,
        "url": canonical,
        "creator": {"@type": "Organization", "name": "U.S. Treasury OFAC"},
        "keywords": ["OFAC", "SDN", "sanctions"] + e["programs"],
        "isAccessibleForFree": True,
        "license": "https://creativecommons.org/publicdomain/mark/1.0/",
    },
    {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type":"ListItem","position":1,"name":"Directory","item":BASE+"/"},
            {"@type":"ListItem","position":2,"name":TYPE_LABEL.get(e['type'], e['type']),
             "item":BASE+type_path(e['type'])},
            {"@type":"ListItem","position":3,"name":name,"item":canonical},
        ],
    }]

def render_entity(e, related_by_program):
    name = e["name"]
    canon = BASE + entity_path(e)
    type_lab = TYPE_LABEL.get(e["type"], e["type"])
    prog_labels = [(p, program_label(p), BASE+program_path(p)) for p in e["programs"]]
    akas = e.get("alternateNames", [])
    primary_prog = e["programs"][0] if e["programs"] else None

    # H1 + meta description carry the highest-intent keywords
    title = f"{name} — OFAC SDN List Designation (UID {e['uid']})"
    desc  = (f"Is {name} on the OFAC sanctions list? Yes — designated under "
             + ", ".join(program_label(p) for p in e["programs"][:3])
             + f". OFAC SDN UID {e['uid']}, type: {e['type']}. "
             + (f"{len(akas)} known aliases. " if akas else "")
             + "Free lookup, no signup.")

    # related: same program, different uid, up to 8
    related = []
    if primary_prog:
        for r in related_by_program.get(primary_prog, []):
            if r["uid"] != e["uid"]:
                related.append(r)
            if len(related) >= 8: break

    aka_block = ""
    if akas:
        aka_block = '<section class="card"><h2>Known aliases / A.K.A.s</h2><ul class="aka-list">' + \
            "".join(f"<li>{esc(a)}</li>" for a in akas) + "</ul></section>"

    prog_block = ""
    if prog_labels:
        prog_block = '<section class="card"><h2>Sanctions programs</h2><ul class="tag-list">' + \
            "".join(f'<li><a href="{u}">{esc(lab)}</a></li>' for _,lab,u in prog_labels) + \
            "</ul></section>"

    related_block = ""
    if related:
        related_block = '<section class="card"><h2>Other designations under the same program</h2><ul class="related-list">' + \
            "".join(
                f'<li><a href="{entity_path(r)}">{esc(r["name"])}</a> '
                f'<span class="muted">{r["type"]}</span></li>'
                for r in related
            ) + "</ul></section>"

    body = f"""
<section class="hero">
  <p class="crumbs"><a href="/">Directory</a> › <a href="{type_path(e['type'])}">{esc(type_lab)}</a></p>
  <span class="badge badge-red">● OFAC SDN Designated</span>
  <h1>{esc(name)}</h1>
  <p class="lede">{esc(type_lab)} &middot; OFAC UID <code>{e['uid']}</code> &middot; {len(akas)} alias{'es' if len(akas)!=1 else ''}</p>
  <p class="meta">Listed under: {', '.join(esc(program_label(p)) for p in e['programs']) or '—'}<br>
  Data source: U.S. Treasury OFAC SDN list &middot; retrieved {LIST_DATE}</p>
</section>

<section class="cta-row">
  <a class="btn btn-primary" href="https://sanctionsai.dev/sanctions?name={urllib.parse.quote(name)}">Screen this name in the API →</a>
  <a class="btn" href="/search/?q={urllib.parse.quote(name)}">Search for related parties</a>
</section>

{prog_block}
{aka_block}
{related_block}

<section class="card card-factbox">
  <h2>What this designation means</h2>
  <p>Being on the OFAC SDN list means U.S. persons are generally prohibited from
  dealing with this party, and any assets in U.S. jurisdiction are blocked.
  The maximum civil penalty for a single OFAC violation is <strong>$377,700</strong>
  or twice the transaction value, whichever is greater (31 CFR 501.701, adjusted
  January 2025).</p>
  <p>This page is a reference index of public OFAC data. It is not legal advice.
  For binding determinations, use the <a href="https://sanctionssearch.ofac.treas.gov/">official OFAC search</a>
  or consult counsel.</p>
</section>

<section class="card card-api">
  <h2>Check this party programmatically</h2>
  <pre><code>curl "https://sanctionsai.dev/sanctions?name={urllib.parse.quote(name)}"</code></pre>
  <p class="muted">Free tier, no API key. Returns a JSON match/no-match in &lt;100ms.</p>
</section>
"""

    # Wallet cross-references: link to wallet pages if this entity has addresses
    ewallets = e.get("wallets", [])
    if ewallets:
        wallet_rows = "".join(
            f'<li><a href="{wallet_path(w)}"><code>{esc(w["address"][:18])}…</code></a>'
            f'<span class="row-meta">{w["chain"]} ({w["symbol"]})</span></li>'
            for w in ewallets[:10]
        )
        suffix = f'<p class="muted" style="margin-top:10px"><a href="/wallets/">Browse all OFAC-designated wallets →</a></p>' if len(ewallets) > 10 else ""
        body += f'<section class="card"><h2>Related crypto wallets ({len(ewallets)})</h2><ul class="dir-list">{wallet_rows}</ul>{suffix}</section>'

    return page(title, desc, canon, body, jsonld=entity_jsonld(e, canon))

def render_program(code, count, members, type_counts):
    canon = BASE + program_path(code)
    lab = program_label(code)
    title = f"{lab} — {count:,} OFAC SDN designations"
    desc = (f"The '{code}' OFAC sanctions program designates {count:,} parties "
            f"on the SDN list. Browse all {lab} designations — individuals, "
            f"entities, vessels and aircraft — with UIDs and aliases.")

    # paginate member list inline (render up to 300, link to search for the rest)
    shown = members[:300]
    list_html = "".join(
        f'<li><a href="{entity_path(m)}" class="row-name">{esc(m["name"])}</a>'
        f'<span class="row-meta">{m["type"]} · UID {m["uid"]}'
        + (f' · {len(m.get("alternateNames",[]))} alias(es)' if m.get("alternateNames") else '')
        + '</span></li>'
        for m in shown
    )
    more = ""
    if len(members) > len(shown):
        more = (f'<p class="muted">Showing {len(shown)} of {len(members):,}. '
                f'<a href="/search/?program={code}">View all {len(members):,} →</a></p>')

    type_breakdown = " · ".join(f"{t}: {n:,}" for t,n in type_counts.most_common())

    body = f"""
<section class="hero">
  <p class="crumbs"><a href="/">Directory</a> › Programs</p>
  <span class="badge">Program {esc(code)}</span>
  <h1>{esc(lab)}</h1>
  <p class="lede">{count:,} designated {('party' if count==1 else 'parties')} on the OFAC SDN list under <code>{esc(code)}</code></p>
  <p class="meta">Breakdown: {esc(type_breakdown)}</p>
</section>

<section class="card">
  <h2>All designations under {esc(lab)}</h2>
  <ul class="dir-list">{list_html}</ul>
  {more}
</section>

<section class="cta-row">
  <a class="btn btn-primary" href="https://sanctionsai.dev/sanctions?program={urllib.parse.quote(code)}">Screen against this program in the API →</a>
  <a class="btn" href="/search/?program={urllib.parse.quote(code)}">Search within program</a>
</section>
"""
    jsonld = {
        "@context":"https://schema.org","@type":"CollectionPage",
        "name":title,"description":desc,"url":canon,
        "isPartOf":{"@type":"WebSite","name":"OFAC SDN Directory","url":BASE+"/"},
    }
    return page(title, desc, canon, body, jsonld=jsonld)

def render_type_hub(t, members, total):
    canon = BASE + type_path(t)
    lab = TYPE_LABEL.get(t, t)
    title = f"{lab}s on the OFAC SDN list — {len(members):,} designations"
    desc = (f"Browse all {len(members):,} {lab.lower()}s designated on the U.S. OFAC "
            f"SDN list. Each entry has its OFAC UID, sanctions program, and known aliases.")
    shown = members[:200]
    list_html = "".join(
        f'<li><a href="{entity_path(m)}" class="row-name">{esc(m["name"])}</a>'
        f'<span class="row-meta">UID {m["uid"]}'
        + (f' · {", ".join(m["programs"][:2])}' if m.get("programs") else '')
        + '</span></li>'
        for m in shown
    )
    more = (f'<p class="muted">Showing {len(shown)} of {len(members):,}. '
            f'<a href="/search/?type={t}">View all →</a></p>') if len(members)>len(shown) else ""
    body = f"""
<section class="hero">
  <p class="crumbs"><a href="/">Directory</a> › {esc(lab)}s</p>
  <h1>{esc(lab)}s on the OFAC SDN list</h1>
  <p class="lede">{len(members):,} designated {lab.lower()}s out of {total:,} total SDN entries</p>
</section>
<section class="card"><h2>Browse {esc(lab)}s</h2><ul class="dir-list">{list_html}</ul>{more}</section>
"""
    return page(title, desc, canon, body)

# ── landing + search + about + wallets hub ─────────────────────────

def render_wallets_hub(wallets_by_chain, wallet_count):
    canon = BASE + "/wallets/"
    title = f"OFAC sanctioned crypto wallets — {wallet_count:,} addresses"
    desc = (f"Browse all {wallet_count:,} crypto wallet addresses designated "
            f"on the U.S. OFAC SDN list, organized by chain. ")
    chain_cards = ""
    for sym, wlist in sorted(wallets_by_chain.items(), key=lambda x: -len(x[1])):
        cname = wlist[0]["chain"]
        entities = len(set(w["uid"] for w in wlist))
        chain_cards += (f'<li><a href="{wallet_chain_path(sym)}" class="row-name">{esc(cname)} '
                        f'<span class="row-stat">{len(wlist)} wallets · {entities} entities</span></a></li>')
    body = f"""
<section class="hero">
  <p class="crumbs"><a href="/">Directory</a></p>
  <h1>OFAC sanctioned crypto wallets</h1>
  <p class="lede">{wallet_count:,} cryptocurrency addresses designated on the U.S. OFAC SDN list across multiple chains, sourced directly from the Treasury</p>
</section>
<section class="card"><h2>Browse by chain</h2><ul class="dir-list">{chain_cards}</ul></section>
<section class="cta-row">
  <a class="btn btn-primary" href="https://sanctionsai.dev/tools/wallet-checker">Check any wallet →</a>
  <a class="btn" href="https://sanctionsai.dev/sanctions?wallet=0x098B716B8Aaf21512996dC57EB0615e2383E2f96">Screen via API</a>
</section>
"""
    return page(title, desc, canon, body)

def render_landing(prog_counts, type_counts, top_programs, sample, wallet_count, wallets_by_chain):
    canon = BASE + "/"
    title = "OFAC SDN Directory — search 19,000+ designated parties"
    desc = ("Free, searchable directory of every party on the U.S. OFAC "
            "Specially Designated Nationals (SDN) list. Individuals, entities, "
            "vessels, and aircraft — with UIDs, programs, and aliases. "
            f"{wallet_count} crypto wallets across multiple chains.")
    prog_chips = "".join(
        f'<a class="chip" href="{program_path(c)}">{esc(program_label(c))} <span>{n:,}</span></a>'
        for c,n in top_programs[:18]
    )
    type_chips = "".join(
        f'<a class="chip chip-type" href="{type_path(t)}">{esc(TYPE_LABEL.get(t,t))}s <span>{n:,}</span></a>'
        for t,n in type_counts.most_common()
    )
    # wallet chain chips
    wallet_chips = ""
    if wallets_by_chain:
        top_chains = sorted(wallets_by_chain.items(), key=lambda x: -len(x[1]))[:12]
        wallet_chips = "".join(
            f'<a class="chip chip-wallet" href="{wallet_chain_path(sym)}">{esc(wlist[0]["chain"])} <span>{len(wlist):,}</span></a>'
            for sym, wlist in top_chains
        )
    sample_rows = "".join(
        f'<li><a href="{entity_path(e)}">{esc(e["name"])}</a>'
        f'<span class="muted"> · {", ".join(e["programs"][:2])} · UID {e["uid"]}</span></li>'
        for e in sample
    )
    body = f"""
<section class="hero hero-home">
  <p class="eyebrow">The open reference index for U.S. sanctions data</p>
  <h1>Search the entire OFAC SDN list</h1>
  <p class="lede">{TOTAL_ENTRIES:,} designated parties · 39,000+ names &amp; aliases · refreshed daily from the U.S. Treasury</p>
  <form action="/search/" class="search-box" role="search">
    <input name="q" type="search" placeholder="Search a name, alias, entity, vessel…" aria-label="Search the OFAC SDN list" autofocus>
    <button type="submit">Search</button>
  </form>
  <div class="type-chips">{type_chips}</div>
</section>

<section class="home-stats">
  <div><strong>{TOTAL_ENTRIES:,}</strong><span>SDN designations</span></div>
  <div><strong>39,604</strong><span>names &amp; aliases</span></div>
  <div><strong>{wallet_count}</strong><span>crypto wallets</span></div>
  <div><strong>{len(prog_counts)}</strong><span>sanctions programs</span></div>
</section>

<section class="card">
  <h2>Browse by sanctions program</h2>
  <div class="chips">{prog_chips}</div>
</section>

<section class="card">
  <h2>OFAC-designated crypto wallets by chain</h2>
  <div class="chips">{wallet_chips}</div>
</section>

<section class="card">
  <h2>Recently viewed / featured</h2>
  <ul class="dir-list">{sample_rows}</ul>
</section>

<section class="card card-why">
  <h2>Why a directory?</h2>
  <p>The OFAC SDN list is public, but the Treasury's own search is a single
  text box with no permalinks. Every compliance officer, journalist, lawyer,
  and crypto investigator who needs to reference a specific designation ends
  up on a random blog. This directory gives every one of the {TOTAL_ENTRIES:,}
  designated parties a stable, citable URL — and a fast, free lookup that
  feeds the <a href="https://sanctionsai.dev">SanctionsAI</a> screening API.</p>
</section>
"""
    jsonld = [{
        "@context":"https://schema.org","@type":"WebSite","name":"OFAC SDN Directory",
        "url":BASE+"/","description":desc,
        "potentialAction":{
            "@type":"SearchAction",
            "target":{"@type":"EntryPoint","urlTemplate":BASE+"/search/?q={search_term_string}"},
            "query-input":"required name=search_term_string",
        },
    },{
        "@context":"https://schema.org","@type":"Organization",
        "name":"OFAC SDN Directory","url":BASE+"/",
        "parentOrganization":{"@type":"Organization","name":"SanctionsAI","url":"https://sanctionsai.dev"},
    }]
    return page(title, desc, canon, body, jsonld=jsonld, body_class="home")

def render_search():
    canon = BASE + "/search/"
    body = f"""
<section class="hero">
  <h1>Search the OFAC SDN list</h1>
  <p class="lede">{TOTAL_ENTRIES:,} designated parties and 39,000+ aliases, full-text.</p>
  <form class="search-box" onsubmit="return doSearch(event)" role="search">
    <input id="q" type="search" placeholder="Name, alias, entity, vessel, UID…" aria-label="Search query">
    <button type="submit">Search</button>
  </form>
  <div id="filter-row" class="filter-row">
    <select id="ftype"><option value="">All types</option>
      <option value="individual">Individuals</option><option value="entity">Entities</option>
      <option value="vessel">Vessels</option><option value="aircraft">Aircraft</option></select>
    <select id="fprog"><option value="">All programs</option></select>
  </div>
</section>
<section class="card"><h2 id="result-head">Type to search…</h2>
  <p id="result-count" class="muted"></p>
  <ul id="results" class="dir-list"></ul>
  <div id="pager" class="pager"></div>
</section>
"""
    return page("Search the OFAC SDN list — full text", "Full-text search across every OFAC SDN designation and alias.", canon, body, extra_head='<script src="/search-index.js" defer></script>')

def render_about():
    canon = BASE + "/about/"
    title = "About — methodology & data provenance"
    desc = "How the OFAC SDN Directory is built: data sources, refresh cadence, licensing, and limitations."
    body = f"""
<section class="hero">
  <h1>About this directory</h1>
  <p class="lede">A free, machine-readable reference index of U.S. sanctions data.</p>
</section>
<section class="card"><h2>Data source</h2>
  <p>Every record is sourced first-hand from the U.S. Treasury OFAC list service:</p>
  <ul>
    <li><a href="https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV">SDN.CSV</a> — primary designations</li>
    <li><a href="https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/ALT.CSV">ALT.CSV</a> — alternate names / aliases</li>
  </ul>
  <p>Same source the official <a href="https://sanctionssearch.ofac.treas.gov/">OFAC search</a> uses; fetched directly, not via a third-party mirror.</p>
</section>
<section class="card"><h2>Refresh cadence</h2>
  <p>The Treasury publishes updates frequently (often multiple times per week). This directory rebuilds daily. The <code>retrieved</code> date on each page reflects the last fetch.</p>
</section>
<section class="card"><h2>URL stability</h2>
  <p>Every designated party has a permanent URL of the form <code>/sdn/&lt;slug&gt;-&lt;uid&gt;/</code>, keyed on OFAC's own UID. UIDs are stable across list updates, so permalinks do not break.</p>
</section>
<section class="card"><h2>Licensing & attribution</h2>
  <p>OFAC SDN data is <a href="https://www.treas.gov/foia">public U.S. government data</a>, not subject to domestic copyright. This directory adds structure, search, and stable URLs. Page markup is &copy; OFAC SDN Directory; the underlying data is public domain (<a href="https://creativecommons.org/publicdomain/mark/1.0/">CC-PDM 1.0</a>).</p>
</section>
<section class="card"><h2>Relationship to SanctionsAI</h2>
  <p>This directory is a companion reference site to <a href="https://sanctionsai.dev">SanctionsAI</a>, the OFAC screening API for AI agents. The directory answers "who is on the list"; the API answers "should this specific payment be blocked?"</p>
</section>
<section class="card"><h2>Limitations</h2>
  <p>This is a reference tool, not legal advice. The OFAC list changes constantly. For any binding determination (a real payment, a real compliance decision), always verify against the <a href="https://sanctionssearch.ofac.treas.gov/">official Treasury search</a> and consult qualified counsel.</p>
</section>
"""
    return page(title, desc, canon, body)

# ── wallet pages ────────────────────────────────────────────────────────
def render_wallet(w):
    canon = BASE + wallet_path(w)
    chain_lab = w["chain"]
    sym = w["symbol"]
    addr = w["address"]
    e_path = entity_path({"uid": w["uid"], "name": w["entity_name"]})

    title = f"OFAC sanctioned {chain_lab} wallet {addr[:10]}… — UID {w['uid']}"
    desc = (f"This {chain_lab} address ({addr}) belongs to OFAC-designated party "
            f"'{w['entity_name']}' (UID {w['uid']}) under program(s) "
            + ", ".join(w["programs"][:3])
            + ". Any U.S. person transacting with this address risks a $377,700 fine. "
            "Free lookup, no signup.")

    prog_links = " · ".join(
        f'<a href="{program_path(p)}">{esc(program_label(p))}</a>'
        for p in w["programs"]
    ) or "—"

    explorer_link = ""
    if w.get("explorer"):
        explorer_link = (f'<a href="{w["explorer"]}" rel="nofollow noopener" '
                        f'class="btn">View on {chain_lab} explorer ↗</a>')

    body = f"""
<section class="hero">
  <p class="crumbs"><a href="/">Directory</a> › <a href="{wallet_chain_path(sym)}">OFAC {chain_lab} wallets</a></p>
  <span class="badge badge-red">● OFAC SDN Designated — {chain_lab}</span>
  <h1>Sanctioned <code>{esc(addr[:20])}…</code></h1>
  <p class="lede">This {chain_lab} wallet belongs to <a href="{e_path}"><strong>{esc(w['entity_name'])}</strong></a> — a designated party on the U.S. OFAC SDN list (UID {w['uid']}).</p>
  <p class="meta">Programs: {prog_links}<br>Data: U.S. Treasury OFAC SDN list · retrieved {LIST_DATE}</p>
</section>

<section class="card" style="overflow-wrap:anywhere;word-break:break-all">
  <h2>Wallet details</h2>
  <table class="info-table">
    <tr><td>Address</td><td><code class="addr-code">{esc(addr)}</code></td></tr>
    <tr><td>Chain</td><td>{esc(chain_lab)} ({sym})</td></tr>
    <tr><td>Designated entity</td><td><a href="{e_path}">{esc(w['entity_name'])}</a></td></tr>
    <tr><td>Entity type</td><td>{esc(TYPE_LABEL.get(w['entity_type'], w['entity_type']))}</td></tr>
    <tr><td>OFAC UID</td><td>{w['uid']}</td></tr>
    <tr><td>Sanctions programs</td><td>{prog_links}</td></tr>
  </table>
</section>

<section class="cta-row">
  <a class="btn btn-primary" href="https://sanctionsai.dev/sanctions?wallet={urllib.parse.quote(addr)}">Screen this wallet via API →</a>
  {explorer_link}
  <a class="btn" href="https://sanctionsai.dev/tools/wallet-checker">Use the SanctionsAI wallet checker</a>
</section>

<section class="card card-factbox">
  <h2>What this means</h2>
  <p>Any U.S. person who transacts with a wallet belonging to an OFAC-designated party
  is in violation of U.S. sanctions law. The maximum civil penalty for one violation
  is <strong>$377,700</strong> or twice the transaction value, whichever is greater
  (31 CFR 501.701, adjusted January 2025). Criminal penalties can reach 20 years
  imprisonment and $1 million in fines.</p>
  <p>Before sending any payment, screen the counterparty address. The
  <a href="https://sanctionsai.dev">SanctionsAI API</a> checks every wallet
  against the full OFAC SDN list in under 100ms.</p>
</section>
"""
    jsonld = [{
        "@context":"https://schema.org","@type":"WebApplication",
        "name":f"OFAC screening for {chain_lab} wallet {addr[:12]}…",
        "description":desc,"url":canon,
        "applicationCategory":"FinanceApplication",
        "offers":{"@type":"Offer","price":"0","priceCurrency":"USD"},
    },{
        "@context":"https://schema.org","@type":"BreadcrumbList",
        "itemListElement":[
            {"@type":"ListItem","position":1,"name":"Directory","item":BASE+"/"},
            {"@type":"ListItem","position":2,"name":f"OFAC {chain_lab} wallets","item":BASE+wallet_chain_path(sym)},
            {"@type":"ListItem","position":3,"name":f"Wallet {addr[:12]}…","item":canon},
        ],
    }]
    return page(title, desc, canon, body, jsonld=jsonld)

def render_wallet_chain(sym, wallets, chain_name):
    canon = BASE + wallet_chain_path(sym)
    title = f"OFAC sanctioned {chain_name} wallets — {len(wallets):,} addresses"
    desc = (f"All {len(wallets):,} {chain_name} wallet addresses designated "
            f"on the U.S. OFAC SDN list. Each address is linked to its designated "
            f"entity with UID and sanctions programs. Free lookup, no signup.")
    rows = "".join(
        f'<li><a href="{wallet_path(w)}" class="row-name"><code>{esc(w["address"][:16])}…</code></a>'
        f'<span class="row-meta">{esc(w["entity_name"])} · UID {w["uid"]}</span></li>'
        for w in sorted(wallets, key=lambda w: w["entity_name"].lower())
    )
    body = f"""
<section class="hero">
  <p class="crumbs"><a href="/">Directory</a> › Wallets</p>
  <h1>OFAC sanctioned {esc(chain_name)} wallets</h1>
  <p class="lede">{len(wallets):,} {chain_name} addresses linked to OFAC SDN designations</p>
</section>
<section class="card"><h2>All {esc(chain_name)} wallets</h2><ul class="dir-list">{rows}</ul></section>
<section class="cta-row">
  <a class="btn btn-primary" href="https://sanctionsai.dev/tools/wallet-checker">Check any wallet with SanctionsAI →</a>
</section>
"""
    return page(title, desc, canon, body)

# ── sitemaps + auxiliary files ──────────────────────────────────────────
def write_file(relpath, content):
    # paths come in absolute ("/sdn/..."); strip leading slash so os.path.join
    # keeps them under DIST instead of treating them as filesystem-rooted.
    relpath = relpath.lstrip("/")
    full = os.path.join(DIST, relpath)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    if isinstance(content, str):
        content = content.encode("utf-8")
    with open(full, "wb") as f:
        f.write(content)

def build_sitemaps(urls):
    """Chunk to 50,000 urls per file (Google's limit) + a sitemap index."""
    CHUNK = 50000
    files = []
    today = datetime.date.today().isoformat()
    for i in range(0, len(urls), CHUNK):
        chunk = urls[i:i+CHUNK]
        name = f"sitemap-{i//CHUNK}.xml" if len(urls) > CHUNK else "sitemap.xml"
        body = ['<?xml version="1.0" encoding="UTF-8"?>',
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
        for u, prio in chunk:
            body.append(f"<url><loc>{u}</loc><lastmod>{today}</lastmod>"
                        f"<changefreq>weekly</changefreq><priority>{prio}</priority></url>")
        body.append("</urlset>")
        write_file(name, "\n".join(body))
        files.append(name)
    if len(files) > 1:
        idx = ['<?xml version="1.0" encoding="UTF-8"?>',
               '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
        for name in files:
            idx.append(f"<sitemap><loc>{BASE}/{name}</loc><lastmod>{today}</lastmod></sitemap>")
        idx.append("</sitemapindex>")
        write_file("sitemap.xml", "\n".join(idx))

# ── main ────────────────────────────────────────────────────────────────
def main():
    global TOTAL_ENTRIES, LIST_DATE, CSS, JS
    with open(DATA) as f:
        doc = json.load(f)
    entries   = doc["entries"]
    TOTAL_ENTRIES = len(entries)
    LIST_DATE = doc.get("retrieved", datetime.date.today().isoformat())

    # load CSS/JS templates (kept in separate files for editing sanity)
    with open(os.path.join(ROOT, "style.css")) as f: CSS = f.read()
    with open(os.path.join(ROOT, "search.js")) as f:  JS  = f.read()

    # pre-index
    print(f"Building {TOTAL_ENTRIES:,} entity pages…", flush=True)
    type_counts = Counter(e["type"] for e in entries)
    prog_counts = Counter()
    by_program  = defaultdict(list)
    for e in entries:
        for p in e["programs"]:
            prog_counts[p] += 1
            by_program[p].append(e)
        # also bucket by type for hubs
    by_type = defaultdict(list)
    for e in entries:
        by_type[e["type"]].append(e)
    for t in by_type:
        by_type[t].sort(key=lambda e: e["name"].lower())

    sitemap_urls = [(BASE+"/", "1.0"), (BASE+"/search/", "0.9"), (BASE+"/about/", "0.6"),
                    (BASE+"/wallets/", "0.9")]

    # ---- pre-compute wallet aggregates (used by both wallet pages and landing) ----
    all_wallets = doc.get("wallets", [])
    wallet_count = doc.get("walletCount", len(all_wallets))
    wallets_by_chain = defaultdict(list)
    for w in all_wallets:
        wallets_by_chain[w["symbol"]].append(w)

    # ---- entity pages ----
    n = 0
    for e in entries:
        html_doc = render_entity(e, by_program)
        write_file(entity_path(e) + "index.html", html_doc)
        sitemap_urls.append((BASE + entity_path(e), "0.8"))
        n += 1
        if n % 5000 == 0: print(f"  {n:,} entities…", flush=True)

    # ---- program pages ----
    print(f"Building {len(prog_counts)} program pages…", flush=True)
    for code, count in prog_counts.items():
        members = sorted(by_program[code], key=lambda e: e["name"].lower())
        tc = Counter(m["type"] for m in members)
        write_file(program_path(code) + "index.html",
                   render_program(code, count, members, tc))
        sitemap_urls.append((BASE + program_path(code), "0.7"))

    # ---- type hubs ----
    for t, members in by_type.items():
        write_file(type_path(t) + "index.html", render_type_hub(t, members, TOTAL_ENTRIES))
        sitemap_urls.append((BASE + type_path(t), "0.8"))

    # ---- wallet pages + chain hubs ----
    if all_wallets:
        print(f"Building {wallet_count} wallet pages + chain hubs…", flush=True)
        by_chain = defaultdict(list)
        for w in all_wallets:
            by_chain[w["symbol"]].append(w)
        nw = 0
        for w in all_wallets:
            write_file(wallet_path(w) + "index.html", render_wallet(w))
            sitemap_urls.append((BASE + wallet_path(w), "0.9"))
            nw += 1
            if nw % 200 == 0: print(f"  {nw} wallet pages…", flush=True)
        # chain hubs
        for sym, wlist in by_chain.items():
            cname = wlist[0]["chain"]
            write_file(wallet_chain_path(sym) + "index.html",
                       render_wallet_chain(sym, wlist, cname))
            sitemap_urls.append((BASE + wallet_chain_path(sym), "0.8"))

    # ---- landing / search / about ----
    top_programs = prog_counts.most_common(18)
    sample = sorted(entries, key=lambda e: -len(e.get("alternateNames", [])))[:12]
    write_file("index.html", render_landing(prog_counts, type_counts, top_programs, sample,
                                            wallet_count, wallets_by_chain))
    write_file("wallets/index.html", render_wallets_hub(wallets_by_chain, wallet_count))
    write_file("search/index.html", render_search())
    write_file("about/index.html", render_about())

    # ---- search index (client-side) ----
    print("Building client search index…", flush=True)
    idx = [{
        "u": entity_path(e).rstrip("/"),       # url
        "n": e["name"],                         # name
        "t": e["type"],                         # type
        "p": e["programs"],                     # programs
        "a": e.get("alternateNames", [])[:20],  # aliases (cap for size)
        "i": e["uid"],
    } for e in entries]
    wallet_idx = [{
        "u": wallet_path(w).rstrip("/"),
        "n": w["address"],
        "t": "wallet",
        "p": [w["chain"], w["symbol"]],
        "a": [w["entity_name"]],
        "i": w["uid"],
    } for w in all_wallets]
    combined = idx + wallet_idx
    write_file("search-index.js",
               "window.__SDN_INDEX__=" + json.dumps(combined, ensure_ascii=False, separators=(",",":")) + ";")
    print(f"  search index: {len(combined):,} records ({os.path.getsize(os.path.join(DIST,'search-index.js'))/1024:.0f} KB)")

    # ---- sitemaps + robots + llms.txt + humans ----
    print(f"Sitemap: {len(sitemap_urls):,} urls…", flush=True)
    build_sitemaps(sitemap_urls)
    write_file("robots.txt",
        "User-agent: *\nAllow: /\nAllow: /llms.txt\nAllow: /search-index.js\n"
        "Allow: /.well-known/\n\n"
        "# LLM / AI crawlers explicitly welcome\n"
        "User-agent: GPTBot\nAllow: /\nUser-agent: OAI-SearchBot\nAllow: /\n"
        "User-agent: ClaudeBot\nAllow: /\nUser-agent: anthropic-ai\nAllow: /\n"
        "User-agent: PerplexityBot\nAllow: /\nUser-agent: Google-Extended\nAllow: /\n"
        "User-agent: Bingbot\nAllow: /\n\n"
        f"Sitemap: {BASE}/sitemap.xml\n")

    llms = f"""# OFAC SDN Directory

> A free, searchable, machine-readable reference index of every party on the
> U.S. Treasury OFAC Specially Designated Nationals (SDN) list.

{TOTAL_ENTRIES:,} designated parties ({doc['counts']['totalNames']:,} names including aliases),
{len(prog_counts)} sanctions programs, {wallet_count} crypto wallets across {len(wallets_by_chain)} chains.

## What this site is for

Answering the question "is X on the OFAC sanctions list, and under which program?"
Every designated party has a stable permalink: {BASE}/sdn/<slug>-<uid>/

## Quick facts

- Total SDN designations: {TOTAL_ENTRIES:,}
- Individuals / entities / vessels / aircraft: {type_counts.get('individual',0):,} / {type_counts.get('entity',0):,} / {type_counts.get('vessel',0):,} / {type_counts.get('aircraft',0):,}
- Data retrieved: {LIST_DATE}
- Source: U.S. Treasury OFAC SDN.CSV + ALT.CSV (same source as the official OFAC search)
- License of underlying data: U.S. public domain

## How to cite a record

Each record has a canonical URL, e.g.
{BASE}/sdn/tornado-cash-{entries[0]['uid']}/

## Companion API

For programmatic screening (should a specific payment be blocked?), use the
SanctionsAI API: https://sanctionsai.dev  (free tier, no key)
"""
    write_file("llms.txt", llms)
    write_file("humans.txt",
        "# OFAC SDN Directory\n\n"
        "BUILT BY: SanctionsAI\n"
        "BUILT WITH: Python stdlib static-site generator, zero runtime dependencies.\n"
        "DATA: U.S. Treasury OFAC SDN + ALT lists (public domain)\n"
        "SITE: https://directory.sanctionsai.dev\n")

    # ---- 404 (friendly, with search) ----
    write_file("404.html", page(
        "Not found — OFAC SDN Directory",
        "Page not found. Try the search.",
        BASE+"/404", """
<section class="hero"><h1>404</h1><p class="lede">That designation wasn't found.</p>
<form action="/search/" class="search-box"><input name="q" type="search" placeholder="Search a name…" autofocus><button>Search</button></form>
<p class="muted">Or <a href="/">browse the directory</a>.</p></section>"""))

    print(f"\n✓ Done. {TOTAL_ENTRIES:,} entity pages + {len(prog_counts)} program pages + "
          f"{len(by_type)} type hubs written to {DIST}/")
    print(f"  sitemap entries: {len(sitemap_urls):,}")
    print(f"  total dist size: {sum(os.path.getsize(os.path.join(DIST,f)) for f in os.listdir(DIST))/1024/1024:.1f} MB (recursive — see du)")

if __name__ == "__main__":
    main()
