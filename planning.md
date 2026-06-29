# Provenance Guard — planning.md

## Architecture Narrative

A piece of text arrives at the `POST /submit` endpoint with a `text` field and a
`creator_id`. The Flask app first checks the rate limiter (10/minute, 100/day per
IP). If allowed, it generates a unique `content_id` and passes the text to the
detection pipeline.

The pipeline runs two independent signals. Signal 1 sends the text to Groq's
LLM with a prompt asking for a 0.0–1.0 score of how AI-generated it reads.
Signal 2 computes stylometric metrics (sentence-length variance, type-token
ratio, punctuation density) in pure Python and combines them into a 0.0–1.0
structural score. The two scores are blended (weighted average — LLM 60%,
stylometrics 40%) into a single `confidence` value where 1.0 = very likely AI
and 0.0 = very likely human.

The confidence value maps to one of three transparency labels: high-AI
(confidence ≥ 0.7), uncertain (0.4–0.7), or high-human (< 0.4). The label text,
confidence, both signal scores, and content_id are written as one structured
entry to the SQLite audit log, then returned in the JSON response.

The appeal flow is a separate endpoint, `POST /appeal`. It takes the
`content_id` and the creator's reasoning, flips that content's status to
`under_review` in the audit log, appends a new log entry capturing the appeal
text and timestamp, and returns confirmation. A `GET /log` endpoint exposes the
audit log entries as JSON for grading visibility.

## Detection Signals

**Signal 1 — LLM semantic assessment (Groq, llama-3.3-70b-versatile)**

- Measures: holistic semantic / stylistic coherence — does the text _read_ as AI?
- Why it differs human vs AI: AI text tends toward uniform register, hedged
  formality, and an even rhetorical rhythm; humans break voice, drop register,
  and write idiosyncratically.
- Output: a float 0.0–1.0 where 1.0 = "reads as AI."
- Blind spot: heavily edited AI output that's been humanized passes; very
  formal human writing (academic, corporate) can falsely score high.

**Signal 2 — Stylometric heuristics (pure Python)**

- Measures: structural statistics — sentence-length variance, type-token ratio
  (unique words ÷ total words), and punctuation density.
- Why it differs human vs AI: AI prose has lower sentence-length variance and a
  narrower vocabulary band; humans vary sentence length wildly and reach for
  unusual words.
- Output: a float 0.0–1.0 derived by normalizing each metric and averaging.
- Blind spot: short texts (<50 words) have too little signal to measure
  variance reliably; poetry or very casual writing can read as "low-variance"
  even when human.

The two signals are genuinely independent: one judges meaning, the other counts
structure. Their disagreement is itself informative.

## False Positive Walkthrough

Scenario: a non-native English speaker submits a polished blog post they wrote
themselves. Signal 1 (LLM) flags it as AI-sounding because of the formal
register. Signal 2 (stylometry) returns mid-range because the sentences are
similar in length. Combined confidence lands around 0.65 → "uncertain" label,
NOT "high-AI." The label text shown to the reader explicitly says the system
isn't sure, and links the creator to the appeal endpoint. The creator submits
an appeal with their reasoning; status flips to `under_review`; a new audit-log
entry captures the appeal. A human reviewer queue sees both the original
decision and the appeal side by side.

The system never returns a binary AI/not-AI verdict — that's the design choice
that protects against false positives.

## API Surface

| Endpoint  | Method | Input                                           | Output                                                                                         |
| --------- | ------ | ----------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `/submit` | POST   | `{"text": str, "creator_id": str}`              | `{"content_id": str, "attribution": str, "confidence": float, "label": str, "signals": {...}}` |
| `/appeal` | POST   | `{"content_id": str, "creator_reasoning": str}` | `{"status": "under_review", "content_id": str, "message": str}`                                |
| `/log`    | GET    | (none)                                          | `{"entries": [ ...audit log entries... ]}`                                                     |

## Architecture

### Submission flow

```
POST /submit
   │  {text, creator_id}
   ▼
Rate limiter (10/min, 100/day per IP)
   │
   ▼
generate content_id (uuid)
   │
   ▼
Signal 1: Groq LLM ──► llm_score (0.0–1.0)
   │
   ▼
Signal 2: Stylometry ──► stylo_score (0.0–1.0)
   │
   ▼
Combine: confidence = 0.6 * llm_score + 0.4 * stylo_score
   │
   ▼
Map to label:
   ≥ 0.7   → "high-AI"
   0.4–0.7 → "uncertain"
   < 0.4   → "high-human"
   │
   ▼
Write entry to audit log (SQLite)
   │
   ▼
Return JSON {content_id, attribution, confidence, label, signals}
```

### Appeal flow

```
POST /appeal
   │  {content_id, creator_reasoning}
   ▼
Look up content_id in audit log
   │  (if not found → 404)
   ▼
Update original entry: status = "under_review"
   │
   ▼
Append new audit-log entry:
   {timestamp, content_id, event: "appeal",
    creator_reasoning, status: "under_review"}
   │
   ▼
Return JSON {status: "under_review", content_id, message}
```

## Spec — Required Questions

### 1. Detection signals (recap with implementation detail)

Two signals, both producing a float 0.0–1.0 where 1.0 = strongest AI signal.

**Signal 1 — LLM semantic assessment.** Call Groq's `llama-3.3-70b-versatile`
with `temperature=0` and a prompt that asks: given this text, return a single
number between 0.0 and 1.0 where 1.0 means clearly AI-generated and 0.0 means
clearly human-written. Parse the response by extracting the first number found.
Output: `llm_score` (float).

