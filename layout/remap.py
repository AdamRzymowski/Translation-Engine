"""Moves translations onto new unit ids after extract.py has been changed and re-run.

Units are matched by page and English text (in page order, so repeated texts such as
"Height 1–3 only." pair up one by one). Prints the translations that could not be matched,
so they can be redone by hand.

Usage: python layout/remap.py <old_units.json> <new_units.json> <translations.json> [...]
       (each translations file is rewritten in place)
"""
import json
import sys
from collections import defaultdict


def main(old_path, new_path, *translation_paths):
    old = {u["id"]: u for u in json.load(open(old_path, encoding="utf-8"))}
    new_by_text = defaultdict(list)
    for u in json.load(open(new_path, encoding="utf-8")):
        new_by_text[(u["page"], u["text"])].append(u["id"])

    for path in translation_paths:
        translations = json.load(open(path, encoding="utf-8"))
        remapped, lost = {}, []
        for old_id, text in translations.items():
            unit = old.get(old_id)
            candidates = new_by_text.get((unit["page"], unit["text"])) if unit else None
            if candidates:
                remapped[candidates.pop(0)] = text
            else:
                # Kept under its old id (unused until fixed) so the translation is not lost.
                remapped[f"unmatched:{old_id}"] = text
                lost.append(f"  {old_id}: {unit['text'][:60] if unit else '?'}")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(remapped, f, ensure_ascii=False, indent=1)
        print(f"{path}: {len(remapped)} kept, {len(lost)} need redoing")
        if lost:
            print("\n".join(lost))


if __name__ == "__main__":
    main(*sys.argv[1:])
