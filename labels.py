from config import HIGH_AI_THRESHOLD, HIGH_HUMAN_THRESHOLD


def make_label(confidence: float) -> tuple[str, str]:
    """
    Map a confidence score to one of three transparency labels.

    Returns (label_variant_name, label_text).
    """
    if confidence >= HIGH_AI_THRESHOLD:
        variant = "high_ai"
        text = (
            f"⚠️ Likely AI-generated. Our detection system gave this content a "
            f"{confidence:.0%} likelihood of being AI-generated, based on its semantic "
            f"style and structural patterns. The creator can appeal this classification "
            f"if they believe it is incorrect."
        )
    elif confidence < HIGH_HUMAN_THRESHOLD:
        variant = "high_human"
        text = (
            f"✅ Likely human-written. Our detection system found no strong indicators "
            f"of AI generation in this content ({confidence:.0%} AI-likelihood). "
            f"This is not a guarantee, but suggests the work is the creator's own."
        )
    else:
        variant = "uncertain"
        text = (
            f"🤔 Attribution unclear. Our detection signals are mixed on this content "
            f"(confidence {confidence:.0%}). It may have been written by a human, an "
            f"AI, or both. Treat the attribution as undetermined."
        )

    return variant, text