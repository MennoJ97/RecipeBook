#!/usr/bin/env python3
"""Draws the Çok Lekker app icons: the tea glass (draft D, "İnce belli", in logo-drafts.html).

Writes the SVG sources into static/icons/ and renders the PNGs the manifest and iOS use.
macOS only: the PNGs come from Quick Look (qlmanage), which renders SVG with WebKit,
then Pillow scales them down. Run from the repo root: python3 design/make-icons.py
"""
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

OUT = Path(__file__).resolve().parent.parent / "static" / "icons"
GLASS = "M180 150 C182 210 220 228 220 268 C220 310 196 330 198 368 L314 368 C316 330 292 310 292 268 C292 228 330 210 332 150 Z"

def icon(scale, dy, *, steam=True, shine=True, radius=0, outline=8, label="Çok Lekker"):
    # The scene is drawn around (256, 232) at scale 1; `scale` and `dy` place it on the 512 canvas.
    steam_paths = '''
    <path d="M232 128 C214 108 250 92 232 66" stroke="#fffdf8" stroke-width="10" fill="none" stroke-linecap="round" opacity=".75"/>
    <path d="M280 132 C262 112 298 96 280 70" stroke="#fffdf8" stroke-width="10" fill="none" stroke-linecap="round" opacity=".75"/>''' if steam else ''
    shine_path = '''
      <path d="M200 200 C206 230 228 244 230 268" stroke="#fffdf8" stroke-width="9" fill="none" stroke-linecap="round" opacity=".35"/>''' if shine else ''
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" role="img" aria-label="{label}">
  <defs><clipPath id="glass"><path d="{GLASS}"/></clipPath></defs>
  <rect width="512" height="512" rx="{radius}" fill="#2348a0"/>
  <g transform="translate(256 {232 + dy}) scale({scale}) translate(-256 -232)">{steam_paths}
    <ellipse cx="256" cy="374" rx="124" ry="24" fill="#fffdf8"/>
    <ellipse cx="256" cy="370" rx="124" ry="24" fill="none" stroke="#e0a030" stroke-width="6"/>
    <g clip-path="url(#glass)">
      <rect x="170" y="140" width="172" height="240" fill="#fffdf8" opacity=".22"/>
      <rect x="170" y="186" width="172" height="200" fill="#b8341e"/>
      <rect x="170" y="186" width="172" height="10" fill="#d9573a"/>{shine_path}
    </g>
    <path d="{GLASS}" fill="none" stroke="#fffdf8" stroke-width="{outline}" stroke-linejoin="round"/>
    <rect x="176" y="146" width="160" height="10" rx="5" fill="#e0a030"/>
  </g>
</svg>
'''

# Full-bleed square: "any" icons and the iPhone home screen (iOS rounds the corners itself).
(OUT / "icon.svg").write_text(icon(1.16, 4))
# Maskable: everything inside the 80% safe circle, since Android crops to its own shape.
(OUT / "icon-maskable.svg").write_text(icon(0.98, 24))
# Favicon and header mark: rounded tile, no steam or shine, heavier lines so it holds at 16 px.
(OUT / "favicon.svg").write_text(icon(1.34, 22, steam=False, shine=False, radius=112, outline=14))

with tempfile.TemporaryDirectory() as tmp:
    for name in ("icon", "icon-maskable"):
        subprocess.run(["qlmanage", "-t", "-s", "512", "-o", tmp, OUT / f"{name}.svg"],
                       check=True, capture_output=True)
    full = Image.open(Path(tmp) / "icon.svg.png").convert("RGB")
    full.save(OUT / "icon-512.png", optimize=True)
    full.resize((192, 192), Image.LANCZOS).save(OUT / "icon-192.png", optimize=True)
    full.resize((180, 180), Image.LANCZOS).save(OUT / "apple-touch-icon.png", optimize=True)
    Image.open(Path(tmp) / "icon-maskable.svg.png").convert("RGB").save(OUT / "icon-maskable-512.png", optimize=True)
