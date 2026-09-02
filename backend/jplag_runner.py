"""
Thin wrapper around the JPlag jar: runs it against a submissions folder
and parses its result (.zip or .jplag of JSON files) into plain Python
data the rest of the backend can work with.
"""
import json
import shutil
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
    result_base = output_dir  # JPlag appends .zip or .jplag itself

    cmd = [
        "java", "-Xmx512m",   # cap heap — Render free tier only has ~512 MB total
        "-jar", str(jar_path),
        str(submissions_dir),
        "-l", language,
        "--mode", "RUN",
        "-r", str(result_base),
        "-m", "0.0",   # include ALL pairs regardless of similarity score (supported in all JPlag versions)
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)

    # Surface real JPlag errors immediately — before looking for the zip.
    # Without this check a crash (OOM, bad args, no valid files) is silently
    # swallowed and surfaces as the misleading "topComparisons.json missing" error.
    if proc.returncode != 0:
        raise JPlagError(
            f"JPlag exited with code {proc.returncode}.\n\n"
            f"stdout:\n{proc.stdout[-2000:]}\n"
            f"stderr:\n{proc.stderr[-2000:]}"
        )

    # Find the output zip file (.zip or .jplag or result.zip)
    result_zip = None
    possible_paths = [
        output_dir.with_suffix(".zip"),
        output_dir.with_suffix(".jplag"),
        output_dir / "result.zip",
        output_dir / "result.jplag",
        output_dir.parent / "result.zip",
        output_dir.parent / "result.jplag",
        output_dir.parent / f"{output_dir.name}.zip",
        output_dir.parent / f"{output_dir.name}.jplag",
    ]

    for p in possible_paths:
        if p.exists() and p.is_file():
            result_zip = p
            break

    if not result_zip:
        # Fallback search in output_dir and parent directory for any zip/jplag file
        for sdir in [output_dir.parent, output_dir]:
            if sdir.exists():
                zips = list(sdir.glob("*.zip")) + list(sdir.glob("*.jplag"))
                if zips:
                    result_zip = zips[0]
                    break

    if not result_zip or not result_zip.exists():
        raise JPlagError(
            "JPlag did not produce a result file. This usually means the "
            "language didn't match the files (e.g. report files that "
            "aren't plain .txt), or there weren't enough valid submissions.\n\n"
            f"JPlag stdout:\n{proc.stdout[-1500:]}\n"
            f"JPlag stderr:\n{proc.stderr[-1500:]}"
        )

    extract_dir = output_dir.parent / "extracted"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(result_zip) as zf:
        zf.extractall(extract_dir)

    def _get_sub_id(val) -> str:
        if isinstance(val, str):
            return val
        if isinstance(val, dict):
            return str(val.get("name") or val.get("id") or val.get("submissionId") or "")
        return str(val) if val else ""

    top = []
    top_file = extract_dir / "topComparisons.json"
    overview_file = extract_dir / "overview.json"

    def _extract_top_from_dict(d: dict) -> list:
        if not isinstance(d, dict):
            return []
        for key in ["topComparisons", "comparisons", "results", "pairwiseComparisons"]:
            if key in d and isinstance(d[key], list):
                return d[key]
        if "metrics" in d and isinstance(d["metrics"], dict):
            for m_val in d["metrics"].values():
                if isinstance(m_val, dict):
                    res = _extract_top_from_dict(m_val)
                    if res:
                        return res
        return []

    if top_file.exists():
        try:
            with open(top_file, encoding="utf-8", errors="replace") as f:
                data = json.load(f)
                if isinstance(data, list):
                    top = data
                elif isinstance(data, dict):
                    top = _extract_top_from_dict(data)
        except Exception:
            pass

    if not top and overview_file.exists():
        try:
            with open(overview_file, encoding="utf-8", errors="replace") as f:
                data = json.load(f)
                if isinstance(data, list):
                    top = data
                elif isinstance(data, dict):
                    top = _extract_top_from_dict(data)
        except Exception:
            pass

    if not top:
        # Search recursively for topComparisons.json, overview.json, or any list/dict with comparisons
        for json_file in extract_dir.rglob("*.json"):
            if json_file.name.lower() in ["topcomparisons.json", "overview.json"]:
                try:
                    with open(json_file, encoding="utf-8", errors="replace") as f:
                        data = json.load(f)
                    if isinstance(data, list):
                        top = data
                        break
                    elif isinstance(data, dict):
                        top = _extract_top_from_dict(data)
                        if top:
                            break
                except Exception:
                    pass

    if not top:
        # Direct Pairwise JSON Fallback: Read individual studentA-studentB.json files (e.g. JPlag v5 root format)
        system_json_names = {
            "overview.json", "topcomparisons.json", "options.json",
            "submissionfileindex.json", "submissionmappings.json",
            "cluster.json", "distribution.json", "runinformation.json"
        }
        fallback_pairs = []
        for json_file in extract_dir.rglob("*.json"):
            if json_file.name.lower() not in system_json_names:
                try:
                    with open(json_file, encoding="utf-8", errors="replace") as f:
                        pdata = json.load(f)
                    if isinstance(pdata, dict):
                        first = (
                            _get_sub_id(pdata.get("firstSubmission")) or
                            _get_sub_id(pdata.get("first_submission")) or
                            _get_sub_id(pdata.get("firstSubmissionId")) or
                            _get_sub_id(pdata.get("submission1")) or
                            _get_sub_id(pdata.get("id1"))
                        )
                        second = (
                            _get_sub_id(pdata.get("secondSubmission")) or
                            _get_sub_id(pdata.get("second_submission")) or
                            _get_sub_id(pdata.get("secondSubmissionId")) or
                            _get_sub_id(pdata.get("submission2")) or
                            _get_sub_id(pdata.get("id2"))
                        )
                        if not first or not second:
                            if "-" in json_file.stem:
                                parts = json_file.stem.split("-", 1)
                                if len(parts) == 2:
                                    first, second = parts[0], parts[1]

                        if first and second:
                            pdata["firstSubmission"] = first
                            pdata["secondSubmission"] = second
                            fallback_pairs.append(pdata)
                except Exception:
                    pass
        if fallback_pairs:
            top = fallback_pairs

    if not top:
        # topComparisons.json / overview.json / pairwise files were not found or empty
        zip_contents = []
        try:
            with zipfile.ZipFile(result_zip) as _zf:
                zip_contents = _zf.namelist()
        except Exception:
            pass
        raise JPlagError(
            "JPlag ran but produced no comparisons.\n"
            "This usually means the submitted files were empty, too short to "
            "tokenise, or in the wrong format for the detected language.\n\n"
            f"Zip contents: {zip_contents}\n"
            f"JPlag stdout:\n{proc.stdout[-1000:]}\n"
            f"JPlag stderr:\n{proc.stderr[-1000:]}"
        )

    comparisons = []
    for entry in top:
        sims = entry.get("similarities", {})
        if isinstance(sims, dict):
            avg = sims.get("AVG", sims.get("MAX", 0.0))
        elif isinstance(sims, (int, float)):
            avg = float(sims)
        else:
            avg = entry.get("similarity", entry.get("avgSimilarity", 0.0))

        first = _get_sub_id(entry.get("firstSubmission") or entry.get("first_submission") or entry.get("firstSubmissionId") or entry.get("submission1") or entry.get("id1"))
        second = _get_sub_id(entry.get("secondSubmission") or entry.get("second_submission") or entry.get("secondSubmissionId") or entry.get("submission2") or entry.get("id2"))

        matches = entry.get("matches") or _load_matches(extract_dir, first, second)

        if first and second:
            comparisons.append({
                "a": first,
                "b": second,
                "similarity": round(avg * 100, 1) if avg <= 1.0 else round(avg, 1),
                "matches": matches,
            })

    analyzed_students = set()
    sub_file_index = extract_dir / "submissionFileIndex.json"
    sub_mappings = extract_dir / "submissionMappings.json"

    if sub_file_index.exists():
        try:
            with open(sub_file_index, encoding="utf-8", errors="replace") as f:
                data = json.load(f)
                file_indexes = data.get("fileIndexes", {})
                for stud_id, files in file_indexes.items():
                    if files:
                        analyzed_students.add(stud_id)
        except Exception:
            pass

    if sub_mappings.exists() and not analyzed_students:
        try:
            with open(sub_mappings, encoding="utf-8", errors="replace") as f:
                data = json.load(f)
                sub_ids = data.get("submissionIds", {})
                analyzed_students.update(sub_ids.keys())
        except Exception:
            pass

    if not analyzed_students:
        for entry in top:
            a = entry.get("firstSubmission", entry.get("first_submission", ""))
            b = entry.get("secondSubmission", entry.get("second_submission", ""))
            if a:
                analyzed_students.add(a)
            if b:
                analyzed_students.add(b)

    all_student_folders = set(p.name for p in submissions_dir.iterdir() if p.is_dir())
    unparsed_students = sorted(list(all_student_folders - analyzed_students))
    skipped_students_details = [{"name": s, "reason": "No valid tokenized source lines found"} for s in unparsed_students]

    return {
        "comparisons": comparisons,
        "analyzedStudents": sorted(list(analyzed_students)),
        "skippedStudents": skipped_students_details,
    }


