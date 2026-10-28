"""Combines translation files into one, filling repeated labels from a phrasebook.

Every unit whose English text exactly matches a phrasebook entry (e.g. "Type", "One Turn",
the page footer) gets that translation, unless a translation file gives one for that unit.

Usage: python layout/merge.py <units.json> <phrasebook.json> <out.json> <translations.json>...
"""
import json
import re
import sys


def main(units_path, phrasebook_path, out_path, *translation_paths):
    units = json.load(open(units_path, encoding="utf-8"))
    phrasebook = json.load(open(phrasebook_path, encoding="utf-8"))
    merged = {}
    for u in units:
        if u["text"] in phrasebook:
            merged[u["id"]] = phrasebook[u["text"]]
    explicit = 0
    colours = {u["id"]: (re.findall(r"color:(#[0-9a-f]{6})", u["html"]) or ["#83198c"])[0] for u in units}
    for path in translation_paths:
        for key, value in json.load(open(path, encoding="utf-8")).items():
            if not key.startswith("unmatched:"):
                # [[words]] marks a linked cross-reference, shown purple and bold like the original.
                colour = colours.get(key, "#83198c")
                merged[key] = re.sub(r"\[\[(.+?)\]\]", rf'<span style="color:{colour}"><b>\1</b></span>', value)
                explicit += 1
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=0)
    print(f"{len(merged)} translations ({explicit} from files, the rest from the phrasebook)")


if __name__ == "__main__":
    main(*sys.argv[1:])
