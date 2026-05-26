"""
extract_pdf_universal.py
------------------------
A universal PDF content extractor – auto‑detects languages (ISO 639‑3),
document type, and structure (mono/parallel).  Writes CSV incrementally.
Output filename follows the input PDF name:  <stem>_parallel.csv  or  <stem>_mono.csv

Now works in batch mode:
  - Scans the SOURCES_DIR for any PDF that has no matching CSV in DATA_DIR
  - Processes them one after another automatically.
  - No GUI, no interactive prompts.

Requirements:
    pip install google-genai pypdfium2 Pillow
"""

import csv
import io
import json
import os
import random
import re
import sys
import time
from pathlib import Path

import pypdfium2 as pdfium
from google import genai
from google.genai import types

# ════════════════════════════════════════════════════════════
# CONFIGURATION  (edit these values)
# ════════════════════════════════════════════════════════════

API_KEY           = os.getenv("GEMINI_API_KEY", "GEMINI-API-KEY-HERE")
MODEL_NAME        = "gemini-3.1-flash-lite"        # user preference

# ----- Batch directories -----
SOURCES_DIR       = "/home/owusus/Documents/GitHub/pdf2data/sources/twi"   # PDF input folder
DATA_DIR          = "/home/owusus/Documents/GitHub/pdf2data/data/twi"      # CSV output folder

RECIPE_PATH       = "document_recipe.json"         # temporary recipe (overwritten per file)

START_PAGE        = 1                              # 1‑based, inclusive
END_PAGE          = None                           # None = all pages
SAMPLE_PAGES      = 6
DPI               = 150
SLEEP_BETWEEN     = 2.0                            # seconds between API calls

# ════════════════════════════════════════════════════════════
# PROMPTS
# ════════════════════════════════════════════════════════════

SAMPLING_PROMPT = """You are analysing a sample page from a PDF document.

Your job is to describe what kind of content appears on this page so we can
build an extraction recipe for the whole document.

Please describe:
1. What TYPE of page is this? (cover, table of contents, body/content,
   index, bibliography, blank, appendix, figure-only, etc.)
2. If it is a CONTENT page — what are the repeating structured entries?
   Describe the pattern: what fields/data appear per entry?
   Give 2–3 concrete examples directly from the page.
3. Are there section headings or categories? How are they formatted?
4. What language(s) appear? List the ISO 639-3 code(s) (e.g. "eng", "twi") for each language.
   Also describe the script (Latin, Arabic, etc.) and whether any special characters appear.
5. Is the content monolingual (only one language per entry) or parallel
   (the same entry given in two or more languages side‑by‑side)?
6. What is the overall topic or domain of this document?  Give a single
   lowercase word that best categorises the document type (e.g. "dictionary",
   "medical", "stories", "grammar", "bible", "legal").

Be specific and literal — quote actual text you see. Respond in plain English prose.
"""

