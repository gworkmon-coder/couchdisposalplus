#!/usr/bin/env python3
"""
make_blog.py — blog section for the new build.

Two sources of truth:
  1. vendor/blog-content/*.md      posts restored verbatim from the live
                                   WordPress site (front-matter + markdown)
  2. data/blog_index.json          the known post inventory (from GSC), used
                                   to list posts on index/category pages even
                                   when their body isn't statically restored.
                                   Those links resolve through the WordPress
                                   proxy rule in _redirects once WP_ORIGIN is
                                   configured.

Emits: /blog/, /blog/<category>/, /blog/<category>/<slug>/ for restored posts.
Called from build.py; uses its partials and CSS pipeline.
"""
import json, os, re
from html import escape
from localcontent import GTM_HEAD_SNIPPET as GTM_HEAD

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse_post(path):
    raw = open(path, encoding="utf-8").read()
    _, fm, body = raw.split("---", 2)
    meta = {}
    for line in fm.strip().split("\n"):
        k, v = line.split(":", 1)
        meta[k.strip()] = v.strip()
    return meta, body.strip()


def md_to_html(md):
    """Small, deliberate markdown subset: h2/h3, tables, ul, bold, links, p."""
    out, i = [], 0
    lines = md.split("\n")
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1; continue
        if line.startswith("### "):
            out.append(f"<h3>{inline(line[4:])}</h3>")
        elif line.startswith("## "):
            out.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.startswith("| "):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip("|").split("|")])
                i += 1
            i -= 1
            head, body_rows = rows[0], [r for r in rows[1:] if not set("".join(r)) <= set("- ")]
            thead = "".join(f"<th>{inline(c)}</th>" for c in head)
            tbody = "\n".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>"
                              for r in body_rows)
            out.append(f'<div class="post-table-wrap"><table class="post-table">'
                       f"<thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table></div>")
        elif line.startswith("- "):
            items = []
            while i < len(lines) and lines[i].startswith("- "):
                items.append(f"<li>{inline(lines[i][2:])}</li>")
                i += 1
            i -= 1
            out.append("<ul>" + "".join(items) + "</ul>")
        else:
            para = [line]
            while i + 1 < len(lines) and lines[i + 1].strip() and not re.match(r"^(#|\||- )", lines[i + 1]):
                i += 1
                para.append(lines[i])
            out.append(f"<p>{inline(' '.join(para))}</p>")
        i += 1
    return "\n      ".join(out)


def inline(t):
    t = escape(t, quote=False)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', t)
    return t


def hero_picture(key, photos, alt):
    if not key or key not in photos:
        return "", ""
    hp = photos[key]
    webp = ", ".join(f"/assets/img/{key}-{w}.webp {w}w" for w in hp["widths"])
    jpg = ", ".join(f"/assets/img/{key}-{w}.jpg {w}w" for w in hp["widths"])
    fig = f'''    <figure class="post-hero">
      <picture>
        <source type="image/webp" srcset="{webp}" sizes="(max-width: 820px) 100vw, 780px">
        <img src="/assets/img/{key}-{hp['widths'][-1]}.jpg" srcset="{jpg}"
             sizes="(max-width: 820px) 100vw, 780px" width="{hp['width']}" height="{hp['height']}"
             loading="eager" fetchpriority="high" decoding="async" alt="{escape(alt)}">
      </picture>
    </figure>'''
    return fig, f"/assets/img/{key}-{hp['widths'][-1]}.jpg"


def shell(cfg, load_partial, title, desc, canonical, body, breadcrumb_html, schema=""):
    nav = load_partial("nav.html").replace("{{CITY_SLUG}}", "")
    footer = (load_partial("footer.html")
              .replace("{{CITY_NAME}}", "Nationwide")
              .replace("{{FOOTER_CITY_LINKS}}",
                       '          <li><a href="/locations/">Browse all cities</a></li>'))
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{escape(title)}</title>
<meta name="description" content="{escape(desc)}">
<link rel="canonical" href="{cfg['domain']}{canonical}">
<meta name="robots" content="index, follow, max-image-preview:large">
{GTM_HEAD}
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter+Tight:wght@400;500;600;700;800;900&family=JetBrains+Mono:ital,wght@0,400;0,500;0,600;0,700;1,400;1,500&display=swap" rel="stylesheet">
{schema}<link rel="stylesheet" href="/assets/site.css">
</head>
<body>
{nav}
<div class="wrap">
  <nav class="breadcrumb" aria-label="Breadcrumb">{breadcrumb_html}</nav>
