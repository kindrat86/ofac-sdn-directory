#!/usr/bin/env python3
"""
Fetch the authoritative OFAC SDN + ALT lists directly from the U.S. Treasury
source and merge them into a single normalized JSON document.

Primary source (load-bearing):
  https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV
  https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/ALT.CSV

This is the SAME source sanctionsai.dev uses, but fetched first-hand so the
directory is authoritative rather than downstream of a third-party GitHub repo.
"""
import csv, json, os, re, sys, urllib.request, datetime, hashlib

SDN_URL  = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV"
ALT_URL  = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/ALT.CSV"
OUT      = os.path.join(os.path.dirname(__file__), "..", "data", "ofac-sdn.json")
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

def fetch(url, label):
    """Fetch with a browser UA; treasury 403s blank UAs."""
    print(f"  fetching {label}: {url}", flush=True)
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/126.0 Safari/537.36",
        "Accept": "text/csv,application/csv,*/*;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=90) as r:
        raw = r.read()
    # Treasury CSVs are UTF-16-ish / odd; the published convention is they
    # arrive as plain ASCII CSV. Decode defensively.
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    print(f"  {label}: {len(text):,} bytes", flush=True)
    return text

def parse_csv(text):
    """Treasury files are quoted CSV; csv.reader handles embedded commas."""
    return list(csv.reader(text.splitlines()))

def parse_programs(raw):
    """OFAC SDN.CSV lists programs as '[FTO] [SDGT] [DRCONGO]' (sometimes
    with a missing closing bracket on the last one), OR comma-separated for
    some legacy rows. Handle both robustly."""
    if not raw:
        return []
    s = raw.strip()
    # bracket form
    if "[" in s or "]" in s:
        # split on ']' then strip leading '[' and whitespace
        parts = [p.strip().lstrip("[").strip() for p in s.split("]")]
        return [p for p in parts if p and p != "-0-"]
    # comma form
    return [p.strip() for p in s.split(",") if p.strip() and p.strip() != "-0-"]

def norm_type(raw):
    """Treasury SDN.CSV uses literal strings: 'individual', 'vessel',
    'aircraft', and '-0- ' (the OFAC placeholder) for entities/businesses."""
    t = raw.strip().lower().rstrip("-").strip()
    if t == "individual": return "individual"
    if t == "vessel":     return "vessel"
    if t == "aircraft":   return "aircraft"
    return "entity"  # '-0-', '', or any business/org designation


# ── structured-data extraction from the Remarks field ───────────────────
# OFAC packs a semi-structured "key-value" list into col 11, delimited by ';'.
# Examples:  "DOB 25 Mar 1977; POB Wuhan City, Hubei, China; citizen China;"
#            "Digital Currency Address - XBT 1Abc...; alt. Digital Currency..."
#            "Vessel Call Sign XYZ; Vessel Type Bulk Carrier; Email Address a@b"
_WALLET_RE = re.compile(
    r'Digital Currency Address\s*-\s*([A-Z]+)\s+([0-9a-zA-Z]+)', re.I)
# ETH addresses are distinctive (0x + 40 hex). BTC base58 is [13][...]{25,34}.
_ADDR_VALID = {
    "ETH":  re.compile(r'^0x[a-fA-F0-9]{40}$'),
    "XBT":  re.compile(r'^[13][a-km-zA-HJ-NP-Z1-9]{25,34}$|^bc1[a-z0-9]{6,87}$'),
    "BTC":  re.compile(r'^[13][a-km-zA-HJ-NP-Z1-9]{25,34}$|^bc1[a-z0-9]{6,87}$'),
    "TRX":  re.compile(r'^T[1-9A-HJ-NP-Za-km-z]{33}$'),
    "USDT": re.compile(r'^T[1-9A-HJ-NP-Za-km-z]{33}$'),
    "XMR":  re.compile(r'^[48][0-9AB][1-9A-HJ-NP-Za-km-z]{93}$'),
    "LTC":  re.compile(r'^[LM][a-km-zA-HJ-NP-Z1-9]{26,34}$|^ltc1[a-z0-9]{6,87}$'),
    "ZEC":  re.compile(r'^[tz][1-9A-HJ-NP-Za-km-z]{34}$'),
    "DASH": re.compile(r'^X[1-9A-HJ-NP-Za-km-z]{33}$'),
    "BCH":  re.compile(r'^[13]|^bitcoincash:'),
    "DOGE": re.compile(r'^D[1-9A-HJ-NP-Za-km-z]{30,34}$'),
    "SOL":  re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$'),
}
# Chain-symbol canonicalization for display
CHAIN_NAME = {
    "XBT": "Bitcoin", "BTC": "Bitcoin",
    "ETH": "Ethereum", "ETC": "Ethereum Classic",
    "TRX": "Tron", "USDT": "Tether (Tron)",
    "USDC": "USD Coin", "XMR": "Monero",
    "LTC": "Litecoin", "ZEC": "Zcash",
    "DASH": "Dash", "BTG": "Bitcoin Gold",
    "XVG": "Verge", "BCH": "Bitcoin Cash",
    "DOGE": "Dogecoin", "SOL": "Solana",
}
# Explorer URLs (for outbound links on wallet pages)
EXPLORER_URL = {
    "XBT":  "https://www.blockchain.com/explorer/addresses/btc/{a}",
    "BTC":  "https://www.blockchain.com/explorer/addresses/btc/{a}",
    "ETH":  "https://etherscan.io/address/{a}",
    "ETC":  "https://blockscout.com/etc/mainnet/address/{a}/transactions",
    "TRX":  "https://tronscan.org/#/address/{a}",
    "USDT": "https://tronscan.org/#/address/{a}",
    "USDC": "https://tronscan.org/#/address/{a}",
    "XMR":  "https://xmrchain.net/search?value={a}",
    "LTC":  "https://blockchair.com/litecoin/address/{a}",
    "ZEC":  "https://blockchair.com/zcash/address/{a}",
    "DASH": "https://blockchair.com/dash/address/{a}",
    "BTG":  "https://blockchair.com/bitcoin-gold/address/{a}",
    "XVG":  "https://blockchair.com/verge/address/{a}",
    "BCH":  "https://blockchair.com/bitcoin-cash/address/{a}",
    "DOGE": "https://blockchair.com/dogecoin/address/{a}",
    "SOL":  "https://solscan.io/account/{a}",
}

