import { PDFDocument } from "pdf-lib";

export async function loadPdf(bytes: Uint8Array): Promise<PDFDocument> {
  return PDFDocument.load(bytes, { ignoreEncryption: true });
}

// Copies pages `from`..`to` (1-based, inclusive) into a new, smaller PDF to send to Claude.
export async function extractPages(source: PDFDocument, from: number, to: number): Promise<Uint8Array> {
  const chunk = await PDFDocument.create();
  const indices = Array.from({ length: to - from + 1 }, (_, i) => from - 1 + i);
  for (const page of await chunk.copyPages(source, indices)) chunk.addPage(page);
  return chunk.save();
}
