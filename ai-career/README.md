# ai-career: daily internship job pipeline (GitHub Actions)

This folder adds an "ai-career" workflow plugin and a daily job pipeline on top of the upstream fork.

What it does (daily):
1) Fetch job postings from multiple public ATS sources (Greenhouse / Lever / Ashby)
2) Filter for internship-like roles + your target domain keywords
3) Score jobs against your profile keywords (optional but recommended)
4) Triage today's jobs into Apply / Maybe / Skip and generate one markdown "job card" per job
5) Maintain a small persistent state file so you can separate:
   - today's newly-seen jobs
   - older backlog jobs you still haven't applied/ignored/closed

---

## Quick start (local)

From repo root:

    python ai-career/scripts/fetch_jobs.py
    python ai-career/scripts/score_jobs.py
    python ai-career/scripts/triage_jobs.py

---

## Configuration

### 1) Targets (where to fetch jobs)

Edit:

- `ai-career/config/targets.json`

This controls which companies/sources you fetch from and the filtering rules.

### 2) Profile (how to score/triage)

Edit:

- `ai-career/config/profile.json`

This controls keyword lists used by scoring/triage, plus location preferences.

Tip: keep keywords simple and stable first, then iterate.

---

## Outputs (generated files)

Main outputs are under:

- `ai-career/data/`

### Current snapshot (overwritten every run)
- `jobs.json`
- `jobs.md`

### Today vs backlog
- `jobs_today.json`
- `jobs_today.md`
- `jobs_backlog.json`
- `jobs_backlog.md`

Meaning:
- **Today** = jobs first seen on today’s UTC date (same-day reruns do NOT change “first seen”)
- **Backlog** = jobs first seen on previous days and not marked applied/ignored/closed

### Persistent state (do not delete)
- `state.json`

This stores per-job metadata such as:
- `first_seen_date_utc`
- `last_seen_at_utc`
- `status` (new/applied/ignored/closed)

### Daily archive (last run of the day wins)
- `ai-career/data/archive/jobs.YYYY-MM-DD.json`
- `ai-career/data/archive/jobs.YYYY-MM-DD.md`

Same-day reruns overwrite the SAME archive file, so you always keep the last snapshot for that date.

### Scoring output
- `scored_jobs.json`
- `scored_jobs.md`

### Triage output + per-job markdown cards
- `triage_today.md` (summary)
- `cards/by_day/YYYY-MM-DD/<job_id>.md` (one job card per job)

Job cards are stable per job id; within the same day, re-running updates the same card file.

---

## GitHub Actions (daily automation)

The repo includes a scheduled workflow (plus manual run) that runs:

- fetch → score → triage → commit generated outputs back to the repo

Run it now:
1) Go to GitHub → **Actions**
2) Select the workflow (e.g., “Fetch jobs” / “Job pipeline”)
3) Click **Run workflow**

---

## Marking job status (applied / ignored / closed)

The pipeline can’t know if you applied automatically, so you mark status yourself.

Recommended workflow:
- Use a dedicated GitHub Actions workflow that updates `ai-career/data/state.json`
- You provide the job id and status via manual “Run workflow” inputs

Status meanings:
- `new`: default; still in your queue
- `applied`: hide from backlog
- `ignored`: hide from backlog
- `closed`: hide from backlog (useful if posting disappears)

After you mark a job as `applied/ignored/closed`, it will stop showing in backlog outputs.

---

## Notes / FAQ

### Why “today vs backlog” is based on *first seen* (not posted date)?

Different ATS sources expose timestamps inconsistently. “first seen” is robust:
- You always get a clean “today” list for review
- Backlog stays stable across days until you mark items as applied/ignored/closed

### Will running multiple times per day overwrite files?

Yes for the “current snapshot” and “today/backlog” outputs — they reflect the latest run.
Archive uses one file per day (YYYY-MM-DD), also overwritten within the same day.

### I see a weird non-intern role in scored/triage but not in jobs.md

That usually means scoring/triage ran on an older `jobs.json` (or the workflow didn’t run the steps you expect).
Fix: ensure the workflow runs fetch → score → triage in the same job, and commits all outputs.

---

## Attribution

This repository is a fork of `anthropics/knowledge-work-plugins`.
It includes an additional "ai-career" workflow plugin and job pipeline contributed by the fork owner.

Upstream project:
- https://github.com/anthropics/knowledge-work-plugins

License:
- This fork remains licensed under the Apache License 2.0 (see the root LICENSE file).
