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

def _split_sentences(text: str):
    """
    Lightweight sentence splitter for evidence extraction.
    We prefer stable behavior over perfect NLP.
    """
    if not text:
        return []
    t = re.sub(r"\s+", " ", text.strip())
    parts = re.split(r"(?<=[.!?])\s+", t)
    return [p.strip() for p in parts if len(p.strip()) >= 20]

def _highlight_phrases(text: str, phrases):
    """
    Highlight phrases in plain text using <<...>> so it pops even without Markdown.
    """
    out = text
    uniq = []
    seen = set()
    for p in (phrases or []):
        if not p:
            continue
        pl = p.lower()
        if pl not in seen:
            seen.add(pl)
            uniq.append(p)
    for ph in sorted(uniq, key=len, reverse=True):
        out = re.sub(re.escape(ph), f"<<{ph}>>", out, flags=re.IGNORECASE)
    return out

def _pick_evidence_sentences(text: str, phrases, max_sentences: int = 2):
    """
    Pick up to N sentences containing any of phrases; fall back to snippet if needed.
    """
    sents = _split_sentences(text)
    hits = []
    phrases_low = [p.lower() for p in (phrases or []) if p]

    for s in sents:
        sl = s.lower()
        if any(p in sl for p in phrases_low):
            hits.append(_highlight_phrases(s, phrases))
        if len(hits) >= max_sentences:
            break

    if not hits:
        snippet = (text or "").strip().replace("\n", " ")
        snippet = snippet[:220] + ("..." if len(snippet) > 220 else "")
        if snippet:
            hits = [_highlight_phrases(snippet, phrases)]
    return hits

def classify_clearance(text: str):
    """
    Clearance tri-state classifier:
      - CLEAR: explicit "not required" statements
      - BLOCK: strong positive signals (very low false positives)
      - REVIEW: mentions clearance but ambiguous; do NOT block automatically
      - NONE: no signal

    Returns dict: {"status": "CLEAR"|"BLOCK"|"REVIEW"|"NONE", "evidence": [str, ...]}
    """
    if not text:
        return {"status": "NONE", "evidence": []}

    lower = text.lower()

    # Strong negations (CLEAR) — prioritize these to avoid false blocks.
    neg_phrases = [
        "no clearance required",
        "no security clearance required",
        "security clearance not required",
        "clearance is not required",
        "clearance not required",
        "do not require a security clearance",
        "does not require a security clearance",
    ]

    # Strong positive (BLOCK) — keep conservative to minimize false positives.
    pos_phrases = [
        "ts/sci",
        "ts-sci",
        "top secret",
        "secret clearance",
        "active security clearance",
        "current security clearance",
        "must have a security clearance",
        "must have security clearance",
        "ability to obtain a security clearance",
        "able to obtain a security clearance",
        "eligible for a security clearance",
        "eligible for security clearance",
    ]

    # Weak triggers (REVIEW) — mention only.
    weak_triggers = [
        "security clearance",
        "clearance",
        "ts/sci",
        "top secret",
    ]

    # Handle common misspelling "clearence" too
    if "clearence" in lower and "clearance" not in lower:
        lower = lower.replace("clearence", "clearance")
        text = re.sub(r"clearence", "clearance", text, flags=re.IGNORECASE)

    if any(p in lower for p in neg_phrases):
        return {"status": "CLEAR", "evidence": _pick_evidence_sentences(text, neg_phrases)}

    if any(p in lower for p in pos_phrases):
        return {"status": "BLOCK", "evidence": _pick_evidence_sentences(text, pos_phrases)}

    if any(p in lower for p in weak_triggers):
        return {"status": "REVIEW", "evidence": _pick_evidence_sentences(text, weak_triggers)}

    return {"status": "NONE", "evidence": []}

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

def _extract_pay_lines(benefits_text: str, max_lines: int = 4):
    """
    Keep only pay/comp-related lines from benefits to reduce noise.
    """
    if not benefits_text:
        return ""
    lines = [ln.strip() for ln in benefits_text.splitlines() if ln.strip()]
    keep = []
    for ln in lines:
        low = ln.lower()
        if ("$" in ln) or ("salary" in low) or ("rate" in low) or ("stipend" in low) or ("compensation" in low) or ("housing" in low) or ("hour" in low) or ("monthly" in low):
            keep.append(ln)
        if len(keep) >= max_lines:
            break
    return "\n".join(keep)

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

    # Citizenship/clearance flags
    text_low = haystack.lower()

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

    # --- Clearance tri-state (BLOCK/REVIEW/CLEAR) with evidence for readability ---
    clr = classify_clearance(haystack)
    clearance_status = clr.get("status") or "NONE"
    clearance_evidence = clr.get("evidence") or []

    if clearance_status == "BLOCK":
        hard_flags.append("clearance_required")
    elif clearance_status == "REVIEW":
        soft_flags.append("clearance_needs_review")
    elif clearance_status == "CLEAR":
        soft_flags.append("clearance_not_required")

    citizen_ctx = find_contexts(text_low, "citizen", window=90)
    if citizen_ctx:
        if term_is_hard_required(citizen_ctx, req_citizen, neg_citizen):
            hard_flags.append("citizen_required")
        else:
            soft_flags.append("citizen_mentioned")

    sections = extract_sections(content)

    # Clean up benefits section to reduce noise (keep only pay/stipend/housing lines)
    if isinstance(sections, dict) and sections.get("benefits"):
        pay = _extract_pay_lines(sections.get("benefits") or "", max_lines=4)
        if pay:
            sections["benefits"] = pay
        else:
            # If no pay-like lines, drop benefits to avoid long generic perks
            sections.pop("benefits", None)

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
        "sections": sections,
        "clearance_status": clearance_status,
        "clearance_evidence": clearance_evidence
    }

