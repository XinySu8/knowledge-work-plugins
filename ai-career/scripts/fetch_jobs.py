import json
import os
import re
import sys
import time
import hashlib
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

ROOT = os.path.dirname(os.path.dirname(__file__))  # ai-career/
CONFIG_PATH = os.path.join(ROOT, "config", "targets.json")
OUT_DIR = os.path.join(ROOT, "data")
OUT_JSON = os.path.join(OUT_DIR, "jobs.json")
OUT_MD = os.path.join(OUT_DIR, "jobs.md")

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
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&")
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _norm(s: str) -> str:
    return (s or "").lower()

def contains_any(text: str, keywords) -> bool:
    if not keywords:
        return True
    t = (text or "")
    for k in keywords:
        if not (isinstance(k, str) and k.strip()):
            continue
        kk = k.strip()

        # If it's a phrase (has space or hyphen), substring match is fine
        if (" " in kk) or ("-" in kk):
            if kk.lower() in t.lower():
                return True
            continue

        # Single token: use word-boundary regex to avoid matching internal/international/paid/html etc.
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

    kept = []
    dropped = []  # dicts with reason

    per_company = {}

    for j in jobs:
        title = j.get("title") or ""
        loc = j.get("location") or ""
        content = j.get("content_plain") or j.get("content_text") or ""
        haystack = f"{title}\n{loc}\n{content}"

        # Exclude first
        if exclude_any and contains_any(haystack, exclude_any):
            dropped.append({"id": j.get("id"), "reason": "excluded_keyword", "title": title, "company": j.get("company")})
            continue


        # 1) Must be internship-ish
        # Use Ashby structured field as a strong POSITIVE signal (not a veto),
        # and always allow keyword-based fallback.
        ashby_types = filters.get("ashby_internship_types") or []
        ashby_types = {(_norm(x)) for x in ashby_types if isinstance(x, str) and x.strip()}

        is_internship = False

        # Strong positive signal from Ashby
        if j.get("source") == "ashby" and j.get("employment_type") and ashby_types:
            et = _norm(str(j.get("employment_type")).strip())
            if et in ashby_types:
                is_internship = True

        # Keyword-based fallback (for non-Ashby sources OR when employment_type isn't helpful)
        if not is_internship and internship_any:
            if contains_any(title, internship_any) or contains_any(haystack, internship_any):
                is_internship = True

        if not is_internship:
            dropped.append({"id": j.get("id"), "reason": "not_internship", "title": title, "company": j.get("company")})
            continue


        # 2) Must match domain direction (ML/Data/SWE/AI...) in title or content
        if domain_any and not contains_any(haystack, domain_any):
            dropped.append({"id": j.get("id"), "reason": "domain_mismatch", "title": title, "company": j.get("company")})
            continue

        # Optional location filter (off by default)
        if locations_any and not contains_any(loc, locations_any):
            dropped.append({"id": j.get("id"), "reason": "location_mismatch", "title": title, "company": j.get("company")})
            continue

        # Cap per company
        if max_per_company:
            c = j.get("company") or "unknown"
            per_company.setdefault(c, 0)
            if per_company[c] >= int(max_per_company):
                dropped.append({"id": j.get("id"), "reason": "company_cap", "title": title, "company": j.get("company")})
                continue
            per_company[c] += 1

        kept.append(j)

    return kept, dropped


def fetch_greenhouse(board_token: str, company: str):
    # Official Job Board API style endpoint (public jobs)
    # Common endpoint: https://api.greenhouse.io/v1/boards/{token}/jobs?content=true
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
            "content_text": (j.get("content") or "").strip(),  # raw HTML
            "content_plain": html_to_text((j.get("content") or "").strip())
        })
    return jobs

def fetch_lever(lever_slug: str, company: str):
    # Lever Postings API (public postings)
    # Common endpoint: https://api.lever.co/v0/postings/{slug}?mode=json
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
                "location": j.get("categories", {}).get("location"),
                "url": j.get("hostedUrl"),
                "updated_at": j.get("createdAt"),
                "created_at": j.get("createdAt"),
                "departments": [categories.get("team")] if categories.get("team") else [],
                "content_text": (j.get("descriptionPlain") or "").strip(),
                "content_plain": (j.get("descriptionPlain") or "").strip()
            })
    return jobs
    
def fetch_ashby(job_board_name: str, company: str):
    # Ashby public job postings API (no auth)
    # Docs: https://api.ashbyhq.com/posting-api/job-board/{JOB_BOARD_NAME}
    url = f"https://api.ashbyhq.com/posting-api/job-board/{job_board_name}?includeCompensation=false"
    data = http_get_json(url)

    jobs = []
    for j in data.get("jobs", []) or []:
        # Use jobUrl/applyUrl as stable unique identifiers when available
        job_url = j.get("jobUrl") or ""
        apply_url = j.get("applyUrl") or ""
        title = j.get("title")
        location = j.get("location")
        published_at = j.get("publishedAt")
        employment_type = j.get("employmentType")  # e.g., "Intern", "FullTime", ...
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


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)

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

        time.sleep(0.3)  # be polite

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

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "fetched_count": fetched_count,
        "filtered_count": filtered_count,
        "count": len(all_jobs),
        "per_company_fetched": per_company_fetched,
        "per_source_fetched": per_source_fetched,
        "errors": errors,
        "dropped_sample": dropped[:50],
        "jobs": all_jobs
    }

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # Human-friendly markdown
    lines = []
    lines.append(f"# Job feed (auto)\n")
    lines.append(f"Generated at (UTC): {payload['generated_at_utc']}\n")
    lines.append(f"Total jobs: {payload['count']}\n")
    lines.append(f"Fetched jobs: {payload.get('fetched_count')}\n")
    lines.append(f"Filtered jobs: {payload.get('filtered_count')}\n")
    if errors:
        lines.append("## Errors\n")
        for e in errors:
            lines.append(f"- {e}\n")
        lines.append("\n")

    lines.append("## Jobs\n")
    for j in all_jobs[:200]:
        title = j.get("title") or "Untitled"
        company = j.get("company") or "unknown"
        loc = j.get("location") or "Unknown location"
        url = j.get("url") or ""
        source = j.get("source")
        lines.append(f"- [{company}] {title} ({loc}) — {source} — {url}\n")

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.writelines(lines)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("fatal:", repr(e))
        sys.exit(1)
