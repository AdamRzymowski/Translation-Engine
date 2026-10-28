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

## Glossary

- Stored in `Glossary.csv` at the repository root.
- Columns: `English,Translation` — one term per row.
- It is one combined glossary shared across all batches; never create a separate glossary per batch.
- Existing entries are not changed automatically; the user decides when a translation changes.

## Implementation language

- **TypeScript** on Node.js (chosen for now; may be revisited).
- Known trade-off: TypeScript PDF libraries (e.g. pdf-lib) cannot easily replace existing text
  in place, so keeping the original PDF layout will be harder than in Python. Plan output
  around rebuilding pages from the translated text unless a better library is found.

## Translation backend

- Translation is done with the **Claude API**, using the official Anthropic TypeScript SDK
  (`@anthropic-ai/sdk`); no raw HTTP calls.
- Default model: `claude-opus-5-5`.
- The glossary is sent with every request as the source of truth for terms, placed before the
  document text so it can be prompt-cached across files in a batch.
- New terms the model finds are returned as structured output (term + proposed translation)
  so they can be added to `Glossary.csv`, rather than parsed out of free text.
- Never hard-code an API key; read it from the environment (`ANTHROPIC_API_KEY`).

## Open decisions

- Interface (e.g. command-line tool or web app).
- Output format: PDFs that keep the original layout, or translated text/Word files.
- Whether to support more than one target language (one glossary column per language).
