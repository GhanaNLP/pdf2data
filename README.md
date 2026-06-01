# archives2data

Extract structured data from PDF documents with local-language content and turn it into reusable corpora for local-language AI.

This project is especially focused on documents that are not already available in clean, structured form — for example scanned dictionaries, glossaries, word lists, school subject references, and other PDFs that contain valuable language data but are hard to reuse directly.

The extracted data is intended to support downstream tasks such as retrieval, evaluation, translation, question answering, and representation learning. One target use case is the Twi-English quality estimation / embedding work shown in the demo below:

- Demo: https://huggingface.co/spaces/ghananlpcommunity/twi-eng-qe-e5-demo
- Model: https://huggingface.co/ghananlpcommunity/twi-eng-qe-e5

## What is in this repository?

The repository currently contains:

- `sources/` — source PDFs to extract from
- `data/` — extracted CSV outputs and cleaned corpora
- `extract_data_gemini.py` — batch PDF extractor powered by Gemini
- `build_parallel_corpus.py` — corpus cleaner/merger for parallel CSV files
- `document_recipe.json` — the current extraction recipe used by the extractor

## Current data focus

The bundled sample data is centered on Twi and related local-language material, including:

- dictionaries
- glossaries
- bilingual word lists
- school subject glossaries
- language guides
- historical/legal texts with local-language content

If you are looking for good starter documents, prioritize:

- scanned or OCR-friendly PDFs
- dictionaries and glossaries
- well-structured bilingual or parallel content
- older documents that are still useful but not widely available in structured format
- documents you are allowed to redistribute or process

## How the extraction pipeline works

1. PDFs are placed in `sources/<language>/`.
2. `extract_data_gemini.py` scans the PDFs and uses Gemini to:
   - identify the document structure
   - detect the languages present
   - generate or update an extraction recipe
   - extract rows into CSV format
3. The output CSVs are written to `data/<language>/`.
4. `build_parallel_corpus.py` cleans and merges the parallel CSVs into corpus files such as:
   - `parallel_sentences.csv`
   - `parallel_words.csv`

## Requirements

- Python 3.10+
- A valid Gemini API key
- The Python packages used by the extractor:
  - `google-genai`
  - `pypdfium2`
  - `Pillow`

## Setup

Create a virtual environment and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install google-genai pypdfium2 Pillow
```

Set your Gemini API key:

```bash
export GEMINI_API_KEY="your-api-key-here"
```

## Important configuration note

The current scripts use absolute paths in their configuration:

- `extract_data_gemini.py` points `SOURCES_DIR` and `DATA_DIR` to a local machine path
- `build_parallel_corpus.py` points `DATA_DIR` to a local machine path

Before running the scripts in a new checkout, update those paths so they point to your local copy of this repository.

## Running the extractor

After updating the local paths in `extract_data_gemini.py`, run:

```bash
python extract_data_gemini.py
```

The extractor runs in batch mode:

- it scans the `sources/` folder for PDFs
- it skips PDFs that already have a matching CSV in `data/`
- it writes one CSV per PDF, using a name like:
  - `<pdf_stem>_parallel.csv`
  - `<pdf_stem>_mono.csv`

The extractor also saves the generated recipe to `document_recipe.json`.

## Building the parallel corpora

After extraction, update the local path in `build_parallel_corpus.py` and run:

```bash
python build_parallel_corpus.py
```

This script:

- reads all CSV files in the data directory
- identifies the canonical language set
- cleans noisy dictionary markup and cross references
- separates entries into sentence-like and word-like corpora
- writes:
  - `parallel_sentences.csv`
  - `parallel_words.csv`

## Repository layout

```text
archives2data/
├── build_parallel_corpus.py
├── document_recipe.json
├── extract_data_gemini.py
├── data/
│   └── twi/
└── sources/
    └── twi/
```

## Contributing

Contributions are welcome. Because this repository is meant to grow useful local-language data, the most helpful contribution is often a new PDF source plus the corresponding extracted CSV.

### Good contribution candidates

Please consider adding PDFs that are:

- dictionaries or glossaries
- bilingual or parallel
- scanned but readable
- high quality and text-rich
- useful for local-language AI
- legally shareable or otherwise permitted for redistribution

### Suggested contribution workflow

1. Fork the repository and create a feature branch.
2. Add the PDF to the appropriate folder under `sources/`.
   - For example: `sources/twi/`
3. If needed, run `extract_data_gemini.py` to generate the matching CSV.
4. Run `build_parallel_corpus.py` if the new data should be included in the shared parallel corpora.
5. Verify that the output looks correct and that special characters are preserved.
6. Open a pull request.

### What to include in your pull request

Please describe:

- the document title
- source or URL, if available
- language(s)
- whether it is monolingual or parallel
- why the document is useful
- any licensing or redistribution notes

### Suggested PR hygiene

- Do not commit API keys or secrets.
- Prefer descriptive filenames for new PDFs and CSVs.
- Avoid adding low-quality scans unless they are uniquely valuable.
- If you are adding generated CSVs, make sure the source PDF is also included or clearly referenced.

## Notes for local-language data work

This project is intentionally designed to preserve local-language characters and orthography. When contributing new documents, please be careful to keep:

- diacritics
- special characters such as `ɛ`, `ɔ`, and `ŋ`
- standard orthography for the target language

If a document contains multiple variants, synonyms, or semicolon-separated subentries, prefer a clean structured extraction that splits them into usable rows.

## License and permissions

Only add documents that you have permission to process and redistribute. If a PDF has unclear licensing, check the source first or avoid including it in a public pull request.

## Acknowledgements

This work is aligned with the broader GhanaNLP effort to build better resources for local-language NLP and evaluation.

