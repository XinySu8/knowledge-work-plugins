import re
import json
import hashlib
from typing import Dict, Any, List, Tuple

WHITESPACE_RE = re.compile(r"\s+")
CONTROL_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")

def read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def write_json(path: str, obj: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def clean_text(s: str) -> str:
    if not s:
        return ""
    s = CONTROL_RE.sub(" ", s)
    s = s.replace("\u00a0", " ")
    s = WHITESPACE_RE.sub(" ", s).strip()
    return s

def sha1_text(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()

def job_uid(job: Dict[str, Any]) -> str:
    if job.get("id"):
        return str(job["id"])
    parts = [
        str(job.get("source", "")),
        str(job.get("company", "")),
        str(job.get("title", "")),
        str(job.get("url", "")),
    ]
    return sha1_text("||".join(parts))

def compile_regex_list(patterns: List[str]) -> List[re.Pattern]:
    out = []
    for p in patterns or []:
        out.append(re.compile(p))
    return out

def contains_any_phrase(text_lower: str, phrases: List[str]) -> Tuple[bool, str]:
    for ph in phrases or []:
        ph_l = ph.lower().strip()
        if ph_l and ph_l in text_lower:
            return True, ph
    return False, ""

def matches_any_regex(text: str, regex_list: List[re.Pattern]) -> Tuple[bool, str]:
    for r in regex_list or []:
        if r.search(text):
            return True, r.pattern
    return False, ""

def keyword_score(text_lower: str, keyword_weights: Dict[str, float]) -> Tuple[float, List[str]]:
    score = 0.0
    hits = []
    for k, w in (keyword_weights or {}).items():
        k_l = k.lower().strip()
        if not k_l:
            continue
        if k_l in text_lower:
            score += float(w)
            hits.append(k)
    return score, hits

def minmax_norm(values: List[float]) -> List[float]:
    if not values:
        return []
    vmin = min(values)
    vmax = max(values)
    if vmax - vmin < 1e-9:
        return [0.5 for _ in values]
    return [(v - vmin) / (vmax - vmin) for v in values]