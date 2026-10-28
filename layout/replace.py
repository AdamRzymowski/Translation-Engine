"""Writes translations back into the original PDF pages, keeping all graphics and layout.

For every translated unit from extract.py it removes only the original text (images, artwork,
backgrounds and line art are kept, nothing is painted over) and writes the translation into the
same place, in the matching font, size, colour and alignment. Where the translation is longer,
it may use free space to the right and below; if it still does not fit, the font is shrunk.

Usage: python layout/replace.py <source.pdf> <units.json> <translations.json> <out.pdf>

translations.json maps unit id -> translated HTML (using <b>, <i>, <sup> like the unit's "html").
Units without a translation are left exactly as they are (page numbers, version labels, ...).
"""
import html as htmllib
import json
import re
import sys

import pymupdf

FONT_DIR = "C:/Windows/Fonts"

# Original font family -> replacement font with Polish characters.
# Caladea is the book's own body font. Cinzel and Caslon are not installed, so Book Antiqua stands in.
FONTS = {
    "caladea": ("Caladea", "Caladea-Regular.ttf", "Caladea-Bold.ttf", "Caladea-Italic.ttf", "Caladea-BoldItalic.ttf"),
    "antiqua": ("Antiqua", "BKANT.TTF", "ANTQUAB.TTF", "ANTQUAI.TTF", "ANTQUABI.TTF"),
}

CSS_TEMPLATE = """
@font-face {{font-family: {name}; src: url({regular});}}
@font-face {{font-family: {name}; src: url({bold}); font-weight: bold;}}
@font-face {{font-family: {name}; src: url({italic}); font-style: italic;}}
@font-face {{font-family: {name}; src: url({bolditalic}); font-weight: bold; font-style: italic;}}
"""
CSS = "".join(
    CSS_TEMPLATE.format(name=n, regular=r, bold=b, italic=i, bolditalic=bi) for n, r, b, i, bi in FONTS.values()
) + "* {margin: 0; padding: 0;}"


def family_for(font):
    return "Caladea" if "caladea" in font.lower() else "Antiqua"


def small_caps(text, size):
    """Cinzel-style headings: capitals, with each word's first letter larger."""
    words = []
    for word in htmllib.unescape(re.sub(r"<[^>]+>", "", text)).split(" "):
        if word:
            words.append(f"{htmllib.escape(word[0].upper())}<span style='font-size:{size * 0.78:.2f}pt'>{htmllib.escape(word[1:].upper())}</span>")
    return " ".join(words)


def overlaps(a0, a1, b0, b1):
    """True when two ranges share more than a sliver (30% of the shorter one)."""
    return min(a1, b1) - max(a0, b0) > 0.3 * min(a1 - a0, b1 - b0)


def free_box(unit, others, right_margin, page_rect, centred):
    """The area this unit's translation may use without running into other text."""
    x0, y0, x1, y1 = unit["box"]
    footer = y0 > page_rect.height * 0.92
    body = [o for o in others if o["box"][1] <= page_rect.height * 0.92]
    if centred:
        # Centred titles may widen equally on both sides, up to the page margins.
        margin = page_rect.width - right_margin
        x0, x1 = min(x0, margin), max(x1, right_margin)
    elif not footer and not any(o["box"][0] > x1 and o["box"][1] < y1 + 150 and o["box"][3] > y0 - 150 for o in body):
        # No column runs alongside this text, so it may extend to the page's right margin.
        x1 = max(x1, right_margin)
    if unit["align"] == "right":
        # Right-aligned labels such as "50 pts" may grow to the left.
        x0 -= 30
    for o in others:
        ox0, oy0, ox1, oy1 = o["box"]
        # Text to the right on the same lines limits the width.
        if overlaps(y0, y1, oy0, oy1) and ox0 > x0 + 5:
            x1 = min(x1, ox0 - 4)
    below = [o["box"][1] for o in others if overlaps(x0, x1, o["box"][0], o["box"][2]) and o["box"][1] >= y1 - 1]
    bottom = min(below + [y1 + 4 * unit["size"]]) - 1
    # Footers and large single-line titles stay on one line; they shrink instead of wrapping.
    if len(unit["lines"]) == 1 and (footer or unit["size"] >= 14):
        bottom = y1 + 1.5
    return pymupdf.Rect(x0, y0 - 0.5, x1, max(y1 + 1.5, bottom))


def is_centred(unit, page_width):
    """A single line placed in the middle of the page, like a title."""
    if len(unit["lines"]) != 1:
        return False
    x0, _, x1, _ = unit["lines"][0]
    return abs((x0 + x1) / 2 - page_width / 2) < 12 and x0 > page_width * 0.15


