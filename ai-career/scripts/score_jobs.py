import json
import os
import re
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(__file__))  # ai-career/
IN_JSON = os.path.join(ROOT, "data", "jobs.json")
PROFILE_PATH = os.path.join(ROOT, "config", "profile.json")

OUT_JSON = os.path.join(ROOT, "data", "scored_jobs.json")
OUT_MD = os.path.join(ROOT, "data", "scored_jobs.md")


def norm(s: str) -> str:
    return (s or "").lower()


def hit_list(text: str, keywords):
    """
    Return a de-duplicated list of matched keywords (preserve order).
    - Phrases (contain space or hyphen): substring match
    - Single tokens: word-boundary regex match (avoids intern->internal, ai->paid, ml->html, etc.)
    """
    t_raw = text or ""
    t = t_raw.lower()
    hits = []

    for k in keywords or []:
        if not (isinstance(k, str) and k.strip()):
            continue
        kk = k.strip()
        k_low = kk.lower()

        # phrase: allow substring
        if (" " in kk) or ("-" in kk):
            if k_low in t:
                hits.append(kk)
            continue

        # single token: word-boundary regex
        pattern = r"\b" + re.escape(k_low) + r"\b"
        if re.search(pattern, t, flags=re.IGNORECASE):
            hits.append(kk)

    # dedupe keep order
    seen = set()
    out = []
    for x in hits:
        lx = x.lower()
        if lx not in seen:
            seen.add(lx)
            out.append(x)
    return out


def contains_any(text: str, keywords) -> bool:
    """
    Boolean version of hit_list: True if any keyword matches.
    Uses the same matching rules (phrase substring, token word-boundary).
    """
    if not keywords:
        return True

    t_raw = text or ""
    t = t_raw.lower()

    for k in keywords or []:
        if not (isinstance(k, str) and k.strip()):
            continue
        kk = k.strip()
        k_low = kk.lower()

        # phrase: substring
        if (" " in kk) or ("-" in kk):
            if k_low in t:
                return True
            continue

        # token: word boundary
        pattern = r"\b" + re.escape(k_low) + r"\b"
        if re.search(pattern, t, flags=re.IGNORECASE):
            return True

    return False


# ---- identity/eligibility helpers ----
def find_contexts(text: str, term: str, window: int = 80):
    """Return a list of small context snippets around each occurrence of term."""
    if not text:
        return []
    t = text.lower()
    term = term.lower()
    contexts = []
    for m in re.finditer(re.escape(term), t):
        start = max(0, m.start() - window)
        end = min(len(t), m.end() + window)
        contexts.append(t[start:end])
    return contexts


def term_is_hard_required(contexts, required_patterns, negation_patterns):
    """
    True if any context indicates requirement and that specific occurrence is NOT negated.
    We check negations per-context so "clearance ... not required" won't trigger hard.
    """
    for c in contexts:
        if any(re.search(p, c) for p in negation_patterns):
            continue
        if any(re.search(p, c) for p in required_patterns):
            return True
    return False


