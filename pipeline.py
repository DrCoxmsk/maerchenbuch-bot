"""
pipeline.py – Märchenbuch-Produktionspipeline
==============================================
Schritte:
  1. GPT-4o Vision analysiert Kinderzeichnung → Charakter-Beschreibungen
  2. DALL-E 3 generiert 3 Referenzblätter zur Auswahl
  3. GPT-4o generiert Story (10 Seiten)
  4. DALL-E 3 generiert Cover + 10 Seitenbilder
  5. ReportLab baut druckfertiges A4-PDF mit Bleed + Schnittmarken

Alle Funktionen arbeiten mit einem Order-Dict:
  {
    "order_id": str,
    "child_name": str,
    "child_age": int,
    "language": str,         # "de" | "en"
    "mood": str,             # "abenteuer" | "magie" | "familie" | "freundschaft"
    "story_wish": str,       # optional
    "dedication": str,       # optional
    "drawing_path": str,     # Pfad zur hochgeladenen Zeichnung
    "work_dir": str,         # Arbeitsverzeichnis für diesen Auftrag
  }
"""

import os
import json
import math
import random
import base64
import shutil
import time
import datetime
from pathlib import Path

from openai import OpenAI

# ── Konfiguration ────────────────────────────────────────────────────────────

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

MOOD_MAP = {
    "abenteuer": "an exciting adventure with discoveries and brave choices",
    "magie": "a magical journey with wonder, enchantment and gentle surprises",
    "familie": "a warm family trip full of love, togetherness and shared joy",
    "freundschaft": "a story about friendship, courage and helping each other",
}

LANG_MAP = {
    "de": {"label": "Deutsch", "story_lang": "German"},
    "en": {"label": "Englisch", "story_lang": "English"},
}

# ── Font-Setup ───────────────────────────────────────────────────────────────

FONT_DIR = Path("fonts")
FONT_DIR.mkdir(exist_ok=True)

LOGO_PATH = Path(__file__).parent / "malory.PNG"

SYSTEM_FONTS = {
    "Baloo2-Bold.ttf":  "/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf",
    "Lora-Regular.ttf": "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "Lora-Italic.ttf":  "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
}


def ensure_fonts():
    """Kopiert System-Fallback-Fonts, falls die echten TTFs noch nicht da sind."""
    for filename, sys_path in SYSTEM_FONTS.items():
        dest = FONT_DIR / filename
        if not dest.exists() and os.path.exists(sys_path):
            shutil.copy(sys_path, dest)


def get_client() -> OpenAI:
    return OpenAI(api_key=OPENAI_API_KEY)


# ══════════════════════════════════════════════════════════════════════════════
# BILDMODERATION
# ══════════════════════════════════════════════════════════════════════════════

MODERATION_PROMPT = """
Analysiere dieses Bild. Antworte NUR als JSON, kein Text davor/danach:
{
  "ist_kinderzeichnung": true/false,
  "konfidenz": 0.0-1.0,
  "ablehnungsgrund": null | "kein_bild_einer_zeichnung" | "foto_statt_zeichnung" | "unangemessener_inhalt" | "text_dokument"
}
Akzeptiere: Kinderzeichnungen, Malereien, Strichmännchen, Kritzeleien,
abstrakte Zeichnungen. Im Zweifel akzeptieren (konfidenz >= 0.55 reicht).
Lehne ab bei: echten Fotos von Menschen, erotischen Inhalten,
Körperteilen, Text/Dokumenten, Screenshots.
"""


def moderate_image(drawing_path: str) -> dict:
    """GPT-4o Vision moderiert das Bild. Gibt Moderations-Dict zurück."""
    try:
        client = get_client()
        img_b64 = base64.b64encode(Path(drawing_path).read_bytes()).decode()
        ext = Path(drawing_path).suffix.lower()
        mime = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"

        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": MODERATION_PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img_b64}"}},
                ]
            }],
            max_tokens=200,
        )

        raw = resp.choices[0].message.content
        start = raw.find("{")
        end = raw.rfind("}") + 1
        return json.loads(raw[start:end])
    except Exception as e:
        print(f"[pipeline] Moderation Fehler: {e}")
        return {"ist_kinderzeichnung": True, "konfidenz": 1.0, "ablehnungsgrund": None}