def run_jplag_grouped(jar_path: Path, submissions_dir: Path, output_dir: Path, mode: str = "code", initial_skipped: list = None) -> dict:
    """
    Groups student submissions by their detected language, runs JPlag per group,
    and merges the resulting comparisons, analyzed students, and detailed skipped students.
    """
    from language_detector import detect_language
    
    skipped_records = []
    if initial_skipped:
        for s in initial_skipped:
            if isinstance(s, dict):
                skipped_records.append(s)
            else:
                skipped_records.append({"name": str(s), "reason": "Skipped during fetch"})

    student_folders = [p for p in submissions_dir.iterdir() if p.is_dir()]
    
    if mode == "report":
        if len(student_folders) < 2:
            for sf in student_folders:
                skipped_records.append({"name": sf.name, "reason": "Fewer than 2 student reports available for comparison"})
            return {"comparisons": [], "analyzedStudents": [sf.name for sf in student_folders], "skippedStudents": skipped_records}
        
        res = run_jplag(jar_path, submissions_dir, "text", output_dir / "report_result")
        res["skippedStudents"] = skipped_records + res.get("skippedStudents", [])
        return res

    # Mode is "code": Detect language per student folder
    lang_groups = {}
    for sf in student_folders:
        lang = detect_language(sf)
        if not lang:
            skipped_records.append({"name": sf.name, "reason": "No supported source code files found"})
        else:
            lang_groups.setdefault(lang, []).append(sf)

    all_comparisons = []
    analyzed_set = set()

    for lang, folders in lang_groups.items():
        if len(folders) < 2:
            for sf in folders:
                skipped_records.append({"name": sf.name, "reason": f"Only 1 student submitted {lang} code (minimum 2 needed for comparison)"})
            continue

        # Prepare sub-submissions directory for this language group
        group_sub_dir = output_dir.parent / f"group_sub_{lang}"
        if group_sub_dir.exists():
            shutil.rmtree(group_sub_dir)
        group_sub_dir.mkdir(parents=True, exist_ok=True)

        for sf in folders:
            shutil.copytree(sf, group_sub_dir / sf.name)

        group_out_dir = output_dir / f"group_res_{lang}"
        try:
            res = run_jplag(jar_path, group_sub_dir, lang, group_out_dir)
            all_comparisons.extend(res.get("comparisons", []))
            analyzed_set.update(res.get("analyzedStudents", []))
            for sk in res.get("skippedStudents", []):
                skipped_records.append(sk)
        except Exception as e:
            for sf in folders:
                skipped_records.append({"name": sf.name, "reason": f"JPlag failed for {lang}: {str(e)}"})

    return {
        "comparisons": all_comparisons,
        "analyzedStudents": sorted(list(analyzed_set)),
        "skippedStudents": skipped_records,
    }


