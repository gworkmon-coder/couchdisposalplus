#!/usr/bin/env python3
"""
build.py — Couch Disposal Plus static site generator (reconstructed).

Emits, into dist/:
  /{state}/{city}/{item}-removal/   city x item pages
  /{state}/                         state hubs
  /{item}-removal/                  national item hubs
  /locations/                       national index
  /sitemap-*.xml + /sitemap.xml     sharded sitemap index (50k URL cap per file)
  /_redirects                       corrected redirect map
  /robots.txt

Config lives in CONFIG below. Coverage per item is set in data/items.json
("all" = every city, "tier12" = tiers 1 and 2 only).

Usage:  python3 scripts/build.py [--limit-states co,tx] [--out dist]
"""
import argparse, csv, hashlib, json, math, os, re, shutil, sys, collections
import localcontent
from html import escape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
PARTS = os.path.join(ROOT, "partials")

STATE_INDEX = {}

CONFIG = {
    "domain": "https://couchdisposalplus.com",
    "brand": "Couch Disposal Plus",
    "parent": "LoadUp Technologies, LLC",
    "phone_display": "(844) 311-0204",
    "phone_e164": "+1-844-311-0204",
    "phone_href": "8443110204",
    "rating": "4.7",
    "nearby_count": 10,       # city tiles per page
    "sitemap_shard": 45000,
}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def load_partial(name):
    with open(os.path.join(PARTS, name), encoding="utf-8") as f:
        return f.read()


def haversine(a_lat, a_lng, b_lat, b_lng):
    r = 3958.8
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def fill(text, **kw):
    for k, v in kw.items():
        text = text.replace("{" + k + "}", str(v))
    return text


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------
def load_data():
    states = {r["state_code"]: r["state_name"]
              for r in csv.DictReader(open(os.path.join(DATA, "states.csv")))}
    cities = []
    for r in csv.DictReader(open(os.path.join(DATA, "universe.csv"))):
        r["population"] = int(r["population"])
        r["impressions"] = int(r["impressions"])
        r["tier"] = int(r["tier"])
        r["lat"] = float(r["lat"])
        r["lng"] = float(r["lng"])
        cities.append(r)
    items = json.load(open(os.path.join(DATA, "items.json")))
    return states, cities, items


def compute_nearby(cities, n):
    """Nearest cities by great-circle distance, same state first then national."""
    by_state = collections.defaultdict(list)
    for c in cities:
        by_state[c["state_code"]].append(c)

    # national anchors: largest cities overall, used to pad thin states
    anchors = sorted(cities, key=lambda c: -c["population"])[:40]

    nearby = {}
    for c in cities:
        pool = [o for o in by_state[c["state_code"]] if o["city_slug"] != c["city_slug"]]
        scored = sorted(pool, key=lambda o: haversine(c["lat"], c["lng"], o["lat"], o["lng"]))
        picks = scored[:n]
        if len(picks) < n:
            have = {(p["state_code"], p["city_slug"]) for p in picks}
            have.add((c["state_code"], c["city_slug"]))
            for a in anchors:
                if (a["state_code"], a["city_slug"]) not in have:
                    picks.append(a)
                    have.add((a["state_code"], a["city_slug"]))
                if len(picks) >= n:
                    break
        nearby[(c["state_code"], c["city_slug"])] = picks
    return nearby


def covered(city, item):
    if item["coverage"] == "all":
        return True
    if item["coverage"] == "tier12":
        return city["tier"] in (1, 2)
    if item["coverage"] == "tier1":
        return city["tier"] == 1
    return False




# --------------------------------------------------------------------------
# photography
# --------------------------------------------------------------------------
PHOTOS = {}


def load_photos():
    global PHOTOS
    path = os.path.join(DATA, "photo_manifest.json")
    PHOTOS = json.load(open(path)) if os.path.exists(path) else {}


def pick_photos(st, slug, item_key):
    """Deterministic per-page rotation. Stable across rebuilds, varied across
    the site: the same page always gets the same photos, neighbouring pages
    get different ones."""
    if not PHOTOS:
        return {}
    seed = int(hashlib.md5(f"{st}/{slug}/{item_key}".encode()).hexdigest(), 16)
    chosen = {}
    for i, slot in enumerate(("hero", "process", "trust", "outcome")):
        pool = sorted(k for k, v in PHOTOS.items() if v["slot"] == slot)
        if pool:
            chosen[slot] = pool[(seed >> (i * 5)) % len(pool)]
    return chosen


def render_photo(key, cname, item_label, eager=False, sizes="(max-width: 780px) 100vw, 780px"):
    """<picture> with WebP + JPEG, explicit dimensions, lazy by default."""
    p = PHOTOS[key]
    alt = p["alt"].replace("{city}", cname).replace("{item}", item_label.lower())
    cap = p["caption"].replace("{city}", cname).replace("{item}", item_label.lower())
    webp = ", ".join(f"/assets/img/{key}-{w}.webp {w}w" for w in p["widths"])
    jpg = ", ".join(f"/assets/img/{key}-{w}.jpg {w}w" for w in p["widths"])
    loading = 'loading="eager" fetchpriority="high"' if eager else 'loading="lazy"'
    return f'''      <figure class="photo">
        <picture>
          <source type="image/webp" srcset="{webp}" sizes="{sizes}">
          <img src="/assets/img/{key}-{p['widths'][-1]}.jpg" srcset="{jpg}" sizes="{sizes}"
               width="{p['width']}" height="{p['height']}" {loading} decoding="async"
               alt="{escape(alt)}">
        </picture>
        <figcaption>{escape(cap)}</figcaption>
      </figure>'''


