"""
build_parallel_corpus.py (v3 – stricter dictionary cleaning)
----------------------------------------------------------------------
Scans all CSVs in DATA_DIR.  For each parallel CSV (≥2 language columns),
reads every row (ignoring 'page') and builds a set of (value1, value2, ...)
tuples in canonical (sorted) language order.

Cleans text aggressively:
  - Removes parentheses, brackets, braces, angle brackets but keeps content
  - Removes leading dictionary markup: "= alternatives", abbreviation tokens (v., n., s., etc.)
  - Strips stray numbering/bullets, isolated punctuation
  - Normalizes Unicode and whitespace
  - Drops pure cross-reference entries (e.g. "inf., s. gye bàtá")
  - Drops Bible/scripture citation fragments (e.g. "Mt. 26,8")
  - Splits or drops semicolon-separated sub-entries

Output: parallel_sentences.csv  and  parallel_words.csv
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

MIN_WORD_COUNT_FOR_SENTENCE = 4          # words in any language
DICTIONARY_CLEAN = True                  # enable aggressive annotation removal

# Known linguistic abbreviations to strip from beginning of fields
ABBREVIATIONS = {
    "v.", "s.", "n.", "adj.", "adv.", "prep.", "conj.", "interj.", "pron.",
    "red.",           # reduplicated form
    "cf.", "pr.",     # cross‑reference, proverb
    "F.", "Ak.", "Mf.", "Twi", "Eng",
    "inf.", "pl.", "sing.", "lit.", "fig.", "syn.", "ant.",
    "e.g.", "i.e.", "etc.",
    "caus.", "intr.", "tr.",   # grammatical voices
    "br.",                     # e.g. "óyè br." (abbreviation found in corpus)
    "Mt.", "Mk.", "Lk.", "Jn.", "Ac.", "Rom.", "Cor.", "Gal.", "Eph.",
    "Phil.", "Col.", "Th.", "Tim.", "Tit.", "Heb.", "Jas.", "Pet.", "Rev.",
}

# Regex for Bible/scripture citations like "Mt. 26,8" or "Jn. 3:16"
BIBLE_CITATION_RE = re.compile(
    r'\b(?:Mt|Mk|Lk|Jn|Ac|Rom|[12]\s*Cor|Gal|Eph|Phil|Col|[12]\s*Th|'
    r'[12]\s*Tim|Tit|Heb|Jas|[12]\s*Pet|[12]\s*Jn|Jude|Rev)\.'
    r'\s*\d+[,:]\d+',
    re.IGNORECASE,
)

# After initial abbreviation stripping, if the remainder starts with a
# cross-reference pattern ("s. <word>", "cf. <word>", "v. <word>") the
# whole entry is a pointer — not a translatable pair.
CROSS_REF_RE = re.compile(
    r'^(?:s|cf|v|see|vide)\.\s+\S',
    re.IGNORECASE,
)

# Matches entries that are *only* a scripture citation (possibly with minor noise)
PURE_CITATION_RE = re.compile(
    r'^[\w.]+\s+\d+[,;:\s]\d+[\s.,;]*$'
)

# ════════════════════════════════════════════════════════════
# TEXT CLEANING
# ════════════════════════════════════════════════════════════

def strip_leading_abbreviations(text: str) -> str:
    """Repeatedly remove known abbreviation tokens from the start of text."""
    while True:
        tokens = text.split()
        if not tokens:
            break
        first = tokens[0]
        if first.lower().rstrip('.,;:') in {a.lower().rstrip('.,;:') for a in ABBREVIATIONS} \
                or first in ABBREVIATIONS:
            text = ' '.join(tokens[1:]).lstrip(' ,;')
        else:
            break
    return text


def clean_text(text: str, dictionary_mode: bool = True) -> str:
    """
    Clean a text field thoroughly.
    Returns empty string if the field should be discarded entirely.
    """
    if not text or not isinstance(text, str):
        return ""

    # 1. Unicode normalisation
    text = unicodedata.normalize('NFKC', text)

    # 2. Remove enclosing brackets/parentheses but keep content
    text = re.sub(r'\(([^)]*)\)', r'\1', text)
    text = re.sub(r'\[([^\]]*)\]', r'\1', text)
    text = re.sub(r'\{([^}]*)\}', r'\1', text)
    text = re.sub(r'\<([^>]*)\>', r'\1', text)

    if dictionary_mode:
        # 3. Remove Bible/scripture citations entirely
        text = BIBLE_CITATION_RE.sub('', text)

        # 4. Remove leading "= alternatives"
        text = re.sub(r'^=\s*', '', text)

        # 5. Remove leading stray numbers/bullets
        text = re.sub(r'^\s*\d+[.)]\s*', '', text)
        text = re.sub(r'^\s*[•\-\–\—\*\+]\s*', '', text)

        # 6. Strip leading abbreviation tokens
        text = strip_leading_abbreviations(text)

        # 7. After stripping, if what remains is a cross-reference, discard
        if CROSS_REF_RE.match(text.strip()):
            return ""

        # 8. Remove isolated punctuation at start/end
        text = re.sub(r'^\s*[,;:\/]\s*', '', text)
        text = re.sub(r'\s*[,;:\-–—\/]\s*$', '', text)

        # 9. If the whole value is a scripture citation pattern, discard
        if PURE_CITATION_RE.match(text.strip()):
            return ""

    # 10. Collapse whitespace
    text = re.sub(r'\s+', ' ', text)

    # 11. Remove space before punctuation
    text = re.sub(r'\s+([.,!?;:])', r'\1', text)

    # 12. Ensure space after sentence-ending punctuation if followed by a letter
    text = re.sub(r'([.!?])([A-Za-zɛɔŋɲƐƆŊƝ])', r'\1 \2', text)

    return text.strip()


def split_semicolons(text: str) -> list[str]:
    """
    Split a field on semicolons and return the non-empty cleaned parts.
    Each part is itself cleaned for leading abbreviations.
    """
    parts = [p.strip() for p in text.split(';')]
    result = []
    for part in parts:
        part = strip_leading_abbreviations(part.lstrip(' ,'))
        # Drop parts that are cross-references or pure citations
        if CROSS_REF_RE.match(part):
            continue
        if PURE_CITATION_RE.match(part):
            continue
        if part:
            result.append(part)
    return result


def clean_tuple(values: tuple[str, ...]) -> list[tuple[str, ...]]:
    """
    Clean all values in a tuple.
    Returns a list of tuples (may be >1 if semicolons split into sub-entries,
    or empty list if the row should be discarded entirely).
    """
    # First pass: basic cleaning on each field
    cleaned = [clean_text(v, DICTIONARY_CLEAN) for v in values]

    # If any field is empty after cleaning → discard row
    if not all(cleaned):
        return []

    # Second pass: handle semicolon sub-entries
    # Only split if at least one field contains a semicolon after cleaning
    if DICTIONARY_CLEAN and any(';' in v for v in cleaned):
        split_fields = [split_semicolons(v) for v in cleaned]
        # Take the minimum number of sub-parts across all fields
        n = min(len(parts) for parts in split_fields)
        if n == 0:
            return []
        result = []
        for i in range(n):
            candidate = tuple(parts[i] for parts in split_fields)
            if all(candidate):
                result.append(candidate)
        return result if result else []

    return [tuple(cleaned)]


# ════════════════════════════════════════════════════════════
# HELPER
# ════════════════════════════════════════════════════════════

def is_sentence(tup: tuple[str, ...], threshold: int) -> bool:
    """Return True if any part of the tuple contains >= threshold words."""
    return any(len(text.split()) >= threshold for text in tup)


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
    parallel_files = []
    lang_sets = []

    for fpath in csv_files:
        with open(fpath, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if not header:
                continue
            lang_cols = [col for col in header if col.lower() != "page"]
            if len(lang_cols) >= 2:
                parallel_files.append((fpath, lang_cols))
                lang_sets.append(frozenset(lang_cols))

    if not parallel_files:
        print("No parallel CSV files found (need at least 2 language columns).")
        return

    set_counts = Counter(lang_sets)
    canonical_set, _ = set_counts.most_common(1)[0]
    canonical_langs = sorted(canonical_set)

    print(f"Canonical language columns (sorted): {canonical_langs}")

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

    twi_col = canonical_langs[0]
    for col in canonical_langs:
        if 'twi' in col.lower():
            twi_col = col
            break

    tuple_files: dict[tuple, set] = defaultdict(set)
    skipped_empty = 0
    skipped_crossref = 0
    total_raw = 0
    total_produced = 0

    for fpath, original_cols in matching_files:
        stem = fpath.stem
        with open(fpath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                total_raw += 1
                try:
                    raw_values = tuple(row[lang].strip() for lang in canonical_langs)
                except KeyError:
                    continue

                produced = clean_tuple(raw_values)

                if not produced:
                    skipped_empty += 1
                    continue

                for tup in produced:
                    # Final guard: skip if any value is only punctuation remnants
                    if all(ch in '.,;:()[]{}' for ch in tup[0]):
                        skipped_crossref += 1
                        continue
                    tuple_files[tup].add(stem)
                    total_produced += 1

    all_entries = dict(tuple_files)

    print(f"\n  Total raw rows processed : {total_raw}")
    print(f"  Skipped (empty/discarded): {skipped_empty}")
    print(f"  Skipped (cross-ref/other): {skipped_crossref}")
    print(f"  Unique parallel entries  : {len(all_entries)}  (from {total_produced} produced)")

    sentences = {}
    words = {}
    for tup, files in all_entries.items():
        if is_sentence(tup, MIN_WORD_COUNT_FOR_SENTENCE):
            sentences[tup] = files
        else:
            words[tup] = files

    print(f"  → Sentences: {len(sentences)}")
    print(f"  → Words    : {len(words)}")

    print(f"\n  🧹 Cleaning examples (first 5):")
    for i, tup in enumerate(list(all_entries.keys())[:5]):
        print(f"    {i+1}. {tup}")

    def write_corpus(output_path: Path, entries: dict):
        if not entries:
            print(f"  No entries for {output_path.name}, skipping.")
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

    write_corpus(data_dir / OUTPUT_SENTENCES, sentences)
    write_corpus(data_dir / OUTPUT_WORDS, words)

    print(f"\n✅ Sentence corpus → {data_dir / OUTPUT_SENTENCES}")
    print(f"✅ Word corpus     → {data_dir / OUTPUT_WORDS}")
    print(f"   count ≥ 1 – sentences: {sum(1 for f in sentences.values() if len(f)-1 >= 1)}")
    print(f"   count ≥ 1 – words    : {sum(1 for f in words.values() if len(f)-1 >= 1)}")


if __name__ == "__main__":
    main()