**Signal 2 — Stylometric heuristics.** Compute three metrics on the text:

- _Sentence-length variance_ — split on `.!?`, compute the standard deviation
  of word counts per sentence. Low variance is AI-like.
- _Type-token ratio_ — unique words ÷ total words. AI sits in a narrow band
  (~0.45–0.55); humans range wider.
- _Punctuation density_ — non-alphanumeric punctuation chars ÷ total chars.
  Humans tend to use more commas, dashes, ellipses.

Each metric is min-max normalized to 0.0–1.0 where 1.0 = AI-like, then averaged.
Output: `stylo_score` (float).

**Combining the two:** weighted average with the LLM weighted higher
(it's the stronger semantic signal):

```
confidence = 0.6 * llm_score + 0.4 * stylo_score
```

### 2. Uncertainty representation

A confidence score is a **probability the text is AI-generated**, on a continuous
0.0–1.0 scale. It is not a binary verdict and the label text never claims certainty.

Three calibrated bands map to three labels:

- `confidence >= 0.7` → high-AI label
- `0.4 <= confidence < 0.7` → uncertain label
- `confidence < 0.4` → high-human label

The 0.4 lower edge (rather than 0.5) deliberately widens the "uncertain" band to
protect against false positives — when in doubt, the system says it's unsure
rather than accusing a human writer.

### 3. Transparency label — three variants (verbatim)

**High-confidence AI** (confidence ≥ 0.7):

> "⚠️ Likely AI-generated. Our detection system gave this content a {confidence:.0%} likelihood of being AI-generated, based on its semantic style and structural patterns. The creator can appeal this classification if they believe it is incorrect."

**Uncertain** (0.4 ≤ confidence < 0.7):

> "🤔 Attribution unclear. Our detection signals are mixed on this content (confidence {confidence:.0%}). It may have been written by a human, an AI, or both. Treat the attribution as undetermined."

**High-confidence human** (confidence < 0.4):

> "✅ Likely human-written. Our detection system found no strong indicators of AI generation in this content ({confidence:.0%} AI-likelihood). This is not a guarantee, but suggests the work is the creator's own."

### 4. Appeals workflow

**Who can appeal:** the original creator (matched by `content_id` from their
`/submit` response). Authentication is out of scope for this project — in
production this would require auth tying the creator to the submission.

**What they provide:** the `content_id` of the disputed classification, plus
free-text `creator_reasoning` explaining why they believe it was misclassified.

**What the system does on appeal:**

1. Look up `content_id` in the SQLite audit log; if not found, return 404.
2. Update the original entry: set `status` from `classified` to `under_review`.
3. Append a new audit-log entry with `event: "appeal"`, the creator's
   reasoning, timestamp, and `status: "under_review"`.
4. Return `{status: "under_review", content_id, message}` confirmation.

Automated re-classification is not implemented — appeals are queued for human
review.

**What a reviewer would see:** opening the audit log filtered to
`status = "under_review"` shows: the original submission's text, both signal
scores, the assigned label, and the creator's reasoning, side by side.

### 5. Anticipated edge cases

**Edge case 1 — Non-native English speakers writing formally.** A polished
essay from a non-native speaker can score high on Signal 1 because formal
hedged register reads as AI-like. Signal 2 partially compensates because
human writing still has higher sentence-length variance, but the combined
score may land in "uncertain." Mitigation: the uncertain band is intentionally
wide, and the appeal path lets creators contest false positives.

**Edge case 2 — Short texts (under 50 words).** Stylometric variance is
statistically unreliable on short inputs — there aren't enough sentences to
measure variance meaningfully. Signal 2 will be noisy and Signal 1 carries
most of the weight. The system still returns a result but should ideally
flag short-text submissions as low-evidence; for this project the wide
uncertain band partially absorbs this.

## AI Tool Plan

**Milestone 3 — submission endpoint + first signal:**
I'll give Claude the Detection Signals section (Signal 1 details) and the
submission-flow diagram, and ask it to generate (1) the Flask app skeleton
with `POST /submit` and `GET /log` routes, and (2) the LLM signal function.
Before wiring it in I'll test the signal function directly with two inputs
(a clearly AI paragraph and a clearly human one) to confirm the score is
roughly directionally correct, and confirm the Flask route returns the
expected JSON shape.

**Milestone 4 — second signal + confidence scoring:**
I'll give Claude the Detection Signals (Signal 2 details) plus the Uncertainty
Representation section. I'll ask it to generate (1) the stylometry function
returning a single 0.0–1.0 score from the three metrics, and (2) the
combining function using the 0.6/0.4 weighting. I'll verify by running the
four test inputs the project provides (clearly AI, clearly human, formal
human, lightly edited AI) and confirming the scores fall into the expected
bands. If stylometry diverges from the LLM on the formal-human case, that's
expected — I'll note it in the README as evidence the signals are independent.

**Milestone 5 — production layer:**
I'll give Claude the Transparency Label variants and the Appeals Workflow
section. I'll ask it to generate (1) a `make_label(confidence)` function that
returns the correct verbatim variant for each band, and (2) the `POST /appeal`
endpoint with audit-log update logic. I'll verify by submitting three inputs
that produce all three bands and reading the labels, then appealing one and
checking the log shows `status: "under_review"` with the appeal entry.