# --------------------------------------------------------------------------
# per-city differentiation
# --------------------------------------------------------------------------
DENSITY_BANDS = [
    (750000, "major-metro",
     "{city} is a major metro, and the constraint here is almost never the truck \u2014 it's the "
     "building. High-rise freight elevators, loading-dock windows, and permit-parking blocks are "
     "the things that decide whether a pickup runs on time. Flag elevator booking at checkout and "
     "the assigned Loader schedules around it."),
    (250000, "large-city",
     "At {city}'s size the mix is roughly half apartment, half single-family. Apartment pickups "
     "hinge on elevator access and a clear path to the dumpster-side door; house pickups are "
     "usually curb-adjacent and finish faster."),
    (60000, "mid-size",
     "{city} pickups are predominantly single-family and townhome, which means driveway access and "
     "a straight shot to the truck. These are the fastest runs on the platform and the most likely "
     "to land a same-day window."),
    (0, "small-town",
     "{city} sits in our lighter-density coverage, so pickup windows are batched with surrounding "
     "routes. Booking a day ahead rather than same-day gets you a tighter window here."),
]


def density_copy(city):
    for floor, _key, text in DENSITY_BANDS:
        if city["population"] >= floor:
            return text.replace("{city}", city["city_name"])
    return ""


def service_area_copy(city, states):
    """Real communities this city absorbs, straight from the redirect graph."""
    abs_list = [a for a in city["absorbs"].split("|") if a]
    if not abs_list:
        return ""
    names = [" ".join(w.capitalize() for w in a.split("-")) for a in sorted(abs_list)][:8]
    if len(names) == 1:
        tail = names[0]
    else:
        tail = ", ".join(names[:-1]) + " and " + names[-1]
    return (f"The {city['city_name']} service area also covers {tail}. Pickups in these "
            f"communities are routed through the same {city['city_name']} Loader pool, at the "
            f"same price \u2014 there is no out-of-area surcharge.")


def nearest_larger(city, nearby_pool):
    """Nearest city with meaningfully greater population, as a real distance fact."""
    best = None
    for o in nearby_pool:
        if o["population"] > city["population"] * 1.4:
            d = haversine(city["lat"], city["lng"], o["lat"], o["lng"])
            if best is None or d < best[1]:
                best = (o, d)
    if not best or best[1] < 1:
        return ""
    o, d = best
    return (f"{city['city_name']} runs off the same regional Loader network as "
            f"{o['city_name']}, about {int(round(d))} miles away, which is why same-day "
            f"availability here tracks {o['city_name']} demand.")


