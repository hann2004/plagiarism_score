"""
CSV Processing module for 10 Academy CSV exports.
Clones GitHub repos and downloads Google Docs/Drive links in parallel.

The parse_csv_submissions function mirrors the robust logic in process_csv.py
so that the same CSV file that works on the command-line also works when
uploaded through the UI.
"""
import csv
import io
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import requests

from docx_converter import convert_docx_to_txt
from ipynb_converter import convert_ipynb_to_py


def normalize_student_name(name: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_\-\s]", "", str(name))
    return clean.strip().replace(" ", "_").lower()


def extract_drive_id(url: str) -> str:
    match = re.search(r'/(?:document/d/|file/d/|id=)([a-zA-Z0-9-_]+)', url)
    return match.group(1) if match else None


def download_google_doc_or_drive(url: str, student_dir: Path, student_name: str) -> bool:
    doc_id = extract_drive_id(url)
    if not doc_id:
        return False

    student_dir.mkdir(parents=True, exist_ok=True)

    export_url = f"https://docs.google.com/document/d/{doc_id}/export?format=docx"
    try:
        resp = requests.get(export_url, timeout=12)
        if resp.status_code == 200 and "text/html" not in resp.headers.get("Content-Type", ""):
            dest = student_dir / f"report_{student_name}.docx"
            dest.write_bytes(resp.content)
            return True
    except Exception:
        pass

    drive_url = f"https://drive.google.com/uc?export=download&id={doc_id}"
    try:
        resp = requests.get(drive_url, timeout=12)
        if resp.status_code == 200 and "text/html" not in resp.headers.get("Content-Type", ""):
            dest = student_dir / f"report_{student_name}.txt"
            dest.write_bytes(resp.content)
            return True
    except Exception:
        pass

    return False


def clone_github_repo(student_folder_name: str, github_url: str, code_dir: Path) -> bool:
    student_dir = code_dir / student_folder_name
    if student_dir.exists():
        return True
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", github_url, str(student_dir)],
            check=True,
            capture_output=True,
            timeout=120,
        )
        git_folder = student_dir / ".git"
        if git_folder.exists():
            shutil.rmtree(git_folder)

        convert_ipynb_to_py(student_dir)
        return True
    except Exception:
        if student_dir.exists():
            shutil.rmtree(student_dir)
        return False


