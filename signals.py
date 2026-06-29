import re
from groq import Groq
from config import GROQ_API_KEY, LLM_MODEL

_client = Groq(api_key=GROQ_API_KEY)


def llm_signal(text: str) -> float:
    """
    Signal 1: ask the LLM how AI-generated this text reads.
    Returns a float 0.0–1.0 where 1.0 = clearly AI, 0.0 = clearly human.
    """
    prompt = (
        "You are an AI-content detector. Read the text below and rate how likely "
        "it is to be AI-generated on a scale from 0.0 to 1.0:\n"
        "  - 0.0 = clearly written by a human\n"
        "  - 0.5 = genuinely ambiguous\n"
        "  - 1.0 = clearly AI-generated\n\n"
        "Reply with ONLY the number on the first line. No words, no explanation.\n\n"
        f"Text:\n{text}"
    )

    try:
        response = _client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=20,
            temperature=0,
        )
        raw = response.choices[0].message.content.strip()
        match = re.search(r"\d+(?:\.\d+)?", raw)
        if not match:
            return 0.5
        score = float(match.group())
        return max(0.0, min(1.0, score))
    except Exception:
        return 0.5

import statistics


def _split_sentences(text: str) -> list[str]:
    """Crude sentence split on .!? — good enough for stylometry."""
    parts = re.split(r"[.!?]+", text)
    return [p.strip() for p in parts if p.strip()]


def stylo_signal(text: str) -> tuple[float, dict]:
    """
    Signal 2: stylometric heuristics.

    Computes three metrics:
      - sentence length variance (low variance = AI-like)
      - type-token ratio (mid band = AI-like)
      - punctuation density (low = AI-like)

    Each is mapped to a 0.0–1.0 AI-likeness sub-score, then averaged.
    Returns (combined_score, metrics_dict).
    """
    sentences = _split_sentences(text)
    words = re.findall(r"[A-Za-z']+", text.lower())

    # Defensive: very short texts can't be measured reliably
    if len(words) < 20 or len(sentences) < 2:
        return 0.5, {
            "sentence_length_stdev": None,
            "type_token_ratio": None,
            "punct_density": None,
            "note": "Text too short for reliable stylometry",
        }

    # 1) Sentence length variance — std-dev of word counts per sentence
    sent_lens = [len(s.split()) for s in sentences]
    stdev = statistics.pstdev(sent_lens)
    # Map: stdev of 0 → 1.0 (AI), stdev of 10+ → 0.0 (human)
    variance_score = max(0.0, min(1.0, 1.0 - (stdev / 10.0)))

    # 2) Type-token ratio — unique words / total words.
    # Short texts naturally have high TTR, so we adjust by length using
    # the Carroll's Corrected TTR formula: unique / sqrt(2 * total).
    # AI text tends to score lower (more repeated common words).
    ttr = len(set(words)) / len(words)
    cttr = len(set(words)) / ((2 * len(words)) ** 0.5)
    # Map: cttr below ~5.0 → AI-like (vocabulary feels narrow for length)
    #      cttr above ~7.5 → human-like
    # Calibrated to short paragraph inputs: cttr ~3.8 → AI-like (1.0),
    # cttr ~5.5+ → human-like (0.0). Falls between for ambiguous text.
    ttr_score = max(0.0, min(1.0, 1.0 - ((cttr - 3.8) / 1.7)))

    # 3) Punctuation density — non-word punctuation / total chars
    punct_chars = sum(1 for c in text if c in ",;:()\"'-—–…")
    punct_density = punct_chars / max(1, len(text))
    # Map: low density → AI-like; humans hit 0.03+
    punct_score = max(0.0, min(1.0, 1.0 - (punct_density / 0.04)))

    combined = (variance_score + ttr_score + punct_score) / 3.0

    return combined, {
        "sentence_length_stdev": round(stdev, 3),
        "type_token_ratio": round(ttr, 3),
        "punct_density": round(punct_density, 4),
        "variance_score": round(variance_score, 3),
        "ttr_score": round(ttr_score, 3),
        "corrected_ttr": round(cttr, 3),
        "punct_score": round(punct_score, 3),
    }

from config import LLM_WEIGHT, STYLO_WEIGHT


def combine_signals(llm_score: float, stylo_score: float) -> float:
    """Weighted combination of the two signal scores."""
    return LLM_WEIGHT * llm_score + STYLO_WEIGHT * stylo_score