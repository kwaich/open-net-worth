"""Bake the stand-alone mark into plain geometry: the disc minus the widened facet cuts.

WebKit renders an SVG <mask> inside an <img> at 1x, so the masked mark was blurry on every
iPhone and in Safari. This writes the same shape as one even-odd path, no mask.

    uv run --with shapely --with svgelements docs-site/scripts/brand-source/bake-mark.py
"""

import re
from pathlib import Path

from shapely.geometry import Point, Polygon
from shapely.ops import unary_union
from svgelements import Close, Move
from svgelements import Path as SvgPath

HERE = Path(__file__).parent
BOLD = 12  # keep in step with generate-brand-assets.mjs
RADIUS = 320

source = (HERE / "logo.svg").read_text()
facets = re.search(r'<path id="facet-cuts"[^>]* d="([^"]+)"', source).group(1)

rings, ring = [], []
for seg in SvgPath(facets):
    if isinstance(seg, Move):
        ring = [seg.end]
    elif isinstance(seg, Close):
        rings.append(ring)
        ring = []
    else:
        steps = 1 if type(seg).__name__ == "Line" else 24
        ring.extend(seg.point(i / steps) for i in range(1, steps + 1))

cuts = unary_union([Polygon([(p.x, p.y) for p in r]).buffer(0) for r in rings if len(r) > 2])
cuts = cuts.buffer(BOLD / 2, join_style="round", quad_segs=8)
mark = Point(0, 0).buffer(RADIUS, quad_segs=64).difference(cuts)


def ring_d(coords):
    pts = [f"{x:.2f} {y:.2f}".replace(".00", "") for x, y in list(coords)[:-1]]
    return "M" + "L".join(pts) + "Z"


parts = list(getattr(mark, "geoms", [mark]))
d = "".join(ring_d(r.coords) for p in parts for r in [p.exterior, *p.interiors])
(HERE / "logo-mark.d").write_text(d + "\n")
print(f"{len(parts)} parts, {len(d)} chars")
