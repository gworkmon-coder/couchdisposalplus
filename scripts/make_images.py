#!/usr/bin/env python3
"""
make_images.py — process brand photography into responsive web assets.

Source photos are 1024px wide, so 1024 is the ceiling. Emits WebP (primary) and
JPEG (fallback) at 480 / 768 / 1024 into dist/assets/img/, and writes
data/photo_manifest.json with real dimensions for width/height attributes
(prevents layout shift).

Run before build.py, or just use build.sh which sequences it.
"""
import json, os, sys
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "vendor", "photos")
WIDTHS = [480, 768, 1024]
WEBP_Q = 82
JPEG_Q = 80


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "dist", "assets", "img")
    os.makedirs(out_dir, exist_ok=True)

    meta = json.load(open(os.path.join(ROOT, "data", "photos.json")))
    manifest = {}
    src_bytes = out_bytes = 0

    for key in sorted(meta):
        path = os.path.join(SRC, key + ".jpg")
        if not os.path.isfile(path):
            print(f"  MISSING {key}.jpg")
            continue
        src_bytes += os.path.getsize(path)
        im = Image.open(path).convert("RGB")
        w0, h0 = im.size
        variants = []
        for w in WIDTHS:
            if w > w0:
                continue
            h = round(h0 * w / w0)
            resized = im.resize((w, h), Image.LANCZOS)
            wp = os.path.join(out_dir, f"{key}-{w}.webp")
            jp = os.path.join(out_dir, f"{key}-{w}.jpg")
            resized.save(wp, "WEBP", quality=WEBP_Q, method=6)
            resized.save(jp, "JPEG", quality=JPEG_Q, optimize=True, progressive=True)
            out_bytes += os.path.getsize(wp) + os.path.getsize(jp)
            variants.append({"w": w, "h": h})
        if not variants:
            continue
        largest = variants[-1]
        manifest[key] = {
            "widths": [v["w"] for v in variants],
            "width": largest["w"],
            "height": largest["h"],
            "aspect": round(w0 / h0, 4),
            "category": meta[key]["category"],
            "slot": meta[key]["slot"],
            "alt": meta[key]["alt"],
            "caption": meta[key]["caption"],
        }

    with open(os.path.join(ROOT, "data", "photo_manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    by_slot = {}
    for k, v in manifest.items():
        by_slot.setdefault(v["slot"], []).append(k)
    print(f"photos processed : {len(manifest)}")
    print(f"variants written : {sum(len(v['widths']) for v in manifest.values()) * 2} files "
          f"(webp + jpeg at {WIDTHS})")
    print(f"source           : {src_bytes/1024/1024:.1f} MB")
    print(f"output           : {out_bytes/1024/1024:.1f} MB")
    for s in sorted(by_slot):
        print(f"  slot {s:<9} {len(by_slot[s])} photos")


if __name__ == "__main__":
    main()
