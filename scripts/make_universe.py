#!/usr/bin/env python3
"""
make_universe.py — build the full city universe for all 50 states + DC.

Union of two sources:
  A. data/cities.csv        the 1,869 slugs already in production redirects.
                            MUST be preserved -- these are live redirect targets
                            and existing ranking URLs. Never drop one.
  B. geonamescache          3,407 US cities >15k population, with population
                            and lat/lng for all 51 states.

Coordinates enable real nearest-neighbour internal linking. Cities from source A
with no geo match inherit coordinates from their state centroid so they still
participate in linking (flagged geo_source=inherited).

Output: data/universe.csv
  state_code,city_slug,city_name,population,lat,lng,tier,impressions,absorbs,geo_source,in_production
"""
import csv, os, re, math, collections
from geonamescache import GeonamesCache

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

STATE_NAMES = {r["state_code"]: r["state_name"]
               for r in csv.DictReader(open(os.path.join(DATA, "states.csv")))}


def slugify(name: str) -> str:
    s = name.lower()
    s = s.replace("'", "").replace(".", "").replace(",", "")
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def main():
    # ---- source A: production cities ------------------------------------
    prod = {}
    for r in csv.DictReader(open(os.path.join(DATA, "cities.csv"))):
        prod[(r["state_code"], r["city_slug"])] = r

    # ---- source B: geonamescache ----------------------------------------
    gc = GeonamesCache()
    geo = {}
    for c in gc.get_cities().values():
        if c["countrycode"] != "US":
            continue
        st = c["admin1code"].lower()
        if st not in STATE_NAMES:
            continue
        slug = slugify(c["name"])
        key = (st, slug)
        # keep the larger record if a slug collides within a state
        if key in geo and geo[key]["population"] >= c["population"]:
            continue
        geo[key] = {"name": c["name"], "population": c["population"],
                    "lat": c["latitude"], "lng": c["longitude"]}

    # ---- state centroids for inherited geo ------------------------------
    cent = collections.defaultdict(list)
    for (st, _), g in geo.items():
        cent[st].append((g["lat"], g["lng"]))
    centroid = {st: (sum(a for a, _ in v) / len(v), sum(b for _, b in v) / len(v))
                for st, v in cent.items()}

    # ---- union -----------------------------------------------------------
    rows = {}
    for key in set(prod) | set(geo):
        st, slug = key
        p = prod.get(key)
        g = geo.get(key)
        name = p["city_name"] if p else g["name"]
        if g:
            lat, lng, gsrc = g["lat"], g["lng"], "geonames"
        else:
            lat, lng = centroid.get(st, (0.0, 0.0))
            gsrc = "inherited"
        rows[key] = {
            "state_code": st,
            "city_slug": slug,
            "city_name": name,
            "population": g["population"] if g else 0,
            "lat": round(float(lat), 5),
            "lng": round(float(lng), 5),
            "tier": int(p["tier"]) if p else 3,
            "impressions": int(p["impressions"]) if p else 0,
            "absorbs": p["absorbs"] if p else "",
            "geo_source": gsrc,
            "in_production": "1" if p else "0",
        }

    # Re-tier using population where we have no impression signal, so that
    # big cities we've never ranked for still get tier-1 link priority.
    for r in rows.values():
        if r["impressions"] >= 500:
            r["tier"] = 1
        elif r["population"] >= 100000:
            r["tier"] = 1
        elif r["impressions"] > 0 or r["absorbs"] or r["population"] >= 30000:
            r["tier"] = 2
        else:
            r["tier"] = 3

    out = sorted(rows.values(),
                 key=lambda r: (r["state_code"], -r["population"], -r["impressions"], r["city_slug"]))
    with open(os.path.join(DATA, "universe.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)

    per_state = collections.Counter(r["state_code"] for r in out)
    tiers = collections.Counter(r["tier"] for r in out)
    inherited = sum(1 for r in out if r["geo_source"] == "inherited")
    kept = sum(1 for r in out if r["in_production"] == "1")

    print(f"universe          : {len(out)} cities across {len(per_state)} states")
    print(f"  from production : {kept} (all preserved)")
    print(f"  net new         : {len(out) - kept}")
    print(f"  inherited geo   : {inherited}")
    print(f"  tier 1 / 2 / 3  : {tiers[1]} / {tiers[2]} / {tiers[3]}")
    print(f"thinnest states   : {sorted(per_state.items(), key=lambda x: x[1])[:6]}")
    assert kept == len(prod), "lost a production city!"


if __name__ == "__main__":
    main()
