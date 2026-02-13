# ai-career (Job Feed + Scoring + Triage)

A lightweight, GitHub Actions–friendly job workflow:
- Fetch public job postings (Greenhouse / Lever / Ashby)
- Filter to internships + your target domain
- Score jobs against your profile keywords
- Triage into Apply / Maybe / Skip
- Persist state across days (so “today’s new jobs” vs “backlog” is stable)
- Generate per-job Markdown “cards” for quick review

## Quick Start (GitHub Actions)

1) Fork / clone this repo

2) Edit targets and filters:
- `ai-career/config/targets.json`

3) Edit your profile for scoring / triage:
- `ai-career/config/profile.json`

4) Run the workflow:
- Go to **Actions** → **Fetch jobs** → **Run workflow**

Outputs will be committed back to the repo under:
- `ai-career/data/`

## Outputs (What you should read daily)

### Job feeds
- `ai-career/data/jobs_today.md`  
  Jobs first seen **today (UTC)**.
- `ai-career/data/jobs_backlog.md`  
  Older jobs you haven’t marked as applied/ignored/closed.
- `ai-career/data/jobs.md`  
  Current filtered snapshot (all).

### Scoring
- `ai-career/data/scored_jobs.md`  
  Ranked list with score breakdown and keyword hits.
- `ai-career/data/scored_jobs.json`  
  Same data in JSON.

### Triage (actionable buckets)
- `ai-career/data/triage_today.md`  
  Apply / Maybe / Skip for today’s jobs.
- `ai-career/data/by_day/YYYY-MM-DD/<job_id>.md`  
  Per-job review cards generated for today.

### State / history
- `ai-career/data/state.json`  
  Persistent state:
  - `first_seen_date_utc` (stable within a day)
  - `last_seen_at_utc`
  - `status` (new/applied/ignored/closed)
- `ai-career/data/archive/jobs.YYYY-MM-DD.*`  
  Daily archive. Same-day reruns overwrite the same daily file (last run wins).

## Configuration

### `targets.json` (sources + filters)

**Targets** support:
- Greenhouse: `{ "source": "greenhouse", "company": "...", "board_token": "..." }`
- Lever: `{ "source": "lever", "company": "...", "lever_slug": "..." }`
- Ashby: `{ "source": "ashby", "company": "...", "job_board_name": "..." }`

**Filters** (high level):
- `internship_any`: internship keywords (title/content)
- `domain_any`: SWE/ML/Data direction keywords
- `exclude_any`: seniority/leadership terms to drop
- `degree_required_any`, `major_required_any`: optional gating
- `max_jobs_per_company`: cap noise per company

Important note:
- Hyphenated tokens like `co-op` are matched with safe boundaries to avoid false positives (e.g. `co-opetition`).

### `profile.json` (scoring + location prefs)
You can tune:
- `titles_target`: title keywords (high weight)
- `skills_have`, `skills_want`, `bonus_keywords`
- `preferred_locations_tier1`, `preferred_locations_tier2`

## How “Today vs Backlog” Works (State logic)

- On each run, every job gets a stable `id`.
- The first time an `id` is seen, we record `first_seen_date_utc` in `state.json`.
- Same-day reruns do **not** change `first_seen_date_utc`, so “today” stays consistent even if you run multiple times.
- “Backlog” is jobs with `first_seen_date_utc < today` and `status` not in `{applied, ignored, closed}`.

## Workflow

The default GitHub Actions workflow runs daily and on-demand:
- Fetch → Score → Triage → Commit outputs

Cron note:
- GitHub schedules use **UTC**. If you want Boulder time (America/Denver), convert MT → UTC in the cron.

## Development / Local Run (optional)

```bash
python ai-career/scripts/fetch_jobs.py
python ai-career/scripts/score_jobs.py
python ai-career/scripts/triage_jobs.py
```
# Attribution

This repository is a fork of anthropics/knowledge-work-plugins.
It includes an additional "ai-career" workflow plugin and job pipeline contributed by the fork owner.

Upstream project:
- https://github.com/anthropics/knowledge-work-plugins

License:
- This fork remains licensed under the Apache License 2.0 (see the root LICENSE file).
