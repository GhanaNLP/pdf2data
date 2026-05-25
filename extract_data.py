"""
extract_tmg_glossary.py
-----------------------
Sends each page of TMG2016_FinalGlossaryOnlineversion_FULL.pdf to the
Gemini vision API and extracts symptom descriptions with their English
translations and categories, then writes everything to a CSV.

Requirements:
    pip install google-genai pypdf pypdfium2 Pillow

Usage:
    python extract_tmg_glossary.py
"""

import base64
import csv
import io
import json
import os
import time

import pypdfium2 as pdfium
from google import genai
from google.genai import types

# ============================================================
# CONFIGURATION — adjust these paths/values as needed
# ============================================================
GEMINI_API_KEY  = "GEMINI-API-KEY-HERE"
PDF_PATH        = "TMG2016_FinalGlossaryOnlineversion_FULL.pdf"
OUTPUT_CSV      = "tmg_glossary_extracted.csv"
MODEL_NAME      = "gemini-3.1-flash-lite"   # same family as gemini-*-flash-lite
DPI             = 150          # resolution for page rasterisation
START_PAGE      = 1            # 1-based; set higher to resume a run
END_PAGE        = None         # None = process all pages
SLEEP_BETWEEN   = 2.0          # seconds between API calls (rate-limit buffer)
# ============================================================

client = genai.Client(api_key=GEMINI_API_KEY)

EXTRACTION_PROMPT = """You are analysing a page from the "Twi Medical Glossary" (TMG 2016).

This glossary lists medical/clinical symptom phrases in Twi (Asante Twi) together with
their English meaning and the medical category they belong to.

A typical entry looks like this:
    Me barima awu –
    I am impotent.
    {mpagya/Me barima mpagya –
    I don't have any erection.

Your task: extract EVERY symptom entry visible on this page.

For each entry return:
- "category": the section/category heading this entry falls under
  (e.g. "Sexual Problems", "Head", "Chest", etc.).
  If no heading is visible on this page, use the most recent one you can infer,
  or "Unknown" if truly unclear.
- "twi_phrase": the Twi phrase exactly as printed (preserve special characters like ], [, ɔ, ɛ, etc.)
- "english_meaning": the English translation/meaning given for that phrase

If a page contains no symptom entries (e.g. it is a cover page, table of contents,
or blank page) return an empty results list.

Respond ONLY with valid JSON matching this exact schema — no markdown, no extra text:
{
  "results": [
    {
      "category": "...",
      "twi_phrase": "...",
      "english_meaning": "..."
    }
  ]
}"""


def rasterise_page(pdf_doc, page_index: int, dpi: int = 150) -> bytes:
    """Render a single PDF page to a JPEG byte string."""
    page = pdf_doc[page_index]
    scale = dpi / 72          # pdfium renders at 72 dpi by default
    bitmap = page.render(scale=scale, rotation=0)
    pil_image = bitmap.to_pil()
    buf = io.BytesIO()
    pil_image.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def extract_page(jpeg_bytes: bytes, page_num: int) -> list[dict]:
    """Send one page image to Gemini and return the extracted entries."""
    b64 = base64.b64encode(jpeg_bytes).decode("utf-8")

    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=[
            types.Part.from_bytes(data=jpeg_bytes, mime_type="image/jpeg"),
            types.Part.from_text(text=EXTRACTION_PROMPT),
        ],
        config=types.GenerateContentConfig(
            temperature=0.1,                     # low temp → more faithful extraction
            response_mime_type="application/json",
        ),
    )

    raw = response.text.strip()

    # Strip accidental markdown fences if the model adds them
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    parsed = json.loads(raw)
    entries = parsed.get("results", [])

    # Tag each entry with its source page number
    for e in entries:
        e["page"] = page_num

    return entries


def main():
    if not os.path.exists(PDF_PATH):
        raise FileNotFoundError(f"PDF not found: {PDF_PATH}")

    pdf_doc  = pdfium.PdfDocument(PDF_PATH)
    total    = len(pdf_doc)
    start    = START_PAGE - 1          # convert to 0-based
    end      = (END_PAGE or total)     # exclusive upper bound

    print(f"PDF has {total} pages. Processing pages {START_PAGE}–{end}.")

    fieldnames = ["page", "category", "twi_phrase", "english_meaning"]
    all_rows   = []

    for page_index in range(start, end):
        page_num = page_index + 1
        print(f"  Page {page_num}/{end} …", end=" ", flush=True)

        try:
            jpeg_bytes = rasterise_page(pdf_doc, page_index, dpi=DPI)
            entries    = extract_page(jpeg_bytes, page_num)
            print(f"{len(entries)} entries found.")
            all_rows.extend(entries)
        except json.JSONDecodeError as exc:
            print(f"JSON parse error — skipping. ({exc})")
        except Exception as exc:
            print(f"ERROR — skipping. ({exc})")

        time.sleep(SLEEP_BETWEEN)

    # Write to CSV
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"\nDone! {len(all_rows)} entries written to '{OUTPUT_CSV}'.")


if __name__ == "__main__":
    main()
