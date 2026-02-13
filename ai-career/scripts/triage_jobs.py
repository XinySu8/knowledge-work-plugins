import json
import os
import re
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(__file__))  # ai-career/
IN_TODAY_JSON = os.path.join(ROOT, "data", "jobs_today.json")
PROFILE_PATH = os.path.join(ROOT, "config", "profile.json")

OUT_DIR = os.path.join(ROOT, "data", "triage")
ARCHIVE_DIR = os.path.join(OUT_DIR, "by_day")

# ---------------- helpers ----------------

def norm(s: str) -> str:
    return (s or "").lower()

def utc_date_str(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")

def safe_filename(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s or "")
    return s[:120] if s else "item"

def _token_pattern(token: str) -> str:
    """
    Boundary-safe token regex:
    - For hyphenated tokens like 'co-op', avoid matching inside longer words like 'co-opetition'.
    - For normal tokens, use \\b.
    """
    tok = (token or "").strip()
    if not tok:
        return r"$^"
    if "-" in tok:
        return r"(?<![A-Za-z0-9])" + re.escape(tok) + r"(?![A-Za-z0-9])"
    return r"\b" + re.escape(tok) + r"\b"

def hit_list(text: str, keywords):
    """
    Return matched keywords (dedup, keep order).
    - Phrase (has space): substring match
    - Token (incl hyphenated): boundary-safe regex
    """
    t = (text or "").lower()
    hits = []
    for k in (keywords or []):
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

def find_contexts(text: str, term: str, window: int = 90):
    if not text:
        return []
    t = text.lower()
    term = term.lower()
    ctx = []
    for m in re.finditer(re.escape(term), t):
        start = max(0, m.start() - window)
        end = min(len(t), m.end() + window)
        ctx.append(t[start:end])
    return ctx

def term_is_hard_required(contexts, required_patterns, negation_patterns):
    for c in contexts:
        if any(re.search(p, c) for p in negation_patterns):
            continue
        if any(re.search(p, c) for p in required_patterns):
            return True
    return False

def extract_sections(text: str):
    """
    Very simple JD section extractor:
    tries to find chunks under headings like Responsibilities/Requirements/Qualifications/Preferred/Benefits.
    Works on plain text and many HTML-stripped JDs.
    """
    t = (text or "")
    lines = [ln.strip() for ln in t.splitlines()]
    lines = [ln for ln in lines if ln]

    headings = {
        "responsibilities": ["responsibilities", "what you’ll do", "what you'll do", "you will", "role", "the role"],
        "requirements": ["requirements", "required", "must have", "minimum qualifications"],
        "qualifications": ["qualifications", "basic qualifications"],
        "preferred": ["preferred", "nice to have", "bonus", "preferred qualifications"],
        "benefits": ["benefits", "perks", "compensation", "salary"]
    }

    # Build a map of line index -> heading key
    idx_to_key = {}
    for i, ln in enumerate(lines):
        low = ln.lower().strip(":")
        for key, keys in headings.items():
            for k in keys:
                if low == k or low.startswith(k + ":"):
                    idx_to_key[i] = key

    # If no headings detected, return first N lines as "summary"
    if not idx_to_key:
        return {"summary": "\n".join(lines[:25])}

    # Slice sections by heading boundaries
    section_keys = []
    for i in sorted(idx_to_key.keys()):
        section_keys.append((i, idx_to_key[i]))
    section_keys.append((len(lines), "__end__"))

    out = {}
    for n in range(len(section_keys) - 1):
        start_i, key = section_keys[n]
        end_i, _ = section_keys[n + 1]
        chunk = lines[start_i+1:end_i]
        if chunk:
            out[key] = "\n".join(chunk[:40])  # cap
    return out

# ---------------- triage logic ----------------

def triage_one(job: dict, profile: dict):
    title = job.get("title") or ""
    loc = job.get("location") or ""
    url = job.get("url") or ""
    company = job.get("company") or "unknown"
    src = job.get("source") or ""
    content = job.get("content_plain") or job.get("content_text") or ""
    haystack = f"{title}\n{loc}\n{content}"

    titles_target = profile.get("titles_target") or []
    skills_have = profile.get("skills_have") or []
    skills_want = profile.get("skills_want") or []
    bonus_keywords = profile.get("bonus_keywords") or []

    matched_titles = hit_list(title, titles_target)
    matched_have = hit_list(haystack, skills_have)
    matched_want = hit_list(haystack, skills_want)
    matched_bonus = hit_list(haystack, bonus_keywords)

    # Citizenship/clearance flags (reuse your scoring logic idea)
    text_low = haystack.lower()

    neg_clearance = [
        r"\bno\s+clearance\s+required\b",
        r"\bclearance\s+not\s+required\b",
        r"\bnot\s+require\s+(a\s+)?clearance\b",
        r"\bdoes\s+not\s+require\s+(a\s+)?clearance\b",
    ]
    req_clearance = [
        r"\b(active\s+)?security\s+clearance\b",
        r"\bclearance\s+(is\s+)?required\b",
        r"\bmust\s+(have|hold|obtain)\s+(an?\s+)?clearance\b",
        r"\brequires?\s+(an?\s+)?clearance\b",
    ]

    neg_citizen = [
        r"\bcitizenship\s+not\s+required\b",
        r"\bnot\s+required\s+to\s+be\s+(a\s+)?(u\.s\.\s+)?citizen\b",
    ]
    req_citizen = [
        r"\b(u\.s\.\s+)?citizen(s)?\s+only\b",
        r"\bmust\s+be\s+(a\s+)?(u\.s\.\s+)?citizen\b",
        r"\bcitizenship\s+(is\s+)?required\b",
        r"\brequires?\s+(u\.s\.\s+)?citizenship\b",
    ]

    hard_flags = []
    soft_flags = []

    clearance_ctx = find_contexts(text_low, "clearance", window=90) + find_contexts(text_low, "clearence", window=90)
    if clearance_ctx:
        if term_is_hard_required(clearance_ctx, req_clearance, neg_clearance):
            hard_flags.append("clearance_required")
        else:
            soft_flags.append("clearance_mentioned")

    citizen_ctx = find_contexts(text_low, "citizen", window=90)
    if citizen_ctx:
        if term_is_hard_required(citizen_ctx, req_citizen, neg_citizen):
            hard_flags.append("citizen_required")
        else:
            soft_flags.append("citizen_mentioned")

    sections = extract_sections(content)

    # Simple suggestion rule:
    # - If any hard flag => Skip
    # - Else if matched_titles exists and (have or want) has >= 2 => Apply
    # - Else => Maybe
    suggestion = "Maybe"
    if hard_flags:
        suggestion = "Skip"
    else:
        if matched_titles and (len(matched_have) + len(matched_want) >= 2):
            suggestion = "Apply"

    return {
        "id": job.get("id"),
        "company": company,
        "title": title,
        "location": loc,
        "url": url,
        "source": src,
        "suggestion": suggestion,
        "matched_titles": matched_titles,
        "matched_skills_have": matched_have,
        "matched_skills_want": matched_want,
        "matched_bonus": matched_bonus,
        "hard_flags": hard_flags,
        "soft_flags": soft_flags,
        "sections": sections
    }

def write_job_md(path: str, tri: dict, today_str: str, generated_at: str):
    lines = []
    lines.append(f"# {tri['company']} — {tri['title']}\n")
    lines.append(f"- Date (UTC): {today_str}\n")
    lines.append(f"- Generated at (UTC): {generated_at}\n")
    lines.append(f"- Location: {tri.get('location') or 'N/A'}\n")
    lines.append(f"- Source: {tri.get('source') or 'N/A'}\n")
    lines.append(f"- Link: {tri.get('url') or ''}\n")
    lines.append(f"- Suggestion: **{tri.get('suggestion')}**\n\n")

    if tri.get("hard_flags") or tri.get("soft_flags"):
        lines.append("## Eligibility flags\n")
        if tri.get("hard_flags"):
            lines.append(f"- Hard: {', '.join(tri['hard_flags'])}\n")
        if tri.get("soft_flags"):
            lines.append(f"- Soft: {', '.join(tri['soft_flags'])}\n")
        lines.append("\n")

    lines.append("## Keyword matches\n")
    lines.append(f"- Matched titles: {', '.join(tri.get('matched_titles') or [])}\n")
    lines.append(f"- Skills have: {', '.join(tri.get('matched_skills_have') or [])}\n")
    lines.append(f"- Skills want: {', '.join(tri.get('matched_skills_want') or [])}\n")
    lines.append(f"- Bonus: {', '.join(tri.get('matched_bonus') or [])}\n\n")

    lines.append("## JD extracted sections\n")
    sections = tri.get("sections") or {}
    for key in ["summary", "responsibilities", "requirements", "qualifications", "preferred", "benefits"]:
        if key in sections and sections[key]:
            lines.append(f"### {key}\n")
            lines.append(sections[key] + "\n\n")

    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)