RECIPE_SYNTHESIS_PROMPT = """You are designing a data extraction recipe for a PDF document.

Below are descriptions of {n} randomly sampled pages from the document:

{descriptions}

Based on these descriptions, create a JSON extraction recipe that:
1. Identifies what the content pages contain and what fields to extract per entry.
2. Determines the languages present and whether the document is monolingual or parallel.
   - "languages": a list of ISO 639-3 codes (e.g. ["eng", "twi"]). Order is important: for parallel
     documents the first language column will correspond to the first code, etc.
   - "parallel": true if the entries are parallel (same content in multiple languages),
     false if monolingual.
3. Provides a concise extraction prompt (field: "extraction_prompt") that will be
   sent to an AI model along with each page image. The prompt must:
   - Explain the document type and entry structure.
   - For each entry, instruct the model to return an object with keys EXACTLY equal to
     the ISO 639-3 codes from "languages". If parallel=true, all keys must be present
     and contain the corresponding language text. If parallel=false, only the single
     language key should be present (the other languages list will have only one entry).
   - **Important for splitting**: If a single entry contains multiple words or phrases
     (e.g. separated by commas, semicolons, or slashes) that are separate variants or
     synonyms mapped to the same equivalent, split them into individual results.
     For example, if a dictionary entry has "aba, afa" mapped to "child", produce two
     results: {{"eng": "child", "twi": "aba"}} and {{"eng": "child", "twi": "afa"}}.
   - **Important for orthography**: If the document contains a Ghanaian language (e.g.
     Akan/Twi, Ga, Ewe, Dagbani), instruct the model to use the current standard
     orthography for that language (e.g. for Akan: use the Akuapem/Asante standard as
     promoted by the Bureau of Ghana Languages). Diacritics and special characters must
     be correctly preserved (e.g. ɛ, ɔ, ŋ).
   - Instruct the model to skip non-content pages (covers, TOC, blanks) by
     returning an empty results list.
   - Instruct the model to return ONLY valid JSON with schema:
     {{"results": [{{"eng": "...", "twi": "..."}}]}}  (or a single language object).
   - Preserve special characters exactly.
4. Lists the field names (field: "fields") that will become CSV columns.
   Always include a "page" field (added automatically; do not include it in the
   extraction_prompt's JSON schema). The other fields should be the ISO 639-3 codes.
5. Gives the recipe a short descriptive name (field: "name") and a one-sentence
   description (field: "description").
6. Provides a one‑word, lowercase document‑type label (field: "doc_type").
   This must be a single word like "dictionary", "medical", "stories", "bible", etc.
   Use underscores if absolutely necessary (e.g. "legal_contracts"), but prefer a
   single unhyphenated word.
7. Marks which pages appear to be non-content (field: "skip_page_types",
   a list of strings like ["cover", "table_of_contents", "blank"]).

Respond ONLY with valid JSON — no markdown, no extra text — matching this schema:
{{
  "name": "...",
  "description": "...",
  "languages": ["...", "..."],
  "parallel": true/false,
  "doc_type": "dictionary",
  "fields": ["...", "..."],
  "skip_page_types": ["...", ...],
  "extraction_prompt": "..."
}}
"""


# ════════════════════════════════════════════════════════════
# CORE LOGIC
# ════════════════════════════════════════════════════════════

def make_client(api_key: str) -> genai.Client:
    if api_key in ("", "GEMINI-API-KEY-HERE"):
        sys.exit("\n  ✗  No valid Gemini API key set. Set API_KEY in the script or GEMINI_API_KEY in environment.\n")
    return genai.Client(api_key=api_key)


def rasterise_page(pdf_doc, page_index: int, dpi: int) -> bytes:
    page   = pdf_doc[page_index]
    bitmap = page.render(scale=dpi / 72, rotation=0)
    buf    = io.BytesIO()
    bitmap.to_pil().save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        inner = parts[1]
        if inner.startswith("json"):
            inner = inner[4:]
        text = inner.strip()
    return text


def call_vision(client, jpeg_bytes: bytes, prompt: str,
                model: str, json_mode: bool = False) -> str:
    kw = {"temperature": 0.1}
    if json_mode:
        kw["response_mime_type"] = "application/json"
    resp = client.models.generate_content(
        model=model,
        contents=[
            types.Part.from_bytes(data=jpeg_bytes, mime_type="image/jpeg"),
            types.Part.from_text(text=prompt),
        ],
        config=types.GenerateContentConfig(**kw),
    )
    return resp.text.strip()


def call_text(client, prompt: str, model: str) -> str:
    resp = client.models.generate_content(
        model=model,
        contents=[types.Part.from_text(text=prompt)],
        config=types.GenerateContentConfig(temperature=0.2),
    )
    return resp.text.strip()


# ── Phase 1: Sample ──────────────────────────────────────────

def phase_sample(client, pdf_doc, cfg: dict) -> list[dict]:
    total = len(pdf_doc)
    
    # Decide which pages can be sampled.
    # Normally skip the first page (cover), but if the document is very small
    # we include all pages to have enough material for the recipe.
    if total <= cfg["sample_pages"]:
        # Use every page
        population = list(range(total))
    else:
        # Exclude page 0 (cover); keep pages 1 … total-1
        population = list(range(1, total))
    
    if not population:
        print("\n  ⚠  Document has no sampleable pages (0 pages?). Skipping sampling.")
        return []
    
    # Never try to sample more pages than exist
    n = min(cfg["sample_pages"], len(population))
    indices = sorted(random.sample(population, n))
    
    print(f"\n  Sampling {n} pages (of {total}) …\n")
    samples = []
    for idx in indices:
        page_num = idx + 1          # 1‑based for printing
        print(f"    Page {page_num} …", end=" ", flush=True)
        try:
            jpeg = rasterise_page(pdf_doc, idx, cfg["dpi"])
            desc = call_vision(client, jpeg, SAMPLING_PROMPT, cfg["model"])
            print("✓")
            samples.append({"page_num": page_num, "description": desc})
        except Exception as e:
            print(f"✗ ({e})")
        time.sleep(cfg["sleep"])
    return samples


