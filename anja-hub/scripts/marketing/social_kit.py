"""social_kit.py — generatore kit social (carosello + PDF + README).

Portato da anja-marketer (`scripts/gen_kit_*`): core PIL riusabile per produrre un kit
social nello stile del brand. Slide 1080×1350, palette brand, top-bar gradiente
viola→magenta, footer brand, PDF LinkedIn (reportlab). Deterministico: il CONTENUTO
(le slide) lo passa il social agent; qui si **rende e si assembla** il kit.

Output in `<ws>/files/social/<campagna>/`:
  slide-01..N.png (+ .jpg per IG che rifiuta i PNG) · carosello-linkedin.pdf ·
  hero.png (se passato) · README.md (la copy) · media-urls.json (placeholder).

Spec di una slide (dict): {kicker, title, sub?, body?, items?[[head,txt],...],
  title_size?, body_size?, accent?('white'|'green'|...), scorri?}.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1350
SAFE_BOTTOM = H - 280

# Palette brand di default (BRAND.md). Sovrascrivibile via `palette`.
DEFAULT_PALETTE = {
    "bg": (15, 14, 23), "purple": (145, 12, 230), "magenta": (240, 0, 105),
    "white": (255, 255, 255), "muted": (155, 151, 168), "green": (0, 208, 132),
}

_SUP = "/System/Library/Fonts/Supplemental/"
_FONT_CANDIDATES = {
    "black": [_SUP + "Arial Black.ttf", "/Library/Fonts/Arial Black.ttf"],
    "bold": [_SUP + "Arial Bold.ttf", "/Library/Fonts/Arial Bold.ttf"],
    "reg": [_SUP + "Arial.ttf", "/Library/Fonts/Arial.ttf"],
}


def _font(kind: str, size: int):
    for p in _FONT_CANDIDATES[kind]:
        if Path(p).is_file():
            return ImageFont.truetype(p, size)
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)  # bundled con Pillow
    except Exception:
        return ImageFont.load_default()


def _base(pal: dict):
    img = Image.new("RGB", (W, H), pal["bg"])
    d = ImageDraw.Draw(img)
    for x in range(W):
        t = x / W
        c = tuple(int(pal["purple"][i] + (pal["magenta"][i] - pal["purple"][i]) * t) for i in range(3))
        d.line([(x, 0), (x, 26)], fill=c)
    return img, d


def _wrap(d, text, fnt, max_w):
    out = []
    for para in str(text).split("\n"):
        cur = ""
        for w in para.split(" "):
            t = (cur + " " + w).strip()
            if d.textlength(t, font=fnt) <= max_w:
                cur = t
            else:
                out.append(cur)
                cur = w
        out.append(cur)
    return out


def _block(d, text, fnt, x, y, max_w, fill, lh=1.25):
    asc, desc = fnt.getmetrics()
    step = int((asc + desc) * lh)
    for ln in _wrap(d, text, fnt, max_w):
        d.text((x, y), ln, font=fnt, fill=fill)
        y += step
    return y


def _kicker(d, text, pal, y=170):
    f = _font("bold", 38)
    cx = 90
    for ch in str(text):
        d.text((cx, y), ch, font=f, fill=pal["purple"])
        cx += d.textlength(ch, font=f) + 4


def _footer(d, pal, brand, scorri=True):
    d.line([(90, H - 110), (140, H - 110)], fill=pal["purple"], width=10)
    d.text((160, H - 128), brand, font=_font("bold", 34), fill=pal["white"])
    if scorri:
        d.text((90, H - 240), "Scorri →", font=_font("bold", 42), fill=pal["magenta"])


def render_slide(spec: dict, brand: str, pal: dict):
    """Rende una slide da spec. Ritorna (img, overflow_bool)."""
    img, d = _base(pal)
    _kicker(d, spec.get("kicker", ""), pal)
    y = 280
    accent = pal.get(spec.get("accent", "white"), pal["white"])
    y = _block(d, spec.get("title", ""), _font("black", int(spec.get("title_size", 88))),
               90, y, W - 180, accent, lh=1.14)
    if spec.get("sub"):
        y += 30
        y = _block(d, spec["sub"], _font("reg", 46), 90, y, W - 180, pal["muted"], lh=1.3)
    if spec.get("body"):
        y += 40
        y = _block(d, spec["body"], _font("reg", int(spec.get("body_size", 42))),
                   90, y, W - 180, pal["white"], lh=1.38)
    if spec.get("items"):
        y += 44
        for n, item in enumerate(spec["items"], 1):
            head = item[0] if isinstance(item, (list, tuple)) else str(item)
            txt = item[1] if isinstance(item, (list, tuple)) and len(item) > 1 else None
            d.ellipse([90, y + 2, 146, y + 58], fill=pal["purple"])
            nf = _font("black", 34)
            d.text((118 - d.textlength(str(n), font=nf) / 2, y + 10), str(n), font=nf, fill=pal["white"])
            yy = _block(d, head, _font("bold", 40), 170, y, W - 260, pal["white"], lh=1.2)
            if txt:
                yy = _block(d, txt, _font("reg", 36), 170, yy + 6, W - 260, pal["muted"], lh=1.28)
            y = yy + 34
    overflow = y > SAFE_BOTTOM
    _footer(d, pal, brand, scorri=bool(spec.get("scorri", True)))
    return img, overflow


def _build_pdf(out_dir: Path, n: int):
    try:
        from reportlab.lib.pagesizes import portrait
        from reportlab.pdfgen import canvas
        path = out_dir / "carosello-linkedin.pdf"
        c = canvas.Canvas(str(path), pagesize=portrait((1080, 1350)))
        for i in range(1, n + 1):
            c.drawImage(str(out_dir / f"slide-{i:02d}.png"), 0, 0, 1080, 1350)
            c.showPage()
        c.save()
        return path.name
    except Exception:
        return None


def build_kit(out_dir, slides, brand: str = "example.com", hero_path=None,
              readme=None, palette=None, make_jpeg: bool = True) -> dict:
    """Genera il kit completo in out_dir. Ritorna manifest {ok, dir, slides, files, warnings}."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pal = {**DEFAULT_PALETTE, **(palette or {})}
    files, warnings = [], []
    n = len(slides)
    for i, spec in enumerate(slides, 1):
        if i == n and "scorri" not in spec:   # ultima slide: niente "Scorri →"
            spec = {**spec, "scorri": False}
        img, overflow = render_slide(spec, brand, pal)
        img.save(out_dir / f"slide-{i:02d}.png")
        files.append(f"slide-{i:02d}.png")
        if make_jpeg:  # IG via API rifiuta i PNG → versione JPEG
            img.convert("RGB").save(out_dir / f"slide-{i:02d}.jpg", "JPEG", quality=92)
            files.append(f"slide-{i:02d}.jpg")
        if overflow:
            warnings.append(f"slide {i}: testo oltre il margine sicuro — accorcia il contenuto")
    pdf = _build_pdf(out_dir, n)
    if pdf:
        files.append(pdf)
    if hero_path and Path(hero_path).is_file():
        shutil.copyfile(hero_path, out_dir / "hero.png")
        files.append("hero.png")
    if readme:
        (out_dir / "README.md").write_text(readme, encoding="utf-8")
        files.append("README.md")
    (out_dir / "media-urls.json").write_text(
        json.dumps({"_note": "compilare con gli URL pubblici dopo wp_upload_media"}, indent=2),
        encoding="utf-8")
    files.append("media-urls.json")
    return {"ok": True, "dir": str(out_dir), "slides": n, "files": files, "warnings": warnings}