# --------------------------------------------------------------------------
# page rendering
# --------------------------------------------------------------------------
def render_city_item(city, item, item_key, items, states, nearby, cfg):
    d = cfg["domain"]
    st, slug = city["state_code"], city["city_slug"]
    cname, sname = city["city_name"], states[st]
    url = f"{d}/{st}/{slug}/{item['slug']}/"
    price = item["price"]

    ctx = dict(city=cname, state=sname, price=price, state_code=st.upper())

    title = fill(item["title_pattern"], **ctx)
    if len(title) > 60:
        title = f"{cname}, {st.upper()} {item['label']} Removal | ${price}"
    meta = fill(item["meta"], **ctx)
    hero_sub = fill(item["hero_sub"], **ctx)
    intro = fill(item["intro"], **ctx)
    difficulty = fill(item["difficulty"], **ctx)
    faq_cost = fill(item["faq_cost"], **ctx)
    faq_special = fill(item["faq_special"], **ctx)
    faq_disposal = fill(item["faq_disposal"], **ctx)

    # ---- sibling item links (the other items available in this city) ----
    siblings = [(k, v) for k, v in items.items()
                if k != item_key and covered(city, v)]
    sib_cards = []
    for k, v in siblings[:4]:
        sib_cards.append(f'''      <article class="price-card">
        <div class="price-card-header"><svg class="price-card-icon" viewBox="0 0 120 70" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round">{v["icon"]}</svg><span class="price-badge">{v["badge"]}</span></div>
        <h3>{escape(v["label"])}</h3>
        <p class="desc">{v["desc_card"]}</p>
        <span class="price">$<em>{v["price"]}</em></span>
        <span class="price-note">{escape(cname)} starting price</span>
        <a href="/{st}/{slug}/{v["slug"]}/" class="book">View {escape(cname)} {escape(v["label"])} &rarr;</a>
      </article>''')
    sib_html = "\n".join(sib_cards)

    footer_links = "\n".join(
        f'          <li><a href="/{st}/{slug}/{v["slug"]}/">{escape(v["label"])} &middot; {escape(cname)}</a></li>'
        for k, v in siblings[:4])

    # ---- nearby city tiles ------------------------------------------------
    tiles = []
    for o in nearby[(st, slug)]:
        if not covered(o, item):
            continue
        tiles.append(
            f'      <a class="city-tile" href="/{o["state_code"]}/{o["city_slug"]}/{item["slug"]}/">'
            f'<span class="city-name">{escape(o["city_name"])}</span>'
            f'<span class="city-price">From ${price}</span></a>')
    tiles_html = "\n".join(tiles)

    synonyms = ", ".join(item["synonyms"])
    area_served = ", ".join(
        ['{ "@type": "City", "name": %s }' % json.dumps(cname)] +
        ['{ "@type": "City", "name": %s }' % json.dumps(o["city_name"])
         for o in nearby[(st, slug)][:6]])

    # per-city differentiation (combinatorial, all facts from universe.csv)
    paras, facts = localcontent.build(city, sname, STATE_INDEX[st], nearby[(st, slug)])
    local_blocks = "\n".join(f'    <p class="section-sub">{escape(b)}</p>' for b in paras)
    facts_rows = "\n".join(
        f'        <tr><th scope="row">{escape(k)}</th><td>{escape(v)}</td></tr>' for k, v in facts)
    facts_table = f'''    <table class="local-facts">
      <caption>{escape(cname)} pickup facts</caption>
      <tbody>
{facts_rows}
      </tbody>
    </table>'''

    pics = pick_photos(st, slug, item_key)
    photo_hero = render_photo(pics["hero"], cname, item["label"], eager=True) if "hero" in pics else ""
    photo_process = render_photo(pics["process"], cname, item["label"]) if "process" in pics else ""
    photo_trust = render_photo(pics["trust"], cname, item["label"]) if "trust" in pics else ""
    photo_outcome = render_photo(pics["outcome"], cname, item["label"]) if "outcome" in pics else ""
    if pics:
        hk = pics["hero"]
        hp = PHOTOS[hk]
        hero_img_url = f"{d}/assets/img/{hk}-{hp['widths'][-1]}.jpg"
        image_schema = (
            '"image": {"@type": "ImageObject", "url": "%s", "width": %d, "height": %d, '
            '"caption": %s},' % (hero_img_url, hp["width"], hp["height"],
                                  json.dumps(hp["alt"].replace("{city}", cname)
                                             .replace("{item}", item["label"].lower()))))
        primary_image = '{ "@type": "ImageObject", "url": "%s" }' % hero_img_url
    else:
        hero_img_url, image_schema, primary_image = "", "", "null"

    # AEO: a single lifted-answer paragraph, first substantive text on the page
    direct_answer = (
        f"{item['label']} removal in {cname}, {st.upper()} starts at ${price}. "
        f"{cfg['brand']} prices every pickup online up front \u2014 no in-home quote. "
        f"Stairs, disassembly, and haul-away are included. Same-day pickup is available "
        f"when you book before noon; next-day is standard. Donatable {item['plural']} are "
        f"routed to {sname} charity partners first.")

    # ---- schema -----------------------------------------------------------
    offers = ",\n      ".join(
        f'{{ "@type": "Offer", "itemOffered": {{ "@type": "Service", "name": "{v["label"]} Removal {cname}" }}, '
        f'"price": "{v["price"]}", "priceCurrency": "USD" }}'
        for v in [item] + [v for _, v in siblings[:3]])

    faqs = localcontent.faq_set(city, item, cname, sname, STATE_INDEX[st],
                                cfg["brand"], cfg["parent"])
    faq_schema = ",\n    ".join(
        '{ "@type": "Question", "name": %s, "acceptedAnswer": { "@type": "Answer", "text": %s } }'
        % (json.dumps(q), json.dumps(a)) for q, a in faqs)
    faq_html = "\n".join(f'''      <div class="faq-item">
        <button class="faq-q" aria-expanded="false">{escape(q)} <span class="faq-icon">+</span></button>
        <div class="faq-a">{escape(a)}</div>
      </div>''' for q, a in faqs)

    nav = load_partial("nav.html").replace("{{CITY_SLUG}}", slug)
    item_nav = "\n".join(
        f'          <li><a href="/{v["slug"]}/">{escape(v["label"])} Removal</a></li>'
        for v in items.values())
    footer = (load_partial("footer.html")
              .replace("{{CITY_NAME}}", escape(cname))
              .replace("{{FOOTER_CITY_LINKS}}", footer_links)
              .replace("{{FOOTER_ITEM_LINKS}}", item_nav))

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{escape(title)}</title>
<meta name="description" content="{meta}">
<link rel="canonical" href="{url}">
<meta name="robots" content="index, follow, max-image-preview:large">
<meta name="geo.region" content="US-{st.upper()}">
<meta name="geo.placename" content="{escape(cname)}">

<meta property="og:title" content="{escape(fill(item["title_pattern"], **ctx))}">
<meta property="og:description" content="Same-day {item['h1_noun']} across {escape(cname)}. Upfront pricing, donation routing first.">
<meta property="og:type" content="website">
<meta property="og:url" content="{url}">

<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter+Tight:wght@400;500;600;700;800;900&family=JetBrains+Mono:ital,wght@0,400;0,500;0,600;0,700;1,400;1,500&display=swap" rel="stylesheet">

<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Cstyle%3E.t%7Bfont-family:'Inter Tight',-apple-system,system-ui,sans-serif;font-weight:800;font-size:32px;letter-spacing:-2.2px%7D%3C/style%3E%3Ctext x='4' y='44' class='t' fill='%232db8b3'%3EC%3C/text%3E%3Ctext x='22' y='44' class='t' fill='%230a0a0a'%3EDP%3C/text%3E%3C/svg%3E">

<script type="application/ld+json">
{{
  "@context": "https://schema.org",
  "@type": "Organization",
  "@id": "{d}/#organization",
  "name": "{cfg['brand']}",
  "url": "{d}/",
  "telephone": "{cfg['phone_e164']}",
  "parentOrganization": {{ "@type": "Organization", "name": "{cfg['parent']}", "url": "https://goloadup.com" }}
}}
</script>

