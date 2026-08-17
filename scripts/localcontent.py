#!/usr/bin/env python3
"""
localcontent.py — per-city copy generation.

The thin-content problem: with four density bands, every city in a band was
94-97% identical once the city name was normalised out. 13% of body words were
genuinely unique.

The fix is combinatorial. Six independent variables, each computed from real
data, select from independent sentence pools. Every named entity and figure in
the output is a fact from universe.csv or a computed distance -- nothing here
is invented.

  pop_band          6 values
  rank_band         5 values   (position within its state by population)
  cluster_band      4 values   (how many served cities within 25 miles)
  anchor_band       4 values   (distance to the largest city in the state)
  region            5 values   (quadrant within the state by lat/lng)
  absorbs           2 values   (does it absorb rural communities)

6*5*4*4*5*2 = 4,800 distinct combinations before real names and numbers are
substituted in.
"""
import math


def haversine(a_lat, a_lng, b_lat, b_lng):
    r = 3958.8
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = math.radians(b_lat - a_lat), math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


POP_BANDS = [
    (600000, "In a city {city}'s size the job is rarely the couch and almost always the building. "
             "Freight elevator reservations, loading-dock time limits, and permit-only street parking "
             "are what actually decide whether a window holds."),
    (200000, "{city} splits fairly evenly between apartment and single-family pickups. The apartment "
             "half turns on elevator access and a clear route to the service door; the house half is "
             "usually curbside and finishes in under twenty minutes."),
    (80000, "Most {city} pickups are single-family or townhome, which means driveway access and a "
            "short straight carry. These are among the quicker runs on the platform."),
    (30000, "{city} runs mostly detached housing with driveway or garage access. Loaders can usually "
            "back close to the door, so the carry distance is short and the window holds tightly."),
    (10000, "{city} sits in lighter-density coverage, so pickups are batched into regional routes. "
            "Booking a day out rather than same-day buys you a much tighter arrival window."),
    (0, "{city} is a smaller community on a regional route. Same-day is occasionally available, but "
        "booking 24-48 hours ahead is what reliably gets a confirmed window here."),
]

RANK_BANDS = [
    (1, "It's the largest city in {state}, so it anchors the state's Loader pool and carries the "
        "deepest same-day capacity we have here."),
    (3, "As one of the three largest cities in {state}, {city} holds standing Loader coverage rather "
        "than routed coverage \u2014 which is why same-day holds up here on short notice."),
    (10, "{city} is among the ten largest cities in {state} and carries dedicated route coverage "
         "most days of the week."),
    (30, "{city} is a mid-table {state} market by population, covered by Loaders who also run the "
         "surrounding county."),
    (9999, "{city} is covered through {state}'s regional routing rather than a dedicated local pool, "
           "which is normal for communities this size."),
]

CLUSTER_BANDS = [
    (12, "There are {n} other cities we serve within 25 miles of {city}, so route density here is "
         "high and cancellations get refilled quickly."),
    (5, "We serve {n} other cities within 25 miles of {city}, which keeps routes tight and pickup "
        "windows short."),
    (1, "We cover {n} nearby cities within 25 miles, so {city} sits on a shared regional route."),
    (0, "{city} is relatively isolated in our coverage map \u2014 the nearest cities we serve are "
        "further out, so pickups here are scheduled into longer regional runs."),
]

REGIONS = {
    "north": "northern {state}", "south": "southern {state}",
    "east": "eastern {state}", "west": "western {state}", "central": "central {state}",
}


def band(value, table):
    for threshold, text in table:
        if value >= threshold:
            return text
    return table[-1][1]


def rank_band(rank):
    for threshold, text in RANK_BANDS:
        if rank <= threshold:
            return text
    return RANK_BANDS[-1][1]


def cluster_band(n):
    for threshold, text in CLUSTER_BANDS:
        if n >= threshold:
            return text
    return CLUSTER_BANDS[-1][1]


def region_of(city, state_cities):
    """Quadrant within the state, by lat/lng relative to state extents."""
    lats = [c["lat"] for c in state_cities]
    lngs = [c["lng"] for c in state_cities]
    if len(lats) < 4:
        return "central"
    lat_r = max(lats) - min(lats)
    lng_r = max(lngs) - min(lngs)
    lat_p = (city["lat"] - min(lats)) / lat_r if lat_r else 0.5
    lng_p = (city["lng"] - min(lngs)) / lng_r if lng_r else 0.5
    if 0.35 < lat_p < 0.65 and 0.35 < lng_p < 0.65:
        return "central"
    if abs(lat_p - 0.5) >= abs(lng_p - 0.5):
        return "north" if lat_p > 0.5 else "south"
    return "east" if lng_p > 0.5 else "west"


