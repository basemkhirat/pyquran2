#!/usr/bin/env python3
"""Generate a KenLM n-gram language model from Quran text (hafs.json).

Usage:
    python scripts/generate_lm.py                  # diacritized LM (default)
    python scripts/generate_lm.py --strip-tashkeel  # strip diacritics (legacy)
    python scripts/generate_lm.py --hafs ./assets/narrations/hafs.json --output ./assets/quran_lm.arpa

By default, diacritics are KEPT because the wav2vec2 Quran ASR model
(rabah2026/wav2vec2-large-xlsr-53-arabic-quran-v_final) was trained on
diacritized emlaey text and its vocabulary includes tashkeel characters.

If the KenLM binaries (lmplz, build_binary) are on $PATH, the script
generates a proper 5-gram binary LM.  Otherwise it creates a simple
unigram ARPA file that pyctcdecode can still use for lexicon-constrained
beam search.
"""
import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile

# Add project root to path so we can import backend modules if needed
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import re

from pyarabic import araby

# Characters that appear in emlaey_text but are NOT in the wav2vec2 model vocabulary.
# These must be stripped so that LM words only contain characters the model can output.
#   U+0657 (ٗ) - Inverted Damma
#   U+06E1 (ۡ) - Small High Dotless Head of Khah
_CHARS_NOT_IN_VOCAB = re.compile("[\u0657\u06E1]")


def normalize_for_model(text: str) -> str:
    """Remove characters not in the wav2vec2 model vocabulary."""
    return _CHARS_NOT_IN_VOCAB.sub("", text)


def extract_quran_text(hafs_path: str, strip_tashkeel: bool = False) -> list[str]:
    """Return list of verse texts from hafs.json (one line per verse).

    By default keeps diacritics to match the wav2vec2 Quran model output.
    Set strip_tashkeel=True only for legacy models that output plain text.
    """
    with open(hafs_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    lines = []
    for chapter in data["chapters"]:
        for verse in chapter["verses"]:
            text = verse.get("emlaey_text", "")
            if strip_tashkeel:
                text = araby.strip_tashkeel(text)
            # Strip characters not in the model vocabulary
            text = normalize_for_model(text)
            text = text.strip()
            if text:
                lines.append(text)
    return lines


def build_arpa_with_kenlm(corpus_path: str, output_path: str, order: int = 5) -> bool:
    """Try building ARPA using KenLM binaries.  Returns True on success."""
    lmplz = shutil.which("lmplz")
    if not lmplz:
        return False

    arpa_path = output_path
    print(f"Building {order}-gram LM with KenLM...")
    try:
        with open(corpus_path, "r") as fin:
            result = subprocess.run(
                [lmplz, "-o", str(order), "--discount_fallback"],
                stdin=fin,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
        with open(arpa_path, "wb") as fout:
            fout.write(result.stdout)

        # Optionally build binary for faster loading
        build_binary = shutil.which("build_binary")
        if build_binary:
            bin_path = arpa_path.replace(".arpa", ".bin")
            subprocess.run(
                [build_binary, arpa_path, bin_path],
                check=True,
                capture_output=True,
            )
            print(f"Binary LM written to {bin_path}")

        print(f"ARPA LM written to {arpa_path}")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"KenLM failed: {e}")
        return False


def build_simple_arpa(lines: list[str], output_path: str):
    """Build a simple unigram + bigram ARPA file from Quran text.

    KenLM requires at least a bigram model, so we extract both unigram
    and bigram counts from the verse lines.
    """
    # Count word and bigram frequencies
    word_counts: dict[str, int] = {}
    bigram_counts: dict[tuple[str, str], int] = {}
    total_words = 0
    total_bigrams = 0

    for line in lines:
        words = line.split()
        for word in words:
            word_counts[word] = word_counts.get(word, 0) + 1
            total_words += 1
        for i in range(len(words) - 1):
            bg = (words[i], words[i + 1])
            bigram_counts[bg] = bigram_counts.get(bg, 0) + 1
            total_bigrams += 1

    n_unigrams = len(word_counts) + 3  # +3 for <s>, </s>, and <unk>
    n_bigrams = len(bigram_counts)

    print(
        f"Building ARPA from {total_words} word tokens "
        f"({len(word_counts)} unique words, {n_bigrams} bigrams)..."
    )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\\data\\\n")
        f.write(f"ngram 1={n_unigrams}\n")
        f.write(f"ngram 2={n_bigrams}\n")
        f.write("\n\\1-grams:\n")

        # Special tokens with backoff weight
        f.write("-1\t<s>\t0\n")
        f.write("-1\t</s>\n")
        f.write("-100\t<unk>\n")

        # Word-level log probabilities with backoff weight 0
        for word, count in sorted(word_counts.items()):
            log_prob = math.log10(count / total_words)
            f.write(f"{log_prob:.4f}\t{word}\t0\n")

        # Bigrams
        f.write("\n\\2-grams:\n")
        for (w1, w2), count in sorted(bigram_counts.items()):
            log_prob = math.log10(count / total_bigrams)
            f.write(f"{log_prob:.4f}\t{w1} {w2}\n")

        f.write("\n\\end\\\n")

    print(f"Simple ARPA LM written to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Generate Quran KenLM language model")
    parser.add_argument(
        "--hafs",
        default=os.path.join(os.path.dirname(__file__), "..", "assets", "narrations", "hafs.json"),
        help="Path to hafs.json",
    )
    parser.add_argument(
        "--output",
        default=os.path.join(os.path.dirname(__file__), "..", "assets", "quran_lm.arpa"),
        help="Output ARPA file path",
    )
    parser.add_argument("--order", type=int, default=5, help="N-gram order (default 5)")
    parser.add_argument(
        "--strip-tashkeel",
        action="store_true",
        default=False,
        help="Strip diacritics (default: keep diacritics for wav2vec2 Quran model)",
    )
    args = parser.parse_args()

    hafs_path = os.path.abspath(args.hafs)
    output_path = os.path.abspath(args.output)

    if not os.path.exists(hafs_path):
        print(f"Error: hafs.json not found at {hafs_path}")
        sys.exit(1)

    lines = extract_quran_text(hafs_path, strip_tashkeel=args.strip_tashkeel)
    mode = "diacritics stripped" if args.strip_tashkeel else "WITH diacritics (matching wav2vec2 Quran model)"
    print(f"Extracted {len(lines)} verses from Quran ({mode})")

    # Write corpus file for KenLM
    corpus_path = os.path.join(tempfile.gettempdir(), "quran_corpus.txt")
    with open(corpus_path, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")

    # Try KenLM first, fall back to simple ARPA
    if not build_arpa_with_kenlm(corpus_path, output_path, args.order):
        print("KenLM not found, generating simple unigram ARPA fallback...")
        build_simple_arpa(lines, output_path)

    # Clean up corpus
    os.remove(corpus_path)
    print("Done!")


if __name__ == "__main__":
    main()
