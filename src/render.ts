import fs from "node:fs";
import { createRequire } from "node:module";
import PdfPrinter from "pdfmake";
import type { Content, TDocumentDefinitions } from "pdfmake/interfaces.js";
import type { Block } from "./translate.js";

// pdfmake ships Roboto, which covers Polish characters, as base64 font files.
const require = createRequire(import.meta.url);
const vfs: Record<string, string> = require("pdfmake/build/vfs_fonts.js");
const font = (name: string) => Buffer.from(vfs[name], "base64");

const printer = new PdfPrinter({
  Roboto: {
    normal: font("Roboto-Regular.ttf"),
    bold: font("Roboto-Medium.ttf"),
    italics: font("Roboto-Italic.ttf"),
    bolditalics: font("Roboto-MediumItalic.ttf"),
  },
});

// Groups consecutive blocks of the same list or table into one pdfmake element.
function toContent(blocks: Block[]): Content[] {
  const content: Content[] = [];
  let i = 0;
  while (i < blocks.length) {
    const block = blocks[i];
    if (block.kind === "heading") {
      const level = Math.min(Math.max(block.level, 1), 3);
      content.push({ text: block.text, style: `h${level}` });
      i++;
    } else if (block.kind === "paragraph") {
      content.push({ text: block.text, style: "body" });
      i++;
    } else if (block.kind === "table_row") {
      const rows: string[][] = [];
      while (i < blocks.length && blocks[i].kind === "table_row") rows.push(blocks[i++].cells);
      const columns = Math.max(1, ...rows.map((r) => r.length));
      const body = rows.map((r) => Array.from({ length: columns }, (_, c) => r[c] ?? ""));
      content.push({
        table: { headerRows: 1, widths: Array(columns).fill("*"), body },
        layout: "lightHorizontalLines",
        style: "table",
      });
    } else {
      const kind = block.kind;
      const level = Math.max(block.level, 1);
      const items: string[] = [];
      while (i < blocks.length && blocks[i].kind === kind && Math.max(blocks[i].level, 1) === level) {
        items.push(blocks[i++].text);
      }
      const list = kind === "bullet" ? { ul: items } : { ol: items };
      content.push({ ...list, style: "body", margin: [12 * (level - 1), 0, 0, 6] });
    }
  }
  return content;
}

export async function renderPdf(blocks: Block[], outputPath: string, title: string): Promise<void> {
  const definition: TDocumentDefinitions = {
    info: { title },
    pageSize: "A4",
    pageMargins: [50, 50, 50, 60],
    content: toContent(blocks),
    defaultStyle: { font: "Roboto", fontSize: 10, lineHeight: 1.2 },
    styles: {
      h1: { fontSize: 18, bold: true, margin: [0, 16, 0, 8] },
      h2: { fontSize: 14, bold: true, margin: [0, 12, 0, 6] },
      h3: { fontSize: 11.5, bold: true, margin: [0, 8, 0, 4] },
      body: { margin: [0, 0, 0, 6] },
      table: { fontSize: 9, margin: [0, 4, 0, 10] },
    },
    footer: (page: number, pages: number) => ({
      text: `${page} / ${pages}`,
      alignment: "center",
      fontSize: 8,
      margin: [0, 20, 0, 0],
    }),
  };

  const doc = printer.createPdfKitDocument(definition);
  const done = new Promise<void>((resolve, reject) => {
    const out = fs.createWriteStream(outputPath);
    out.on("finish", resolve);
    out.on("error", reject);
    doc.pipe(out);
  });
  doc.end();
  await done;
}