def _load_matches(extract_dir: Path, a: str, b: str) -> list:
    """
    Pulls matched fragments out of JPlag's per-pair comparison file.
    Returns full line ranges (start+end for both files) so the frontend
    can render a proper side-by-side diff view with highlights.
    """
    candidates = [
        extract_dir / "comparisons" / f"{a}-{b}.json",
        extract_dir / "comparisons" / f"{b}-{a}.json",
        extract_dir / f"{a}-{b}.json",
        extract_dir / f"{b}-{a}.json",
    ]

    found_path = None
    for path in candidates:
        if path.exists():
            found_path = path
            break

    if not found_path and a and b:
        # Search recursively for comparison JSON files matching the student pair names
        for path in extract_dir.rglob("*.json"):
            filename = path.stem
            if a in filename and b in filename:
                found_path = path
                break

    if found_path:
        try:
            with open(found_path, encoding="utf-8", errors="replace") as f:
                data = json.load(f)
            matches = data.get("matches", [])
            result = []
            for m in matches:
                a_start = m.get("startInFirst", {}).get("line") or m.get("firstLine")
                b_start = m.get("startInSecond", {}).get("line") or m.get("secondLine")
                a_length = m.get("lengthOfFirst", 0) or m.get("length", 0)
                b_length = m.get("lengthOfSecond", a_length) or a_length
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

