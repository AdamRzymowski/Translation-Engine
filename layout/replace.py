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


def rule_lines(page):
    """Thin horizontal lines drawn on the page (e.g. separators on spell cards), as pseudo-units
    so translated text stops above them instead of running across."""
    rules = []
    for d in page.get_drawings():
        r = d["rect"]
        if r.height < 2.5 and 30 < r.width < page.rect.width:
            rules.append({"id": "rule", "box": [r.x0, r.y0, r.x1, r.y1], "text": "", "size": 0})
    return rules


def free_box(unit, others, right_margin, page_rect, centred, rules=()):
    """The area this unit's translation may use without running into other text.

    Text may widen to the right edge of its own column (the widest text above or below it that
    shares its horizontal span), but never into a neighbouring column or a label on the same line.
    """
    x0, y0, x1, y1 = unit["box"]
    footer = y0 > page_rect.height * 0.92
    body = [o for o in others if (o["box"][1] > page_rect.height * 0.92) == footer]
    if centred:
        # Centred titles may widen equally on both sides, up to the page margins.
        margin = page_rect.width - right_margin
        x0, x1 = min(x0, margin), max(x1, right_margin)
    elif not footer:
        # Its column: nearby text starting at (about) the same left edge.
        column = [o["box"][2] for o in body if abs(o["box"][0] - x0) < 25 and abs(o["box"][1] - y0) < 120]
        x1 = max([x1] + column)
        if len(unit["lines"]) == 1 and unit["align"] != "right":
            # A single line (label, name, heading) may run on until the next text on its row.
            x1 = max(x1, right_margin)
    if unit["align"] == "right":
        # Right-aligned labels such as "50 pts" may grow to the left.
        x0 -= 30
    if len(unit["text"]) <= 4:
        # Short numbers (page numbers, values) get a little slack instead of being shrunk.
        x0, x1 = x0 - 3, x1 + 3
    own_right = max(line[2] for line in unit["lines"])
    for o in others:
        ox0, oy0, ox1, oy1 = o["box"]
        # Text to the right on the same lines limits the width, and so does text in a column
        # further right on the lines the translation may grow into.
        beside = overlaps(y0, y1, oy0, oy1) and ox0 > x0 + 5
        next_column = overlaps(y0, y1 + 2 * unit["size"], oy0, oy1) and ox0 >= own_right + 5
        if (beside or next_column) and ox0 < x1:
            x1 = min(x1, ox0 - 4)
    below = [o["box"][1] for o in list(others) + list(rules) if overlaps(x0, x1, o["box"][0], o["box"][2]) and o["box"][1] >= y0 + unit["size"]]
    # Never grow below the lowest body text on the page (into the decorated footer band).
    if not footer:
        below.append(max([o["box"][3] for o in body] + [y1]) + 0.3 * unit["size"])
    # Keep a small gap above the next text: the last line's descenders reach below its box.
    bottom = min(below + [y1 + 4 * unit["size"]]) - 0.3 * unit["size"]
    # Footers, large titles and short labels (e.g. table cells) stay on one line: they shrink
    # instead of wrapping, as wrapping would break the table or banner they sit in.
    if len(unit["lines"]) == 1 and (footer or unit["size"] >= 14 or len(unit["text"]) <= 20):
        bottom = y1 + 1.5
    return pymupdf.Rect(x0, y0 - 0.5, x1, max(y1 + 1.5, bottom))


def is_centred(unit, page_width, others=()):
    """A single line in the middle of the page with nothing beside it, like a title."""
    if len(unit["lines"]) != 1:
        return False
    x0, y0, x1, y1 = unit["lines"][0]
    if any(overlaps(y0, y1, o["box"][1], o["box"][3]) for o in others):
        return False
    return abs((x0 + x1) / 2 - page_width / 2) < 12 and x0 > page_width * 0.15


def split_html(html_text, visible_chars):
    """Splits HTML after roughly `visible_chars` characters of text, at a space, keeping
    <b>/<i> tags balanced in both halves."""
    tokens = re.split(r"(<[^>]+>)", html_text)
    count, open_tags, first = 0, [], []
    for t_index, token in enumerate(tokens):
        if token.startswith("<"):
            name = token.strip("</>").split()[0]
            if token.startswith("</"):
                if name in open_tags:
                    open_tags.remove(name)
            elif not token.endswith("/>"):
                open_tags.append(name)
            first.append(token)
            continue
        if count + len(token) <= visible_chars:
            count += len(token)
            first.append(token)
            continue
        cut = token.rfind(" ", 0, max(0, visible_chars - count))
        if cut < 0:
            cut = 0
        first.append(token[:cut])
        rest = [f"<{n}>" for n in open_tags] + [token[cut:].lstrip()] + tokens[t_index + 1:]
        first += [f"</{n}>" for n in reversed(open_tags)]
        return "".join(first), "".join(rest)
    return html_text, ""


