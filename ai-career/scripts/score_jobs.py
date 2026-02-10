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


def score_job(job, profile):
    title = job.get("title") or ""
    loc = job.get("location") or ""
    content = job.get("content_plain") or job.get("content_text") or ""
    haystack = f"{title}\n{loc}\n{content}"

    titles_target = profile.get("titles_target") or []
    skills_have = profile.get("skills_have") or []
    skills_want = profile.get("skills_want") or []
    bonus_keywords = profile.get("bonus_keywords") or []

    # NEW: location preferences from profile.json
    preferred_work_mode = [x.lower() for x in (profile.get("preferred_work_mode") or [])]
    preferred_locations_any = profile.get("preferred_locations_any") or []

    matched_titles = hit_list(title, titles_target)  # title only
    matched_have = hit_list(haystack, skills_have)
    matched_want = hit_list(haystack, skills_want)
    matched_bonus = hit_list(haystack, bonus_keywords)

    # ----- 100-point scoring (Title+Location heavier) -----
    TITLE_MAX = 45
    LOC_MAX = 25
    HAVE_MAX = 20
    WANT_BONUS_MAX = 10

    # caps
    TITLE_CAP = 3
    HAVE_CAP = 10
    WANT_CAP = 10
    BONUS_CAP = 5

    title_score = round(TITLE_MAX * min(len(matched_titles), TITLE_CAP) / TITLE_CAP)

    # Location score = mode (0-10) + city (0-15)
    loc_low = (loc or "").lower()

    # mode score (max 10): based on preference order
    mode_score = 0
    # you can tune these numbers
    mode_weights = {
        "in person": 10,
        "hybrid": 7,
        "remote": 4
    }
    # detect mode from location text (best-effort)
    detected_mode = None
    if "in person" in loc_low or "onsite" in loc_low or "on-site" in loc_low:
        detected_mode = "in person"
    elif "hybrid" in loc_low:
        detected_mode = "hybrid"
    elif "remote" in loc_low:
        detected_mode = "remote"

    if detected_mode in mode_weights:
        mode_score = mode_weights[detected_mode]

    # city score (max 15): keyword match against preferred cities/regions
    city_score = 0
    if preferred_locations_any:
        if contains_any(loc, preferred_locations_any):
            city_score = 15

    loc_score = min(LOC_MAX, mode_score + city_score)

    have_score = round(HAVE_MAX * min(len(matched_have), HAVE_CAP) / HAVE_CAP)

    # want+bonus share 10 points
    want_part = round(7 * min(len(matched_want), WANT_CAP) / WANT_CAP)
    bonus_part = round(3 * min(len(matched_bonus), BONUS_CAP) / BONUS_CAP)
    want_bonus_score = min(WANT_BONUS_MAX, want_part + bonus_part)

    score = title_score + loc_score + have_score + want_bonus_score  # <= 100

    analysis = {
        "score": score,
        "breakdown": {
            "title_score": title_score,
            "location_score": loc_score,
            "skills_have_score": have_score,
            "want_bonus_score": want_bonus_score
        },
        "matched_titles": matched_titles,
        "matched_skills_have": matched_have,
        "matched_skills_want": matched_want,
        "matched_bonus": matched_bonus
    }
    return score, analysis


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

        lines.append(f"- [{score}] [{company}] {title} ({loc}) — {url}\n")
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