# ── Phase 2: Recipe ──────────────────────────────────────────

def phase_recipe(client, samples: list[dict], cfg: dict) -> dict:
    block  = "\n\n".join(
        f"--- Page {s['page_num']} ---\n{s['description']}"
        for s in samples
    )
    prompt = RECIPE_SYNTHESIS_PROMPT.format(n=len(samples), descriptions=block)

    print("\n  🧠 Synthesising recipe …", end=" ", flush=True)
    raw    = call_text(client, prompt, cfg["model"])
    recipe = json.loads(strip_fences(raw))
    print("✓")
    return recipe


def save_recipe(recipe: dict, path: Path) -> None:
    path.write_text(json.dumps(recipe, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  💾 Recipe saved → {path}")


def load_recipe(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def print_recipe(recipe: dict) -> None:
    print("\n  ── Recipe ───────────────────────────────────────────")
    print(f"  Name       : {recipe.get('name', '—')}")
    print(f"  Doc type   : {recipe.get('doc_type', '—')}")
    print(f"  Parallel   : {recipe.get('parallel', False)}")
    print(f"  Languages  : {', '.join(recipe.get('languages', []))}")
    print(f"  Fields     : {', '.join(recipe.get('fields', []))}")
    print(f"  Skip       : {', '.join(recipe.get('skip_page_types', []))}")
    print(  "  ─────────────────────────────────────────────────────")


# ── Phase 3: Extract (incremental CSV writing) ───────────────

def phase_extract(client, pdf_doc, recipe: dict, cfg: dict,
                  start: int, end: int, out_csv: Path) -> None:
    languages   = recipe["languages"]
    fieldnames  = ["page"] + languages

    total_pages = end - start + 1
    print(f"\n  🚀 Extracting pages {start}–{end} …\n")

    csvfile = open(out_csv, "w", newline="", encoding="utf-8")
    writer  = csv.DictWriter(csvfile, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    csvfile.flush()

    entries_count = 0

    try:
        for page_num in range(start, end + 1):
            page_index = page_num - 1
            done = page_num - start + 1
            bar_w = 30
            filled = int(bar_w * done / total_pages)
            bar = "█" * filled + "░" * (bar_w - filled)
            print(f"  [{bar}] {done}/{total_pages}  page {page_num} …", end=" ", flush=True)

            try:
                jpeg    = rasterise_page(pdf_doc, page_index, cfg["dpi"])
                raw     = call_vision(client, jpeg, recipe["extraction_prompt"],
                                      cfg["model"], json_mode=True)
                parsed  = json.loads(strip_fences(raw))
                entries = parsed.get("results", [])

                for entry in entries:
                    row = {"page": page_num}
                    for lang in languages:
                        row[lang] = entry.get(lang, "")
                    writer.writerow(row)
                    csvfile.flush()
                    entries_count += 1

                print(f"{len(entries)} entries")
            except json.JSONDecodeError as exc:
                print(f"⚠ JSON error ({exc})")
            except Exception as exc:
                print(f"✗ ({exc})")

            time.sleep(cfg["sleep"])

    finally:
        csvfile.close()

    print(f"\n  ✅  {entries_count} entries written → {out_csv}\n")


# ── Output filename: <pdf_stem>_parallel.csv or _mono.csv ────

def generate_output_csv_path(recipe: dict, pdf_stem: str, data_dir: Path) -> Path:
    """Generate output path using the PDF's stem and mono/parallel suffix."""
    suffix = "parallel" if recipe.get("parallel") else "mono"
    filename = f"{pdf_stem}_{suffix}.csv"
    return data_dir / filename


# ── Check if any output CSV already exists for a given PDF stem ──

def csv_already_exists(pdf_stem: str, data_dir: Path) -> bool:
    """
    Return True if a CSV file whose name matches the PDF stem exists.
    Checks for:
      - <stem>.csv
      - <stem>_parallel.csv
      - <stem>_mono.csv
      - any file starting with <stem> and ending with .csv
    """
    # Exact matches (most common)
    candidates = [
        data_dir / f"{pdf_stem}.csv",
        data_dir / f"{pdf_stem}_parallel.csv",
        data_dir / f"{pdf_stem}_mono.csv",
    ]
    if any(p.exists() for p in candidates):
        return True

    # Wildcard: any CSV whose filename starts with the stem
    # This catches files like <stem>_something.csv
    for f in data_dir.glob("*.csv"):
        if f.stem == pdf_stem or f.stem.startswith(pdf_stem + "_"):
            return True

    return False


# ════════════════════════════════════════════════════════════
# MAIN (batch mode)
# ════════════════════════════════════════════════════════════

def main():
    # ----- verify directories -----
    sources_dir = Path(SOURCES_DIR)
    data_dir = Path(DATA_DIR)

    if not sources_dir.is_dir():
        sys.exit(f"  ✗  SOURCES_DIR does not exist or is not a directory: {sources_dir}")
    if not data_dir.is_dir():
        print(f"  ⚠  DATA_DIR does not exist – creating: {data_dir}")
        data_dir.mkdir(parents=True, exist_ok=True)

    # ----- gather all PDF files in sources root -----
    pdf_files = sorted(sources_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"  ✓  No PDF files found in {sources_dir}. Nothing to do.\n")
        return

    # ----- filter out already processed -----
    to_process = []
    skipped = []
    for pdf_path in pdf_files:
        if csv_already_exists(pdf_path.stem, data_dir):
            skipped.append(pdf_path.name)
        else:
            to_process.append(pdf_path)

    print(f"  📂 Found {len(pdf_files)} PDF(s) in {sources_dir}")
    if skipped:
        print(f"  ⏭  Already processed (CSV exists): {', '.join(skipped)}")
    if not to_process:
        print("  ✅  All PDFs already have a corresponding CSV. Exiting.\n")
        return

    print(f"  📝 Will process {len(to_process)} new PDF(s):\n")
    for p in to_process:
        print(f"       • {p.name}")
    print()

    # ----- global client -----
    cfg = {
        "api_key":      API_KEY,
        "model":        MODEL_NAME,
        "sample_pages": SAMPLE_PAGES,
        "dpi":          DPI,
        "sleep":        SLEEP_BETWEEN,
    }
    client = make_client(cfg["api_key"])

    recipe_path = Path(RECIPE_PATH)

    # ----- process each PDF -----
    for idx, pdf_path in enumerate(to_process, 1):
        print(f"\n{'='*60}")
        print(f"  [{idx}/{len(to_process)}]  {pdf_path.name}")
        print(f"{'='*60}")

        try:
            pdf_doc = pdfium.PdfDocument(str(pdf_path))
        except Exception as e:
            print(f"  ✗  Failed to open PDF: {e}")
            continue

        total_pages = len(pdf_doc)
        print(f"  ✓  Loaded '{pdf_path.name}'  ({total_pages} pages)")

        start = START_PAGE
        end   = END_PAGE if END_PAGE is not None else total_pages
        if not (1 <= start <= total_pages) or not (start <= end <= total_pages):
            print(f"  ✗  Page range {start}-{end} invalid for this document. Skipping.")
            pdf_doc.close()
            continue

        # ---- Phase 1+2: Sample & Recipe ----
        samples = phase_sample(client, pdf_doc, cfg)
        if not samples:
            print("  ✗  No pages sampled successfully. Skipping this PDF.")
            pdf_doc.close()
            continue

        recipe = phase_recipe(client, samples, cfg)
        save_recipe(recipe, recipe_path)          # overwritten each time, okay
        print_recipe(recipe)

        # ---- Phase 3: Extract ----
        out_csv = generate_output_csv_path(recipe, pdf_path.stem, data_dir)
        print(f"  📄 Output file → {out_csv}")
        phase_extract(client, pdf_doc, recipe, cfg,
                      start=start, end=end, out_csv=out_csv)

        pdf_doc.close()

    print("\n  🎉  Batch processing complete.\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  Interrupted. Bye!\n")