def unit_html(unit, translation, page_width):
    size = unit["size"]
    family = family_for(unit["font"])
    align = "center" if is_centred(unit, page_width) else unit["align"]
    body = small_caps(translation, size) if unit["font"].lower().startswith("cinzel") else translation
    return (
        f"<div style=\"font-family:{family}; font-size:{size:.2f}pt; color:{unit['color']}; "
        f"line-height:1.18; text-align:{align}\">{body}</div>"
    )


def replace(src, units_path, translations_path, out):
    units = json.load(open(units_path, encoding="utf-8"))
    translations = json.load(open(translations_path, encoding="utf-8"))
    doc = pymupdf.open(src)
    archive = pymupdf.Archive(FONT_DIR)
    first = min(u["page"] for u in units)
    last = max(u["page"] for u in units)
    report = []

    for pno in range(first, last + 1):
        page = doc[pno - 1]
        page_units = [u for u in units if u["page"] == pno]
        todo = [u for u in page_units if translations.get(u["id"], "").strip() and translations[u["id"]] != u["html"]]
        right_margin = max(u["lines"][0][2] for u in page_units) if page_units else page.rect.width - 50

        # Stencil text takes its colour from the fill behind it, and removing that fill also hides
        # stencil text we did not translate (e.g. page numbers), so all of it is rewritten.
        if any(u.get("stencil") for u in todo):
            fills = [d["fill"] for d in page.get_drawings() if d.get("fill") and "f" in d["type"]]
            colour = max(set(fills), key=fills.count) if fills else (0, 0, 0)
            hexcolour = "#" + "".join(f"{round(c * 255):02x}" for c in colour)
            for u in page_units:
                if u.get("stencil"):
                    u["color"] = hexcolour
                    if u not in todo:
                        translations[u["id"]] = u["html"]
                        todo.append(u)
            todo.sort(key=lambda u: int(u["id"].split("-u")[1]))

        # Removing text also removes links over it (e.g. clickable contents entries); keep them.
        links = page.get_links()

        # 1. Remove only the original text of the translated units; keep images and line art.
        # Stencil text (invisible text that cuts a coloured fill into letters) is removed in a
        # second pass together with the fill it shapes, as otherwise the letters stay visible.
        for stencil in (False, True):
            group = [u for u in todo if bool(u.get("stencil")) == stencil]
            if not group:
                continue
            for u in group:
                for x0, y0, x1, y1 in u["lines"]:
                    inset = (y1 - y0) * 0.2
                    page.add_redact_annot(pymupdf.Rect(x0, y0 + inset, x1, y1 - inset), fill=False)
            page.apply_redactions(
                images=pymupdf.PDF_REDACT_IMAGE_NONE,
                graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED if stencil else pymupdf.PDF_REDACT_LINE_ART_NONE,
                text=pymupdf.PDF_REDACT_TEXT_REMOVE,
            )
        for link in page.get_links():
            page.delete_link(link)
        for link in links:
            page.insert_link(link)

        # 2. Write each translation into its original place. A unit translated as "-" was merged
        # into the unit before it (one paragraph split across text blocks): its text is removed
        # above, and its area is given to that previous unit.
        merged = {u["id"] for u in todo if translations[u["id"]].strip() == "-"}
        for i, u in enumerate(todo):
            if u["id"] in merged:
                continue
            u = dict(u)
            indent = 0
            for nxt in todo[i + 1:]:
                if nxt["id"] not in merged:
                    break
                indent = max(indent, u["box"][0] - nxt["box"][0])
                u["box"] = [min(u["box"][0], nxt["box"][0]), u["box"][1], max(u["box"][2], nxt["box"][2]), max(u["box"][3], nxt["box"][3])]
                u["lines"] = u["lines"] + nxt["lines"]
                u["align"] = "justify"
            others = [o for o in page_units if o["id"] != u["id"] and o["id"] not in merged]
            rect = free_box(u, others, right_margin, page.rect, is_centred(u, page.rect.width))
            if indent:
                translations[u["id"]] = f"<span style='padding-left:{indent:.1f}pt'></span>{translations[u['id']]}"
            spare, scale = page.insert_htmlbox(
                rect, unit_html(u, translations[u["id"]], page.rect.width), css=CSS, archive=archive, scale_low=0.6
            )
            if spare < 0 or scale < 0.9:
                report.append(f"  {u['id']}: {'did not fit' if spare < 0 else f'shrunk to {scale:.0%}'} - {u['text'][:50]}")

    doc.select(list(range(first - 1, last)))
    doc.save(out, garbage=3, deflate=True)
    print(f"wrote {out} ({len(units)} units, pages {first}-{last})")
    if report:
        print("units that needed shrinking:")
        print("\n".join(report))


if __name__ == "__main__":
    replace(*sys.argv[1:5])
