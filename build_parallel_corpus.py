"""
build_parallel_corpus.py
------------------------
Scans all CSVs in DATA_DIR.  For each parallel CSV (≥2 language columns),
reads every row (ignoring 'page') and builds a set of (value1, value2, ...)
tuples in canonical (sorted) language order.

Cleans text by:
  - Removing parentheses but keeping their content
  - Removing brackets but keeping their content
  - Collapsing multiple spaces
  - Trimming leading/trailing whitespace
  - Removing stray numbering and non-sentence-ending punctuation artifacts
  - Normalizing Unicode characters

The final corpus is split into two files:
  - parallel_sentences.csv  (entries with at least N words in any language)
  - parallel_words.csv      (shorter entries)

Output columns: sorted language names, 'count' (number of additional files),
and 'source_files' (list of source CSV stems).
"""

import csv
import re
import unicodedata
from pathlib import Path
from collections import defaultdict, Counter

# ════════════════════════════════════════════════════════════
# CONFIGURATION
# ════════════════════════════════════════════════════════════

DATA_DIR = "/home/owusus/Documents/GitHub/pdf2data/data/twi"
OUTPUT_SENTENCES = "parallel_sentences.csv"
OUTPUT_WORDS     = "parallel_words.csv"

MIN_WORD_COUNT_FOR_SENTENCE = 4   # adjust this threshold as needed

# ════════════════════════════════════════════════════════════
# TEXT CLEANING
# ════════════════════════════════════════════════════════════

def clean_text(text: str) -> str:
    """
    Clean a text field:
    1. Remove parentheses ( ) but keep their content
    2. Remove brackets [ ] but keep their content
    3. Remove curly braces { } but keep their content
    4. Normalize Unicode (combining characters, fullwidth, etc.)
    5. Remove stray leading numbers/bullets (like "1.", "2)", "•", "- ")
    6. Remove non-sentence-ending punctuation artifacts (isolated commas, colons, semicolons at edges)
    7. Collapse multiple spaces into single space
    8. Trim leading/trailing whitespace
    """
    if not text or not isinstance(text, str):
        return ""
    
    # Normalize Unicode (NFKC handles fullwidth chars, ligatures, etc.)
    text = unicodedata.normalize('NFKC', text)
    
    # Remove parentheses but keep content: "palm (of the hand)" → "palm of the hand"
    text = re.sub(r'\(([^)]*)\)', r'\1', text)
    
    # Remove brackets but keep content: "nsam [variant]" → "nsam variant"
    text = re.sub(r'\[([^\]]*)\]', r'\1', text)
    
    # Remove curly braces but keep content
    text = re.sub(r'\{([^}]*)\}', r'\1', text)
    
    # Remove angle brackets but keep content: "<see note>" → "see note"
    text = re.sub(r'\<([^>]*)\>', r'\1', text)
    
    # Remove stray leading numbers with dots/parens: "1. word" → "word", "2) phrase" → "phrase"
    text = re.sub(r'^\s*\d+[.)]\s*', '', text)
    
    # Remove leading bullets and dashes: "• word", "- word", "– word", "— word", "* word"
    text = re.sub(r'^\s*[•\-\–\-\—\*\+]\s*', '', text)
    
    # Remove isolated punctuation at start of text (commas, colons, semicolons, slashes)
    text = re.sub(r'^\s*[,;:\/]\s*', '', text)
    
    # Remove isolated punctuation at end that aren't sentence endings
    # Keep . ! ? but remove trailing , ; : - – —
    text = re.sub(r'\s*[,;:\-–—]\s*$', '', text)
    
    # Remove trailing slash: "word/" → "word"
    text = re.sub(r'\s*\/\s*$', '', text)
    
    # Collapse multiple spaces (including non-breaking spaces) into single space
    text = re.sub(r'\s+', ' ', text)
    
    # Remove spaces before punctuation
    text = re.sub(r'\s+([.,!?;:])', r'\1', text)
    
    # Ensure space after sentence-ending punctuation if followed by letter
    text = re.sub(r'([.!?])([A-Za-zɛɔŋɲƐƆŊƝ])', r'\1 \2', text)
    
    # Trim leading/trailing whitespace
    text = text.strip()
    
    return text


def clean_tuple(values: tuple[str, ...]) -> tuple[str, ...]:
    """Apply cleaning to all values in a tuple."""
    return tuple(clean_text(v) for v in values)


# ════════════════════════════════════════════════════════════
# HELPER
# ════════════════════════════════════════════════════════════

def is_sentence(tup: tuple[str, ...], threshold: int) -> bool:
    """Return True if any part of the tuple contains >= threshold words."""
    for text in tup:
        if len(text.split()) >= threshold:
            return True
    return False


# ════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════

