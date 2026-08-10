"""
Thin wrapper around the JPlag jar: runs it against a submissions folder
and parses its .jplag result (a zip of JSON files) into plain Python
data the rest of the backend can work with.
"""
import json
import subprocess
import zipfile
from pathlib import Path


class JPlagError(Exception):
    pass


def run_jplag(jar_path: Path, submissions_dir: Path, language: str, output_dir: Path) -> dict:
    """
    Runs JPlag on submissions_dir and returns:
        {"comparisons": [ {a, b, similarity, matches: [...] }, ... ]}
    similarity is 0-100 (JPlag reports 0-1, we scale it here).
    """
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    result_base = output_dir  # JPlag appends .jplag itself
    result_zip = output_dir.with_suffix(".jplag")

    cmd = [
        "java", "-jar", str(jar_path),
        str(submissions_dir),
        "-l", language,
        "--mode", "RUN",
        "-r", str(result_base),
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)

    if not result_zip.exists():
        raise JPlagError(
            "JPlag did not produce a result file. This usually means the "
            "language didn't match the files (e.g. report files that "
            "aren't plain .txt), or there weren't enough valid submissions.\n\n"
            f"JPlag said:\n{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}"
        )

    extract_dir = output_dir.parent / "extracted"
    with zipfile.ZipFile(result_zip) as zf:
        zf.extractall(extract_dir)

    top_file = extract_dir / "topComparisons.json"
    if not top_file.exists():
        raise JPlagError("JPlag ran, but topComparisons.json was missing from its output.")

    with open(top_file) as f:
        top = json.load(f)

    comparisons = []
    for entry in top:
        sims = entry.get("similarities", {})
        avg = sims.get("AVG", 0.0)
        comparisons.append({
            "a": entry["firstSubmission"],
            "b": entry["secondSubmission"],
            "similarity": round(avg * 100, 1),
            "matches": _load_matches(extract_dir, entry["firstSubmission"], entry["secondSubmission"]),
        })

    return {"comparisons": comparisons}


def _load_matches(extract_dir: Path, a: str, b: str) -> list:
    """
    Pulls matched fragments out of JPlag's per-pair comparison file.
    Returns full line ranges (start+end for both files) so the frontend
    can render a proper side-by-side diff view with highlights.
    """
    candidates = [
        extract_dir / "comparisons" / f"{a}-{b}.json",
        extract_dir / "comparisons" / f"{b}-{a}.json",
    ]
    for path in candidates:
        if path.exists():
            try:
                with open(path) as f:
                    data = json.load(f)
                matches = data.get("matches", [])[:20]
                result = []
                for m in matches:
                    a_start = m.get("startInFirst", {}).get("line")
                    b_start = m.get("startInSecond", {}).get("line")
                    a_length = m.get("lengthOfFirst", 0)
                    b_length = m.get("lengthOfSecond", a_length)
                    a_file = m.get("fileInFirst") or m.get("firstFileName") or m.get("firstFile")
                    b_file = m.get("fileInSecond") or m.get("secondFileName") or m.get("secondFile")
                    result.append({
                        "aFile": a_file,
                        "bFile": b_file,
                        "aStartLine": a_start,
                        "aEndLine": (a_start + a_length - 1) if a_start and a_length else a_start,
                        "bStartLine": b_start,
                        "bEndLine": (b_start + b_length - 1) if b_start and b_length else b_start,
                        "aLines": a_start,
                        "bLines": b_start,
                        "length": a_length,
                    })
                return result
            except Exception:
                return []
    return []