# ══════════════════════════════════════════════════════════════════════════════
# SCHRITT 1: Zeichnung analysieren → Charakter-Bibel
# ══════════════════════════════════════════════════════════════════════════════

VISION_PROMPT = """You are analyzing a child's drawing for a personalized picture book.

Describe EVERY recognizable figure, animal, or character in this drawing.
For each, provide an EXTREMELY PRECISE visual description in English so that
an image generator can reproduce them consistently across 10+ pages.

For each figure describe:
- Role guess (main character, parent, pet, friend, etc.)
- Exact colors used
- Hair/body shape and features
- Clothing or accessories
- Size relative to other figures
- Any distinctive details

Also describe:
- Overall art style of the drawing (line quality, colors, technique)
- Setting/background elements (house, tree, sun, etc.)
- Mood/atmosphere

Respond ONLY as JSON:
{
  "hauptfigur": "...",
  "figuren": [{"name": "...", "beschreibung": "..."}],
  "setting": "...",
  "stil": "...",
  "stimmung": "..."
}
"""


def analyze_drawing(order: dict) -> dict:
    """GPT-4o Vision analysiert die Kinderzeichnung und extrahiert Charakter-Beschreibungen."""
    client = get_client()
    drawing_path = order["drawing_path"]

    img_b64 = base64.b64encode(Path(drawing_path).read_bytes()).decode()
    ext = Path(drawing_path).suffix.lower()
    mime = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"

    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": VISION_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img_b64}"}},
            ]
        }],
        max_tokens=1500,
    )

    raw = resp.choices[0].message.content
    start = raw.find("{")
    end = raw.rfind("}") + 1
    chars = json.loads(raw[start:end])

    # Speichern
    work = Path(order["work_dir"])
    with open(work / "charakter_bibel.json", "w") as f:
        json.dump(chars, f, indent=2, ensure_ascii=False)

    return chars


# ══════════════════════════════════════════════════════════════════════════════
# SCHRITT 2: 3 Referenzbilder generieren
# ══════════════════════════════════════════════════════════════════════════════

def _build_ref_prompt(chars: dict, order: dict, variant: int) -> str:
    """Baut Referenzblatt-Prompt für eine von 3 Varianten."""
    child_name = order["child_name"]
    hauptfigur = chars.get("hauptfigur", "a small child")
    stil = chars.get("stil", "warm watercolor children's book style")
    setting = chars.get("setting", "green hills, cozy house, bright sun")

    style_variants = [
        "warm soft watercolor style with gentle textures and pastel tones",
        "bright colorful picture-book style with bold outlines and saturated colors",
        "gentle pencil-sketch style with soft shading and delicate lines",
    ]

    figuren_text = ""
    for fig in chars.get("figuren", []):
        figuren_text += f"\n- {fig.get('name', 'Figure')}: {fig.get('beschreibung', '')}"

    return f"""Character reference sheet for a children's picture book.
Style: {style_variants[variant]}

Show the main characters standing in a row on white background, clearly separated:
- MAIN CHARACTER ({child_name}): {hauptfigur}
{figuren_text}

Setting elements to include: {setting}

Art style inspired by the original drawing: {stil}
White background, no text in image, each figure clearly distinct.
"""