def extract_wallets(remarks):
    """Return list of {chain, symbol, address, explorer} for each digital
    currency address in the remarks. Deduped on (symbol, address)."""
    out = []; seen = set()
    for m in _WALLET_RE.finditer(remarks or ""):
        sym = m.group(1).upper()
        addr = m.group(2)
        # Validate against chain-specific pattern; skip junk
        pat = _ADDR_VALID.get(sym)
        if pat and not pat.match(addr):
            continue
        key = (sym, addr)
        if key in seen: continue
        seen.add(key)
        out.append({
            "chain": CHAIN_NAME.get(sym, sym),
            "symbol": sym,
            "address": addr,
            "explorer": EXPLORER_URL.get(sym, "").format(a=addr),
        })
    return out

def extract_meta(remarks):
    """Parse the semi-structured ';'-delimited key-value list in remarks.
    Returns a dict of structured fields for richer entity pages."""
    if not remarks: return {}
    parts = [p.strip() for p in remarks.split(";") if p.strip()]
    meta = {
        "dob": [], "pob": [], "nationality": [], "citizen": [],
        "email": [], "website": [], "passport": [], "id_numbers": [],
        "gender": None, "title": None, "swift_bic": None, "target_type": None,
        "vessel_call_sign": None, "vessel_type": None, "vessel_flag": None,
        "secondary_sanctions": False,
    }
    for p in parts:
        low = p.lower()
        if low.startswith("dob "):
            meta["dob"].append(p[4:].strip())
        elif low.startswith("pob "):
            meta["pob"].append(p[4:].strip())
        elif low.startswith("nationality "):
            meta["nationality"].append(p[12:].strip())
        elif low.startswith("citizen "):
            meta["citizen"].append(p[8:].strip())
        elif low.startswith(("email address", "alt. email address")):
            # strip leading label
            val = re.sub(r'^(alt\.\s*)?email address\s*', '', p, flags=re.I).strip()
            if val and val not in meta["email"]:
                meta["email"].append(val)
        elif low.startswith(("website", "alt. website")):
            val = re.sub(r'^(alt\.\s*)?website\s*', '', p, flags=re.I).strip()
            if val and val not in meta["website"]:
                meta["website"].append(val)
        elif low.startswith("gender "):
            meta["gender"] = p[7:].strip()
        elif low.startswith("title "):
            meta["title"] = p[6:].strip()
        elif "swift/bic" in low or low.startswith("swift/bic"):
            m = re.search(r'SWIFT/BIC\s*([A-Z0-9]+)', p, re.I)
            if m: meta["swift_bic"] = m.group(1)
        elif low.startswith("target type"):
            meta["target_type"] = p[12:].strip()
        elif low.startswith("vessel call sign"):
            meta["vessel_call_sign"] = p[17:].strip()
        elif low.startswith("vessel type"):
            meta["vessel_type"] = p[12:].strip()
        elif low.startswith("vessel flag") or low.startswith("vessel tonnage flag"):
            meta["vessel_flag"] = p.split("flag",1)[1].strip()
        elif "passport" in low:
            meta["passport"].append(p)
        elif low.startswith("identification number"):
            meta["id_numbers"].append(p)
        elif "secondary sanctions risk" in low or "subject to secondary sanctions" in low:
            meta["secondary_sanctions"] = True
    # strip empties
    return {k:v for k,v in meta.items()
            if not (isinstance(v, list) and not v)
            and v not in (None, "", False)}