def parse_csv_submissions(csv_content: str) -> dict:
    """
    Parses CSV content and extracts student submissions, GitHub links, and Report links.
    Detects standard 10 Academy CSV columns automatically.

    This uses the same robust detection logic as process_csv.py:
    - Finds the student ID column by checking known column names first, then fuzzy fallback.
    - Finds the GitHub URL column and report/drive column by name matching.
    - Falls back to scanning ALL columns per-row for URLs if dedicated columns aren't found.
    - Routes each URL to the correct bucket (code vs report) based on the URL domain.
    """
    f = io.StringIO(csv_content)
    reader = csv.DictReader(f)
    rows = list(reader)

    if not rows:
        return {
            "rows": [],
            "code_tasks": {},
            "report_tasks": {},
            "submissions_list": [],
            "detected_title": "CSV Batch",
        }

    original_fields = reader.fieldnames or []
    fieldnames = [fn.lower().strip() for fn in original_fields]

    # ── Student name / ID column detection ──────────────────────────────────
    name_col = None
    # Exact known names first
    for orig, fn in zip(original_fields, fieldnames):
        if fn in ["trainee_id", "all_user_id", "user_id", "student_id",
                  "student_name", "student", "full_name"]:
            name_col = orig
            break

    # Fuzzy fallback: any column that looks like a name/user/id field
    if not name_col:
        for orig, fn in zip(original_fields, fieldnames):
            if (
                ("name" in fn or "user" in fn or "id" in fn)
                and "category" not in fn
                and "title" not in fn
                and "submission" not in fn
                and "url" not in fn
            ):
                name_col = orig
                break

    # Last resort: first column
    if not name_col:
        name_col = original_fields[0]

    # ── GitHub / Report column detection ────────────────────────────────────
    github_col = None
    report_col = None
    interim_report_col = None
    non_technical_col = None

    for orig, fn in zip(original_fields, fieldnames):
        if fn == "github_url" or "github" in fn:
            github_col = orig
        elif ("interim" in fn and ("report" in fn or "submission" in fn or "doc" in fn or "drive" in fn or "url" in fn)):
            interim_report_col = orig
        elif fn in ["report_url", "doc_link", "drive_link"] or "report" in fn or "drive" in fn:
            if not non_technical_col:
                non_technical_col = orig

    # Prefer interim report link over non-technical report link
    report_col = interim_report_col or non_technical_col

    # Fallback: a column literally named "url" — we'll smart-route per row
    fallback_url_col = None
    if not github_col or not report_col:
        for orig, fn in zip(original_fields, fieldnames):
            if fn == "url":
                fallback_url_col = orig
                break
        # Also scan all columns for a URL-looking column name
        if not fallback_url_col:
            for orig, fn in zip(original_fields, fieldnames):
                if "url" in fn and orig != name_col:
                    fallback_url_col = orig
                    break

    # ── Group rows by category_name / title / assignment column if present ────
    category_col = None
    for orig, fn in zip(original_fields, fieldnames):
        if fn in ["category_name", "category", "title", "assignment", "task_name"]:
            category_col = orig
            break

    category_rows_map = {}
    if category_col:
        for row in rows:
            cat_val = (row.get(category_col) or "").strip()
            if not cat_val:
                cat_val = "CSV Batch"
            if cat_val not in category_rows_map:
                category_rows_map[cat_val] = []
            category_rows_map[cat_val].append(row)
    else:
        # Fallback title detection
        category_title = "CSV Batch"
        for r in rows:
            c_name = r.get("category_name") or r.get("title")
            if c_name and c_name.strip():
                category_title = c_name.strip()
                break
        category_rows_map[category_title] = rows

    category_groups = []
    for cat_name, cat_rows in category_rows_map.items():
        cat_code_tasks = {}
        cat_report_tasks = {}
        cat_submissions_dict = {}

        for idx, row in enumerate(cat_rows, 1):
            raw_id = (row.get(name_col) or f"student_{idx}").strip()
            clean_id = normalize_student_name(raw_id) or str(idx)
            student_folder_name = clean_id if clean_id.startswith("student_") else f"student_{clean_id}"

            if student_folder_name not in cat_submissions_dict:
                cat_submissions_dict[student_folder_name] = {
                    "student_name": raw_id,
                    "github_link": "",
                    "doc_link": "",
                }

            gh = (row.get(github_col) or "").strip() if github_col else ""
            rep = (row.get(report_col) or "").strip() if report_col else ""
            fallback_val = (row.get(fallback_url_col) or "").strip() if fallback_url_col else ""

            if fallback_val:
                if not gh and "github.com" in fallback_val.lower():
                    gh = fallback_val
                elif not rep and ("docs.google.com" in fallback_val.lower() or "drive.google.com" in fallback_val.lower()):
                    rep = fallback_val

            if not gh or not rep:
                for col_orig, col_fn in zip(original_fields, fieldnames):
                    if col_orig == name_col:
                        continue
                    val = (row.get(col_orig) or "").strip()
                    if not val:
                        continue
                    if not gh and "github.com" in val.lower():
                        gh = val
                    elif not rep and ("docs.google.com" in val.lower() or "drive.google.com" in val.lower()):
                        rep = val

            if gh and "github.com" in gh.lower():
                cat_submissions_dict[student_folder_name]["github_link"] = gh
                if student_folder_name not in cat_code_tasks:
                    cat_code_tasks[student_folder_name] = gh

            if rep and ("docs.google.com" in rep.lower() or "drive.google.com" in rep.lower()):
                cat_submissions_dict[student_folder_name]["doc_link"] = rep
                if student_folder_name not in cat_report_tasks:
                    cat_report_tasks[student_folder_name] = rep

        clean_cat_id = re.sub(r'[^a-zA-Z0-9_]', '_', cat_name.lower()).strip('_')[:32]
        if not clean_cat_id:
            clean_cat_id = "csv_batch"

        category_groups.append({
            "category_name": cat_name,
            "batch_id": clean_cat_id,
            "batch_label": cat_name,
            "code_tasks": cat_code_tasks,
            "report_tasks": cat_report_tasks,
            "submissions_list": list(cat_submissions_dict.values())
        })

    return {
        "rows": rows,
        "category_groups": category_groups,
        "detected_title": category_groups[0]["category_name"] if category_groups else "CSV Batch"
    }
