import json
import os
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
            "content_text": (j.get("content") or "").strip()  # can be large
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
                "content_text": (j.get("descriptionPlain") or "").strip()
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
            else:
                errors.append(f"Unknown source: {source} ({company})")
        except (HTTPError, URLError, KeyError, TimeoutError) as e:
            errors.append(f"{company} ({source}) failed: {repr(e)}")

        time.sleep(0.3)  # be polite

    # Sort newest-ish first (best effort; timestamps differ)
    def sort_key(j):
        v = j.get("updated_at") or j.get("created_at") or ""
        return str(v)
    all_jobs.sort(key=sort_key, reverse=True)

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "count": len(all_jobs),
        "errors": errors,
        "jobs": all_jobs
    }

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # Human-friendly markdown
    lines = []
    lines.append(f"# Job feed (auto)\n")
    lines.append(f"Generated at (UTC): {payload['generated_at_utc']}\n")
    lines.append(f"Total jobs: {payload['count']}\n")
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