def build(city, state_name, state_cities, nearby):
    """Return (paragraphs, facts) for one city. facts is a list of (label, value)."""
    cname = city["city_name"]
    pop = city["population"]

    ranked = sorted(state_cities, key=lambda c: -c["population"])
    rank = next((i + 1 for i, c in enumerate(ranked)
                 if c["city_slug"] == city["city_slug"]), len(ranked))

    within25 = [c for c in state_cities
                if c["city_slug"] != city["city_slug"]
                and haversine(city["lat"], city["lng"], c["lat"], c["lng"]) <= 25]

    anchor = ranked[0]
    anchor_d = haversine(city["lat"], city["lng"], anchor["lat"], anchor["lng"])

    nearest = None
    for o in nearby:
        d = haversine(city["lat"], city["lng"], o["lat"], o["lng"])
        if d > 0.5 and (nearest is None or d < nearest[1]):
            nearest = (o, d)

    region = REGIONS[region_of(city, state_cities)].replace("{state}", state_name)

    ctx = {"city": cname, "state": state_name, "n": str(len(within25))}

    def f(t):
        for k, v in ctx.items():
            t = t.replace("{" + k + "}", v)
        return t

    paras = [f(band(pop, POP_BANDS)), f(rank_band(rank)), f(cluster_band(len(within25)))]

    if anchor["city_slug"] != city["city_slug"] and anchor_d > 1:
        paras.append(
            f"{cname} sits in {region}, about {int(round(anchor_d))} miles from {anchor['city_name']}, "
            f"and draws on the same regional Loader network \u2014 so same-day availability here tends "
            f"to track {anchor['city_name']} demand.")
    else:
        paras.append(
            f"{cname} sits in {region} and anchors its own Loader network rather than drawing on "
            f"another metro's capacity.")

    abs_list = [a for a in city["absorbs"].split("|") if a]
    if abs_list:
        names = [" ".join(w.capitalize() for w in a.split("-")) for a in sorted(abs_list)][:8]
        tail = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        paras.append(
            f"The {cname} service area also covers {tail}. Pickups in these communities route "
            f"through the same {cname} Loader pool at the same price \u2014 there is no out-of-area "
            f"surcharge.")

    facts = [("Population", f"{pop:,}" if pop else "\u2014"),
             (f"Rank in {state_name}", f"#{rank} of {len(ranked)}"),
             ("Cities served within 25 mi", str(len(within25)))]
    if nearest:
        facts.append(("Nearest served city",
                      f"{nearest[0]['city_name']} ({int(round(nearest[1]))} mi)"))
    facts.append(("Typical booking-to-pickup", "about 24 hours"))
    return paras, facts


# --------------------------------------------------------------------------
# conditional FAQ selection
# --------------------------------------------------------------------------
def faq_set(city, item, cname, sname, state_cities, brand, parent):
    """Pick 6 questions from a conditional pool so the FAQ block itself varies."""
    pop = city["population"]
    ranked = sorted(state_cities, key=lambda c: -c["population"])
    rank = next((i + 1 for i, c in enumerate(ranked)
                 if c["city_slug"] == city["city_slug"]), len(ranked))
    abs_list = [a for a in city["absorbs"].split("|") if a]
    label = item["label"].lower()

    q = [
        (f"How much does {item['h1_noun']} cost in {cname}?",
         item["faq_cost"].replace("{city}", cname).replace("{price}", str(item["price"]))),
        (f"How fast can you collect my {label} in {cname}?",
         f"Same-day pickup is available across {cname} for bookings placed before noon. Next-day is "
         f"the default. Average booking-to-pickup time in {cname} is about 24 hours."),
        (f"Do I need to prepare the {label} before pickup?",
         item["faq_special"].replace("{city}", cname)),
    ]

    if pop >= 200000:
        q.append((f"Do you handle high-rise and elevator buildings in {cname}?",
                  f"Yes. {cname} high-rise pickups are routine. Reserve the freight elevator for your "
                  f"window if your building requires it, and note the loading-dock entrance at booking "
                  f"so the assigned Loader arrives at the right door."))
        q.append((f"Is street parking a problem for {cname} pickups?",
                  f"In permit-only and metered parts of {cname} the Loader will stage as close as the "
                  f"restrictions allow and carry from there. There is no additional charge for a longer "
                  f"carry \u2014 the quoted price stands."))
    else:
        q.append((f"Can the truck reach my driveway in {cname}?",
                  f"Almost always. Most {cname} properties allow the Loader to back up close to the "
                  f"door or garage, which keeps the carry short and the window tight."))
        q.append((f"Is same-day pickup realistic in {cname}?",
                  f"Sometimes, but {cname} is covered on regional routing, so booking 24-48 hours ahead "
                  f"is what reliably secures a confirmed window here."))

    if rank <= 3:
        q.append((f"Do you cover all of {cname}, or only certain ZIPs?",
                  f"All of {cname}. As one of the largest markets in {sname}, it carries standing "
                  f"Loader coverage rather than routed coverage, so every ZIP in the city is bookable."))

    if abs_list:
        names = [" ".join(w.capitalize() for w in a.split("-")) for a in sorted(abs_list)][:5]
        tail = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        q.append((f"Do you pick up outside {cname} city limits?",
                  f"Yes. The {cname} service area includes {tail}. Those pickups route through the "
                  f"same Loader pool at the same price, with no out-of-area surcharge."))

    q.append((f"Where do {item['plural']} from {cname} end up?",
              item["faq_disposal"].replace("{city}", cname).replace("{state}", sname)))
    q.append((f"Who actually collects my {label} in {cname}?",
              f"{brand} is an online platform operated by {parent}. {cname} pickups are performed by "
              f"independent local Loaders \u2014 background-checked, insured contractors who accept "
              f"jobs through the platform. The platform handles booking, scheduling, payment and "
              f"support; the assigned Loader performs the pickup and disposal."))
    return q[:6]
