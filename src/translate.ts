import Anthropic from "@anthropic-ai/sdk";
import { z } from "zod";
import { MODEL, SOURCE_LANGUAGE, TARGET_LANGUAGE } from "./config.js";
import type { Glossary } from "./glossary.js";

const BLOCK_KINDS = ["heading", "paragraph", "bullet", "numbered", "table_row"] as const;

const BlockSchema = z.object({
  kind: z.enum(BLOCK_KINDS),
  level: z.number().int(),
  text: z.string(),
  cells: z.array(z.string()),
});

const ChunkResultSchema = z.object({
  blocks: z.array(BlockSchema),
  new_terms: z.array(z.object({ english: z.string(), translation: z.string() })),
});

export type Block = z.infer<typeof BlockSchema>;
export type ChunkResult = z.infer<typeof ChunkResultSchema>;

// JSON schema for structured output; mirrors ChunkResultSchema. Every block has every field so
// the schema stays flat: unused fields are 0, "" or [].
const OUTPUT_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["blocks", "new_terms"],
  properties: {
    blocks: {
      type: "array",
      items: {
        type: "object",
        additionalProperties: false,
        required: ["kind", "level", "text", "cells"],
        properties: {
          kind: { type: "string", enum: [...BLOCK_KINDS] },
          level: { type: "integer" },
          text: { type: "string" },
          cells: { type: "array", items: { type: "string" } },
        },
      },
    },
    new_terms: {
      type: "array",
      items: {
        type: "object",
        additionalProperties: false,
        required: ["english", "translation"],
        properties: {
          english: { type: "string" },
          translation: { type: "string" },
        },
      },
    },
  },
};

const INSTRUCTIONS = `You translate tabletop wargame rulebooks from ${SOURCE_LANGUAGE} into ${TARGET_LANGUAGE}.

Translate every piece of text on the pages you are given. Do not summarise, shorten or skip content. Leave out only running page headers, footers and page numbers.

The glossary below is binding. Whenever a glossary term appears, use exactly the ${TARGET_LANGUAGE} translation it gives. ${TARGET_LANGUAGE} words change their endings with grammatical case and number: inflect the glossary translation to fit the sentence, but never swap it for a different word.

Some terms recur across the books but are not in the glossary yet: rule names, special rules, unit and model names, character names, spells, items, army and place names, and game-specific keywords. For each one, decide on a translation (keep the original spelling for names that should stay untranslated), use it consistently, and list it in new_terms in its base (dictionary) form. Do not list ordinary words or terms already in the glossary.

Return the translated content as blocks in reading order:
- heading: text is the heading, level is 1 (chapter), 2 (section) or 3 (subsection).
- paragraph: one paragraph of body text; level is 0.
- bullet / numbered: one list item; level is the nesting depth starting at 1.
- table_row: one table row with each cell translated in cells; the first row of a table is its header row; level is 0 and text is "".
For blocks other than table_row, cells is [].`;

export class RefusalError extends Error {}

// Translates one chunk of pages. `pdf` is a small PDF holding just those pages.
export async function translateChunk(
  client: Anthropic,
  glossary: Glossary,
  pdf: Uint8Array,
  description: string,
): Promise<ChunkResult> {
  const stream = client.beta.messages.stream({
    model: MODEL,
    max_tokens: 64000,
    betas: ["server-side-fallback-2026-07-01"],
    fallbacks: "default",
    output_config: {
      effort: "high",
      format: { type: "json_schema", schema: OUTPUT_SCHEMA },
    },
    system: [
      { type: "text", text: INSTRUCTIONS },
      {
        type: "text",
        text: `Glossary (${SOURCE_LANGUAGE} => ${TARGET_LANGUAGE}):\n${glossary.toPromptText()}`,
        cache_control: { type: "ephemeral" },
      },
    ],
    messages: [
      {
        role: "user",
        content: [
          {
            type: "document",
            source: { type: "base64", media_type: "application/pdf", data: Buffer.from(pdf).toString("base64") },
          },
          { type: "text", text: `Translate ${description} into ${TARGET_LANGUAGE}.` },
        ],
      },
    ],
  });
  const message = await stream.finalMessage();

  if (message.stop_reason === "refusal") {
    throw new RefusalError(`Claude declined to translate ${description}: ${message.stop_details?.explanation ?? "no reason given"}`);
  }
  if (message.stop_reason === "max_tokens") {
    throw new Error(`The translation of ${description} hit the output limit. Lower PAGES_PER_CHUNK in src/config.ts.`);
  }

  // If a fallback model took over, only the text after the last switch point is the answer.
  const lastFallback = message.content.findLastIndex((block) => block.type === "fallback");
  const json = message.content
    .slice(lastFallback + 1)
    .map((block) => (block.type === "text" ? block.text : ""))
    .join("");
  return ChunkResultSchema.parse(JSON.parse(json));
}
