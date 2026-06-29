# Provenance Guard

A multi-signal AI content detection backend for creative-sharing platforms. Given a piece of submitted text, the system runs two independent detection signals, computes a calibrated confidence score, generates a transparency label for the reader, supports appeals from creators who believe they were misclassified, and writes every decision to a structured audit log.

This is **AI201 Project 4 — Show What You Know**.

## Setup

```bash
python -m venv .venv
source .venv/Scripts/activate     # Windows (Git Bash)
# or: source .venv/bin/activate   # Mac/Linux

pip install -r requirements.txt
```

Create a `.env` file in the project root:

```
GROQ_API_KEY=your_key_here
```

Run the server:

```bash
python app.py
```

The API listens on `http://localhost:5000`.

## Architecture Overview

A submission flows through six steps from input to label:

1. **`POST /submit`** receives JSON with `text` and `creator_id`.
2. **Rate limiter** checks the request against per-IP limits (10/min, 100/day).
3. **Signal 1 — LLM semantic assessment** sends the text to Groq's `llama-3.3-70b-versatile` with `temperature=0` and asks for a 0.0–1.0 AI-likelihood score.
4. **Signal 2 — Stylometric heuristics** computes sentence-length variance, corrected type-token ratio, and punctuation density in pure Python, then averages the three sub-scores into a single 0.0–1.0 score.
5. **Confidence scoring** combines the two signals with a weighted average: `confidence = 0.6 × llm_score + 0.4 × stylo_score`. This score is mapped to one of three transparency-label variants.
6. **Audit log** writes a structured SQLite entry containing the content ID, creator ID, both signal scores, the combined confidence, the label, and the classification status.

The response returns the `content_id`, `attribution`, `confidence`, `label_variant`, label text, and individual signal scores. The `content_id` is what a creator uses later to submit an appeal.

## Detection Signals

The system uses **two independent signals** that capture genuinely different properties of the text.

**Signal 1 — LLM semantic assessment.**
What it measures: holistic semantic and stylistic coherence — does the text _read_ as AI? Captured by sending the input to Groq's LLM and asking it to score AI-likelihood on a 0.0–1.0 scale.
Why I chose it: it picks up on the things stylometry can't — register, rhetorical rhythm, hedged formality, semantic uniformity.
What it misses: heavily edited AI output that's been humanized can pass; highly formal human writing (academic, corporate) can false-positive.