def generate_reference_images(order: dict, chars: dict) -> list[str]:
    """Generiert 3 Referenzbilder. Gibt Liste der Dateipfade zurück."""
    client = get_client()
    work = Path(order["work_dir"])
    paths = []

    for i in range(3):
        out = work / f"ref_{i+1}.png"
        if out.exists():
            paths.append(str(out))
            continue

        prompt = _build_ref_prompt(chars, order, i)
        try:
            resp = client.images.generate(
                model="dall-e-3",
                prompt=prompt,
                n=1,
                size="1024x1024",
                quality="standard",
                response_format="b64_json",
            )
            out.write_bytes(base64.b64decode(resp.data[0].b64_json))
            paths.append(str(out))
            time.sleep(1.5)  # Rate-Limit-Schutz
        except Exception as e:
            print(f"[pipeline] Ref-Bild {i+1} Fehler: {e}")

    return paths


# ══════════════════════════════════════════════════════════════════════════════
# SCHRITT 3: Story generieren
# ══════════════════════════════════════════════════════════════════════════════

def generate_story(order: dict, chars: dict) -> list[str]:
    """Generiert 10 Story-Texte, gibt Liste von Strings zurück."""
    client = get_client()
    child = order["child_name"]
    age = order["child_age"]
    lang_code = order.get("language", "de")
    lang = LANG_MAP.get(lang_code, LANG_MAP["de"])["story_lang"]
    mood = MOOD_MAP.get(order.get("mood", "abenteuer"), MOOD_MAP["abenteuer"])
    wish = order.get("story_wish", "")

    hauptfigur = chars.get("hauptfigur", "a brave child")
    figuren_text = ", ".join(
        f.get("name", "?") for f in chars.get("figuren", [])
    )

    prompt = f"""Write a 10-page children's story in {lang}.

Main character: {child} (age {age}). {hauptfigur}
Other characters from the drawing: {figuren_text}
Mood/theme: {mood}
{"User's story wish: " + wish if wish else ""}

Rules:
- Exactly 10 short paragraphs (one per page), each 3-5 sentences
- Age-appropriate language for a {age}-year-old
- The story should have a clear beginning, adventure in the middle, warm ending
- Characters from the drawing should appear naturally in the story
- Last page should be a cozy/peaceful ending

Respond ONLY as a JSON array of 10 strings:
["Page 1 text...", "Page 2 text...", ...]
"""

    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=3000,
    )

    raw = resp.choices[0].message.content
    start = raw.find("[")
    end = raw.rfind("]") + 1
    pages = json.loads(raw[start:end])

    work = Path(order["work_dir"])
    with open(work / "story.json", "w") as f:
        json.dump(pages, f, indent=2, ensure_ascii=False)

    return pages


# ══════════════════════════════════════════════════════════════════════════════
# SCHRITT 4: Seitenbilder generieren
# ══════════════════════════════════════════════════════════════════════════════

def _build_page_prompt(chars: dict, ref_style: str, scene: str, page_num: int) -> str:
    """Baut konsistenten Seitenprompt mit Charakter-Bibel."""
    hauptfigur = chars.get("hauptfigur", "a small child")
    setting = chars.get("setting", "green hills, cozy house, bright sun")
    stil = chars.get("stil", "warm watercolor children's book style")

    figuren_block = ""
    for fig in chars.get("figuren", []):
        figuren_block += f"\n{fig.get('name', 'Figure')}: {fig.get('beschreibung', '')}"

    return f"""Children's picture book illustration, page {page_num}.
Style: {ref_style}

CHARACTERS (must look identical on every page):
Main character: {hauptfigur}
{figuren_block}

Setting: {setting}

THIS PAGE'S SCENE:
{scene}

Style consistency: {stil}
No text in the image. Full-bleed illustration.
"""


def generate_scene_descriptions(story_pages: list[str], chars: dict) -> list[str]:
    """GPT-4o erzeugt Szenen-Beschreibungen für DALL-E aus den Story-Texten."""
    client = get_client()

    prompt = f"""Given these 10 story pages and character descriptions, create a visual scene
description for each page that an image generator can use.

Characters: {json.dumps(chars, ensure_ascii=False)}

Story pages:
{json.dumps(story_pages, ensure_ascii=False)}

For each page write a detailed visual scene description in English (what characters
are doing, expressions, environment, lighting, composition).
Also add a COVER scene as the first item.

Respond ONLY as a JSON array of 11 strings (1 cover + 10 pages):
["Cover scene...", "Page 1 scene...", ...]
"""

    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=3000,
    )

    raw = resp.choices[0].message.content
    start = raw.find("[")
    end = raw.rfind("]") + 1
    return json.loads(raw[start:end])