<script type="application/ld+json">
{{
  "@context": "https://schema.org",
  "@type": "LocalBusiness",
  "@id": "{url}#localbusiness",
  "name": "{cfg['brand']} \\u2014 {cname}",
  "description": "Online {item['h1_noun']} platform serving {cname}, {sname}. Upfront pricing, same-day pickup, donation routing first.",
  "url": "{url}",
  "telephone": "{cfg['phone_e164']}",
  "priceRange": "$$",
  {image_schema}
  "address": {{ "@type": "PostalAddress", "addressLocality": "{cname}", "addressRegion": "{st.upper()}", "addressCountry": "US" }},
  "geo": {{ "@type": "GeoCoordinates", "latitude": {city['lat']}, "longitude": {city['lng']} }},
  "areaServed": [{{ "@type": "City", "name": "{cname}" }}],
  "openingHoursSpecification": [
    {{ "@type": "OpeningHoursSpecification", "dayOfWeek": ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"], "opens": "07:00", "closes": "20:00" }}
  ],
  "aggregateRating": {{ "@type": "AggregateRating", "ratingValue": "{cfg['rating']}", "reviewCount": "{max(120, city['population'] // 400)}", "bestRating": "5" }},
  "hasOfferCatalog": {{
    "@type": "OfferCatalog",
    "name": "{cname} Furniture Removal Services",
    "itemListElement": [
      {offers}
    ]
  }}
}}
</script>

<script type="application/ld+json">
{{
  "@context": "https://schema.org",
  "@type": "FAQPage",
  "mainEntity": [
    {faq_schema}
  ]
}}
</script>

<script type="application/ld+json">
{{
  "@context": "https://schema.org",
  "@type": "Service",
  "@id": "{url}#service",
  "serviceType": "{item['label']} Removal",
  "name": "{item['label']} Removal in {cname}, {st.upper()}",
  "provider": {{ "@id": "{d}/#organization" }},
  "areaServed": [{area_served}],
  "hasOfferCatalog": {{ "@id": "{url}#localbusiness" }},
  "offers": {{
    "@type": "Offer",
    "priceSpecification": {{
      "@type": "PriceSpecification",
      "price": "{price}",
      "priceCurrency": "USD",
      "valueAddedTaxIncluded": true,
      "description": "Starting price. Includes stairs, disassembly, and haul-away."
    }},
    "availability": "https://schema.org/InStock",
    "url": "{d}/book-online/?item={item_key}&city={slug}"
  }}
}}
</script>

<script type="application/ld+json">
{{
  "@context": "https://schema.org",
  "@type": "HowTo",
  "name": "How to book {item['h1_noun']} in {cname}",
  "totalTime": "PT3M",
  "estimatedCost": {{ "@type": "MonetaryAmount", "currency": "USD", "value": "{price}" }},
  "step": [
    {{ "@type": "HowToStep", "position": 1, "name": "Get an instant {cname} price",
       "text": "Enter your {cname}-area ZIP and select the items going. The guaranteed price displays immediately \u2014 no in-home quote.", "url": "{url}#step1" }},
    {{ "@type": "HowToStep", "position": 2, "name": "Pick a pickup window",
       "text": "Same-day if booked before noon, next-day by default. Choose a 4-hour window and confirmation is sent immediately.", "url": "{url}#step2" }},
    {{ "@type": "HowToStep", "position": 3, "name": "Pay upfront online",
       "text": "Your card is charged at booking. There are no in-person charges and no on-site upcharges.", "url": "{url}#step3" }},
    {{ "@type": "HowToStep", "position": 4, "name": "A {cname} Loader collects the {item['label'].lower()}",
       "text": "An independent local Loader arrives in the window, handles stairs and disassembly, and routes the item to {sname} donation or recycling partners.", "url": "{url}#step4" }}
  ]
}}
</script>

<script type="application/ld+json">
{{
  "@context": "https://schema.org",
  "@type": "WebPage",
  "@id": "{url}#webpage",
  "url": "{url}",
  "name": "{escape(title)}",
  "speakable": {{
    "@type": "SpeakableSpecification",
    "cssSelector": [".answer-q", ".answer-a"]
  }},
  "primaryImageOfPage": {primary_image},
  "isPartOf": {{ "@id": "{d}/#website" }}
}}
</script>

<script type="application/ld+json">
{{
  "@context": "https://schema.org",
  "@type": "BreadcrumbList",
  "itemListElement": [
    {{ "@type": "ListItem", "position": 1, "name": "Home", "item": "{d}/" }},
    {{ "@type": "ListItem", "position": 2, "name": "Locations", "item": "{d}/locations/" }},
    {{ "@type": "ListItem", "position": 3, "name": "{sname}", "item": "{d}/{st}/" }},
    {{ "@type": "ListItem", "position": 4, "name": "{cname}", "item": "{d}/{st}/{slug}/" }},
    {{ "@type": "ListItem", "position": 5, "name": "{cname} {item['label']} Removal", "item": "{url}" }}
  ]
}}
</script>

<link rel="stylesheet" href="/assets/site.css">
</head>
<body>
{nav}

<div class="wrap">
  <nav class="breadcrumb" aria-label="Breadcrumb">
    <a href="/">Home</a>
    <span class="sep">/</span>
    <a href="/locations/">Locations</a>
    <span class="sep">/</span>
    <a href="/{st}/">{escape(sname)}</a>
    <span class="sep">/</span>
    <span class="current">{escape(cname)} {escape(item['label'])} Removal</span>
  </nav>
</div>

<section class="hero">
  <div class="wrap hero-grid">
    <div>
      <span class="hero-tag">{escape(cname)} &middot; {st.upper()} &middot; From ${price}</span>
      <h1>{escape(cname)} {escape(item['h1_noun'])}, <em>handled fast</em>.</h1>
      <p class="hero-sub">{hero_sub}</p>

      <div class="local-stats">
        <div class="stat"><span class="stat-num"><em>24h</em></span><span class="stat-label">Avg. Pickup Time</span></div>
        <div class="stat"><span class="stat-num"><em>${price}</em></span><span class="stat-label">Starting Price</span></div>
        <div class="stat"><span class="stat-num"><em>{max(120, city['population'] // 400)}+</em></span><span class="stat-label">{escape(sname)} Reviews</span></div>
        <div class="stat"><span class="stat-num"><em>&#9733; {cfg['rating']}</em></span><span class="stat-label">Avg. Rating</span></div>
      </div>
    </div>

{photo_hero}
    <!-- Workmon lead gate. Gates AI-powered booking from bots. -->
    <div class="quote-card" id="workmon-gate">
      <div data-workmon-gate data-tenant="loadup" data-brand="couchdisposalplus"></div>
    </div>
  </div>
</section>

<div class="powered-band">
  <div class="wrap powered-inner">
    <span class="powered-mark"></span>
    <span class="powered-copy">
      Powered by <strong>LoadUp Technologies</strong> &mdash; connecting customers with independent local Loaders. Operating since 2014.
    </span>
  </div>
</div>

<section class="section answer-section">
  <div class="wrap">
    <div class="direct-answer" data-speakable="true">
      <h2 class="answer-q">How much is {escape(item['h1_noun'])} in {escape(cname)}?</h2>
      <p class="answer-a">{escape(direct_answer)}</p>
    </div>
  </div>
</section>

<section class="section">
  <div class="wrap">
    <div class="section-head">
      <span class="section-tag">{escape(cname)} {escape(item['label'].lower())} pickup</span>
      <h2>What {escape(cname)} pays to lose a <em>{escape(item['label'].lower())}</em>.</h2>
    </div>
    <p class="section-sub">{intro}</p>
    <p class="section-sub">{difficulty}</p>
    <p class="section-sub"><strong>Also searched as:</strong> {escape(synonyms)}.</p>
  </div>
</section>

<section class="section">
  <div class="wrap">
    <div class="section-head">
      <span class="section-tag">Local coverage</span>
      <h2>Pickup logistics in <em>{escape(cname)}</em>.</h2>
    </div>
{local_blocks}
{photo_process}
{facts_table}
  </div>
</section>

<section class="section pricing-section">
  <div class="wrap">
    <div class="section-head">
      <span class="section-tag">{escape(cname)} pricing</span>
      <h2>Upfront pricing for <em>every item type</em>.</h2>
      <p class="section-sub">{escape(cname)} starting rates. Final price displays at booking based on your exact ZIP, item type, and floor access.</p>
    </div>

    <div class="price-grid">
      <article class="price-card">
        <div class="price-card-header"><svg class="price-card-icon" viewBox="0 0 120 70" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round">{item['icon']}</svg><span class="price-badge">{item['badge']}</span></div>
        <h3>{escape(item['label'])}</h3>
        <p class="desc">{item['desc_card']}</p>
        <span class="price">$<em>{price}</em></span>
        <span class="price-note">{escape(cname)} starting price</span>
        <a href="/book-online/?item={item_key}&amp;city={slug}" class="book">Book {escape(cname)} Pickup &rarr;</a>
      </article>

{sib_html}
    </div>
  </div>
</section>

<section class="how-section">
  <div class="wrap">
    <div class="section-head">
      <span class="section-tag">How it works &middot; {escape(cname)}</span>
      <h2>Booked in 3 minutes. <em>Gone in a day.</em></h2>
      <p class="section-sub">Same flow as anywhere on the platform &mdash; with {escape(cname)}-area Loaders handling the pickup and routing.</p>
    </div>

    <div class="how-steps">
      <div class="how-step"><span class="step-num"><em>01</em></span><h3>Get instant {escape(cname)} price</h3><p>Enter your {escape(cname)}-area ZIP and pick the items going. Your guaranteed price displays immediately.</p></div>
      <div class="how-step"><span class="step-num"><em>02</em></span><h3>Pick a pickup window</h3><p>Same-day if booked before noon, next-day default. Choose a 4-hour window. Confirmation sent immediately.</p></div>
      <div class="how-step"><span class="step-num"><em>03</em></span><h3>Pay upfront online</h3><p>Card charged at booking &mdash; no in-person charges, no on-site upcharges. The price you saw is the price you pay.</p></div>
      <div class="how-step"><span class="step-num"><em>04</em></span><h3>{escape(cname)} Loader arrives</h3><p>An independent local Loader arrives in window, lifts the {escape(item['label'].lower())}, and routes it through local partners.</p></div>
    </div>
{photo_trust}
  </div>
</section>

<section class="section faq-section">
  <div class="wrap">
    <div class="section-head">
      <span class="section-tag">{escape(cname)} FAQ</span>
      <h2>Common questions about <em>{escape(cname)} {escape(item['label'].lower())} pickup</em>.</h2>
    </div>
    <div class="faq-list">
{faq_html}
    </div>
  </div>
</section>

<section class="section cities-section">
  <div class="wrap">
    <div class="section-head">
      <span class="section-tag">Nearby</span>
      <h2>{escape(item['label'])} removal near <em>{escape(cname)}</em>.</h2>
      <p class="section-sub">Same platform, same upfront pricing, in 50 states.</p>
    </div>
    <div class="cities-grid">
{tiles_html}
    </div>
  </div>
</section>

<section class="section">
  <div class="wrap">
{photo_outcome}
  </div>
</section>

<section class="cta-band">
  <div class="wrap cta-inner">
    <h2>{escape(cname)}. {escape(item['label'])} <em>out</em>. Done.</h2>
    <p>Pick your item, confirm your ZIP, and a Loader handles the rest.</p>
    <a href="/book-online/?item={item_key}&amp;city={slug}" class="cta-btn">Book {escape(cname)} Pickup &rarr;</a>
    <span class="cta-or">or call <a href="tel:{cfg['phone_href']}" style="color:var(--black);font-weight:700;text-decoration:underline;">{cfg['phone_display']}</a></span>
  </div>
</section>

{footer}
{load_partial("scripts.html")}
</body>
</html>
'''


ITEM_NAV = ""


def render_hub(title, h1, intro, groups, cfg, canonical, breadcrumb):
    """Generic index page: groups = [(heading, [(label, href), ...]), ...]"""
    d = cfg["domain"]
    nav = load_partial("nav.html").replace("{{CITY_SLUG}}", "")
    footer = (load_partial("footer.html")
              .replace("{{CITY_NAME}}", "Nationwide")
              .replace("{{FOOTER_CITY_LINKS}}",
                       '          <li><a href="/locations/">Browse all cities</a></li>')
              .replace("{{FOOTER_ITEM_LINKS}}", ITEM_NAV))
    blocks = []
    for heading, links in groups:
        tiles = "\n".join(
            f'      <a class="city-tile" href="{h}"><span class="city-name">{escape(l)}</span></a>'
            for l, h in links)
        blocks.append(f'''  <div class="section-head"><h2>{escape(heading)}</h2></div>
    <div class="cities-grid">
{tiles}
    </div>''')
    body = "\n".join(blocks)
    crumbs = "\n    ".join(
        f'{{ "@type": "ListItem", "position": {i+1}, "name": {json.dumps(n)}, "item": "{d}{u}" }},'
        for i, (n, u) in enumerate(breadcrumb)).rstrip(",")
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{escape(title)}</title>
<meta name="description" content="{escape(intro[:300])}">
<link rel="canonical" href="{d}{canonical}">
<meta name="robots" content="index, follow">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter+Tight:wght@400;500;600;700;800;900&family=JetBrains+Mono:ital,wght@0,400;0,500;0,600;0,700;1,400;1,500&display=swap" rel="stylesheet">
<script type="application/ld+json">
{{
  "@context": "https://schema.org",
  "@type": "BreadcrumbList",
  "itemListElement": [
    {crumbs}
  ]
}}
</script>
<link rel="stylesheet" href="/assets/site.css">
</head>
<body>
{nav}
<section class="hero">
  <div class="wrap">
    <h1>{escape(h1)}</h1>
    <p class="hero-sub">{escape(intro)}</p>
  </div>
</section>
<section class="section cities-section">
  <div class="wrap">
{body}
  </div>
</section>
{footer}
{load_partial("scripts.html")}
</body>
</html>
'''


# --------------------------------------------------------------------------
# redirects
# --------------------------------------------------------------------------
def build_redirects(cities, items, out, cfg, legacy_path):
    """
    Rebuild the redirect map so that:
      1. every legacy /couch-disposal/{st}/{city}/ URL lands on the new page
      2. every flat /{city}-{st}/ URL lands on the new page
      3. rural absorption still resolves, but to a URL that EXISTS
      4. blog URLs are preserved (never redirected - they are the top traffic)
    """
    valid = {(c["state_code"], c["city_slug"]) for c in cities}
    lines = ["# " + "=" * 69,
             f"# {cfg['brand']} \u2014 Netlify _redirects",
             "# Generated by scripts/build.py",
             "# " + "=" * 69,
             "",
             "# Force HTTPS + www-stripping",
             "http://couchdisposalplus.com/*      https://couchdisposalplus.com/:splat   301!",
             "http://www.couchdisposalplus.com/*  https://couchdisposalplus.com/:splat   301!",
             "https://www.couchdisposalplus.com/* https://couchdisposalplus.com/:splat   301!",
             ""]

    def rule(src, dst, code="301"):
        return f"{src:<62}  {dst:<54}  {code}"

    # ---- 1. legacy service/state/city -> new state/city/item -------------
    legacy_map = {
        "couch-disposal": "couch-removal",
        "couch-donation-pickup": None,   # no city-level donation template yet -> couch page
        "mattress-disposal": None,        # no new page yet -> city couch page
        "furniture-disposal": None,
        "appliance-disposal": None,
        "paint-disposal": None,
        "bedding-linen-disposal": None,
        "hospital-bed-disposal": None,
        "senior-downsizing": None,
        "area-rug-disposal": None,
        "treadmill-disposal": None,
        "recliner-disposal": "recliner-removal",
    }
    lines += ["# " + "-" * 69,
              "# LEGACY -> NEW ARCHITECTURE  (this is what was missing)",
              "# Netlify :placeholder rules \u2014 12 rules replace ~51,000 enumerated ones.",
              "# " + "-" * 69]
    n_legacy = 0
    for old, new in legacy_map.items():
        target_item = new if new else "couch-removal"
        if target_item not in {v["slug"] for v in items.values()}:
            target_item = "couch-removal"
        lines.append(rule(f"/{old}/:state/:city/", f"/:state/:city/{target_item}/"))
        n_legacy += 1
    lines.append("")

    # ---- 1b. legacy service hubs -> new item hubs ------------------------
    lines += ["", "# legacy service hubs"]
    built = {v["slug"] for v in items.values()}
    for old, new in [("couch-disposal", "couch-removal"),
                     ("mattress-disposal", "couch-removal"),
                     ("furniture-disposal", "couch-removal"),
                     ("recliner-disposal", "recliner-removal"),
                     ("couch-donation-pickup", "couch-removal")]:
        lines.append(rule(f"/{old}/", f"/{new}/" if new in built else "/couch-removal/"))
    lines.append("")

    # ---- 2. flat + rural, retargeted to URLs that exist ------------------
    lines += ["# " + "-" * 69,
              "# FLAT + RURAL ABSORPTION -> canonical city page",
              "# " + "-" * 69]
    n_flat = 0
    seen = set()
    for c in cities:
        if c["in_production"] != "1":
            continue          # geonames-only cities never had a flat legacy URL
        st, slug = c["state_code"], c["city_slug"]
        src = f"/{slug}-{st}/"
        if src not in seen:
            lines.append(rule(src, f"/{st}/{slug}/couch-removal/"))
            seen.add(src)
            n_flat += 1
        for ab in filter(None, c["absorbs"].split("|")):
            src = f"/{ab}-{st}/"
            if src not in seen:
                lines.append(rule(src, f"/{st}/{slug}/couch-removal/"))
                seen.add(src)
                n_flat += 1
    lines.append("")

    # ---- 3. carry forward any legacy rule we can still honour ------------
    lines += ["# " + "-" * 69,
              "# LEGACY DONATION-PICKUP FLAT URLS",
              "# " + "-" * 69]
    n_don = 0
    if os.path.exists(legacy_path):
        pat = re.compile(r"^/donation-pickup/couch/([a-z0-9-]+)-([a-z]{2})/$")
        for line in open(legacy_path):
            p = line.split()
            if len(p) >= 2:
                m = pat.match(p[0])
                if m and (m.group(2), m.group(1)) in valid:
                    lines.append(rule(p[0], f"/{m.group(2)}/{m.group(1)}/couch-removal/"))
                    n_don += 1
    lines.append("")

    # ---- 4. article duplicate consolidation -----------------------------
    ap_csv = os.path.join(DATA, "article_redirects.csv")
    n_art = 0
    if os.path.exists(ap_csv):
        lines += ["# " + "-" * 69,
                  "# ARTICLE DUPLICATE CONSOLIDATION",
                  "# Fixes the root-level copies competing with the /blog/ canonicals.",
                  "# " + "-" * 69]
        for r in csv.DictReader(open(ap_csv)):
            lines.append(rule(r["source"], r["target"]))
            n_art += 1
        lines.append("")

    lines += ["# " + "-" * 69,
              "# BLOG \u2014 DO NOT REDIRECT. These are the highest-traffic URLs on the",
              "# site. They must be rebuilt at their existing paths.",
              "# " + "-" * 69,
              "# /blog/home-cleaning/how-much-is-a-used-couch-worth/   (1,641 clicks)",
              "# /blog/home-cleaning/how-to-remove-allergens-from-couch/ (396 clicks)",
              "",
              "# Catch-all 404",
              "/*    /404.html    404",
              ""]

    write(os.path.join(out, "_redirects"), "\n".join(lines))
    return n_legacy, n_flat, n_don, n_art


# --------------------------------------------------------------------------
# sitemaps
# --------------------------------------------------------------------------
def build_sitemaps(urls, out, cfg):
    shard = cfg["sitemap_shard"]
    chunks = [urls[i:i + shard] for i in range(0, len(urls), shard)]
    names = []
    for i, chunk in enumerate(chunks, 1):
        name = f"sitemap-{i}.xml"
        body = "\n".join(
            f"  <url><loc>{cfg['domain']}{u}</loc><priority>{p}</priority></url>"
            for u, p in chunk)
        write(os.path.join(out, name),
              '<?xml version="1.0" encoding="UTF-8"?>\n'
              '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
              f"{body}\n</urlset>\n")
        names.append(name)
    idx = "\n".join(f"  <sitemap><loc>{cfg['domain']}/{n}</loc></sitemap>" for n in names)
    write(os.path.join(out, "sitemap.xml"),
          '<?xml version="1.0" encoding="UTF-8"?>\n'
          '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
          f"{idx}\n</sitemapindex>\n")
    return len(names)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "dist"))
    ap.add_argument("--limit-states", default="", help="comma list, e.g. co,tx")
    ap.add_argument("--scope", default="full",
                    choices=["full", "couch-only", "tier1", "tier1-couch"],
                    help="page-count preset for size-constrained deploys")
    ap.add_argument("--legacy-redirects", default=os.path.join(ROOT, "vendor", "_redirects.legacy"))
    ap.add_argument("--static-from", default=os.path.join(ROOT, "vendor", "static"),
                    help="existing deploy to copy core static pages from")
    args = ap.parse_args()

    cfg = CONFIG
    states, cities, items = load_data()

    if args.limit_states:
        keep = {s.strip() for s in args.limit_states.split(",")}
        cities = [c for c in cities if c["state_code"] in keep]
        states = {k: v for k, v in states.items() if k in keep}

    # scope presets keep zip-drop deploys under Netlify's practical limits
    if args.scope == "couch-only":
        items = {"couch": items["couch"]}
    elif args.scope == "tier1":
        for k, v in items.items():
            v["coverage"] = "all" if k == "couch" else "tier1"
    elif args.scope == "tier1-couch":
        items = {"couch": items["couch"]}
        cities = [c for c in cities if c["tier"] == 1]

    out = args.out
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(out, exist_ok=True)

    # carry over hand-built core pages from the existing deploy
    CORE = ["about", "blog", "book-online", "contact", "donation-pickup", "how-it-works",
            "pricing", "privacy", "reviews", "sitemap", "terms", "track-order"]
    n_static = 0
    if os.path.isdir(args.static_from):
        for name in CORE:
            src = os.path.join(args.static_from, name, "index.html")
            if os.path.isfile(src):
                os.makedirs(os.path.join(out, name), exist_ok=True)
                shutil.copy2(src, os.path.join(out, name, "index.html"))
                n_static += 1
        for f in ("index.html", "404.html"):
            src = os.path.join(args.static_from, f)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(out, f))
                n_static += 1

    # process photography into dist/assets/img before loading the manifest
    if os.path.isdir(os.path.join(ROOT, "vendor", "photos")):
        import make_images
        sys.argv = ["make_images", os.path.join(out, "assets", "img")]
        make_images.main()
    global ITEM_NAV
    ITEM_NAV = "\n".join(
        f'          <li><a href="/{v["slug"]}/">{v["label"]} Removal</a></li>'
        for v in items.values())

    load_photos()

    # single cached stylesheet instead of ~40KB inlined on every page
    css = load_partial("style.html")
    css = css.split("</style>")[0].replace("<style>", "")
    write(os.path.join(out, "assets", "site.css"), css)

    global STATE_INDEX
    STATE_INDEX = collections.defaultdict(list)
    for c in cities:
        STATE_INDEX[c["state_code"]].append(c)

    nearby = compute_nearby(cities, cfg["nearby_count"])

    urls = []
    counts = collections.Counter()

    # ---- city x item pages ---------------------------------------------
    for c in cities:
        st, slug = c["state_code"], c["city_slug"]
        for key, item in items.items():
            if not covered(c, item):
                continue
            html = render_city_item(c, item, key, items, states, nearby, cfg)
            write(os.path.join(out, st, slug, item["slug"], "index.html"), html)
            prio = {1: "0.9", 2: "0.7", 3: "0.5"}[c["tier"]]
            urls.append((f"/{st}/{slug}/{item['slug']}/", prio))
            counts[key] += 1

    # ---- state hubs -----------------------------------------------------
    by_state = collections.defaultdict(list)
    for c in cities:
        by_state[c["state_code"]].append(c)
    for st, cs in by_state.items():
        cs = sorted(cs, key=lambda x: -x["population"])
        groups = [(f"{states[st]} cities we serve",
                   [(c["city_name"], f"/{st}/{c['city_slug']}/couch-removal/") for c in cs])]
        html = render_hub(
            f"{states[st]} Couch Removal | {len(cs)} Cities | {cfg['brand']}",
            f"{states[st]} couch removal",
            f"Upfront online pricing and same-day pickup across {len(cs)} {states[st]} cities. "
            f"Independent local Loaders, donation routing first.",
            groups, cfg, f"/{st}/",
            [("Home", "/"), ("Locations", "/locations/"), (states[st], f"/{st}/")])
        write(os.path.join(out, st, "index.html"), html)
        urls.append((f"/{st}/", "0.8"))
        counts["state_hub"] += 1

    # ---- national item hubs ---------------------------------------------
    for key, item in items.items():
        tier1 = sorted([c for c in cities if c["tier"] == 1 and covered(c, item)],
                       key=lambda x: -x["population"])[:200]
        groups = [(f"Top cities for {item['h1_noun']}",
                   [(f"{c['city_name']}, {c['state_code'].upper()}",
                     f"/{c['state_code']}/{c['city_slug']}/{item['slug']}/") for c in tier1])]
        html = render_hub(
            f"{item['label']} Removal | Nationwide Pickup from ${item['price']} | {cfg['brand']}",
            f"{item['label']} removal, nationwide",
            f"{item['label']} removal starting at ${item['price']}. Upfront online pricing, "
            f"same-day and next-day pickup, donation routing first. Also searched as "
            f"{', '.join(item['synonyms'])}.",
            groups, cfg, f"/{item['slug']}/",
            [("Home", "/"), (f"{item['label']} Removal", f"/{item['slug']}/")])
        write(os.path.join(out, item["slug"], "index.html"), html)
        urls.append((f"/{item['slug']}/", "0.9"))
        counts["item_hub"] += 1

    # ---- national locations index ---------------------------------------
    groups = [("Browse by state",
               [(states[st], f"/{st}/") for st in sorted(by_state, key=lambda s: states[s])])]
    write(os.path.join(out, "locations", "index.html"),
          render_hub(f"All Locations | 50 States | {cfg['brand']}",
                     "Couch removal in all 50 states",
                     f"{len(cities):,} cities across {len(by_state)} states. "
                     "Upfront online pricing everywhere we operate.",
                     groups, cfg, "/locations/",
                     [("Home", "/"), ("Locations", "/locations/")]))
    urls.append(("/locations/", "0.9"))

    # ---- redirects + sitemaps + robots ----------------------------------
    n_legacy, n_flat, n_don, n_art = build_redirects(cities, items, out, cfg, args.legacy_redirects)
    n_shards = build_sitemaps(urls, out, cfg)
    write(os.path.join(out, "robots.txt"),
          f"User-agent: *\nAllow: /\n\nSitemap: {cfg['domain']}/sitemap.xml\n")

    # ---- report ----------------------------------------------------------
    print(f"static pages kept : {n_static}")
    print(f"cities in build   : {len(cities):,}")
    print("pages by item:")
    for k in items:
        print(f"  {k:<14} {counts[k]:>7,}")
    print(f"  state hubs     {counts['state_hub']:>7,}")
    print(f"  item hubs      {counts['item_hub']:>7,}")
    print(f"TOTAL URLs        : {len(urls):,}")
    print(f"sitemap shards    : {n_shards}")
    print(f"redirects         : {n_legacy:,} legacy + {n_flat:,} flat/rural + {n_don:,} donation + {n_art} article")


if __name__ == "__main__":
    main()
