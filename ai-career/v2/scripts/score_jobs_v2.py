import os
import argparse
from typing import Dict, Any, List
import yaml

from sentence_transformers import SentenceTransformer
import numpy as np

from utils_v2 import (
    read_json, write_json, read_text, clean_text, sha1_text, job_uid,
    compile_regex_list, contains_any_phrase, matches_any_regex,
    keyword_score, minmax_norm
)

def cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = (np.linalg.norm(a) * np.linalg.norm(b)) + 1e-12
    return float(np.dot(a, b) / denom)

def load_yaml(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def ensure_parent(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="ai-career/v2/config/scoring.yaml")
    args = ap.parse_args()

    cfg = load_yaml(args.config)
    io = cfg["io"]

    jobs_doc = read_json(io["jobs_json"])
    jobs = jobs_doc.get("jobs", [])

    profile = clean_text(read_text("ai-career/v2/config/profile.md"))
    sem_cfg = cfg["semantic_scoring"]
    model_name = sem_cfg["model_name"]
    content_max_chars = int(sem_cfg.get("content_max_chars", 8000))

    cache_path = io["emb_cache_json"]
    ensure_parent(cache_path)
    cache = {}
    if os.path.exists(cache_path):
        cache = read_json(cache_path) or {}

    gate = cfg["hard_gate"]
    excl_phrases = gate.get("exclude_phrases", [])
    excl_regex = compile_regex_list(gate.get("exclude_regex", []))

    hard_cfg = cfg["hard_scoring"]
    must = hard_cfg.get("must_have_keywords", {})
    nice = hard_cfg.get("nice_to_have_keywords", {})
    neg = hard_cfg.get("negative_keywords", {})

    model = SentenceTransformer(model_name)

    profile_vec = model.encode([profile], normalize_embeddings=False)[0]
    profile_vec = np.array(profile_vec, dtype=np.float32)

    scored: List[Dict[str, Any]] = []

    semantic_raw_list = []
    hard_raw_list = []

    for job in jobs:
        uid = job_uid(job)
        text = clean_text(job.get("content_plain", "") or "")
        text = text[:content_max_chars]
        text_hash = sha1_text(text)

        text_lower = text.lower()

        ph_hit, ph = contains_any_phrase(text_lower, excl_phrases)
        rx_hit, rx = matches_any_regex(text, excl_regex)
        hard_gate_hit = ph_hit or rx_hit
        hard_gate_reason = ph if ph_hit else (rx if rx_hit else "")

        must_score, must_hits = keyword_score(text_lower, must)
        nice_score, nice_hits = keyword_score(text_lower, nice)
        neg_score, neg_hits = keyword_score(text_lower, neg)
        hard_raw = must_score + nice_score + neg_score

        sem_raw = 0.0
        reused = False

        item = cache.get(uid)
        if item and item.get("text_hash") == text_hash and item.get("vector"):
            sem_vec = np.array(item["vector"], dtype=np.float32)
            sem_raw = cosine(profile_vec, sem_vec)
            reused = True
        else:
            sem_vec = model.encode([text], normalize_embeddings=False)[0]
            sem_vec = np.array(sem_vec, dtype=np.float32)
            sem_raw = cosine(profile_vec, sem_vec)
            cache[uid] = {
                "text_hash": text_hash,
                "vector": sem_vec.tolist()
            }

        record = {
            "job_uid": uid,
            "source": job.get("source"),
            "company": job.get("company"),
            "title": job.get("title"),
            "location": job.get("location"),
            "url": job.get("url"),
            "updated_at": job.get("updated_at"),
            "created_at": job.get("created_at"),
            "departments": job.get("departments"),
            "hard_gate": {
                "hit": bool(hard_gate_hit),
                "reason": hard_gate_reason
            },
            "hard": {
                "raw": float(hard_raw),
                "must_hits": must_hits,
                "nice_hits": nice_hits,
                "neg_hits": neg_hits
            },
            "semantic": {
                "raw": float(sem_raw),
                "cache_reused": reused
            },
            "text_hash": text_hash
        }
        scored.append(record)

        if not hard_gate_hit:
            semantic_raw_list.append(float(sem_raw))
            hard_raw_list.append(float(hard_raw))

    sem_norm = minmax_norm(semantic_raw_list)
    hard_norm = minmax_norm(hard_raw_list)

    w_h = float(cfg["fusion"]["w_hard"])
    w_s = float(cfg["fusion"]["w_semantic"])

    idx_sem = 0
    idx_hard = 0
    for r in scored:
        if r["hard_gate"]["hit"]:
            r["hard"]["norm"] = 0.0
            r["semantic"]["norm"] = 0.0
            r["final_score"] = 0.0
            r["final_reason"] = f"Hard gate hit: {r['hard_gate']['reason']}"
            continue

        r["hard"]["norm"] = float(hard_norm[idx_hard])
        r["semantic"]["norm"] = float(sem_norm[idx_sem])

        final = w_h * r["hard"]["norm"] + w_s * r["semantic"]["norm"]
        r["final_score"] = float(final)
        r["final_reason"] = (
            f"Fusion: {w_h:.2f}*hard_norm + {w_s:.2f}*semantic_norm; "
            f"must_hits={len(r['hard']['must_hits'])}, nice_hits={len(r['hard']['nice_hits'])}"
        )

        idx_hard += 1
        idx_sem += 1

    out_path = io["scored_jobs_json"]
    ensure_parent(out_path)
    write_json(out_path, {
        "meta": {
            "version": 2,
            "model_name": model_name,
            "jobs_total": len(jobs),
            "jobs_scored": len(scored),
            "non_gated_count": len(hard_raw_list)
        },
        "scored_jobs": scored
    })

    write_json(cache_path, cache)

if __name__ == "__main__":
    main()