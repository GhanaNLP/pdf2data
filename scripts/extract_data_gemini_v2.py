"""
extract_pdf_universal.py
------------------------
A universal PDF content extractor – auto‑detects languages (ISO 639‑3),
document type, and structure (mono/parallel).  Writes CSV incrementally.
Output filename follows the input PDF name:  <stem>_parallel.csv  or  <stem>_mono.csv

Includes a file‑picker dialog (GUI) on Windows, macOS, and Linux.
All settings in the CONFIG section – no interactive prompts.

Requirements:
    pip install google-genai pypdfium2 Pillow
    (tkinter is usually included with Python)
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

# ── GUI helpers (cross‑platform) ──────────────────────────
try:
    import tkinter as tk
    from tkinter import filedialog
    HAS_TK = True
except ImportError:
    HAS_TK = False

# ════════════════════════════════════════════════════════════
# CONFIGURATION  (edit these values)
# ════════════════════════════════════════════════════════════

API_KEY           = os.getenv("GEMINI_API_KEY", "GEMINI-API-KEY-HERE")
MODEL_NAME        = "gemini-3.1-flash-lite"        # user preference

PDF_PATH          = ""                             # fallback; ignored when USE_FILE_PICKER is True
USE_FILE_PICKER   = True                           # show a GUI file‑picker to select the PDF

# Mode: "full"        = sample → recipe → extract CSV
#       "recipe_only" = sample → build recipe, stop
#       "reuse"       = skip sampling, use an existing recipe
MODE              = "full"

RECIPE_PATH       = "document_recipe.json"         # for saving / loading
OUTPUT_DIR        = "/home/owusus/Documents/GitHub/pdf2data/data"   # user specified
                                                   # leave empty to use the PDF's own folder
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
# CORE LOGIC  (unchanged)
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
    total   = len(pdf_doc)
    lo, hi  = 1, total - 1
    n       = min(cfg["sample_pages"], total)
    indices = sorted(random.sample(range(lo, hi + 1), n))

    print(f"\n  Sampling {n} pages (of {total}) …\n")
    samples = []
    for idx in indices:
        page_num = idx + 1
        print(f"    Page {page_num} …", end=" ", flush=True)
        try:
            jpeg  = rasterise_page(pdf_doc, idx, cfg["dpi"])
            desc  = call_vision(client, jpeg, SAMPLING_PROMPT, cfg["model"])
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

def generate_output_csv_path(recipe: dict, pdf_path: Path, output_dir: str) -> Path:
    """Generate output path using the PDF's stem and mono/parallel suffix."""
    out_dir = Path(output_dir) if output_dir else pdf_path.parent
    suffix = "parallel" if recipe.get("parallel") else "mono"
    filename = f"{pdf_path.stem}_{suffix}.csv"
    return out_dir / filename


# ── File picker helper ───────────────────────────────────────

def ask_pdf_path_with_dialog() -> str:
    """Open a native file‑picker dialog and return the chosen PDF path."""
    if not HAS_TK:
        print("\n  ⚠  tkinter not available. Falling back to manual path entry.\n")
        path = input("  Please enter the full path to the PDF file: ").strip().strip('"').strip("'")
        if not path:
            sys.exit("  ✗  No file selected.")
        return path

    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    file_path = filedialog.askopenfilename(
        title="Select a PDF file",
        filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")]
    )
    root.destroy()
    if not file_path:
        sys.exit("  ✗  No file selected. Exiting.")
    return file_path


# ════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════

def main():
    # ---- get PDF path ----
    if USE_FILE_PICKER:
        pdf_path_str = ask_pdf_path_with_dialog()
    else:
        pdf_path_str = PDF_PATH
        if not pdf_path_str:
            sys.exit("  ✗  PDF_PATH is empty and USE_FILE_PICKER is False. Nothing to do.")

    pdf_path = Path(pdf_path_str)
    if not pdf_path.exists():
        sys.exit(f"  ✗  PDF not found: {pdf_path}")

    pdf_doc = pdfium.PdfDocument(str(pdf_path))
    total_pages = len(pdf_doc)
    print(f"  ✓  Loaded '{pdf_path.name}'  ({total_pages} pages)")

    recipe_path = Path(RECIPE_PATH)

    start = START_PAGE
    end   = END_PAGE if END_PAGE is not None else total_pages
    if not (1 <= start <= total_pages):
        sys.exit(f"  ✗  START_PAGE {start} out of range (1–{total_pages}).")
    if not (start <= end <= total_pages):
        sys.exit(f"  ✗  END_PAGE {end} out of range (1–{total_pages}) or before start.")

    # ---- build config dict ----
    cfg = {
        "api_key":      API_KEY,
        "model":        MODEL_NAME,
        "sample_pages": SAMPLE_PAGES,
        "dpi":          DPI,
        "sleep":        SLEEP_BETWEEN,
    }

    client = make_client(cfg["api_key"])

    # ---- recipe ----
    if MODE == "reuse":
        if not recipe_path.exists():
            sys.exit(f"  ✗  Recipe file not found: {recipe_path}")
        recipe = load_recipe(recipe_path)
        print(f"\n  📋 Loaded recipe '{recipe.get('name', '?')}'")
    else:
        samples = phase_sample(client, pdf_doc, cfg)
        if not samples:
            sys.exit("  ✗  No pages sampled successfully. Check your API key and PDF.")
        recipe = phase_recipe(client, samples, cfg)
        save_recipe(recipe, recipe_path)

    print_recipe(recipe)

    if MODE in ("full", "reuse"):
        out_csv = generate_output_csv_path(recipe, pdf_path, OUTPUT_DIR)
        print(f"  📄 Output file → {out_csv}")
        phase_extract(client, pdf_doc, recipe, cfg,
                      start=start, end=end, out_csv=out_csv)
    else:
        print("  Recipe-only mode — done!\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  Interrupted. Bye!\n")