def generate_page_images(
    order: dict, chars: dict, scenes: list[str], ref_choice: int
) -> list[str]:
    """Generiert Cover + 10 Seitenbilder. Gibt Pfadliste zurück."""
    client = get_client()
    work = Path(order["work_dir"])

    style_names = [
        "warm soft watercolor style with gentle textures and pastel tones",
        "bright colorful picture-book style with bold outlines and saturated colors",
        "gentle pencil-sketch style with soft shading and delicate lines",
    ]
    ref_style = style_names[ref_choice]

    paths = []
    names = ["00_cover"] + [f"{i+1:02d}_seite{i+1}" for i in range(10)]

    for i, (name, scene) in enumerate(zip(names, scenes)):
        out = work / f"{name}.png"
        if out.exists():
            paths.append(str(out))
            continue

        prompt = _build_page_prompt(chars, ref_style, scene, i)
        try:
            resp = client.images.generate(
                model="dall-e-3",
                prompt=prompt,
                n=1,
                size="1024x1024",
                quality="standard",
                response_format="b64_json",
            )
            out.write_bytes(base64.b64decode(resp.data[0].b64_json))
            paths.append(str(out))
            time.sleep(1.5)
        except Exception as e:
            print(f"[pipeline] Bild {name} Fehler: {e}")
            paths.append(None)

    return paths


# ══════════════════════════════════════════════════════════════════════════════
# SCHRITT 5: PDF bauen
# ══════════════════════════════════════════════════════════════════════════════

