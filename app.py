import uuid
from flask import Flask, request, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from db import (
    init_db, write_entry, get_all_entries,
    get_entry_by_content_id, update_status,
)
from signals import llm_signal, stylo_signal, combine_signals
from labels import make_label

app = Flask(__name__)
init_db()

# Rate limiting — 10/min and 100/day per IP
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)


@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({
        "error": "Rate limit exceeded",
        "detail": str(e.description),
    }), 429


@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute;100 per day")
def submit():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    creator_id = data.get("creator_id")

    if not text:
        return jsonify({"error": "Missing 'text' field"}), 400
    if not creator_id:
        return jsonify({"error": "Missing 'creator_id' field"}), 400

    content_id = str(uuid.uuid4())

    # Run both signals
    llm_score = llm_signal(text)
    stylo_score, stylo_metrics = stylo_signal(text)
    confidence = combine_signals(llm_score, stylo_score)

    # Map to label variant
    variant, label_text = make_label(confidence)
    attribution = (
        "likely_ai" if variant == "high_ai"
        else "likely_human" if variant == "high_human"
        else "uncertain"
    )

    write_entry({
        "content_id": content_id,
        "creator_id": creator_id,
        "event": "submission",
        "text": text,
        "attribution": attribution,
        "confidence": confidence,
        "llm_score": llm_score,
        "stylo_score": stylo_score,
        "label": label_text,
        "status": "classified",
    })

    return jsonify({
        "content_id": content_id,
        "attribution": attribution,
        "confidence": round(confidence, 3),
        "label_variant": variant,
        "label": label_text,
        "signals": {
            "llm_score": round(llm_score, 3),
            "stylo_score": round(stylo_score, 3),
            "stylo_metrics": stylo_metrics,
        },
    })


@app.route("/appeal", methods=["POST"])
def appeal():
    data = request.get_json(silent=True) or {}
    content_id = (data.get("content_id") or "").strip()
    reasoning = (data.get("creator_reasoning") or "").strip()

    if not content_id:
        return jsonify({"error": "Missing 'content_id'"}), 400
    if not reasoning:
        return jsonify({"error": "Missing 'creator_reasoning'"}), 400

    original = get_entry_by_content_id(content_id)
    if not original:
        return jsonify({"error": f"No submission found for content_id {content_id}"}), 404

    # Flip the original submission's status
    update_status(content_id, "under_review")

    # Append a new audit entry for the appeal
    write_entry({
        "content_id": content_id,
        "creator_id": original.get("creator_id"),
        "event": "appeal",
        "text": None,
        "attribution": original.get("attribution"),
        "confidence": original.get("confidence"),
        "llm_score": original.get("llm_score"),
        "stylo_score": original.get("stylo_score"),
        "label": original.get("label"),
        "status": "under_review",
        "appeal_reasoning": reasoning,
    })

    return jsonify({
        "status": "under_review",
        "content_id": content_id,
        "message": "Appeal received. The original classification has been flagged for human review.",
    })


@app.route("/log", methods=["GET"])
def log():
    return jsonify({"entries": get_all_entries()})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)