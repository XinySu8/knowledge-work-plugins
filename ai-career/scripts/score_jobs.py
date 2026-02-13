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


def _token_pattern(token: str) -> str:
    """
    Boundary-safe regex for tokens.
    - Hyphenated token like 'co-op': avoid matching inside 'co-opetition'
      by using alnum boundaries (not \\b).
    - Normal token: use \\b.
    """
    tok = (token or "").strip()
    if not tok:
        return r"$^"
    if "-" in tok:
        return r"(?<![A-Za-z0-9])" + re.escape(tok) + r"(?![A-Za-z0-9])"
    return r"\b" + re.escape(tok) + r"\b"


def hit_list(text: str, keywords):
    """
    Return de-duplicated matched keywords (preserve order).
    - Phrases (contain space): substring match
    - Tokens (incl hyphenated): boundary-safe regex match
    """
    t = (text or "").lower()
    hits = []

    for k in keywords or []:
        if not (isinstance(k, str) and k.strip()):
            continue
        kk = k.strip()
        k_low = kk.lower()

        if " " in kk:
            if k_low in t:
                hits.append(kk)
            continue

        pattern = _token_pattern(k_low)
        if re.search(pattern, t, flags=re.IGNORECASE):
            hits.append(kk)

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
    Boolean version of hit_list.
    - Phrases (space): substring
    - Tokens: boundary-safe regex
    """
    if not keywords:
        return True
    t = (text or "").lower()

    for k in keywords or []:
        if not (isinstance(k, str) and k.strip()):
            continue
        kk = k.strip()
        k_low = kk.lower()

        if " " in kk:
            if k_low in t:
                return True
            continue

        pattern = _token_pattern(k_low)
        if re.search(pattern, t, flags=re.IGNORECASE):
            return True

    return False


# ---- identity/eligibility helpers ----
def find_contexts(text: str, term: str, window: int = 80):
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

    preferred_locations_tier1 = profile.get("preferred_locations_tier1") or []
    preferred_locations_tier2 = profile.get("preferred_locations_tier2") or []

    matched_titles = hit_list(title, titles_target)
    matched_have = hit_list(haystack, skills_have)
    matched_want = hit_list(haystack, skills_want)
    matched_bonus = hit_list(haystack, bonus_keywords)

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

    clearance_ctx = find_contexts(text, "clearance", window=90) + find_contexts(text, "clearence", window=90)
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

    # ---- scoring ----
    TITLE_MAX = 45
    LOC_MAX = 25
    HAVE_MAX = 20
    WANT_BONUS_MAX = 10

    TITLE_CAP = 3
    HAVE_CAP = 10
    WANT_CAP = 10
    BONUS_CAP = 5

    title_score = round(TITLE_MAX * min(len(matched_titles), TITLE_CAP) / TITLE_CAP) if TITLE_CAP else 0

    loc_low = norm(loc)
    mode_weights = {"in person": 10, "hybrid": 7, "remote": 4}
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

    have_score = round(HAVE_MAX * min(len(matched_have), HAVE_CAP) / HAVE_CAP) if HAVE_CAP else 0

    want_part = round(7 * min(len(matched_want), WANT_CAP) / WANT_CAP) if WANT_CAP else 0
    bonus_part = round(3 * min(len(matched_bonus), BONUS_CAP) / BONUS_CAP) if BONUS_CAP else 0
    want_bonus_score = min(WANT_BONUS_MAX, want_part + bonus_part)

    score = title_score + loc_score + have_score + want_bonus_score

    # penalty (soft flags)
    penalty = 0
    if "citizen_mentioned" in soft_flags:
        penalty += 5
    if "clearance_mentioned" in soft_flags:
        penalty += 5
    score = max(0, score - penalty)

    # hard flags -> force score to 0 (you can also choose to drop them entirely)
    if hard_flags:
        score = 0

    breakdown = {
        "title": title_score,
        "location": loc_score,
        "have": have_score,
        "want+bonus": want_bonus_score,
        "penalty": penalty,
        "detected_mode": detected_mode or "none",
        "city_tier": city_tier,
    }

    return {
        "id": job.get("id"),
        "company": job.get("company"),
        "title": title,
        "location": loc,
        "url": job.get("url"),
        "source": job.get("source"),
        "score": int(score),
        "breakdown": breakdown,
        "hard_flags": hard_flags,
        "soft_flags": soft_flags,
        "matched_titles": matched_titles,
        "matched_skills_have": matched_have,
        "matched_skills_want": matched_want,
        "matched_bonus": matched_bonus,
    }


def write_md(scored, generated_at):
    lines = []
    lines.append("# Scored job feed (auto)\n")
    lines.append(f"Generated at (UTC): {generated_at}\n")
    lines.append(f"Total jobs scored: {len(scored)}\n\n")

    top = [x for x in scored if not x.get("hard_flags")]
    blocked = [x for x in scored if x.get("hard_flags")]

    lines.append("## Top internships\n")
    for x in top[:200]:
        lines.append(f"[{x['score']}/100] [{x.get('company')}] {x.get('title')} ({x.get('location')}) — {x.get('url')}\n")
        b = x.get("breakdown") or {}
        lines.append(
            f"breakdown: title {b.get('title',0)}, location {b.get('location',0)}, have {b.get('have',0)}, want+bonus {b.get('want+bonus',0)}, penalty {b.get('penalty',0)}\n"
        )
        if x.get("soft_flags"):
            lines.append("soft_flags: " + ", ".join(x["soft_flags"]) + "\n")
        if x.get("matched_titles"):
            lines.append("matched_titles: " + ", ".join(x["matched_titles"]) + "\n")
        if x.get("matched_skills_have"):
            lines.append("matched_skills_have: " + ", ".join(x["matched_skills_have"]) + "\n")
        if x.get("matched_skills_want"):
            lines.append("matched_skills_want: " + ", ".join(x["matched_skills_want"]) + "\n")
        if x.get("matched_bonus"):
            lines.append("matched_bonus: " + ", ".join(x["matched_bonus"]) + "\n")
        lines.append("\n")

    if blocked:
        lines.append("## Blocked (hard requirements)\n")
        for x in blocked[:200]:
            lines.append(f"[{x['score']}/100] [{x.get('company')}] {x.get('title')} ({x.get('location')}) — {x.get('url')}\n")
            lines.append("hard_flags: " + ", ".join(x["hard_flags"]) + "\n\n")

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.writelines(lines)


def main():
    now_utc = datetime.now(timezone.utc).isoformat()

    with open(IN_JSON, "r", encoding="utf-8") as f:
        payload = json.load(f)

    jobs = payload.get("jobs") or []
    # 关键：把“它读到的 jobs.json 时间戳”打印出来，排除读旧文件
    print(f"[score_jobs] jobs.json generated_at_utc = {payload.get('generated_at_utc')}")
    print(f"[score_jobs] jobs count = {len(jobs)}")

    with open(PROFILE_PATH, "r", encoding="utf-8") as f:
        profile = json.load(f)

    scored = []
    seen_ids = set()
    for j in jobs:
        jid = j.get("id")
        if jid and jid in seen_ids:
            continue
        if jid:
            seen_ids.add(jid)
        scored.append(score_job(j, profile))

    # sort by score desc
    scored.sort(key=lambda x: int(x.get("score") or 0), reverse=True)

    out_payload = {
        "generated_at_utc": now_utc,
        "source_generated_at_utc": payload.get("generated_at_utc"),
        "count": len(scored),
        "jobs": scored,
    }

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out_payload, f, ensure_ascii=False, indent=2)

    write_md(scored, now_utc)
    print(f"[score_jobs] wrote: {OUT_JSON} and {OUT_MD}")


if __name__ == "__main__":
    main()