def build_pdf(order: dict, story_pages: list[str], image_paths: list[str]) -> str:
    """Baut druckfertiges A4-PDF. Gibt Pfad zur PDF-Datei zurück."""
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.colors import HexColor, Color
    from reportlab.platypus import Paragraph
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    ensure_fonts()

    def reg(name, fn):
        p = FONT_DIR / fn
        if p.exists():
            pdfmetrics.registerFont(TTFont(name, str(p)))
            return True
        return False

    has_b = reg("Baloo2", "Baloo2-Bold.ttf")
    has_l = reg("Lora", "Lora-Regular.ttf")
    has_li = reg("LoraIt", "Lora-Italic.ttf")
    FT = "Baloo2" if has_b else "Helvetica-Bold"
    FB = "Lora" if has_l else "Helvetica"
    FI = "LoraIt" if has_li else "Helvetica-Oblique"

    BLEED = 3 * mm
    TW, TH = A4
    PW = TW + 2 * BLEED
    PH = TH + 2 * BLEED
    M = BLEED + 5 * mm
    IH = 390

    CBG = HexColor('#FEFCF4')
    CFG = HexColor('#1A1A2E')
    CGO = HexColor('#D4A017')
    CAC = HexColor('#E87BB5')
    CST = HexColor('#FFE040')
    CNI = HexColor('#0B1E3A')

    child = order["child_name"]
    dedication = order.get("dedication", "")
    work = Path(order["work_dir"])
    pdf_path = str(work / f"{child}s_Maerchenbuch.pdf")

    def heart(c, x, y, r=7):
        c.setFillColor(CAC)
        c.circle(x - r * 0.5, y, r * 0.55, fill=1, stroke=0)
        c.circle(x + r * 0.5, y, r * 0.55, fill=1, stroke=0)
        p = c.beginPath()
        p.moveTo(x, y - r)
        p.lineTo(x - r, y + r * 0.2)
        p.lineTo(x + r, y + r * 0.2)
        p.close()
        c.drawPath(p, fill=1, stroke=0)

    def star8(c, x, y, r=8, col=None):
        if col is None:
            col = CST
        c.setFillColor(col)
        pts = [
            (
                x + (r if i % 2 == 0 else r * 0.38) * math.cos(math.radians(i * 45)),
                y + (r if i % 2 == 0 else r * 0.38) * math.sin(math.radians(i * 45)),
            )
            for i in range(8)
        ]
        p = c.beginPath()
        p.moveTo(*pts[0])
        for pt in pts[1:]:
            p.lineTo(*pt)
        p.close()
        c.drawPath(p, fill=1, stroke=0)

    def crop_marks(c):
        c.setStrokeColor(HexColor('#999999'))
        c.setLineWidth(0.5)
        ml, off = 5 * mm, 2 * mm
        for x in [BLEED, BLEED + TW]:
            for y in [BLEED, BLEED + TH]:
                dx = ml + off if x > BLEED else -(ml + off)
                dy = ml + off if y > BLEED else -(ml + off)
                xo = off if x > BLEED else -off
                yo = off if y > BLEED else -off
                c.line(x + xo, y, x + dx, y)
                c.line(x, y + yo, x, y + dy)

    def draw_vorsatz():
        cv.setFillColor(HexColor('#FFFFFF'))
        cv.rect(0, 0, PW, PH, fill=1, stroke=0)
        crop_marks(cv)
        cv.showPage()

    def draw_logo_page():
        cv.setFillColor(CBG)
        cv.rect(0, 0, PW, PH, fill=1, stroke=0)
        if LOGO_PATH.exists():
            logo_w = 180
            logo_x = (PW - logo_w) / 2
            logo_y = (PH - logo_w) / 2
            cv.drawImage(str(LOGO_PATH), logo_x, logo_y,
                         width=logo_w, height=logo_w,
                         preserveAspectRatio=True, mask='auto')
        crop_marks(cv)
        cv.showPage()

    def text_area(c, text, pagenum=None):
        ty = PH - IH - 8
        c.setFillColor(CBG)
        c.rect(0, 0, PW, ty, fill=1, stroke=0)
        c.setStrokeColor(CGO)
        c.setLineWidth(2)
        c.line(M + 24, ty - 5, PW - M - 24, ty - 5)
        for hx in [M + 40, PW / 2, PW - M - 40]:
            heart(c, hx, ty - 5, r=6)
        margin = M + 20
        avail = PW - 2 * margin
        style = ParagraphStyle(
            "s", fontName=FB, fontSize=17, leading=27,
            alignment=TA_CENTER, textColor=CFG,
        )
        para = Paragraph(text, style)
        _, ph = para.wrap(avail, ty - 2 * M)
        para.drawOn(c, margin, (ty - 2 * M) / 2 - ph / 2 + M)
        if pagenum:
            c.setFillColor(HexColor('#CCAABB'))
            c.setFont(FI, 10)
            c.drawCentredString(PW / 2, M, f"· {pagenum} ·")

    # ── Vorsatz vorne (Seite 1–2) ────────────────────────────────────────────
    cv = rl_canvas.Canvas(pdf_path, pagesize=(PW, PH))
    cv.setTitle(f"{child}s Märchenbuch")
    draw_vorsatz()
    draw_vorsatz()

    # ── Cover (Seite 3) ──────────────────────────────────────────────────────
    cover_img = image_paths[0] if image_paths else None
    if cover_img and os.path.exists(cover_img):
        cv.drawImage(cover_img, 0, 0, width=PW, height=PH,
                     preserveAspectRatio=False, mask='auto')
    else:
        cv.setFillColor(HexColor('#D4EEB0'))
        cv.rect(0, 0, PW, PH, fill=1, stroke=0)

    bw = PW - 2 * (M + 8)
    bh = 68
    bx = M + 8
    by = M + 8
    cv.setFillColor(Color(0.998, 0.988, 0.957, 0.92))
    cv.setStrokeColor(CAC)
    cv.setLineWidth(2.5)
    cv.roundRect(bx, by, bw, bh, 12, fill=1, stroke=1)
    cv.setFillColor(HexColor('#2A0A1A'))
    cv.setFont(FT, 24)
    cv.drawCentredString(PW / 2, by + bh / 2 - 9, f"{child}s Märchenbuch")
    crop_marks(cv)
    cv.showPage()

    # ── Vorsatz mit Logo (Seite 4) ───────────────────────────────────────────
    draw_logo_page()

    # ── Widmungsseite ────────────────────────────────────────────────────────
    cv.setFillColor(CBG)
    cv.rect(0, 0, PW, PH, fill=1, stroke=0)
    cv.setStrokeColor(CAC)
    cv.setLineWidth(2.5)
    cv.roundRect(M, M, PW - 2 * M, PH - 2 * M, 14, fill=0, stroke=1)

    cv.setFont(FT, 18)
    cv.setFillColor(HexColor('#2A0A1A'))
    cv.drawCentredString(PW / 2, PH - M - 38, f"{child}s Zeichnung")
    cv.setFont(FI, 13)
    cv.setFillColor(HexColor('#998899'))
    cv.drawCentredString(PW / 2, PH - M - 60, "Aus dieser Zeichnung entstand das Buch")

    drawing = order.get("drawing_path", "")
    if drawing and os.path.exists(drawing):
        iw, ih2 = 340, 245
        ix = (PW - iw) / 2
        iy = PH / 2 - 15
        cv.setFillColor(HexColor('#FFFFFF'))
        cv.setStrokeColor(HexColor('#F0C8D8'))
        cv.setLineWidth(2)
        cv.roundRect(ix - 8, iy - 8, iw + 16, ih2 + 16, 8, fill=1, stroke=1)
        cv.drawImage(drawing, ix, iy, width=iw, height=ih2,
                     preserveAspectRatio=True, mask='auto')

    if dedication:
        cv.setFont(FB, 14)
        cv.setFillColor(HexColor('#2A0A1A'))
        cv.drawCentredString(PW / 2, PH / 2 - 60, dedication)
    else:
        cv.setFont(FB, 14)
        cv.setFillColor(HexColor('#2A0A1A'))
        cv.drawCentredString(PW / 2, PH / 2 - 60,
                             f"Gezeichnet von {child}")

    for hx in [M + 28, M + 52, PW / 2, PW - M - 52, PW - M - 28]:
        heart(cv, hx, M + 22, r=7)
    crop_marks(cv)
    cv.showPage()

    # ── Story-Seiten ─────────────────────────────────────────────────────────
    for i, text in enumerate(story_pages):
        img_path = image_paths[i + 1] if i + 1 < len(image_paths) else None
        if img_path and os.path.exists(img_path):
            cv.drawImage(img_path, 0, PH - IH, width=PW, height=IH,
                         preserveAspectRatio=False, mask='auto')
        else:
            cv.setFillColor(HexColor('#D4EEB0'))
            cv.rect(0, PH - IH, PW, IH, fill=1, stroke=0)
            cv.setFillColor(HexColor('#888'))
            cv.setFont('Helvetica', 11)
            cv.drawCentredString(PW / 2, PH - IH / 2, f"[Bild fehlt: Seite {i+1}]")
        text_area(cv, text, pagenum=i + 1)
        crop_marks(cv)
        cv.showPage()

    # ── Ende-Seite ───────────────────────────────────────────────────────────
    cv.setFillColor(CNI)
    cv.rect(0, 0, PW, PH, fill=1, stroke=0)
    random.seed(42)
    for _ in range(50):
        star8(cv, random.uniform(30, PW - 30), random.uniform(80, PH - 80),
              r=random.uniform(3, 8), col=HexColor('#FFE566'))
    for hx, hy, hr in [(PW/2-60, PH/2+90, 28), (PW/2+60, PH/2+90, 20),
                        (PW/2, PH/2+115, 16)]:
        heart(cv, hx, hy, r=hr)
    cv.setFillColor(HexColor('#FEFCF4'))
    cv.setFont(FT, 52)
    cv.drawCentredString(PW / 2, PH / 2, "Ende")
    cv.setFont(FI, 20)
    cv.setFillColor(HexColor('#F4AACE'))
    cv.drawCentredString(PW / 2, PH / 2 - 48,
                         f"Bis zum nächsten Abenteuer, {child}!")
    cv.setFont(FB, 11)
    cv.setFillColor(HexColor('#445566'))
    cv.drawCentredString(PW / 2, M + 8, "Personalisiertes Märchenbuch")
    crop_marks(cv)
    cv.showPage()

    # ── Nachsatz mit Logo (Seite 17) ─────────────────────────────────────────
    draw_logo_page()

    # ── Impressum (Seite 18) ─────────────────────────────────────────────────
    today = datetime.date.today().strftime("%d.%m.%Y")
    cv.setFillColor(CBG)
    cv.rect(0, 0, PW, PH, fill=1, stroke=0)
    imp_lines = [
        ("Erstellt mit Malory.", FT, 16, HexColor('#2A0A1A')),
        ("Kinderzeichnungen werden Märchen.", FI, 13, HexColor('#443344')),
        ("", FB, 12, HexColor('#FFFFFF')),
        ("Illustrationen: KI-generiert auf Basis der eingereichten Kinderzeichnung", FB, 11, HexColor('#666666')),
        (f"Text: KI-generiert \u00b7 Personalisiert für {child}", FB, 11, HexColor('#666666')),
        (f"PDF erstellt: {today}", FB, 11, HexColor('#666666')),
    ]
    y = PH / 2 + 60
    for line_text, line_font, line_size, line_color in imp_lines:
        cv.setFont(line_font, line_size)
        cv.setFillColor(line_color)
        if line_text:
            cv.drawCentredString(PW / 2, y, line_text)
        y -= line_size + 12
    cv.setFont(FB, 8)
    cv.setFillColor(HexColor('#AAAAAA'))
    cv.drawCentredString(
        PW / 2, M + 20,
        "Die eingereichte Zeichnung wird nach 30 Tagen automatisch gelöscht.",
    )
    crop_marks(cv)
    cv.showPage()

    # ── Nachsatz (Seite 19–20) ───────────────────────────────────────────────
    draw_vorsatz()
    draw_vorsatz()

    cv.save()
    return pdf_path


