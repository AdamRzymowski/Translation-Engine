# Translation Engine

## Project aim

A tool that translates batches of PDF files from English into another language while keeping
terminology consistent across every document it produces.

Source PDFs contain recurring terms — rule names, character names, place names and other
proper or game-specific terms. These must be translated the same way in every output file,
no matter which batch they came from.

## How it should work

1. **Ingest a batch of PDFs.** Extract the text from each file.
2. **Collect terms.** Identify recurring terms (rule names, character names, etc.) and add any
   new ones to the combined glossary.
3. **Use the glossary as the source of truth.** Every term already in the glossary must be
   translated exactly as the glossary says. New terms get a translation added to the glossary
   so later batches reuse it.
4. **Translate and output.** Produce the translated version of each PDF.
5. **Repeat with new batches.** The glossary grows over time and carries forward, so all PDFs
   ever processed share the same terminology.

## Target language

- Set by `TARGET_LANGUAGE` in `src/config.ts` — currently **Polish**. Never hard-code the
  language anywhere else; always read it from this setting.
- Polish needs fonts with Polish characters (ą, ć, ę, ł, ń, ó, ś, ź, ż) in the output PDFs.
- Polish words change endings by grammatical case. The glossary holds the base form of each
  term; translations should inflect it correctly for the sentence while keeping the same term.

## Glossary

- Stored in `Glossary.csv` at the repository root.
- Columns: `English,Translation,Category,Notes` — one term per row. Only English and
  Translation are sent to Claude; Category and Notes are for the user's review.
- Notes markers: `CHOOSE` (source gave alternatives; the first is used until the user picks),
  `CHECK` (typo fixed, split or inferred English), `CLASH` (wording overlaps another term).
- Terms Claude adds during translation get the category `Added by translator`.
- Source glossary lists from the user may have section headings written Polish-first
  ("Polish (English)") while the terms are English-first ("English – Polish"); the headings
  become the Category column, not glossary entries.
- It is one combined glossary shared across all batches; never create a separate glossary per batch.
- Existing entries are not changed automatically; the user decides when a translation changes.

## Implementation language

- **TypeScript** on Node.js (chosen for now; may be revisited).
- Known trade-off: TypeScript PDF libraries (e.g. pdf-lib) cannot easily replace existing text
  in place, so keeping the original PDF layout is harder than in Python. Because layout
  fidelity is paramount (see Output format), this is the main technical risk of the project;
  revisit the language choice if in-place replacement proves impractical in TypeScript.

## Interface

- A **command-line tool** run with Node.js: the user points it at a folder of PDFs and it
  writes the translated files to an output folder, updating `Glossary.csv` as it goes.
- A web interface may be added later on top of the same core code, so keep the translation
  logic separate from the command-line handling.

## Translation backend

- Translation is done with the **Claude API**, using the official Anthropic TypeScript SDK
  (`@anthropic-ai/sdk`); no raw HTTP calls.
- Model: `claude-sonnet-5-5` (set in `src/config.ts`).
- **<u>No extra cost: translations must use the user's existing Claude plan allowance.</u>**
  The user does not want to pay for Anthropic API usage, which is billed separately from the
  plan. Do not run anything that calls the API (the current `src/translate.ts` does) and do
  not ask the user to create an API key. Translate through Claude Code instead, either
  directly in a session or by having the tool call Claude Code, which signs in with the plan.
- The glossary is sent with every request as the source of truth for terms, placed before the
  document text so it can be prompt-cached across files in a batch.
- New terms the model finds are returned as structured output (term + proposed translation)
  so they can be added to `Glossary.csv`, rather than parsed out of free text.
- Never hard-code an API key; read it from the environment (`ANTHROPIC_API_KEY`).

## Output format

> **<u>Keeping the original graphics and layout is of paramount importance.</u>**
> A translated PDF must look like the source: same pages, same images, artwork, icons,
> tables, columns, fonts sizes and positions, with only the text swapped for the translation.
> Any change that loses or rearranges graphics or layout is a regression, even if the
> translation itself is good. When translation quality and layout fidelity pull in different
> directions, raise it with the user rather than silently sacrificing the layout.

- Each source PDF produces one translated PDF.
- **Use the Python pipeline in `layout/`** (see below): it keeps the original pages and swaps
  only the text. The older TypeScript pdfmake rebuild (`src/render.ts`) drops all graphics and
  must not be used for output.
- Output PDFs must embed a font that supports Polish characters.
- Output files keep the source file name with the target language added
  (e.g. `Baseline/rules.pdf` → `Target/rules.Polish.pdf`).

## Folders

- `Baseline/` — the source PDFs to translate. The user adds new baseline files here.
- `Target/` — the translated PDFs the tool produces. `Target/.work/` keeps each translated
  chunk as JSON so an interrupted run resumes without paying for the same pages again.
- PDFs in both folders are git-ignored; only the folders themselves are tracked.

## Running

```bash
npx tsx src/cli.ts                       # translate every PDF in Baseline/
npx tsx src/cli.ts some.pdf --pages 1-6  # cheap test on a few pages
npm run typecheck
```

Needs `ANTHROPIC_API_KEY` set. Use `npx tsx src/cli.ts` rather than `npm run translate -- ...`
when passing options, because PowerShell swallows the `--`.

## Code layout

- `src/config.ts` — all settings (languages, folders, model, pages per request).
- `src/cli.ts` — command-line handling only.
- `src/glossary.ts` — loads, extends and saves `Glossary.csv` (UTF-8 with BOM for Excel).
- `src/pdf.ts` — splits a source PDF into page chunks with pdf-lib.
- `src/translate.ts` — one Claude request per chunk: PDF pages in, translated blocks and new
  glossary terms out (structured JSON output, server-side refusal fallback enabled).
- `src/render.ts` — builds the output PDF from translated blocks with pdfmake (Roboto font).

## Layout-preserving pipeline (Python, `layout/`)

The PDF layout step is in Python with PyMuPDF (`pip install --user pymupdf`; a project `.venv`
fails because the folder path is too long for Windows). It replaces the pdfmake rebuild.

1. `python layout/extract.py <src.pdf> <first> <last> <units.json>` — splits the pages into
   translation units (paragraphs, list items, headings, labels) with line boxes, font, size,
   colour, alignment and the text as HTML (`<b>`, `<i>`, `<sup>`).
2. Translate: write `<translations.json>` mapping unit id → Polish HTML with the same tags.
   Units left out stay untouched (page numbers, version labels). `"-"` merges a unit into the
   one before it (one paragraph split across text blocks). Done in a Claude Code session.
3. `python layout/replace.py <src.pdf> <units.json> <translations.json> <out.pdf>` — removes
   only the translated text (images, artwork and line art kept, nothing painted over), writes
   the Polish into the same place, using free space right/below, else shrinking; reports units
   it had to shrink. Handles "stencil" text (invisible text clipping a coloured fill, used for
   the purple contents entries) and restores links removed with the text.

Fonts: Caladea (the book's body font) is installed on this PC; Cinzel (headings) and Caslon
(cover) are not, so Book Antiqua stands in — installing Cinzel/Caslon would match exactly.
Windows paths over 260 characters fail, so keep output file names short (e.g. `Target/AC_p1-6.Polish.pdf`).
Work files live in `Target/.work/layout/`.

## Open decisions

- Whether to support more than one target language (one glossary column per language).