</div>
{body}
{footer}
{load_partial("scripts.html")}
</body>
</html>
'''


def build_blog(out, cfg, load_partial, write, photos=None):
    photos = photos or {}
    inv = json.load(open(os.path.join(ROOT, "data", "blog_index.json")))
    cats = inv["categories"]
    posts_meta = {p["path"]: p for p in inv["posts"]}

    # ---- restored posts --------------------------------------------------
    restored = set()
    cdir = os.path.join(ROOT, "vendor", "blog-content")
    if os.path.isdir(cdir):
        for f in sorted(os.listdir(cdir)):
            if not f.endswith(".md"):
                continue
            meta, md = parse_post(os.path.join(cdir, f))
            path = meta["path"]
            hero_fig, hero_url = hero_picture(meta.get("hero_image"), photos,
                                              meta.get("hero_alt", meta["h1"]))
            restored.add(path)
            html_body = md_to_html(md)
            faq_blocks = [(q, a) for q, a in
                          re.findall(r"<h3>(.*?)</h3>\n      <p>(.*?)</p>", html_body)
                          if q.strip().endswith("?")]
            faq_schema = ""
            if faq_blocks:
                ents = ",\n    ".join(
                    '{ "@type": "Question", "name": %s, "acceptedAnswer": { "@type": "Answer", "text": %s } }'
                    % (json.dumps(re.sub("<[^>]+>", "", q)), json.dumps(re.sub("<[^>]+>", "", a)))
                    for q, a in faq_blocks)
                faq_schema = ('<script type="application/ld+json">\n{ "@context": "https://schema.org", '
                              f'"@type": "FAQPage", "mainEntity": [\n    {ents}\n  ] }}\n</script>\n')
            img_part = ('"image": %s, ' % json.dumps(cfg["domain"] + hero_url)) if hero_url else ""
            art_schema = ('<script type="application/ld+json">\n{ "@context": "https://schema.org", '
                          '"@type": "Article", "headline": %s, %s"datePublished": %s, '
                          '"author": { "@type": "Person", "name": %s }, '
                          '"publisher": { "@type": "Organization", "name": %s }, '
                          '"mainEntityOfPage": %s }\n</script>\n'
                          % (json.dumps(meta["h1"]), img_part, json.dumps(meta["date"]),
                             json.dumps(meta["author"]), json.dumps(cfg["brand"]),
                             json.dumps(cfg["domain"] + path)))
            if hero_url:
                art_schema = ('<meta property="og:image" content="%s">\n' % (cfg["domain"] + hero_url)) + art_schema
            crumbs = (f'<a href="/">Home</a><span class="sep">/</span>'
                      f'<a href="/blog/">Blog</a><span class="sep">/</span>'
                      f'<a href="/blog/{meta["category"]}/">{escape(meta["category_name"])}</a>'
                      f'<span class="sep">/</span><span class="current">{escape(meta["h1"])}</span>')
            body = f'''<article class="section post">
  <div class="wrap post-wrap">
    <span class="section-tag">{escape(meta["category_name"])} &middot; {escape(meta["date"])}</span>
    <h1>{escape(meta["h1"])}</h1>
    <p class="post-byline">By {escape(meta["author"])} &middot; Couch Disposal Plus</p>
{hero_fig}
    <div class="post-body">
      {html_body}
    </div>
    <div class="post-cta">
      <h2>Ready to lose the couch?</h2>
      <p>Upfront price online. Same-day pickup available. Donation routing first.</p>
      <button data-workmon-open class="btn-price">Get Instant Price &rarr;</button>
    </div>
  </div>
</article>'''
            write(os.path.join(out, path.strip("/"), "index.html"),
                  shell(cfg, load_partial, meta["title"], meta["description"], path,
                        body, crumbs, art_schema + faq_schema))

    # ---- category pages ----------------------------------------------------
    for cslug, cname in cats.items():
        cposts = [p for p in inv["posts"] if p["category"] == cslug]
        cards = "\n".join(
            f'''      <a class="city-tile post-tile" href="{p["path"]}">
        <span class="city-name">{escape(p["title"])}</span>
        <span class="city-price">{"Read the guide" if p["path"] in restored else "Read on the blog"} &rarr;</span>
      </a>''' for p in cposts)
        crumbs = (f'<a href="/">Home</a><span class="sep">/</span><a href="/blog/">Blog</a>'
                  f'<span class="sep">/</span><span class="current">{escape(cname)}</span>')
        body = f'''<section class="hero"><div class="wrap">
    <h1>{escape(cname)}</h1>
    <p class="hero-sub">Guides from the Couch Disposal Plus team.</p>
  </div></section>
<section class="section cities-section"><div class="wrap">
    <div class="cities-grid">
{cards}
    </div>
  </div></section>'''
        write(os.path.join(out, "blog", cslug, "index.html"),
              shell(cfg, load_partial, f"{cname} | Couch Disposal Plus Blog",
                    f"{cname} guides from Couch Disposal Plus.",
                    f"/blog/{cslug}/", body, crumbs))

    # ---- blog index --------------------------------------------------------
    by_cat = []
    for cslug, cname in cats.items():
        cposts = [p for p in inv["posts"] if p["category"] == cslug]
        tiles = "\n".join(
            f'''      <a class="city-tile post-tile" href="{p["path"]}">
        <span class="city-name">{escape(p["title"])}</span>
        <span class="city-price">Read &rarr;</span>
      </a>''' for p in cposts)
        by_cat.append(f'''  <div class="section-head"><h2>{escape(cname)}</h2></div>
    <div class="cities-grid">
{tiles}
    </div>''')
    crumbs = '<a href="/">Home</a><span class="sep">/</span><span class="current">Blog</span>'
    body = f'''<section class="hero"><div class="wrap">
    <h1>The Couch Disposal Plus blog</h1>
    <p class="hero-sub">Removal guides, cleaning fixes, donation advice, and what actually happens to a couch after it leaves your living room.</p>
  </div></section>
<section class="section cities-section"><div class="wrap">
{chr(10).join(by_cat)}
  </div></section>'''
    write(os.path.join(out, "blog", "index.html"),
          shell(cfg, load_partial, "Couch Removal Tips & Guides | Couch Disposal Plus Blog",
                "Couch removal tips, disposal guides, and donation advice from Couch Disposal Plus.",
                "/blog/", body, crumbs))

    return len(restored), len(inv["posts"])
