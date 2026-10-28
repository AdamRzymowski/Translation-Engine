import fs from "node:fs";
import { parse } from "csv-parse/sync";
import { stringify } from "csv-stringify/sync";

export interface GlossaryEntry {
  english: string;
  translation: string;
}

const COLUMNS = ["English", "Translation"];

// The combined glossary. Entries are keyed by lower-cased English term so the same term
// is never added twice with different capitalisation.
export class Glossary {
  private entries = new Map<string, GlossaryEntry>();

  constructor(private readonly path: string) {}

  static load(path: string): Glossary {
    const glossary = new Glossary(path);
    if (!fs.existsSync(path)) return glossary;

    const rows: Record<string, string>[] = parse(fs.readFileSync(path, "utf8"), {
      bom: true,
      columns: true,
      skip_empty_lines: true,
      trim: true,
    });
    for (const row of rows) {
      const english = row.English ?? "";
      const translation = row.Translation ?? "";
      if (english) glossary.entries.set(english.toLowerCase(), { english, translation });
    }
    return glossary;
  }

  get size(): number {
    return this.entries.size;
  }

  // Adds terms that are not in the glossary yet. Existing entries are never overwritten:
  // only the user changes an agreed translation. Returns the entries that were added.
  add(terms: GlossaryEntry[]): GlossaryEntry[] {
    const added: GlossaryEntry[] = [];
    for (const term of terms) {
      const english = term.english.trim();
      const translation = term.translation.trim();
      const key = english.toLowerCase();
      if (!english || !translation || this.entries.has(key)) continue;
      const entry = { english, translation };
      this.entries.set(key, entry);
      added.push(entry);
    }
    return added;
  }

  save(): void {
    const rows = [...this.entries.values()].map((e) => [e.english, e.translation]);
    // The BOM lets Excel open the file as UTF-8, so Polish characters display correctly.
    fs.writeFileSync(this.path, stringify(rows, { bom: true, header: true, columns: COLUMNS }));
  }

  // The glossary as sent to Claude: one "English => translation" pair per line.
  toPromptText(): string {
    if (this.entries.size === 0) return "(The glossary is empty so far.)";
    return [...this.entries.values()].map((e) => `${e.english} => ${e.translation}`).join("\n");
  }
}
