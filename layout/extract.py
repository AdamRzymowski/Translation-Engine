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


def link_titles(doc):
    """Bookmark titles by destination (page, y), for naming links whose words are not text."""
    titles = {}
    for level, title, _, dest in doc.get_toc(simple=False):
        if dest.get("kind") == 1 and "to" in dest:
            titles.setdefault(dest["page"], []).append((dest["to"].y, title.strip()))
    return titles


def vector_links(page, titles):
    """Linked words drawn as shapes, not text (the Rulebook's purple cross-references).

    Their words come from the bookmark the link points to. They are returned as lines in a
    purple bold span so they can join the sentence around them.
    """
    words = [pymupdf.Rect(w[:4]) for w in page.get_text("words") if any(c.isalnum() for c in w[4])]
    fills = [d["fill"] for d in page.get_drawings() if d.get("fill") and d["rect"].width > page.rect.width]
    colour = max(set(fills), key=fills.count) if fills else (0.51, 0.1, 0.55)
    hexcolour = "#" + "".join(f"{round(c * 255):02x}" for c in colour)
    out = []
    for link in page.get_links():
        r = link["from"]
        if r.is_empty or any((w & r).get_area() > 0.3 * w.get_area() for w in words):
            continue
        if link.get("kind") == 1:
            candidates = [(abs(y - link["to"].y), t) for y, t in titles.get(link["page"], []) if y <= link["to"].y + 5]
            title = min(candidates)[1] if candidates else "?"
        else:
            title = link.get("uri", "?")
        size = round(r.height / 1.2, 2)
        out.append({
            "bbox": [round(r.x0, 2), round(r.y0, 2), round(r.x1, 2), round(r.y1, 2)],
            "block": [round(r.x0, 2), round(r.y0, 2), round(r.x1, 2), round(r.y1, 2)],
            "text": f"⟦{title}⟧",
            "html": f'<span style="color:{hexcolour}"><b>{html.escape(title)}</b></span>',
            "font": "Caladea-Bold",
            "size": size,
            "color": "#000000",
            "all_bold": False,
            "stencil": False,
            "vector": [[round(v, 2) for v in r]],
        })
    return out


def join(a, b):
    """Joins two pieces of one text row (a to the left of b) into a single line."""
    space = "" if a["text"][-1:] in "([" or b["text"][:1] in ".,;:)" else " "
    out = dict(a)
    out["text"] = a["text"] + space + b["text"]
    out["html"] = a["html"] + space + b["html"]
    out["bbox"] = [min(a["bbox"][0], b["bbox"][0]), min(a["bbox"][1], b["bbox"][1]), max(a["bbox"][2], b["bbox"][2]), max(a["bbox"][3], b["bbox"][3])]
    out["vector"] = a.get("vector", []) + b.get("vector", [])
    if not a.get("vector") and not a["text"].startswith("⟦"):
        out["size"], out["font"], out["color"] = a["size"], a["font"], a["color"]
    else:
        out["size"], out["font"], out["color"] = b["size"], b["font"], b["color"]
    out["all_bold"] = False
    return out


def add_vector_links(lines, links):
    """Joins each linked word to the text on its row (left and right of it), or places it on its
    own in reading order, sized like the page's body text."""
    sizes = sorted(l["size"] for l in lines) or [10]
    body_size = sizes[len(sizes) // 2]
    for link in links:
        link = dict(link, size=body_size)
        lx0, ly0, lx1, ly1 = link["bbox"]

        def same_row(line):
            y0, y1 = line["bbox"][1], line["bbox"][3]
            return min(y1, ly1) - max(y0, ly0) > 0.5 * min(y1 - y0, ly1 - ly0)

        left = next((l for l in lines if same_row(l) and -3 <= lx0 - l["bbox"][2] < 2.5 * body_size), None)
        right = next((l for l in lines if same_row(l) and l is not left and -3 <= l["bbox"][0] - lx1 < 2.5 * body_size), None)
        if left is None and right is None:
            at = next((i for i, l in enumerate(lines) if l["bbox"][1] > ly0 + 2 and min(l["bbox"][2], lx1) > max(l["bbox"][0], lx0)), len(lines))
            lines.insert(at, link)
            continue
        combined = link
        if left is not None:
            combined = join(left, combined)
        if right is not None:
            combined = join(combined, right)
        anchor = left if left is not None else right
        lines[lines.index(anchor)] = combined
        if left is not None and right is not None:
            lines.remove(right)
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
    # Enumerated steps such as "(b) ..." or "2. ..." start a new item.
    if re.match(r"^\(?[a-z0-9]{1,2}[).]\s", line["text"]):
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
    titles = link_titles(doc)
    out = []
    for pno in range(first - 1, last):
        page = doc[pno]
        lines = add_vector_links(page_lines(page), vector_links(page, titles))
        for i, unit in enumerate(group(lines)):
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
                # Areas of linked words drawn as shapes; replace.py removes them with their fill.
                "vector": [r for p in parts for r in p.get("vector", [])],
                "translation": "",
            })
    return out


if __name__ == "__main__":
    src, first, last, dest = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
    units = extract(src, first, last)
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(units, f, ensure_ascii=False, indent=1)
    print(f"{len(units)} units from pages {first}-{last} -> {dest}")