def write_job_md(path: str, tri: dict, today_str: str, generated_at: str):
    lines = []
    lines.append(f"# {tri['company']} — {tri['title']}\n\n")
    lines.append(f"Date (UTC): {today_str}\n")
    lines.append(f"Generated at (UTC): {generated_at}\n")
    lines.append(f"Location: {tri.get('location') or 'N/A'}\n")
    lines.append(f"Source: {tri.get('source') or 'N/A'}\n")
    lines.append(f"Link: {tri.get('url') or ''}\n")
    lines.append(f"Suggestion: {tri.get('suggestion')}\n\n")

    # --- Key signals (make important items visible quickly) ---
    lines.append("KEY SIGNALS\n")
    # Clearance block
    cs = tri.get("clearance_status") or "NONE"
    evs = tri.get("clearance_evidence") or []
    if cs != "NONE":
        if cs == "BLOCK":
            lines.append("SECURITY CHECK: [BLOCK] Requires clearance (do not apply)\n")
        elif cs == "REVIEW":
            lines.append("SECURITY CHECK: [REVIEW] Clearance mentioned — verify context\n")
        elif cs == "CLEAR":
            lines.append("SECURITY CHECK: [CLEAR] No clearance required (per posting)\n")
        for ev in evs[:2]:
            lines.append(f"Evidence: {ev}\n")
    else:
        lines.append("SECURITY CHECK: [NONE]\n")

    if tri.get("hard_flags") or tri.get("soft_flags"):
        if tri.get("hard_flags"):
            lines.append(f"Hard flags: {', '.join(tri['hard_flags'])}\n")
        if tri.get("soft_flags"):
            lines.append(f"Soft flags: {', '.join(tri['soft_flags'])}\n")
    lines.append("\n")

    lines.append("KEYWORD MATCHES\n")
    lines.append(f"Matched titles: {', '.join(tri.get('matched_titles') or [])}\n")
    lines.append(f"Skills have: {', '.join(tri.get('matched_skills_have') or [])}\n")
    lines.append(f"Skills want: {', '.join(tri.get('matched_skills_want') or [])}\n")
    lines.append(f"Bonus: {', '.join(tri.get('matched_bonus') or [])}\n\n")

    # --- JD extracted sections (keep, but already capped; benefits trimmed above) ---
    lines.append("JD EXTRACTED SECTIONS (trimmed)\n")
    sections = tri.get("sections") or {}
    for key in ["summary", "responsibilities", "requirements", "qualifications", "preferred", "benefits"]:
        if key in sections and sections[key]:
            lines.append(f"\n[{key.upper()}]\n")
            lines.append(sections[key] + "\n")

    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)

def write_index_md(path: str, items: list, today_str: str, generated_at: str):
    lines = []
    lines.append("Triage (today)\n")
    lines.append(f"Date (UTC): {today_str}\n")
    lines.append(f"Generated at (UTC): {generated_at}\n")
    lines.append(f"Total: {len(items)}\n\n")

    blocked = [x for x in items if "clearance_required" in (x.get("hard_flags") or [])]
    review = [x for x in items if "clearance_needs_review" in (x.get("soft_flags") or [])]

    # Show clearance-related groups first (so you don't miss red lines, but also avoid mis-blocking)
    if blocked:
        lines.append(f"BLOCKED (Clearance) ({len(blocked)})\n")
        for x in blocked:
            rel = x.get("md_relpath") or ""
            ev = (x.get("clearance_evidence") or [""])[0]
            if ev:
                lines.append(f"- [{x['company']}] {x['title']} ({x.get('location') or 'N/A'}) — {ev} — {rel}\n")
            else:
                lines.append(f"- [{x['company']}] {x['title']} ({x.get('location') or 'N/A'}) — {rel}\n")
        lines.append("\n")

    if review:
        lines.append(f"NEEDS REVIEW (Clearance mentioned) ({len(review)})\n")
        for x in review:
            rel = x.get("md_relpath") or ""
            ev = (x.get("clearance_evidence") or [""])[0]
            if ev:
                lines.append(f"- [{x['company']}] {x['title']} ({x.get('location') or 'N/A'}) — {ev} — {rel}\n")
            else:
                lines.append(f"- [{x['company']}] {x['title']} ({x.get('location') or 'N/A'}) — {rel}\n")
        lines.append("\n")

    # Group by suggestion, excluding clearance-block/review to keep main lists clean
    excluded_ids = set()
    for x in blocked + review:
        if x.get("id"):
            excluded_ids.add(x.get("id"))

    for group in ["Apply", "Maybe", "Skip"]:
        group_items = []
        for x in items:
            if x.get("suggestion") != group:
                continue
            if x.get("id") in excluded_ids:
                continue
            group_items.append(x)

        lines.append(f"{group} ({len(group_items)})\n")
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
        json.dump(
            {"date_utc": today_str, "generated_at_utc": generated_at, "count": len(out_items), "items": out_items},
            f,
            ensure_ascii=False,
            indent=2
        )

    index_md = os.path.join(OUT_DIR, "today_index.md")
    write_index_md(index_md, out_items, today_str, generated_at)

    print(f"triage: wrote {len(out_items)} items into {day_dir}")

if __name__ == "__main__":
    main()
