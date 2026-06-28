#!/usr/bin/env python3
"""Detect German vs English in a text without external dependencies.

Usage:
    detect_lang.py [--default de|en] [text]
    echo "..." | detect_lang.py

Prints "de" or "en" on stdout. Heuristic: count occurrences of common
function words for each language; whichever scores higher wins. Falls
back to --default (or "de") on a tie or empty input.

This is intentionally tiny — it does not need to be perfect, only
"good enough to pick a TTS voice." For mixed-language text, picks the
dominant language.
"""

from __future__ import annotations

import argparse
import re
import sys

# Top function words. Kept intentionally short — adding many makes the
# detector slower without improving accuracy on de/en (the binary case).
DE = {
    "und", "der", "die", "das", "ist", "nicht", "ein", "eine", "den", "dem",
    "des", "im", "mit", "auf", "von", "zu", "sich", "auch", "wie", "war",
    "sind", "werden", "wird", "haben", "hat", "kann", "noch", "nur", "aber",
    "oder", "wenn", "weil", "doch", "schon", "über", "für", "bei", "nach",
    "ich", "du", "er", "sie", "wir", "ihr", "es", "wurde", "worden", "ja",
    "nein", "sehr", "viel", "viele", "einen", "einer", "einem", "eines",
    "alle", "alles", "kein", "keine",
}

EN = {
    "the", "and", "is", "of", "to", "in", "that", "it", "for", "with",
    "on", "as", "are", "was", "be", "this", "have", "has", "had", "not",
    "but", "or", "if", "when", "you", "we", "they", "i", "he", "she",
    "do", "does", "did", "an", "a", "by", "at", "from", "which", "what",
    "no", "yes", "very", "much", "many", "some", "any", "all", "none",
    "would", "could", "should", "will", "shall",
}

WORD_RE = re.compile(r"[A-Za-zÄÖÜäöüß]+")


def score(text: str) -> tuple[int, int]:
    de_count = 0
    en_count = 0
    for w in WORD_RE.findall(text.lower()):
        if w in DE:
            de_count += 1
        if w in EN:
            en_count += 1
    return de_count, en_count


def detect(text: str, default: str = "de") -> str:
    de_count, en_count = score(text)
    if de_count == 0 and en_count == 0:
        return default
    # Treat umlauts/eszett as a strong German signal even if word counts tie.
    if re.search(r"[äöüÄÖÜß]", text):
        de_count += 2
    if de_count > en_count:
        return "de"
    if en_count > de_count:
        return "en"
    return default


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--default", default="de", choices=["de", "en"])
    ap.add_argument("text", nargs="*", help="Text to analyze; if omitted, read stdin")
    args = ap.parse_args()

    text = " ".join(args.text) if args.text else sys.stdin.read()
    print(detect(text, args.default))
    return 0


if __name__ == "__main__":
    sys.exit(main())