**Signal 2 — Stylometric heuristics.**
What it measures: three structural metrics — sentence-length standard deviation, length-corrected type-token ratio (Carroll's CTTR), and punctuation density — combined into a single AI-likeness score. AI prose tends toward uniform sentence length, a narrow vocabulary band, and sparse punctuation.
Why I chose it: it's a structural, statistical signal that's independent from the semantic one. The two signals genuinely disagreeing is informative; both agreeing is strong evidence.
What it misses: texts under ~50 words can't be measured reliably (too few sentences for variance); poetry and very casual writing can read as "low-variance" even when human.

The combination is more informative than either alone because the two signals fail in different directions.

## Confidence Scoring

The two signal scores are combined into a single confidence value:

```
confidence = 0.6 × llm_score + 0.4 × stylo_score
```

The LLM is weighted higher because it captures more nuanced semantic signal; stylometry acts as a structural sanity check. The combined value maps to three calibrated bands:

| Confidence range | Label variant         |
| ---------------- | --------------------- |
| `≥ 0.7`          | high-confidence AI    |
| `0.4 – 0.7`      | uncertain             |
| `< 0.4`          | high-confidence human |

The 0.4 lower edge (rather than 0.5) deliberately **widens the "uncertain" band** to protect against false positives — when the system isn't sure, it says so rather than accusing a human writer.

### Validating the scoring is meaningful

I tested the scoring on four spec inputs and confirmed the confidence values spread cleanly across the bands:

**High-confidence example (clearly AI-generated text):**

> Input: "Artificial intelligence represents a transformative paradigm shift in modern society. It is important to note that while the benefits of AI are numerous, it is equally essential to consider the ethical implications..."
> Result: `confidence: 0.76`, `llm_score: 0.8`, `stylo_score: 0.71`, `label_variant: high_ai`

**Lower-confidence example (clearly human-written text):**

> Input: "ok so i finally tried that new ramen place downtown and honestly? underwhelming. the broth was fine but they put WAY too much sodium in it..."
> Result: `confidence: 0.25`, `llm_score: 0.1`, `stylo_score: 0.62`, `label_variant: high_human`

The signals also legitimately disagree on borderline content: a lightly-edited AI piece scored `llm_score: 0.2` (low — reads casual) but `stylo_score: 0.75` (high — structurally uniform), which combined to `confidence: 0.44` and the `uncertain` label. That's the design working — independent signals catching what each other misses.

## Transparency Label — Three Variants

The label text returned by `/submit` varies by confidence band. Verbatim text below.

| Variant        | Triggered when         | Text returned                                                                                                                                                                                                                              |
| -------------- | ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **high_ai**    | confidence ≥ 0.7       | "⚠️ Likely AI-generated. Our detection system gave this content a {N}% likelihood of being AI-generated, based on its semantic style and structural patterns. The creator can appeal this classification if they believe it is incorrect." |
| **uncertain**  | 0.4 ≤ confidence < 0.7 | "🤔 Attribution unclear. Our detection signals are mixed on this content (confidence {N}%). It may have been written by a human, an AI, or both. Treat the attribution as undetermined."                                                   |
| **high_human** | confidence < 0.4       | "✅ Likely human-written. Our detection system found no strong indicators of AI generation in this content ({N}% AI-likelihood). This is not a guarantee, but suggests the work is the creator's own."                                     |

`{N}` is the confidence rendered as a percentage at runtime.

## Appeals Workflow

Creators who believe they were misclassified can submit an appeal via `POST /appeal` with their `content_id` and free-text `creator_reasoning`. The system:

1. Looks up the original submission in the audit log (404 if not found).
2. Updates the original entry's status from `classified` to `under_review`.
3. Appends a new audit-log entry with `event: "appeal"` capturing the reasoning, timestamp, and `status: "under_review"`.
4. Returns confirmation with `status: "under_review"`.

Automated re-classification is not implemented — appeals are queued for a human reviewer to examine alongside the original signal scores and label.

**Tested example:**

```
POST /appeal {"content_id": "350ba3c4-...", "creator_reasoning": "I wrote this myself..."}
→ {"status": "under_review", "content_id": "350ba3c4-...", "message": "Appeal received..."}
```

The original entry in the log flipped to `status: "under_review"`, and a new appeal entry was appended with the reasoning captured.

## Rate Limiting

I applied Flask-Limiter to `/submit` with **10 requests per minute and 100 per day per IP**.

Reasoning for these values:

- **10/min** — a writer iterating on a piece won't legitimately need to re-submit it more than ~10 times in a minute. A scraper or automated tester would easily exceed this.
- **100/day** — accommodates a creator submitting many pieces over a day's work, but caps abuse cleanly. An adversary trying to probe the detector at scale would hit this within minutes.

**Verified working:** firing 12 rapid requests returns 200 for the first 10 and 429 for requests 11 and 12:

```
200
200
200
200
200
200
200
200
200
200
429
429
```

## Audit Log

Every decision and every appeal is captured in a structured SQLite database (`audit.db`). Each entry stores: `timestamp`, `content_id`, `creator_id`, `event` (`submission` or `appeal`), submitted `text`, `attribution`, `confidence`, `llm_score`, `stylo_score`, `label`, `status`, and `appeal_reasoning`.

Entries can be read via `GET /log`. Example (truncated):

```json
{
  "entries": [
    {
      "entry_id": 27,
      "timestamp": "2026-06-29T02:24:56.615764+00:00",
      "content_id": "350ba3c4-45f6-408c-bd83-41bfc2ca7869",
      "creator_id": "label-test-1",
      "event": "appeal",
      "attribution": "likely_ai",
      "confidence": 0.763,
      "llm_score": 0.8,
      "stylo_score": 0.708,
      "label": "⚠️ Likely AI-generated. Our detection system gave...",
      "status": "under_review",
      "appeal_reasoning": "I wrote this myself from personal experience..."
    },
    {
      "entry_id": 24,
      "timestamp": "2026-06-29T02:18:11.012345+00:00",
      "content_id": "350ba3c4-45f6-408c-bd83-41bfc2ca7869",
      "creator_id": "label-test-1",
      "event": "submission",
      "attribution": "likely_ai",
      "confidence": 0.763,
      "llm_score": 0.8,
      "stylo_score": 0.708,
      "label": "⚠️ Likely AI-generated...",
      "status": "under_review"
    },
    {
      "entry_id": 25,
      "timestamp": "2026-06-29T02:19:42.987654+00:00",
      "content_id": "bf4bd2d3-9465-470f-88cf-fd411e35b03f",
      "creator_id": "label-test-2",
      "event": "submission",
      "attribution": "likely_human",
      "confidence": 0.249,
      "llm_score": 0.1,
      "stylo_score": 0.624,
      "label": "✅ Likely human-written...",
      "status": "classified"
    }
  ]
}
```

The audit log is the canonical record of every classification decision, the signals that produced it, and any appeal filed against it.

## Known Limitations

**Formal human writing scores too high.** The system's biggest known weakness is that polished, formal human writing — academic prose, technical documentation, or writing from non-native English speakers using a careful register — tends to score in the `uncertain` or even `high_ai` band. This is tied to a property of both signals: the LLM signal reads formal register as "AI-like" because most of its training examples of AI text _are_ formal, and the stylometry signal sees uniform sentence length and a narrow vocabulary band — both also true of formal human writing. The wide `uncertain` band partially mitigates this, and the appeals workflow exists specifically as a path for creators to contest these cases, but a production system would want signal 3 (e.g., a model fine-tuned on formal-register human writing) to address the gap directly.

**Short texts (< 50 words) are unreliably scored.** Stylometric variance requires multiple sentences to be meaningful, so submissions shorter than ~50 words fall back to the LLM signal alone. The system flags this internally by returning `stylo_score = 0.5` with a `note` field, but doesn't currently surface that uncertainty in the label.

## Spec Reflection

**Where the spec helped:** writing the three label variants verbatim in `planning.md` before I built the label function meant the implementation was just a mechanical translation — no design decisions left to make at code time, which made the function easy to test (I knew exactly which output to expect for each confidence band).

**Where implementation diverged from the spec:** my original stylometry plan used type-token ratio mapped against a fixed 0.45–0.55 "AI band." When I tested on short paragraphs, every input scored above 0.85 TTR (small samples naturally have high TTR), saturating the metric to 0.0 on every input. I switched to Carroll's Corrected TTR (`unique / sqrt(2 × total)`) calibrated to 3.8–5.5 to handle short inputs, and updated planning.md to reflect this change.

## AI Usage

1. **Stylometry function debugging.** I gave Claude the spec for Signal 2 plus my four test outputs showing that every input was returning `ttr_score: 0.0`. It diagnosed the saturation problem (raw TTR is sample-size-dependent), proposed Carroll's CTTR, and provided a first cut at the new calibration scale. I tested the suggested 5.0–7.5 scale on my four inputs and found it still saturated to 1.0 in the opposite direction, so I overrode that range to 3.8–5.5 based on my actual measured values.

2. **Planning loop architecture.** I gave Claude my detection-signals section plus the submission-flow ASCII diagram and asked it to generate the Flask app skeleton plus the signal-routing logic. The generated code matched the spec; the only changes I made were swapping the placeholder label-generation logic for the real three-variant `make_label()` function in Milestone 5 and tightening the error-handling on missing JSON fields.
