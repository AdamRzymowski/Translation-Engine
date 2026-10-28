"""Extracts the text of a page range as translation units, with position and style, to JSON.

A unit is one paragraph, list item, heading or stand-alone label. Lines are grouped into a
paragraph when they continue each other (same left edge, same size, no bullet, previous line
runs to the column edge). Each unit records the boxes of its original lines (so replace.py can
remove exactly that text) and the area the translation may fill.

  {"id": "p3-u4", "page": 3, "lines": [[x0, y0, x1, y1], ...], "box": [x0, y0, x1, y1],
   "text": "...", "html": "... <b>bold</b> <i>italic</i> ...", "font": "Caladea-Regular",
   "size": 9.96, "color": "#000000", "align": "left|justify|right|center",
   "translation": ""}

"translation" is filled in later (by Claude Code, within the user's plan), as HTML using the same
<b>/<i> tags; replace.py then writes it back into the original page.

Usage: python layout/extract.py <source.pdf> <first_page> <last_page> <out.json>
"""
import html
import json
import re
import sys

import pymupdf

BULLETS = ("•", "–", "-", "·")


def is_bold(span):
    return bool(span["flags"] & 16) or "bold" in span["font"].lower()


def is_italic(span):
    return bool(span["flags"] & 2) or "italic" in span["font"].lower()


def line_html(spans):
    parts = []
    for s in spans:
        text = html.escape(s["text"])
        if s["flags"] & 1:  # superscript, e.g. the "th" in "9th"
            text = f"<sup>{text}</sup>"
        if is_italic(s):
            text = f"<i>{text}</i>"
        if is_bold(s):
            text = f"<b>{text}</b>"
        parts.append(text)
    return "".join(parts).replace("</b><b>", "").replace("</i><i>", "")


def split_spans(spans):
    """Splits a leading number from the rest of a line when they are separate table cells,
    e.g. a spell's "11+" (Casting Value column) and "Augment" (Type column), or "10" and
    "Witchcraft" in a numbered contents entry."""
    first = spans[0]["text"].strip()
    if len(spans) > 1 and re.fullmatch(r"\d+\+?", first) and spans[1]["text"].strip()[:1].isalpha():
        return [spans[:1], spans[1:]]
    return [spans]


def page_lines(page):
    """All non-empty text lines of a page, each with its block's box."""
    lines = []
    for block in page.get_text("dict")["blocks"]:
        if block["type"] != 0:
            continue
        # Some justified text stores every word as its own "line"; join pieces on the same row.
        rows = []
        for line in block["lines"]:
            spans = [s for s in line["spans"] if s["text"].strip()]
            if not spans:
                continue
            prev = rows[-1] if rows else None
            # Word gaps are under one font size; wider gaps separate table columns ("CV", "Type").
            if prev and abs(line["bbox"][1] - prev["y0"]) < 2 and 0 <= line["bbox"][0] - prev["x1"] < 0.9 * spans[0]["size"]:
                spaced = [dict(spans[0], text=" " + spans[0]["text"].lstrip())] + spans[1:]
                prev["spans"] += spaced
                prev["x1"] = line["bbox"][2]
            else:
                rows.append({"spans": spans, "y0": line["bbox"][1], "x1": line["bbox"][2]})
        for row in rows:
            for part in split_spans(row["spans"]):
                lines.append(make_line(part, block))
    return lines


def make_line(spans, block):
    main = max(spans, key=lambda s: len(s["text"]))
    # Heights come from the baseline and font size: some symbols (e.g. the "𝛼" in Cosmology
    # cards) come from fallback fonts with huge ascent/descent that would inflate the box.
    return {
        "bbox": [
            round(min(s["bbox"][0] for s in spans), 2),
            round(min(s["origin"][1] - 0.85 * s["size"] for s in spans), 2),
            round(max(s["bbox"][2] for s in spans), 2),
            round(max(s["origin"][1] + 0.27 * s["size"] for s in spans), 2),
        ],
        "block": [round(v, 2) for v in block["bbox"]],
        "text": "".join(s["text"] for s in spans).strip(),
        "html": line_html(spans).strip(),
        "font": main["font"],
        "size": round(main["size"], 2),
        "color": f"#{main['color']:06x}",
        "all_bold": all(is_bold(s) for s in spans),
        # Invisible text used as a stencil over a coloured fill (e.g. purple contents entries).
        "stencil": any(s.get("alpha", 255) == 0 for s in spans),
    }


def continues(prev, line, para_right):
    """True when `line` is the next line of the same paragraph as `prev`."""
    if line["text"].startswith(BULLETS):
        return False
    # Rows of the Cosmos/Chaos tables on spell cards, one per line.
    if line["text"].startswith("[") and line["text"][1:3].strip().startswith("𝛼") and "]:" in line["text"][:6]:
        return False
    if abs(line["size"] - prev["size"]) > 1 or prev["all_bold"] != line["all_bold"] and prev["all_bold"]:
        return False
    gap = line["bbox"][1] - prev["bbox"][3]
    if gap > 0.6 * prev["size"] or line["bbox"][1] < prev["bbox"][1] + 2:
        return False
    if abs(line["bbox"][0] - prev["bbox"][0]) > 3 and not prev["text"].startswith(BULLETS):
        return False
    # A finished sentence followed by a new one on the next line is a deliberate line break
    # (e.g. "Enchantment: Melee Weapon." then "+6 AP."), so keep them as separate units.
    first_char = next((c for c in line["text"] if c.isalnum() or c in "+-–"), "")
    if prev["text"].rstrip().endswith((".", ":")) and (first_char.isupper() or first_char.isdigit() or first_char in "+-–"):
        return False
    # The previous line stopped well before the column edge, so its paragraph ended there.
    return prev["bbox"][2] >= para_right - 15


def group(lines):
    units = []
    for line in lines:
        cur = units[-1] if units else None
        if cur and continues(cur["parts"][-1], line, max(p["bbox"][2] for p in cur["parts"]) if len(cur["parts"]) > 1 else line["block"][2]):
            cur["parts"].append(line)
        else:
            units.append({"parts": [line]})
    return units


def extract(path, first, last):
    doc = pymupdf.open(path)
    out = []
    for pno in range(first - 1, last):
        page = doc[pno]
        for i, unit in enumerate(group(page_lines(page))):
            parts = unit["parts"]
            first_line = parts[0]
            x0 = min(p["bbox"][0] for p in parts)
            y0 = min(p["bbox"][1] for p in parts)
            x1 = max(p["bbox"][2] for p in parts)
            y1 = max(p["bbox"][3] for p in parts)
            align = "justify" if len(parts) > 2 else "left"
            # Point costs (e.g. "50 pts") are right-aligned in their column.
            if len(parts) == 1 and re.fullmatch(r"\d+\s*pts?\.?", first_line["text"]):
                align = "right"
            out.append({
                "id": f"p{pno + 1}-u{i}",
                "page": pno + 1,
                "lines": [p["bbox"] for p in parts],
                "box": [x0, y0, max(x1, first_line["block"][2]), y1],
                "text": " ".join(p["text"] for p in parts),
                "html": " ".join(p["html"] for p in parts),
                "font": first_line["font"],
                "size": first_line["size"],
                "color": first_line["color"],
                "align": align,
                "stencil": any(p["stencil"] for p in parts),
                "translation": "",
            })
    return out


if __name__ == "__main__":
    src, first, last, dest = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
    units = extract(src, first, last)
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(units, f, ensure_ascii=False, indent=1)
    print(f"{len(units)} units from pages {first}-{last} -> {dest}")