def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    sdn_text = fetch(SDN_URL, "SDN.CSV")
    alt_text = fetch(ALT_URL, "ALT.CSV")

    sdn_rows = parse_csv(sdn_text)
    alt_rows = parse_csv(alt_text)

    # ALT.CSV: group alternate names by uid (col 0)
    akas = {}
    for row in alt_rows:
        if len(row) < 5 or not row[0].isdigit():
            continue
        uid = int(row[0])
        # ALT.CSV layout: uid, type_of_alist, category, lastname, firstname, ...
        last = row[3].strip() if len(row) > 3 else ""
        first = row[4].strip() if len(row) > 4 else ""
        # For entities, alternate name is in the lastname col; for individuals
        # the "name" is last[, first]
        if first:
            aka = f"{last}, {first}".strip(", ").strip()
        else:
            aka = last
        # Treasury appends ", -0-" as a placeholder for empty category fields;
        # strip every trailing "-0-" and the comma that precedes it.
        aka = re.sub(r"(?:\s*,\s*)?-0-\s*$", "", aka).strip().rstrip(",").strip()
        # Filter remaining OFAC placeholders / empties
        if aka and aka != "-0-" and aka.strip("-").strip():
            akas.setdefault(uid, []).append(aka)

    entries = []
    skipped = 0
    for row in sdn_rows:
        # SDN.CSV columns: 0 ent_num, 1 SDN_Name, 2 SDN_Type, 3 Program,
        # 4 Title, 5 Call_Sign, 6 Vessel_Type, 7 Toniage, 8 GrossRegisteredTonnage,
        # 9 VesselFlag, 10 VesselOwner, 11 Remarks, 12 address_id ...
        if len(row) < 3 or not row[0].isdigit():
            continue
        try:
            uid = int(row[0])
        except ValueError:
            continue
        name = (row[1] or "").strip()
        if not name:
            skipped += 1
            continue
        typ_raw = (row[2] or "").strip().lower()
        etype = norm_type(typ_raw)
        # Program can be a single col or multiple joined; SDN.CSV uses comma-separated
        programs = parse_programs(row[3] if len(row) > 3 else "")
        remarks  = (row[11] or "").strip() if len(row) > 11 else ""

        entries.append({
            "uid": uid,
            "name": name,
            "type": etype,
            "programs": programs,
            "alternateNames": akas.get(uid, []),
            "remarks": remarks,
            "wallets": extract_wallets(remarks),
            "meta": extract_meta(remarks),
        })

    # Dedupe on uid (SDN.CSV lists a uid once for primary name)
    seen = set(); deduped = []
    for e in entries:
        if e["uid"] in seen: continue
        seen.add(e["uid"]); deduped.append(e)
    entries = deduped

    # Sort: entities first by name for stable output, but keep uid for permalinks
    entries.sort(key=lambda e: (e["name"].lower(), e["uid"]))

    # ---- aggregate stats for hub pages ----
    from collections import Counter
    type_counts = Counter(e["type"] for e in entries)
    prog_counts = Counter()
    for e in entries:
        for p in e["programs"]:
            prog_counts[p] += 1

    # ---- aggregate wallets for programmatic wallet pages ----
    all_wallets = []
    for e in entries:
        for w in e.get("wallets", []):
            all_wallets.append({
                "address": w["address"],
                "chain": w["chain"],
                "symbol": w["symbol"],
                "explorer": w["explorer"],
                "uid": e["uid"],
                "entity_name": e["name"],
                "entity_type": e["type"],
                "programs": e["programs"],
            })
    wallet_entities = len(set((w["address"], w["symbol"]) for w in all_wallets))

    today = datetime.date.today().isoformat()
    doc = {
        "name": "OFAC Specially Designated Nationals (SDN) List",
        "description": f"The complete U.S. Treasury OFAC SDN list as {len(entries):,} designated "
                       f"entities ({sum(len(e['alternateNames']) for e in entries):,} alternate names). "
                       f"{wallet_entities} associated crypto wallet addresses across "
                       f"{len(CHAIN_NAME)} chains. First-hand from the Treasury source, refreshed daily.",
        "source": "U.S. Treasury OFAC — SDN.CSV + ALT.CSV",
        "sourceUrl": SDN_URL,
        "officialSearchUrl": "https://sanctionssearch.ofac.treas.gov/",
        "published": today,
        "retrieved": today,
        "counts": {
            "entries": len(entries),
            "alternateNames": sum(len(e["alternateNames"]) for e in entries),
            "totalNames": len(entries) + sum(len(e["alternateNames"]) for e in entries),
        },
        "typeCounts": dict(type_counts),
        "topPrograms": prog_counts.most_common(),
        "wallets": all_wallets,
        "walletCount": wallet_entities,
        "entries": entries,
    }
    with open(OUT, "w") as f:
        json.dump(doc, f, ensure_ascii=False)
    size = os.path.getsize(OUT)
    print(f"\n✓ wrote {OUT}")
    print(f"  entries: {len(entries):,}  alt-names: {doc['counts']['alternateNames']:,}  "
          f"total-names: {doc['counts']['totalNames']:,}")
    print(f"  types: {dict(type_counts)}")
    print(f"  distinct programs: {len(prog_counts)}")
    print(f"  crypto wallets: {wallet_entities} ({len(set(w['symbol'] for w in all_wallets))} chains)")
    print(f"  file size: {size/1024:.0f} KB  (skipped malformed: {skipped})")

if __name__ == "__main__":
    main()
