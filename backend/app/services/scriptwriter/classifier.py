"""Application-controlled deterministic risk classifier for script claims (Phase 5 Checkpoint 4).

Classifies factual assertions into 'high', 'normal', and 'creative_flexibility'
per approved risk policies. High risk claims (central thesis, statistics, dates,
scientific mechanisms, named entities) are strictly guarded, and mixed
factual/stylistic assertions never escape scrutiny.
"""

import re
from typing import Optional, Set

from backend.app.domain.fact_check import ClaimRiskLevel


_HIGH_RISK_SCIENTIFIC_TERMS: Set[str] = {
    "temperature", "celsius", "fahrenheit", "kelvin",
    "gravity", "gravitational", "pressure", "pascal", "pascals",
    "atmosphere", "atmospheric", "density", "velocity", "acceleration",
    "friction", "quantum", "electron", "proton", "neutron", "atom", "atomic",
    "molecule", "molecular", "dna", "rna", "gene", "genetic", "cellular",
    "bacteria", "virus", "photosynthesis", "mitochondria", "radiation",
    "magnetic", "electromagnetic", "voltage", "current", "mass", "energy",
    "black hole", "supernova", "light year", "light-year", "speed of light",
    "plate tectonics", "continental drift", "subduction", "fossil",
    "mechanism", "mechanisms",
}

_HIGH_RISK_CAUSAL_TERMS: Set[str] = {
    "causes", "caused", "causing", "leads to", "led to", "leading to",
    "results in", "resulted in", "resulting in", "triggers", "triggered",
    "produces", "produced", "generates", "generated",
}

_CREATIVE_METAPHOR_PATTERNS = [
    r"\bimagine\b",
    r"\bpicture this\b",
    r"\bthink of\b",
    r"\blike an?\b",
    r"\bas if\b",
    r"\bmetaphor\b",
    r"\bdance\b",
    r"\bsymphony\b",
    r"\bcosmic tapestry\b",
    r"\binvisible glue\b",
    r"\bmagic\b",
    r"\bjourney through\b",
]


def classify_claim_risk(
    claim_text: str,
    section_type: Optional[str] = None,
    is_central_thesis: bool = False,
) -> ClaimRiskLevel:
    """Classify a claim's risk level deterministically.

    Categories:
    - 'high': Central thesis assertions, statistics, dates, percentages,
              scientific/causal mechanisms, named entities.
    - 'creative_flexibility': Purely stylistic metaphors, rhetorical framing,
              or narrative color without factual assertions.
    - 'normal': Standard explanatory or narrative factual assertions.

    Mixed sentences: If a sentence contains both stylistic framing and a
    concrete factual/scientific assertion, high risk takes strict precedence.
    """
    clean_text = (claim_text or "").strip()
    if not clean_text:
        return "normal"

    lower_text = clean_text.lower()

    # 1. Central thesis assertions or core hook/takeaway thesis
    if is_central_thesis:
        return "high"

    # 2. Statistics, percentages, measurements, numerical quantities
    # Matches digits, percentages (e.g. 20%, 500, 3.14, 1,000)
    if re.search(r"\b\d+(?:[.,]\d+)?%?\b", clean_text):
        return "high"

    # 3. Dates and years (e.g. 1700-2099, month names)
    if re.search(r"\b(1[6789]\d\d|20\d\d)\b", clean_text):
        return "high"
    if re.search(
        r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\b",
        lower_text,
    ):
        return "high"

    # 4. Causal scientific mechanisms and technical phenomena
    for causal in _HIGH_RISK_CAUSAL_TERMS:
        if re.search(rf"\b{re.escape(causal)}\b", lower_text):
            return "high"

    for term in _HIGH_RISK_SCIENTIFIC_TERMS:
        if re.search(rf"\b{re.escape(term)}\b", lower_text):
            return "high"

    # 5. Named entities (capitalized multi-word proper nouns, e.g. Albert Einstein, Mount Everest)
    # Exclude title at start of sentence if only one word
    tokens = clean_text.split()
    for i in range(len(tokens) - 1):
        w1 = re.sub(r"[^\w]", "", tokens[i])
        w2 = re.sub(r"[^\w]", "", tokens[i + 1])
        if w1 and w2 and w1[0].isupper() and w2[0].isupper() and not w1.isupper() and not w2.isupper():
            # Potential multi-word proper noun / named entity
            return "high"

    # 6. Creative flexibility: metaphors, analogies, rhetorical questions
    # Mixed sentence check: only creative_flexibility if no high-risk factual elements were triggered above
    is_metaphor = any(re.search(pat, lower_text) for pat in _CREATIVE_METAPHOR_PATTERNS)
    is_rhetorical_question = clean_text.endswith("?") and any(
        lower_text.startswith(q) for q in ("have you ever", "what if", "can you imagine", "why does")
    )

    if is_metaphor or is_rhetorical_question:
        return "creative_flexibility"

    return "normal"
