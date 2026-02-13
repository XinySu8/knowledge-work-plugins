import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(__file__))  # ai-career/
STATE_PATH = os.path.join(ROOT, "data", "state.json")
JOBS_JSON = os.path.join(ROOT, "data", "jobs.json")
JOBS_TODAY_JSON = os.path.join(ROOT, "data", "jobs_today.json")
JOBS_BACKLOG_JSON = os.path.join(ROOT, "data", "jobs_backlog.json")


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception:
        return default


def save_json_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def now_iso_utc():
    return datetime.now(timezone.utc).isoformat()


def find_job_id_by_url(url: str):
    """
    Try to find job id by URL from state first, then from jobs snapshots.
    """
    url = (url or "").strip()
    if not url:
        return None

    state = load_json(STATE_PATH, {"version": 1, "jobs": {}})
    jobs_state = state.get("jobs") or {}

    # 1) Search in state
    for jid, rec in jobs_state.items():
        if isinstance(rec, dict) and (rec.get("url") or "").strip() == url:
            return jid

    # 2) Search in jobs snapshots
    for p in [JOBS_TODAY_JSON, JOBS_BACKLOG_JSON, JOBS_JSON]:
        data = load_json(p, {})
        for j in (data.get("jobs") or []):
            if (j.get("url") or "").strip() == url and j.get("id"):
                return j.get("id")

    return None


def main():
    """
    Usage:
      python ai-career/scripts/mark_job.py <status> <url> [note...]

    status: applied | ignored | new | closed
    """
    if len(sys.argv) < 3:
        print("Usage: mark_job.py <status> <url> [note...]")
        sys.exit(2)

    status = (sys.argv[1] or "").strip().lower()
    url = (sys.argv[2] or "").strip()
    note = " ".join(sys.argv[3:]).strip() if len(sys.argv) > 3 else ""

    allowed = {"applied", "ignored", "new", "closed"}
    if status not in allowed:
        print(f"Invalid status: {status}. Allowed: {sorted(list(allowed))}")
        sys.exit(2)

    state = load_json(STATE_PATH, {"version": 1, "jobs": {}})
    if not isinstance(state, dict):
        state = {"version": 1, "jobs": {}}
    jobs_state = state.setdefault("jobs", {})
    if not isinstance(jobs_state, dict):
        jobs_state = {}
        state["jobs"] = jobs_state

    jid = find_job_id_by_url(url)
    if not jid:
        print(f"Could not find job by url: {url}")
        sys.exit(1)

    rec = jobs_state.get(jid)
    if not isinstance(rec, dict):
        rec = {}
        jobs_state[jid] = rec

    rec["url"] = url
    rec["status"] = status
    rec["status_updated_at_utc"] = now_iso_utc()
    if note:
        rec["note"] = note

    save_json_atomic(STATE_PATH, state)

    print(f"OK: {jid} -> status={status} url={url}")


if __name__ == "__main__":
    main()
