import fs from "node:fs";
import path from "node:path";
import { parseArgs } from "node:util";
import Anthropic from "@anthropic-ai/sdk";
import { BASELINE_DIR, GLOSSARY_PATH, PAGES_PER_CHUNK, TARGET_DIR, TARGET_LANGUAGE } from "./config.js";
import { Glossary } from "./glossary.js";
import { extractPages, loadPdf } from "./pdf.js";
import { renderPdf } from "./render.js";
import { translateChunk, type Block, type ChunkResult } from "./translate.js";

const USAGE = `Usage: npx tsx src/cli.ts [file.pdf ...] [--pages 1-6] [--force]
       (npm run translate works too when no options are needed)

  file.pdf    PDFs in ${BASELINE_DIR}/ to translate (default: all of them)
  --pages     translate only this page range, e.g. --pages 1-6 for a cheap test
  --force     translate again even if the ${TARGET_DIR}/ file already exists`;

function parsePages(value: string | undefined, pageCount: number): [number, number] {
  if (!value) return [1, pageCount];
  const match = /^(\d+)(?:-(\d+))?$/.exec(value);
  if (!match) throw new Error(`--pages must look like 5 or 1-6, got "${value}"`);
  const from = Number(match[1]);
  const to = Math.min(Number(match[2] ?? match[1]), pageCount);
  if (from < 1 || from > to) throw new Error(`--pages ${value} is outside the document's ${pageCount} pages`);
  return [from, to];
}

async function translateFile(client: Anthropic, glossary: Glossary, fileName: string, pages: string | undefined, force: boolean) {
  const base = path.basename(fileName, ".pdf");
  const source = await loadPdf(fs.readFileSync(path.join(BASELINE_DIR, fileName)));
  const [from, to] = parsePages(pages, source.getPageCount());
  const range = pages ? `.pages-${from}-${to}` : "";
  const outputPath = path.join(TARGET_DIR, `${base}${range}.${TARGET_LANGUAGE}.pdf`);

  if (fs.existsSync(outputPath) && !force) {
    console.log(`Skipping ${fileName}: ${outputPath} already exists (use --force to redo it).`);
    return;
  }
  console.log(`\n${fileName}: pages ${from}-${to} of ${source.getPageCount()}`);

  // Each translated chunk is saved as it finishes, so an interrupted run picks up where it stopped
  // instead of paying for the same pages again.
  const workDir = path.join(TARGET_DIR, ".work", base);
  fs.mkdirSync(workDir, { recursive: true });

  const blocks: Block[] = [];
  for (let start = from; start <= to; start += PAGES_PER_CHUNK) {
    const end = Math.min(start + PAGES_PER_CHUNK - 1, to);
    const chunkPath = path.join(workDir, `pages-${start}-${end}.json`);

    let result: ChunkResult;
    if (fs.existsSync(chunkPath) && !force) {
      result = JSON.parse(fs.readFileSync(chunkPath, "utf8"));
      console.log(`  pages ${start}-${end}: reusing earlier translation`);
    } else {
      process.stdout.write(`  pages ${start}-${end}: translating... `);
      const chunk = await extractPages(source, start, end);
      result = await translateChunk(client, glossary, chunk, `pages ${start}-${end} of ${fileName}`);
      fs.writeFileSync(chunkPath, JSON.stringify(result, null, 2));

      const added = glossary.add(result.new_terms);
      if (added.length > 0) glossary.save();
      console.log(`done, ${added.length} new glossary term${added.length === 1 ? "" : "s"}`);
      for (const term of added) console.log(`      + ${term.english} => ${term.translation}`);
    }
    blocks.push(...result.blocks);
  }

  await renderPdf(blocks, outputPath, `${base} (${TARGET_LANGUAGE})`);
  console.log(`  wrote ${outputPath}`);
}

async function main() {
  const { values, positionals } = parseArgs({
    allowPositionals: true,
    options: {
      pages: { type: "string" },
      force: { type: "boolean", default: false },
      help: { type: "boolean", short: "h", default: false },
    },
  });
  if (values.help) {
    console.log(USAGE);
    return;
  }
  if (!process.env.ANTHROPIC_API_KEY && !process.env.ANTHROPIC_AUTH_TOKEN) {
    console.error("Set ANTHROPIC_API_KEY to your Anthropic API key first (from console.anthropic.com).");
    process.exitCode = 1;
    return;
  }

  const files = positionals.length > 0
    ? positionals.map((f) => path.basename(f))
    : fs.readdirSync(BASELINE_DIR).filter((f) => f.toLowerCase().endsWith(".pdf")).sort();
  if (files.length === 0) {
    console.error(`No PDFs found in ${BASELINE_DIR}/.`);
    process.exitCode = 1;
    return;
  }

  fs.mkdirSync(TARGET_DIR, { recursive: true });
  const glossary = Glossary.load(GLOSSARY_PATH);
  console.log(`Translating ${files.length} file(s) into ${TARGET_LANGUAGE}; glossary has ${glossary.size} term(s).`);

  const client = new Anthropic();
  for (const file of files) {
    await translateFile(client, glossary, file, values.pages, values.force);
  }
  console.log(`\nFinished. Glossary now has ${glossary.size} term(s).`);
}

main().catch((error) => {
  if (error instanceof Anthropic.AuthenticationError) {
    console.error("\nThe Anthropic API key was rejected. Check ANTHROPIC_API_KEY.");
  } else if (error instanceof Anthropic.RateLimitError) {
    console.error("\nRate limited by the Anthropic API. Wait a minute and run the same command again; finished pages are kept.");
  } else if (error instanceof Anthropic.APIError) {
    console.error(`\nAnthropic API error ${error.status}: ${error.message}`);
  } else {
    console.error(`\n${error instanceof Error ? error.message : error}`);
  }
  process.exitCode = 1;
});
