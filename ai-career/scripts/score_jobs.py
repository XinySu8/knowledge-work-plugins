import json
import os
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(__file__))  # ai-career/
IN_JSON = os.path.join(ROOT, "data", "jobs.json")
PROFILE_PATH = os.path.join(ROOT, "config", "profile.json")

OUT_JSON = os.path.join(ROOT, "data", "scored_jobs.json")
OUT_MD = os.path.join(ROOT, "data", "scored_jobs.md")

def norm(s: str) -> str:
    return (s or "").lower()

def hit_list(text: str, keywords):
    t = norm(text)
    hits = []
    for k in keywords or []:
        if isinstance(k, str) and k.strip() and norm(k) in t:
            hits.append(k)
    # 去重保序
    seen = set()
    out = []
    for x in hits:
        lx = norm(x)
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

    matched_titles = hit_list(title, titles_target)         # title 更重要
    matched_have = hit_list(haystack, skills_have)
    matched_want = hit_list(haystack, skills_want)
    matched_bonus = hit_list(haystack, bonus_keywords)

    score = 0
    score += 8 * len(matched_titles)
    score += 3 * len(matched_have)
    score += 1 * len(matched_want)
    score += 2 * len(matched_bonus)

    # remote/hybrid 小加分
    hlow = norm(haystack)
    if "remote" in hlow or "hybrid" in hlow:
        score += 2

    analysis = {
        "score": score,
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