def drop_cap_for(unit, others):
    """A large single letter just left of the unit's first line (a decorative drop cap)."""
    lx0, ly0, lx1, ly1 = unit["lines"][0]
    for o in others:
        if len(o["text"]) == 1 and o["size"] > 2 * unit["size"] and o["box"][2] <= lx0 + 3 and overlaps(ly0, ly1, o["box"][1], o["box"][3]):
            return o
    return None


def unit_html(unit, translation, centred, indent=0):
    size = unit["size"]
    family = family_for(unit["font"])
    align = "center" if centred else unit["align"]
    body = small_caps(translation, size) if unit["font"].lower().startswith("cinzel") else translation
    return (
        f"<div style=\"font-family:{family}; font-size:{size:.2f}pt; color:{unit['color']}; "
        f"line-height:1.18; text-align:{align}; text-indent:{indent:.1f}pt\">{body}</div>"
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
            drawings = [d for d in page.get_drawings() if d.get("fill") and "f" in d["type"]]
            # The fill a stencil cuts is drawn far larger than the page; icons and art are not.
            fills = [d["fill"] for d in drawings if d["rect"].width > page.rect.width] or [d["fill"] for d in drawings]
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
        rules = rule_lines(page) if todo else []

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

        def write(u, text, indent=0):
            """Writes one block of translated text into the area of unit `u`."""
            others = [o for o in page_units if o["id"] not in u["ids"] and o["id"] not in merged]
            centred = is_centred(u, page.rect.width, others)
            rect = free_box(u, others, right_margin, page.rect, centred, rules)
            cap = drop_cap_for(u, others)
            if cap:
                # The lines beside a drop cap are narrower: fill them first, in their own box.
                beside = pymupdf.Rect(u["lines"][0][0], rect.y0, rect.x1, cap["box"][3])
                n_lines = max(1, round((cap["box"][3] - rect.y0) / (1.18 * u["size"])))
                head, text = split_html(text, int(n_lines * beside.width / (0.47 * u["size"])))
                page.insert_htmlbox(beside, unit_html(dict(u, align="justify"), head, False), css=CSS, archive=archive, scale_low=0.9)
                rect.y0 = cap["box"][3] + 0.5 * u["size"] * 0.18
                indent = 0
            html_text = unit_html(u, text, centred, indent)
            spare, scale = page.insert_htmlbox(rect, html_text, css=CSS, archive=archive, scale_low=0.6)
            if spare < 0:
                # Last resort: smaller text rather than missing text.
                spare, scale = page.insert_htmlbox(rect, html_text, css=CSS, archive=archive, scale_low=0.35)
            if spare < 0 or scale < 0.9:
                report.append(f"  {u['id']}: {'did not fit' if spare < 0 else f'shrunk to {scale:.0%}'} - {u['text'][:50]}")

        for i, u in enumerate(todo):
            if u["id"] in merged:
                continue
            # A paragraph may continue in later units ("-"); those in the same column are joined into
            # one area, and a jump to another column starts a new area the text flows into.
            areas = [dict(u, ids={u["id"]})]
            indent = 0
            for nxt in todo[i + 1:]:
                if nxt["id"] not in merged:
                    break
                area = areas[-1]
                if nxt["box"][0] > area["box"][2] - 5 or nxt["box"][1] < area["box"][1]:
                    areas.append(dict(nxt, ids={nxt["id"]}, align="justify"))
                    continue
                if len(areas) == 1:
                    # The first line may start further right (an indent, or room for a drop cap).
                    indent = max(indent, area["box"][0] - nxt["box"][0])
                area["box"] = [min(area["box"][0], nxt["box"][0]), area["box"][1], max(area["box"][2], nxt["box"][2]), max(area["box"][3], nxt["box"][3])]
                area["lines"] = area["lines"] + nxt["lines"]
                area["ids"].add(nxt["id"])
                if len(area["lines"]) >= 3:
                    area["align"] = "justify"
            text = translations[u["id"]]
            capacity = [(a["box"][2] - a["box"][0]) * (a["box"][3] - a["box"][1]) for a in areas]
            total_chars = len(re.sub(r"<[^>]+>", "", text))
            for n, area in enumerate(areas):
                if n < len(areas) - 1:
                    share = int(total_chars * capacity[n] / sum(capacity))
                    part, text = split_html(text, share)
                else:
                    part = text
                write(area, part, indent if n == 0 else 0)

    # Trailing pages without any text (e.g. a back cover) belong to the book too.
    if not any(page.get_text().strip() for page in doc.pages(last, len(doc))):
        last = len(doc)
    doc.select(list(range(first - 1, last)))
    doc.save(out, garbage=3, deflate=True)
    print(f"wrote {out} ({len(units)} units, pages {first}-{last})")
    if report:
        print("units that needed shrinking:")
        print("\n".join(report))


if __name__ == "__main__":
    replace(*sys.argv[1:5])