# ══════════════════════════════════════════════════════════════════════════════
# ORCHESTRIERUNG: Vollständiger Durchlauf
# ══════════════════════════════════════════════════════════════════════════════

def run_full_pipeline(order: dict, ref_choice: int = 0, progress_cb=None):
    """
    Führt die komplette Pipeline aus.
    progress_cb(step: str, detail: str) wird bei jedem Schritt aufgerufen.
    Gibt den Pfad zum fertigen PDF zurück.
    """
    def notify(step, detail=""):
        if progress_cb:
            progress_cb(step, detail)

    work = Path(order["work_dir"])
    work.mkdir(parents=True, exist_ok=True)

    # 1. Zeichnung analysieren
    notify("analyze", "Zeichnung wird analysiert…")
    chars = analyze_drawing(order)

    # 2. Story generieren
    notify("story", "Geschichte wird geschrieben…")
    story_pages = generate_story(order, chars)

    # 3. Szenen-Beschreibungen
    notify("scenes", "Szenen werden geplant…")
    scenes = generate_scene_descriptions(story_pages, chars)

    # 4. Bilder generieren (Cover + 10 Seiten)
    notify("images", "Illustrationen werden erstellt… (ca. 2–3 Min.)")
    image_paths = generate_page_images(order, chars, scenes, ref_choice)

    # 5. PDF bauen
    notify("pdf", "PDF wird zusammengebaut…")
    pdf_path = build_pdf(order, story_pages, image_paths)

    notify("done", f"Fertig! {len(story_pages) + 3} Seiten")
    return pdf_path