def score_job(job, profile):
    title = job.get("title") or ""
    loc = job.get("location") or ""
    content = job.get("content_plain") or job.get("content_text") or ""
    haystack = f"{title}\n{loc}\n{content}"

    titles_target = profile.get("titles_target") or []
    skills_have = profile.get("skills_have") or []
    skills_want = profile.get("skills_want") or []
    bonus_keywords = profile.get("bonus_keywords") or []

    # NEW: tiered location preferences
    preferred_locations_tier1 = profile.get("preferred_locations_tier1") or []
    preferred_locations_tier2 = profile.get("preferred_locations_tier2") or []

    matched_titles = hit_list(title, titles_target)  # title only
    matched_have = hit_list(haystack, skills_have)
    matched_want = hit_list(haystack, skills_want)
    matched_bonus = hit_list(haystack, bonus_keywords)

    # ---- Eligibility rules with negation handling (clearance/citizen) ----
    text = (haystack or "").lower()

    neg_clearance = [
        r"\bno\s+clearance\s+required\b",
        r"\bclearance\s+not\s+required\b",
        r"\bnot\s+require\s+(a\s+)?clearance\b",
        r"\bdoes\s+not\s+require\s+(a\s+)?clearance\b",
        r"\bpreferred\b.*\bnot\s+required\b",
        r"\bnot\s+required\b.*\bpreferred\b",
    ]
    req_clearance = [
        r"\b(active\s+)?security\s+clearance\b",
        r"\bclearance\s+(is\s+)?required\b",
        r"\bmust\s+(have|hold|obtain)\s+(an?\s+)?clearance\b",
        r"\brequires?\s+(an?\s+)?clearance\b",
    ]

    neg_citizen = [
        r"\bnot\s+required\s+to\s+be\s+(a\s+)?(u\.s\.\s+)?citizen\b",
        r"\bcitizenship\s+not\s+required\b",
        r"\bpreferred\b.*\bcitizenship\b.*\bnot\s+required\b",
        r"\bcitizenship\b.*\bpreferred\b.*\bnot\s+required\b",
    ]
    req_citizen = [
        r"\b(u\.s\.\s+)?citizen(s)?\s+only\b",
        r"\bmust\s+be\s+(a\s+)?(u\.s\.\s+)?citizen\b",
        r"\bcitizenship\s+(is\s+)?required\b",
        r"\brequires?\s+(u\.s\.\s+)?citizenship\b",
    ]

    hard_flags = []
    soft_flags = []

    clearance_ctx = find_contexts(text, "clearance", window=90)
    clearance_ctx += find_contexts(text, "clearence", window=90)
    if clearance_ctx:
        if term_is_hard_required(clearance_ctx, req_clearance, neg_clearance):
            hard_flags.append("clearance_required")
        else:
            soft_flags.append("clearance_mentioned")

    citizen_ctx = find_contexts(text, "citizen", window=90)
    if citizen_ctx:
        if term_is_hard_required(citizen_ctx, req_citizen, neg_citizen):
            hard_flags.append("citizen_required")
        else:
            soft_flags.append("citizen_mentioned")

    # ---- 100-point scoring (Title + Location heavier) ----
    TITLE_MAX = 45
    LOC_MAX = 25
    HAVE_MAX = 20
    WANT_BONUS_MAX = 10

    TITLE_CAP = 3
    HAVE_CAP = 10
    WANT_CAP = 10
    BONUS_CAP = 5

    title_score = round(TITLE_MAX * min(len(matched_titles), TITLE_CAP) / TITLE_CAP) if TITLE_CAP > 0 else 0

    # Location score: mode (0..10) + city tier (0..15)
    loc_low = norm(loc)

    mode_weights = {
        "in person": 10,
        "hybrid": 7,
        "remote": 4
    }

    detected_mode = None
    if "in person" in loc_low or "onsite" in loc_low or "on-site" in loc_low:
        detected_mode = "in person"
    elif "hybrid" in loc_low:
        detected_mode = "hybrid"
    elif "remote" in loc_low:
        detected_mode = "remote"

    mode_score = mode_weights.get(detected_mode, 0)

    city_score = 0
    city_tier = "none"
    if preferred_locations_tier1 and contains_any(loc, preferred_locations_tier1):
        city_score = 15
        city_tier = "tier1"
    elif preferred_locations_tier2 and contains_any(loc, preferred_locations_tier2):
        city_score = 8
        city_tier = "tier2"

    loc_score = min(LOC_MAX, mode_score + city_score)

    have_score = round(HAVE_MAX * min(len(matched_have), HAVE_CAP) / HAVE_CAP) if HAVE_CAP > 0 else 0

    want_part = round(7 * min(len(matched_want), WANT_CAP) / WANT_CAP) if WANT_CAP > 0 else 0
    bonus_part = round(3 * min(len(matched_bonus), BONUS_CAP) / BONUS_CAP) if BONUS_CAP > 0 else 0
    want_bonus_score = min(WANT_BONUS_MAX, want_part + bonus_part)

    pre_penalty_score = title_score + lo_
