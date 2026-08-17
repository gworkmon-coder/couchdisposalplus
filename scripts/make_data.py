#!/usr/bin/env python3
"""
make_data.py — reconstruct the city/state dataset for Couch Disposal Plus.

Sources:
  1. deploy/_redirects   -> the canonical city universe (1,869 cities / 51 states)
                            AND the rural-absorption adjacency graph
  2. GSC Pages.csv       -> impressions per city, used as a population/priority proxy

Outputs:
  data/states.csv   state_code,state_name
  data/cities.csv   state_code,city_slug,city_name,tier,impressions,absorbs
"""
import csv, os, re, sys, collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REDIRECTS = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "vendor", "_redirects.legacy")
GSC_PAGES = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "vendor", "gsc-pages.csv")

STATES = {
    "al":"Alabama","ak":"Alaska","az":"Arizona","ar":"Arkansas","ca":"California",
    "co":"Colorado","ct":"Connecticut","de":"Delaware","dc":"District of Columbia",
    "fl":"Florida","ga":"Georgia","hi":"Hawaii","id":"Idaho","il":"Illinois",
    "in":"Indiana","ia":"Iowa","ks":"Kansas","ky":"Kentucky","la":"Louisiana",
    "me":"Maine","md":"Maryland","ma":"Massachusetts","mi":"Michigan","mn":"Minnesota",
    "ms":"Mississippi","mo":"Missouri","mt":"Montana","ne":"Nebraska","nv":"Nevada",
    "nh":"New Hampshire","nj":"New Jersey","nm":"New Mexico","ny":"New York",
    "nc":"North Carolina","nd":"North Dakota","oh":"Ohio","ok":"Oklahoma","or":"Oregon",
    "pa":"Pennsylvania","ri":"Rhode Island","sc":"South Carolina","sd":"South Dakota",
    "tn":"Tennessee","tx":"Texas","ut":"Utah","vt":"Vermont","va":"Virginia",
    "wa":"Washington","wv":"West Virginia","wi":"Wisconsin","wy":"Wyoming",
}

# Slug -> display name needs real rules; naive title() produces "St-Louis", "Mcdonough".
LOWER = {"of","on","the","and","upon","de","del","la","las","los","at","by","in"}
SPECIAL = {
    "st":"St.", "ft":"Ft.", "mt":"Mt.", "us":"US", "nyc":"NYC", "dc":"DC",
    "afb":"AFB", "ii":"II", "iii":"III",
}
# Slugs that lost an apostrophe or need a hard override.
OVERRIDES = {
    "ofallon":"O'Fallon", "lees-summit":"Lee's Summit", "coeur-dalene":"Coeur d'Alene",
    "obrien":"O'Brien", "ofallon-mo":"O'Fallon", "new-york-city":"New York City",
    "st-louis":"St. Louis", "st-paul":"St. Paul", "st-petersburg":"St. Petersburg",
    "st-charles":"St. Charles", "st-cloud":"St. Cloud", "st-johns":"St. Johns",
    "st-augustine":"St. Augustine", "st-george-island":"St. George Island",
    "washington-dc":"Washington, D.C.", "the-bronx":"The Bronx",
    "wilkes-barre-township":"Wilkes-Barre Township",
}


def prettify(slug: str) -> str:
    if slug in OVERRIDES:
        return OVERRIDES[slug]
    parts = slug.split("-")
    out = []
    for i, p in enumerate(parts):
        if p in SPECIAL:
            out.append(SPECIAL[p])
        elif p in LOWER and i != 0:
            out.append(p)
        elif p.startswith("mc") and len(p) > 3:          # mcdonough -> McDonough
            out.append("Mc" + p[2:].capitalize())
        elif p.startswith("mac") and len(p) > 5:
            out.append("Mac" + p[3:].capitalize())
        elif re.match(r"^o[bcdfghklmnprst]", p) and len(p) > 4 and p[1] not in "aeiou":
            out.append("O'" + p[1:].capitalize())        # ofallon -> O'Fallon
        else:
            out.append(p.capitalize())
    return " ".join(out)


def main():
    # ---- 1. city universe + adjacency from _redirects -------------------
    cities = {}                       # (state, slug) -> name
    absorbs = collections.defaultdict(list)   # (state, slug) -> [absorbed slugs]
    pat = re.compile(r"^/couch-disposal/([a-z]{2})/([a-z0-9-]+)/$")
    src_pat = re.compile(r"^/([a-z0-9-]+)-([a-z]{2})/$")

    for line in open(REDIRECTS):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        m = pat.match(parts[1])
        if not m:
            continue
        st, slug = m.group(1), m.group(2)
        if st not in STATES:
            continue
        cities[(st, slug)] = prettify(slug)
        sm = src_pat.match(parts[0])
        if sm and sm.group(2) == st and sm.group(1) != slug:
            absorbs[(st, slug)].append(sm.group(1))

    # ---- 2. impressions per city from GSC -------------------------------
    impr = collections.Counter()
    if os.path.exists(GSC_PAGES):
        gpat = re.compile(r"/([a-z-]+)/([a-z]{2})/([a-z0-9-]+)/$")
        for row in csv.DictReader(open(GSC_PAGES)):
            g = gpat.search(row["Top pages"])
            if g:
                impr[(g.group(2), g.group(3))] += int(row["Impressions"])

    # ---- 3. tiering ------------------------------------------------------
    # tier 1 = proven demand (has GSC impressions), 2 = absorbs rural traffic,
    # 3 = long tail. Drives internal-link priority and sitemap weighting.
    def tier(key):
        if impr.get(key, 0) >= 500:
            return 1
        if impr.get(key, 0) > 0 or absorbs.get(key):
            return 2
        return 3

    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)

    with open(os.path.join(ROOT, "data", "states.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["state_code", "state_name"])
        for code in sorted(STATES):
            w.writerow([code, STATES[code]])

    rows = []
    for (st, slug), name in cities.items():
        rows.append({
            "state_code": st,
            "city_slug": slug,
            "city_name": name,
            "tier": tier((st, slug)),
            "impressions": impr.get((st, slug), 0),
            "absorbs": "|".join(sorted(absorbs.get((st, slug), []))),
        })
    rows.sort(key=lambda r: (r["state_code"], -r["impressions"], r["city_slug"]))

    with open(os.path.join(ROOT, "data", "cities.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    by_tier = collections.Counter(r["tier"] for r in rows)
    print(f"states : {len(STATES)}")
    print(f"cities : {len(rows)}")
    print(f"  tier 1 (proven demand)  : {by_tier[1]}")
    print(f"  tier 2 (absorbs / some) : {by_tier[2]}")
    print(f"  tier 3 (long tail)      : {by_tier[3]}")
    missing = sorted(set(STATES) - {r["state_code"] for r in rows})
    print(f"states with no cities: {missing or 'none'}")


if __name__ == "__main__":
    main()