def write_index_md(path: str, items: list, today_str: str, generated_at: str):
    lines = []
    lines.append("# Triage (today)\n")
    lines.append(f"Date (UTC): {today_str}\n")
    lines.append(f"Generated at (UTC): {generated_at}\n")
    lines.append(f"Total: {len(items)}\n\n")

    # Group by suggestion
    for group in ["Apply", "Maybe", "Skip"]:
        group_items = [x for x in items if x.get("suggestion") == group]
        lines.append(f"## {group} ({len(group_items)})\n")
        for x in group_items:
            rel = x.get("md_relpath") or ""
            lines.append(f"- [{x['company']}] {x['title']} ({x.get('location') or 'N/A'}) — {rel}\n")
        lines.append("\n")

    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(ARCHIVE_DIR, exist_ok=True)

    now_utc = datetime.now(timezone.utc)
    today_str = utc_date_str(now_utc)
    generated_at = now_utc.isoformat()

    # Load inputs
    if not os.path.exists(IN_TODAY_JSON):
        raise FileNotFoundError(f"Missing {IN_TODAY_JSON}. Run fetch_jobs.py first.")

    with open(IN_TODAY_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)

    jobs = data.get("jobs") or []
    with open(PROFILE_PATH, "r", encoding="utf-8") as f:
        profile = json.load(f)

    day_dir = os.path.join(ARCHIVE_DIR, today_str)
    os.makedirs(day_dir, exist_ok=True)

    out_items = []
    for job in jobs:
        tri = triage_one(job, profile)
        jid = tri.get("id") or safe_filename(tri.get("url") or tri.get("title") or "job")
        md_name = safe_filename(jid) + ".md"
        md_path = os.path.join(day_dir, md_name)

        write_job_md(md_path, tri, today_str, generated_at)
        tri["md_relpath"] = os.path.relpath(md_path, OUT_DIR)

        out_items.append(tri)

    # Write machine json + index md
    out_json = os.path.join(day_dir, "triage.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"date_utc": today_str, "generated_at_utc": generated_at, "count": len(out_items), "items": out_items},
                  f, ensure_ascii=False, indent=2)

    index_md = os.path.join(OUT_DIR, "today_index.md")
    write_index_md(index_md, out_items, today_str, generated_at)

    print(f"triage: wrote {len(out_items)} items into {day_dir}")

if __name__ == "__main__":
    main()
