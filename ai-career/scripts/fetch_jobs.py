import json
import os
import re
import sys
import time
import hashlib
import html
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

ROOT = os.path.dirname(os.path.dirname(__file__))  # ai-career/
CONFIG_PATH = os.path.join(ROOT, "config", "targets.json")
OUT_DIR = os.path.join(ROOT, "data")

OUT_JSON = os.path.join(OUT_DIR, "jobs.json")
OUT_MD = os.path.join(OUT_DIR, "jobs.md")

STATE_PATH = os.path.join(OUT_DIR, "state.json")

OUT_TODAY_JSON = os.path.join(OUT_DIR, "jobs_today.json")
OUT_TODAY_MD = os.path.join(OUT_DIR, "jobs_today.md")

OUT_BACKLOG_JSON = os.path.join(OUT_DIR, "jobs_backlog.json")
OUT_BACKLOG_MD = os.path.join(OUT_DIR, "jobs_backlog.md")

ARCHIVE_DIR = os.path.join(OUT_DIR, "archive")

UA = "ai-career-job-fetcher/0.1 (GitHub Actions)"


def http_get_json(url: str):
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def stable_id(*parts: str) -> str:
    h = hashlib.sha256("||".join(parts).encode("utf-8")).hexdigest()
    return h[:16]