def main():
    data_dir = Path(DATA_DIR)
    if not data_dir.is_dir():
        print(f"✗ DATA_DIR not found: {data_dir}")
        return

    csv_files = sorted(data_dir.glob("*.csv"))
    if not csv_files:
        print("No CSV files found.")
        return

    # First pass: discover all parallel files and their language sets.
    parallel_files = []          # list of (Path, list_of_original_lang_cols)
    lang_sets = []

    for fpath in csv_files:
        with open(fpath, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if not header:
                continue
            # Remove 'page' if present (usually first column)
            lang_cols = [col for col in header if col.lower() != "page"]
            if len(lang_cols) >= 2:   # parallel document
                parallel_files.append((fpath, lang_cols))
                lang_sets.append(frozenset(lang_cols))

    if not parallel_files:
        print("No parallel CSV files found (need at least 2 language columns).")
        return

    # Determine the canonical set of languages – use the most common one.
    set_counts = Counter(lang_sets)
    canonical_set, _ = set_counts.most_common(1)[0]
    canonical_langs = sorted(canonical_set)   # sorted order for consistent column naming

    print(f"Canonical language columns (sorted): {canonical_langs}")

    # Keep only files whose language set matches the canonical set.
    matching_files = []
    for fpath, cols in parallel_files:
        if frozenset(cols) == canonical_set:
            matching_files.append((fpath, cols))
        else:
            print(f"⚠  Skipping {fpath.name} – language set differs: {set(cols)}")

    if not matching_files:
        print("No parallel CSV files match the canonical language set.")
        return

    print(f"Found {len(matching_files)} parallel CSV(s) with matching language set.")

    # Second pass: collect entries with cleaning.
    tuple_files = defaultdict(set)   # key: tuple in canonical order, value: set of file stems
    skipped_empty = 0
    total_raw = 0

    for fpath, original_cols in matching_files:
        stem = fpath.stem
        with open(fpath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                total_raw += 1
                try:
                    raw_values = tuple(row[lang].strip() for lang in canonical_langs)
                except KeyError:
                    continue   # missing a column – skip row
                
                # Clean the values
                cleaned_values = clean_tuple(raw_values)
                
                # Skip if any value is empty after cleaning
                if not all(v for v in cleaned_values):
                    skipped_empty += 1
                    continue
                
                tuple_files[cleaned_values].add(stem)

    all_entries = dict(tuple_files)
    if not all_entries:
        print("No parallel entries found.")
        return

    print(f"  Total raw rows processed: {total_raw}")
    print(f"  Skipped (empty after cleaning): {skipped_empty}")
    print(f"  Collected {len(all_entries)} unique parallel entries from {len(matching_files)} file(s).")

    # Split entries into sentences and words
    sentences = {}
    words = {}
    for tup, files in all_entries.items():
        if is_sentence(tup, MIN_WORD_COUNT_FOR_SENTENCE):
            sentences[tup] = files
        else:
            words[tup] = files

    print(f"  → Sentences: {len(sentences)}")
    print(f"  → Words    : {len(words)}")

    # Show cleaning examples
    print(f"\n  🧹 Cleaning examples (first 5):")
    example_count = 0
    for tup in list(all_entries.keys())[:5]:
        print(f"    → {tup}")
        example_count += 1
    if example_count == 0:
        print("    (no examples to show)")

    # Helper to write a sorted CSV
    def write_corpus(output_path: Path, entries: dict):
        if not entries:
            print(f"  No entries for {output_path.name}, skipping file creation.")
            return
        sorted_entries = sorted(entries.items(),
                                key=lambda item: (-(len(item[1]) - 1), item[0]))
        with open(output_path, "w", newline="", encoding="utf-8") as fout:
            fieldnames = canonical_langs + ["count", "source_files"]
            writer = csv.DictWriter(fout, fieldnames=fieldnames)
            writer.writeheader()
            for tup, files in sorted_entries:
                row = {lang: val for lang, val in zip(canonical_langs, tup)}
                row["count"] = len(files) - 1
                row["source_files"] = "; ".join(sorted(files))
                writer.writerow(row)

    # Write both files
    write_corpus(data_dir / OUTPUT_SENTENCES, sentences)
    write_corpus(data_dir / OUTPUT_WORDS, words)

    print(f"\n✅ Sentence corpus → {data_dir / OUTPUT_SENTENCES}")
    print(f"✅ Word corpus     → {data_dir / OUTPUT_WORDS}")
    print(f"   Entries with count >= 1 – sentences: {sum(1 for f in sentences.values() if len(f)-1 >= 1)}")
    print(f"   Entries with count >= 1 – words    : {sum(1 for f in words.values() if len(f)-1 >= 1)}")


if __name__ == "__main__":
    main()
