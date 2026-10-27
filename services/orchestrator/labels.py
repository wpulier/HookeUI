"""Label keys: one key per kind of thing, so "Stomata (likely)", "central stoma" and
"oval structure (stoma)" share a description (orchestrator/enrich.py).

A small synonym table merges names the vision model uses for the same thing ("phloem fibre
cap" and "sclerenchyma cap"; "erythrocytes" and "red blood cell"). Keep it small and only
for true synonyms: distinct things must keep distinct keys (tests/test_enrich.py).

subject_key is the same idea for the specimen description: "blood smear, blurry" and
"blood smear, ~400x" are one subject, so descriptions are not redone for them.
"""
from __future__ import annotations

import re

IRREGULAR = {"stomata": "stoma", "nuclei": "nucleus", "bacteria": "bacterium", "hyphae": "hypha", "mitochondria": "mitochondrion",
             "cilia": "cilium", "flagella": "flagellum", "algae": "alga", "fungi": "fungus", "protozoa": "protozoan",
             "vacuoles": "vacuole", "plastids": "plastid", "amoebae": "amoeba", "larvae": "larva", "setae": "seta"}
HEDGES = re.compile(r"\b(likely|probably|probable|possible|possibly|maybe|perhaps|presumed|apparent|"
                    r"a|an|the|some|central|small|large|tiny|big|dark|bright|left|right|top|bottom|middle|upper|lower|"
                    r"another|other|second|first)\b")
# Phrases whose words would otherwise be dropped as hedges ("central" is usually a position,
# but a central vacuole is its own thing).
PHRASES = [(re.compile(r"\bcentral vacuoles?\b"), "centralvacuole")]
# Names where the "hedge" word is part of what the thing is: an upper and a lower epidermis
# are different tissues (only one has stomata), a small and a large intestine different organs.
NAMED = re.compile(r"\b(small|large|upper|lower|first|second|central)[ -]"
                   r"(intestines?|epidermis|epidermises|epidermides|instars?|molars?|incisors?|premolars?|nervous system)\b")
# A parenthetical that only says where or what shape ("xylem (dark region)") is not a name.
GENERIC = {"region", "layer", "area", "part", "zone", "structure", "spot", "patch", "view", "side", "edge", "portion",
           "shape", "thing", "object", "feature", "blob", "mass", "section", "band", "line"}
SPELLING = {"fibre": "fiber", "fibres": "fibers", "leucocyte": "leukocyte", "leucocytes": "leukocytes"}
SYNONYMS = {
    "phloem fiber cap": "fiber cap", "sclerenchyma fiber cap": "fiber cap", "sclerenchyma cap": "fiber cap",
    "bundle fiber cap": "fiber cap",
    "phloem fiber": "sclerenchyma fiber",
    "cell nucleus": "nucleus", "plant cell nucleus": "nucleus",
    "erythrocyte": "red blood cell", "rbc": "red blood cell",
    "leukocyte": "white blood cell", "wbc": "white blood cell",
    "bristle": "seta",
    "bristle socket": "socket", "setal socket": "socket", "seta socket": "socket",
    "centralvacuole": "central vacuole", "centralvacuole region": "central vacuole",
}


def singular(word: str) -> str:
    if word in IRREGULAR:
        return IRREGULAR[word]
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("ches", "shes", "xes", "sses")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def label_key(label: str) -> str:
    """'Stomata (likely)' -> 'stoma'; 'oval structure (stoma)' -> 'stoma'; 'central stoma' -> 'stoma';
    'Phloem fibres' -> 'sclerenchyma fiber'; 'cell nucleus' -> 'nucleus'."""
    text = (label or "").lower().strip()
    inner = re.search(r"\(([^)]+)\)", text)
    if inner and not HEDGES.fullmatch(inner.group(1).strip()) and len(inner.group(1).split()) <= 3:
        last = singular(re.sub(r"[^a-z0-9\- ]", " ", inner.group(1)).split()[-1]) if inner.group(1).split() else ""
        if last not in GENERIC:
            text = inner.group(1)
    text = re.sub(r"\([^)]*\)", " ", text)
    for pattern, token in PHRASES:
        text = pattern.sub(token, text)
    text = NAMED.sub(lambda m: m.group(1) + "_" + m.group(2).replace(" ", "_"), text)
    text = re.sub(r"[^a-z0-9\-_ ]", " ", HEDGES.sub(" ", text))
    words = [SPELLING.get(w, w) for w in text.split()]
    if not words:
        return ""
    words[-1] = singular(words[-1])
    key = " ".join(words).replace("_", " ")
    return SYNONYMS.get(key, key)[:40]


MAGNIFICATION = re.compile(r"~?\s*\d+(?:\.\d+)?\s*[x×](?:\s*(?:-|–|to)\s*~?\s*\d+(?:\.\d+)?\s*[x×])?(?![a-z])")
QUALITY = re.compile(r"\b(?:slightly |very |somewhat )?(?:blurry|blurred|out of focus|out-of-focus|in focus|soft|unstained|"
                     r"stained|high magnification|low magnification|high power|low power|close-up|close up|closeup|"
                     r"magnification|magnified|zoomed in|zoomed out|focused)\b")
DANGLING = re.compile(r"^(?:at|under|with|in|of|and|a|an|the)\s+|\s+(?:at|under|with|in|of|and)$")


def subject_key(specimen: str | None) -> str:
    """The specimen description without magnification, focus or staining words and
    punctuation: 'Blood smear, ~400x' and 'blood smear (blurry)' -> 'blood smear'."""
    text = MAGNIFICATION.sub(" ", (specimen or "").lower())
    text = QUALITY.sub(" ", text)
    text = " ".join(re.sub(r"[^a-z0-9 ]", " ", text).split())
    previous = None
    while previous != text:
        previous, text = text, DANGLING.sub("", text).strip()
    return text