def html_to_text(s: str) -> str:
    if not s:
        return ""
    s = html.unescape(s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _norm(s: str) -> str:
    return (s or "").lower()


def contains_any(text: str, keywords) -> bool:
    """
    Match any keyword in text.
    - Phrases (contain spaces) => substring match.
    - Single tokens (including hyphenated like 'co-op') => word-boundary regex match.
      This prevents 'co-op' from matching 'co-opetition', and 'intern' from matching 'internal'.
    """
    if not keywords:
        return True

    t = text or ""
    for k in keywords:
        if not (isinstance(k, str) and k.strip()):
            continue
        kk = k.strip()

        # Phrase: substring match
        if " " in kk:
            if kk.lower() in t.lower():
                return True
            continue

        # Single token (including hyphenated): word-boundary regex
        pattern = r"\b" + re.escape(kk) + r"\b"
        if re.search(pattern, t, flags=re.IGNORECASE):
            return True

    return False


def apply_filters(jobs, filters):
    internship_any = filters.get("internship_any") or []
    domain_any = filters.get("domain_any") or []
    exclude_any = filters.get("exclude_any") or []
    locations_any = filters.get("locations_any") or []
    max_per_company = filters.get("max_jobs_per_company")
    major_required_any = filters.get("major_required_any") or []
    degree_required_any = filters.get("degree_required_any") or []

    kept = []
    dropped = []

    per_company = {}

    for j in jobs:
        title = j.get("title") or ""
        loc = j.get("location") or ""
        content = j.get("content_plain") or j.get("content_text") or ""
        haystack = f"{title}\n{loc}\n{content}"

        # Exclude first
        if exclude_any and contains_any(haystack, exclude_any):
            dropped.append({
                "id": j.get("id"),
                "reason": "excluded_keyword",
                "title": title,
                "company": j.get("company")
            })
            continue

        # 1) Must be internship-ish
        ashby_types = filters.get("ashby_internship_types") or []
        ashby_types = {(_norm(x)) for x in ashby_types if isinstance(x, str) and x.strip()}

        is_internship = False

        # Strong positive signal from Ashby
        if j.get("source") == "ashby" and j.get("employment_type") and ashby_types:
            et = _norm(str(j.get("employment_type")).strip())
            if et in ashby_types:
                is_internship = True

        # Keyword-based fallback
        if not is_internship and internship_any:
            if contains_any(title, internship_any) or contains_any(haystack, internship_any):
                is_internship = True

        if not is_internship:
            dropped.append({
                "id": j.get("id"),
                "reason": "not_internship",
                "title": title,
                "company": j.get("company")
            })
            continue

        # Optional: require degree/major keywords (if provided)
        if degree_required_any and not contains_any(haystack, degree_required_any):
            dropped.append({
                "id": j.get("id"),
                "reason": "degree_mismatch",
                "title": title,
                "company": j.get("company")
            })
            continue

        if major_required_any and not contains_any(haystack, major_required_any):
            dropped.append({
                "id": j.get("id"),
                "reason": "major_mismatch",
                "title": title,
                "company": j.get("company")
            })
            continue

        # 2) Must match domain direction
        if domain_any and not contains_any(haystack, domain_any):
            dropped.append({
                "id": j.get("id"),
                "reason": "domain_mismatch",
                "title": title,
                "company": j.get("company")
            })
            continue

        # Optional location filter
        if locations_any and not contains_any(loc, locations_any):
            dropped.append({
                "id": j.get("id"),
                "reason": "location_mismatch",
                "title": title,
                "company": j.get("company")
            })
            continue

        # Cap per company
        if max_per_company:
            c = j.get("company") or "unknown"
            per_company.setdefault(c, 0)
            if per_company[c] >= int(max_per_company):
                dropped.append({
                    "id": j.get("id"),
                    "reason": "company_cap",
                    "title": title,
                    "company": j.get("company")
                })
                continue
            per_company[c] += 1

        kept.append(j)

    return kept, dropped


def load_state(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict) and "jobs" in data and isinstance(data["jobs"], dict):
                return data
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return {"version": 1, "jobs": {}}


def save_state(path: str, state: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def utc_date_str(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")


def fetch_greenhouse(board_token: str, company: str):
    url = f"https://api.greenhouse.io/v1/boards/{board_token}/jobs?content=true"
    data = http_get_json(url)
    jobs = []
    for j in data.get("jobs", []):
        job_id = stable_id("greenhouse", board_token, str(j.get("id", "")), j.get("absolute_url", ""))
        jobs.append({
            "id": job_id,
            "source": "greenhouse",
            "company": company,
            "title": j.get("title"),
            "location": (j.get("location") or {}).get("name"),
            "url": j.get("absolute_url"),
            "updated_at": j.get("updated_at"),
            "created_at": j.get("created_at"),
            "departments": [d.get("name") for d in (j.get("departments") or []) if isinstance(d, dict)],
            "content_text": (j.get("content") or "").strip(),
            "content_plain": html_to_text((j.get("content") or "").strip())
        })
    return jobs


def fetch_lever(lever_slug: str, company: str):
    url = f"https://api.lever.co/v0/postings/{lever_slug}?mode=json"
    data = http_get_json(url)
    jobs = []
    if isinstance(data, list):
        for j in data:
            job_id = stable_id("lever", lever_slug, str(j.get("id", "")), j.get("hostedUrl", ""))
            categories = j.get("categories") or {}
            jobs.append({
                "id": job_id,
                "source": "lever",
                "company": company,
                "title": j.get("text"),
                "location": (j.get("categories") or {}).get("location"),
                "url": j.get("hostedUrl"),
                "updated_at": j.get("createdAt"),
                "created_at": j.get("createdAt"),
                "departments": [categories.get("team")] if categories.get("team") else [],
                "content_text": (j.get("descriptionPlain") or "").strip(),
                "content_plain": (j.get("descriptionPlain") or "").strip()
            })
    return jobs


def fetch_ashby(job_board_name: str, company: str):
    url = f"https://api.ashbyhq.com/posting-api/job-board/{job_board_name}?includeCompensation=false"
    data = http_get_json(url)

    jobs = []
    for j in data.get("jobs", []) or []:
        job_url = j.get("jobUrl") or ""
        apply_url = j.get("applyUrl") or ""
        title = j.get("title")
        location = j.get("location")
        published_at = j.get("publishedAt")
        employment_type = j.get("employmentType")
        dept = j.get("department")
        team = j.get("team")

        job_id = stable_id("ashby", job_board_name, title or "", job_url, apply_url)

        jobs.append({
            "id": job_id,
            "source": "ashby",
            "company": company,
            "title": title,
            "location": location,
            "url": apply_url or job_url,
            "updated_at": published_at,
            "created_at": published_at,
            "departments": [x for x in [dept, team] if x],
            "employment_type": employment_type,
            "content_text": (j.get("descriptionHtml") or "").strip(),
            "content_plain": (j.get("descriptionPlain") or "").strip()
        })
    return jobs


def write_md(path_md: str, title: str, jobs_list, now_utc: datetime, today_str: str, errors=None):
    lines = []
    lines.append(f"# {title}\n")
    lines.append(f"Generated at (UTC): {now_utc.isoformat()}\n")
    lines.append(f"Today (UTC): {today_str}\n")
    lines.append(f"Total jobs: {len(jobs_list)}\n\n")

    if errors:
        lines.append("## Errors\n")
        for e in errors:
            lines.append(f"- {e}\n")
        lines.append("\n")

    lines.append("## Jobs\n")
    for j in jobs_list[:200]:
        title_j = j.get("title") or "Untitled"
        company = j.get("company") or "unknown"
        loc = j.get("location") or "Unknown location"
        url = j.get("url") or ""
        source = j.get("source")
        lines.append(f"- [{company}] {title_j} ({loc}) — {source} — {url}\n")

    with open(path_md, "w", encoding="utf-8") as f:
        f.writelines(lines)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(ARCHIVE_DIR, exist_ok=True)

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # Load persistent state (first_seen_date_utc etc.)
    state = load_state(STATE_PATH)
    jobs_state = state.setdefault("jobs", {})

    all_jobs = []
    errors = []

    for t in cfg.get("targets", []):
        source = t.get("source")
        company = t.get("company") or "unknown"
        try:
            if source == "greenhouse":
                board_token = t["board_token"]
                all_jobs.extend(fetch_greenhouse(board_token, company))
            elif source == "lever":
                lever_slug = t["lever_slug"]
                all_jobs.extend(fetch_lever(lever_slug, company))
            elif source == "ashby":
                job_board_name = t["job_board_name"]
                all_jobs.extend(fetch_ashby(job_board_name, company))
            else:
                errors.append(f"Unknown source: {source} ({company})")
        except (HTTPError, URLError, KeyError, TimeoutError) as e:
            errors.append(f"{company} ({source}) failed: {repr(e)}")

        time.sleep(0.3)

    fetched_count = len(all_jobs)
    filters = cfg.get("filters") or {}

    per_company_fetched = {}
    per_source_fetched = {}
    for j in all_jobs:
        per_company_fetched[j.get("company") or "unknown"] = per_company_fetched.get(j.get("company") or "unknown", 0) + 1
        per_source_fetched[j.get("source") or "unknown"] = per_source_fetched.get(j.get("source") or "unknown", 0) + 1

    all_jobs, dropped = apply_filters(all_jobs, filters)
    filtered_count = len(all_jobs)

    # Sort newest-ish first (best effort; timestamps differ)
    def sort_key(j):
        v = j.get("updated_at") or j.get("created_at") or ""
        return str(v)

    all_jobs.sort(key=sort_key, reverse=True)

    now_utc = datetime.now(timezone.utc)
    today_str = utc_date_str(now_utc)

    # Update state: first_seen_date_utc should NOT change during same-day reruns
    for j in all_jobs:
        jid = j.get("id")
        if not jid:
            continue

        rec = jobs_state.get(jid)
        if not isinstance(rec, dict):
            rec = {}
            jobs_state[jid] = rec

        if not rec.get("first_seen_date_utc"):
            rec["first_seen_date_utc"] = today_str

        rec["last_seen_at_utc"] = now_utc.isoformat()
        rec["company"] = j.get("company")
        rec["title"] = j.get("title")
        rec["url"] = j.get("url")

        # For later manual workflow
        rec.setdefault("status", "new")  # new/applied/ignored/closed

    save_state(STATE_PATH, state)

    # Split outputs:
    # - TODAY: first_seen_date_utc == today
    # - BACKLOG: older first_seen_date_utc, and not applied/ignored/closed
    today_jobs = []
    backlog_jobs = []

    for j in all_jobs:
        jid = j.get("id")
        rec = jobs_state.get(jid, {})
        first_date = rec.get("first_seen_date_utc") or today_str

        if first_date == today_str:
            today_jobs.append(j)
        else:
            status = (rec.get("status") or "new").lower()
            if status not in ("applied", "ignored", "closed"):
                backlog_jobs.append(j)

    payload = {
        "generated_at_utc": now_utc.isoformat(),
        "today_utc": today_str,
        "fetched_count": fetched_count,
        "filtered_count": filtered_count,
        "count": len(all_jobs),
        "today_count": len(today_jobs),
        "backlog_count": len(backlog_jobs),
        "per_company_fetched": per_company_fetched,
        "per_source_fetched": per_source_fetched,
        "errors": errors,
        "dropped_sample": dropped[:50],
        "jobs": all_jobs
    }

    # Current snapshot (overwritten every run)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # Today snapshot (overwritten every run; always "same-day latest")
    with open(OUT_TODAY_JSON, "w", encoding="utf-8") as f:
        json.dump(
            {
                "generated_at_utc": now_utc.isoformat(),
                "today_utc": today_str,
                "count": len(today_jobs),
                "jobs": today_jobs
            },
            f,
            ensure_ascii=False,
            indent=2
        )

    # Backlog snapshot
    with open(OUT_BACKLOG_JSON, "w", encoding="utf-8") as f:
        json.dump(
            {
                "generated_at_utc": now_utc.isoformat(),
                "today_utc": today_str,
                "count": len(backlog_jobs),
                "jobs": backlog_jobs
            },
            f,
            ensure_ascii=False,
            indent=2
        )

    # Markdown outputs
    write_md(OUT_MD, "Job feed (current)", all_jobs, now_utc, today_str, errors=errors if errors else None)
    write_md(OUT_TODAY_MD, "Job feed (today)", today_jobs, now_utc, today_str)
    write_md(OUT_BACKLOG_MD, "Job feed (backlog)", backlog_jobs, now_utc, today_str)

    # Daily archive: same day reruns overwrite SAME file; last run wins
    archive_json = os.path.join(ARCHIVE_DIR, f"jobs.{today_str}.json")
    archive_md = os.path.join(ARCHIVE_DIR, f"jobs.{today_str}.md")

    with open(archive_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    write_md(
        archive_md,
        f"Job feed (archive {today_str})",
        all_jobs,
        now_utc,
        today_str,
        errors=errors if errors else None
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("fatal:", repr(e))
        sys.exit(1)
