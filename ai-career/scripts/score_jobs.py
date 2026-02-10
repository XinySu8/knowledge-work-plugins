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

    preferred_locations_any = profile.get("preferred_locations_any") or []

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

    # clearance (include common misspelling clearence)
    clearance_ctx = find_contexts(text, "clearance", window=90)
    clearance_ctx += find_contexts(text, "clearence", window=90)
    if clearance_ctx:
        if term_is_hard_required(clearance_ctx, req_clearance, neg_clearance):
            hard_flags.append("clearance_required")
        else:
            soft_flags.append("clearance_mentioned")

    # citizen (also consider 'citizenship' occurrences implicitly via patterns; term search is for context windows)
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

    # Location score: mode (0..10) + preferred city (0..15)
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
    if preferred_locations_any and contains_any(loc, preferred_locations_any):
        city_score = 15

    loc_score = min(LOC_MAX, mode_score + city_score)

    have_score = round(HAVE_MAX * min(len(matched_have), HAVE_CAP) / HAVE_CAP) if HAVE_CAP > 0 else 0

    want_part = round(7 * min(len(matched_want), WANT_CAP) / WANT_CAP) if WANT_CAP > 0 else 0
    bonus_part = round(3 * min(len(matched_bonus), BONUS_CAP) / BONUS_CAP) if BONUS_CAP > 0 else 0
    want_bonus_score = min(WANT_BONUS_MAX, want_part + bonus_part)

    pre_penalty_score = title_score + loc_score + have_score + want_bonus_score  # <= 100

    # Eligibility penalty strategy:
    # - hard flags => 0 out (but we used negation handling to reduce false positives)
    # - soft flags => small penalty (still visible so you don't miss it)
    not_eligible = bool(hard_flags)
    if hard_flags:
        eligibility_penalty = 999
    else:
        eligibility_penalty = 5 * len(soft_flags)

    final_score = max(0, pre_penalty_score - eligibility_penalty)

    analysis = {
        "score": final_score,
        "breakdown": {
            "title_score": title_score,
            "location_score": loc_score,
            "skills_have_score": have_score,
            "want_bonus_score": want_bonus_score,
            "eligibility_penalty": eligibility_penalty,
            "pre_penalty_score": pre_penalty_score,
            "final_score": final_score
        },
        "matched_titles": matched_titles,
        "matched_skills_have": matched_have,
        "matched_skills_want": matched_want,
        "matched_bonus": matched_bonus,
        "eligibility": {
            "not_eligible": not_eligible,
            "hard_flags": hard_flags,
            "soft_flags": soft_flags,
            "penalty": eligibility_penalty
        }
    }

    return final_score, analysis


def main():
    with open(IN_JSON, "r", encoding="utf-8") as f:
        payload = json.load(f)
    jobs = payload.get("jobs") or []

    with open(PROFILE_PATH, "r", encoding="utf-8") as f:
        profile = json.load(f)

    enriched = []
    for j in jobs:
        score, analysis = score_job(j, profile)
        jj = dict(j)
        jj["analysis"] = analysis
        enriched.append(jj)

    enriched.sort(key=lambda x: x.get("analysis", {}).get("score", 0), reverse=True)

    out_payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_generated_at_utc": payload.get("generated_at_utc"),
        "count": len(enriched),
        "jobs": enriched
    }

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out_payload, f, ensure_ascii=False, indent=2)

    # Markdown report
    lines = []
    lines.append("# Scored job feed (auto)\n")
    lines.append(f"Generated at (UTC): {out_payload['generated_at_utc']}\n")
    lines.append(f"Total jobs scored: {out_payload['count']}\n\n")
    lines.append("## Top internships\n")

    for j in enriched[:100]:
        title = j.get("title") or "Untitled"
        company = j.get("company") or "unknown"
        loc = j.get("location") or "Unknown location"
        url = j.get("url") or ""
        a = j.get("analysis") or {}
        score = a.get("score", 0)
        b = a.get("breakdown") or {}
        elig = a.get("eligibility") or {}

        lines.append(f"- [{score}/100] [{company}] {title} ({loc}) — {url}\n")
        lines.append(
            f"  - breakdown: title {b.get('title_score',0)}, "
            f"location {b.get('location_score',0)}, "
            f"have {b.get('skills_have_score',0)}, "
            f"want+bonus {b.get('want_bonus_score',0)}, "
            f"penalty {b.get('eligibility_penalty',0)}\n"
        )

        if elig.get("not_eligible"):
            lines.append("  - ELIGIBILITY: NOT ELIGIBLE (hard requirement detected)\n")
        if elig.get("hard_flags"):
            lines.append(f"  - hard_flags: {', '.join(elig['hard_flags'])}\n")
        if elig.get("soft_flags"):
            lines.append(f"  - soft_flags: {', '.join(elig['soft_flags'])}\n")

        if a.get("matched_titles"):
            lines.append(f"  - matched_titles: {', '.join(a['matched_titles'])}\n")
        if a.get("matched_skills_have"):
            lines.append(f"  - matched_skills_have: {', '.join(a['matched_skills_have'])}\n")
        if a.get("matched_skills_want"):
            lines.append(f"  - matched_skills_want: {', '.join(a['matched_skills_want'])}\n")
        if a.get("matched_bonus"):
            lines.append(f"  - matched_bonus: {', '.join(a['matched_bonus'])}\n")

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.writelines(lines)


if __name__ == "__main__":
    main()
