// Language that all PDFs are translated into. Change this to translate into another language.
export const TARGET_LANGUAGE = "Polish";

// Language of the source PDFs.
export const SOURCE_LANGUAGE = "English";

// Folder holding the source PDFs to translate.
export const BASELINE_DIR = "Baseline";

// Folder the translated PDFs are written to.
export const TARGET_DIR = "Target";

// The one combined glossary shared by every batch.
export const GLOSSARY_PATH = "Glossary.csv";

// Claude model used for translation.
export const MODEL = "claude-opus-5-5";

// Pages sent to Claude per request. Smaller chunks keep each response well under the
// output limit and let new glossary terms carry forward sooner.
export const PAGES_PER_CHUNK = 6;
