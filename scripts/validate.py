#!/usr/bin/env python3
"""
validate.py — pre-deploy checks on a built dist/.

  1. internal link integrity (every href resolves to a file or a redirect rule)
  2. JSON-LD blocks parse as valid JSON
  3. canonical matches the file's own path
  4. title/meta-description uniqueness + length
  5. near-duplicate body text across item pages in the same city
"""
import json, os, re, sys, csv, collections, difflib

OUT = sys.argv[1] if len(sys.argv) > 1 else "dist"

pages = []
for root, dirs, files in os.walk(OUT):
    for f in files:
        if f.endswith(".html"):
            pages.append(os.path.join(root, f))

# redirect sources, so links that 301 aren't flagged as broken
redirect_src = set()
rp = os.path.join(OUT, "_redirects")
if os.path.exists(rp):
    for line in open(rp):
        line = line.strip()
        if line and not line.startswith("#"):
            p = line.split()
            if p:
                redirect_src.add(p[0].rstrip("*"))


def resolves(href):
    p = href.split("?")[0].split("#")[0]
    if not p.startswith("/"):
        return True
    rel = p.strip("/")
    if rel == "":
        return os.path.isfile(os.path.join(OUT, "index.html"))
    if os.path.isfile(os.path.join(OUT, rel, "index.html")):
        return True
    if os.path.isfile(os.path.join(OUT, rel)):
        return True
    return p in redirect_src


broken = collections.Counter()
missing_img = collections.Counter()
no_dims = 0
bad_json = []
bad_canon = []
titles = collections.Counter()
metas = collections.Counter()
long_titles = 0
bodies = {}

TITLE = re.compile(r"<title>(.*?)</title>", re.S)
META = re.compile(r'<meta name="description" content="(.*?)"')
CANON = re.compile(r'<link rel="canonical" href="(.*?)"')
LD = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
HREF = re.compile(r'href="(/[^"]*)"')
TAGS = re.compile(r"<[^>]+>")

for pg in pages:
    html = open(pg, encoding="utf-8").read()
    rel = "/" + os.path.relpath(pg, OUT).replace("\\", "/")
    rel = rel.replace("/index.html", "/")

    for h in set(HREF.findall(html)):
        if not resolves(h):
            broken[h.split("?")[0]] += 1

    for src in re.findall(r'srcset="([^"]*)"', html):
        for part in src.split(","):
            u = part.strip().split(" ")[0]
            if u.startswith("/") and not os.path.isfile(os.path.join(OUT, u.strip("/"))):
                missing_img[u] += 1
    for tag in re.findall(r"<img [^>]*>", html):
        if 'width="' not in tag or 'height="' not in tag:
            no_dims += 1

    for blk in LD.findall(html):
        try:
            json.loads(blk)
        except Exception as e:
            bad_json.append((rel, str(e)[:70]))

    t = TITLE.search(html)
    if t:
        titles[t.group(1)] += 1
        if len(t.group(1)) > 65:
            long_titles += 1
    m = META.search(html)
    if m:
        metas[m.group(1)] += 1
    c = CANON.search(html)
    if c and not c.group(1).endswith(rel):
        bad_canon.append((rel, c.group(1)))

    # strip chrome, keep the unique middle of the page
    body = html.split('<section class="hero">')[-1].split('<section class="cities-section"')[0]
    bodies[rel] = re.sub(r"\s+", " ", TAGS.sub(" ", body)).strip()

# near-duplicate check: compare item pages within the same city
by_city = collections.defaultdict(list)
for rel in bodies:
    parts = rel.strip("/").split("/")
    if len(parts) == 3:
        by_city["/".join(parts[:2])].append(rel)

dupes = []
sample = list(by_city.items())[:400]      # real ratio is O(n^2); sample is enough
for city, rels in sample:
    for i in range(len(rels)):
        for j in range(i + 1, len(rels)):
            r = difflib.SequenceMatcher(None, bodies[rels[i]], bodies[rels[j]]).ratio()
            if r > 0.92:
                dupes.append((rels[i], rels[j], round(r, 3)))

print(f"pages checked        : {len(pages):,}")
print(f"broken internal links: {sum(broken.values())} across {len(broken)} distinct targets")
for k, v in broken.most_common(10):
    print(f"    {v:>4}  {k}")
print(f"missing image files  : {sum(missing_img.values())} refs, {len(missing_img)} distinct")
for k, v_ in missing_img.most_common(5):
    print(f"    {v_:>4}  {k}")
print(f"img without dimensions: {no_dims}")
print(f"invalid JSON-LD      : {len(bad_json)}")
for r, e in bad_json[:5]:
    print(f"    {r}: {e}")
print(f"canonical mismatches : {len(bad_canon)}")
for r, c in bad_canon[:5]:
    print(f"    {r} -> {c}")
dup_t = sum(v for v in titles.values() if v > 1)
dup_m = sum(v for v in metas.values() if v > 1)
print(f"duplicate titles     : {dup_t}")
print(f"duplicate metas      : {dup_m}")
print(f"titles over 65 chars : {long_titles}")
print(f"near-dup item pages  : {len(dupes)} pairs >0.92 (real ratio, {len(sample)}-city sample)")
for a, b, r in dupes[:5]:
    print(f"    {r}  {a}  vs  {b}")
