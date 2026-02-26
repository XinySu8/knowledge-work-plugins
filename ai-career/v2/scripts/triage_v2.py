import os
import argparse
from typing import Dict, Any, List
import yaml
import hashlib
from datetime import datetime, timezone

from utils_v2 import read_json, write_json, clean_text, job_uid


def load_yaml(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def ensure_parent(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)


def sha1_file(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def format_md_item(r: Dict[str, Any]) -> str:
    title = r.get("title") or ""
    company = r.get("company") or ""
    loc = r.get("location") or ""
    url = r.get("url") or ""
    final_score = r.get("final_score", 0.0)
    hard_gate = r.get("hard_gate", {})
    reason = r.get("final_reason", "")

    must_hits = ", ".join((r.get("hard", {}).get("must_hits") or [])[:10])
    nice_hits = ", ".join((r.get("hard", {}).get("nice_hits") or [])[:10])

    gate_str = ""
    if hard_gate.get("hit"):
        gate_str = f" [HARD-GATE: {hard_gate.get('reason','')}]"

    return (
        f"- {company} | {title} | {loc} | score={final_score:.3f}{gate_str}\n"
        f"  - url: {url}\n"
        f"  - must_hits: {must_hits if must_hits else 'None'}\n"
        f"  - nice_hits: {nice_hits if nice_hits else 'None'}\n"
        f"  - reason: {reason}\n"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="ai-career/v2/config/scoring.yaml")
    args = ap.parse_args()

    cfg_path = args.config
    cfg = load_yaml(cfg_path)
    io = cfg["io"]
    engine_name = cfg.get("engine_name", "AutoJob-Agent Feeder")
    config_digest = sha1_file(cfg_path)

    scored_doc = read_json(io["scored_jobs_json"])
    scored = scored_doc.get("scored_jobs", [])

    tri = cfg["triage"]
    th_apply = float(tri["thresholds"]["apply"])
    th_maybe = float(tri["thresholds"]["maybe"])

    top_apply = int(tri["topN"]["apply"])
    top_maybe = int(tri["topN"]["maybe"])
    top_total = int(tri["candidates"]["topN_total"])

    jd_excerpt_chars = int(cfg["semantic_scoring"].get("jd_excerpt_chars", 600))

    scored_sorted = sorted(scored, key=lambda x: float(x.get("final_score", 0.0)), reverse=True)

    apply_list: List[Dict[str, Any]] = []
    maybe_list: List[Dict[str, Any]] = []
    skip_list: List[Dict[str, Any]] = []

    for r in scored_sorted:
        if r.get("hard_gate", {}).get("hit"):
            skip_list.append(r)
            continue
        s = float(r.get("final_score", 0.0))
        if s >= th_apply:
            apply_list.append(r)
        elif s >= th_maybe:
            maybe_list.append(r)
        else:
            skip_list.append(r)

    apply_list = apply_list[:top_apply]
    maybe_list = maybe_list[:top_maybe]

    apply_md = "# APPLY\n\n" + "\n".join(format_md_item(r) for r in apply_list) + "\n"
    maybe_md = "# MAYBE\n\n" + "\n".join(format_md_item(r) for r in maybe_list) + "\n"
    skip_md = "# SKIP (with reasons)\n\n" + "\n".join(format_md_item(r) for r in skip_list) + "\n"

    for path, content in [(io["apply_md"], apply_md), (io["maybe_md"], maybe_md), (io["skip_md"], skip_md)]:
        ensure_parent(path)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    jobs_doc = read_json(cfg["io"]["jobs_json"])
    jobs = jobs_doc.get("jobs", [])
    by_uid = {job_uid(j): j for j in jobs}

    combined = sorted(apply_list + maybe_list, key=lambda x: float(x.get("final_score", 0.0)), reverse=True)[:top_total]

    candidates = []
    for r in combined:
        uid = r.get("job_uid")
        j = by_uid.get(str(uid), {})

        excerpt_src = clean_text(j.get("content_plain", "") or "")
        excerpt = excerpt_src[:jd_excerpt_chars]

        candidates.append({
            # identifiers / provenance
            "job_uid": uid,
            "source": (j.get("source") or r.get("source")),
            "job_id": j.get("id"),
            "company": r.get("company"),
            "title": r.get("title"),
            "location": r.get("location"),
            "url": r.get("url"),
            "departments": (j.get("departments") or r.get("departments")),

            # time fields for incremental agent updates
            "created_at": (j.get("created_at") or r.get("created_at")),
            "updated_at": (j.get("updated_at") or r.get("updated_at")),

            # scores + signals
            "scores": {
                "hard_raw": r.get("hard", {}).get("raw"),
                "hard_norm": r.get("hard", {}).get("norm"),
                "semantic_raw": r.get("semantic", {}).get("raw"),
                "semantic_norm": r.get("semantic", {}).get("norm"),
                "final": r.get("final_score")
            },
            "signals": {
                "must_hits": r.get("hard", {}).get("must_hits"),
                "nice_hits": r.get("hard", {}).get("nice_hits"),
                "neg_hits": r.get("hard", {}).get("neg_hits"),
            },

            # debug / traceability
            "text_hash": r.get("text_hash"),
            "final_reason": r.get("final_reason"),
            "hard_gate": r.get("hard_gate"),

            # text payload for agent
            "jd_excerpt": excerpt
        })

    ensure_parent(io["candidates_json"])
    write_json(io["candidates_json"], {
        "meta": {
            "engine_name": engine_name,
            "version": 2,
            "generated_at": now_utc_iso(),
            "source_jobs_count": len(jobs),
            "candidates_count": len(candidates),
            "candidates_topN_total": top_total,
            "thresholds": {"apply": th_apply, "maybe": th_maybe},
            "config_digest": config_digest
        },
        "candidates": candidates
    })


if __name__ == "__main__":
    main()