"""
CSV Processing module for 10 Academy CSV exports.
Clones GitHub repos and downloads Google Docs/Drive links in parallel.
"""
import csv
import io
import json
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
        res = subprocess.run(
            ["git", "clone", "--depth", "1", github_url, str(student_dir)],
            check=True,
            capture_output=True,
            timeout=60,
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


def parse_csv_submissions(csv_content: str):
    """
    Parses CSV content and extracts student submissions, GitHub links, and Report links.
    Detects standard 10 Academy CSV columns automatically.
    """
    f = io.StringIO(csv_content)
    reader = csv.DictReader(f)
    rows = list(reader)

    if not rows:
        return {"rows": [], "code_tasks": {}, "report_tasks": {}, "detected_title": "CSV Batch"}

    original_fields = reader.fieldnames or []
    fieldnames = [fn.lower().strip() for fn in original_fields]

    name_col = None
    for orig, fn in zip(original_fields, fieldnames):
        if fn in ["trainee_id", "all_user_id", "user_id", "student_id", "student_name", "student", "full_name"]:
            name_col = orig
            break

    if not name_col:
        for orig, fn in zip(original_fields, fieldnames):
            if ("name" in fn or "user" in fn or "id" in fn) and "category" not in fn and "title" not in fn and "submission" not in fn:
                name_col = orig
                break

    if not name_col:
        name_col = original_fields[0]

    github_col = None
    report_col = None

    for orig, fn in zip(original_fields, fieldnames):
        if fn == "github_url" or "github" in fn:
            github_col = orig
        elif fn in ["report_url", "doc_link", "drive_link"] or "report" in fn or "drive" in fn:
            report_col = orig

    if not github_col:
        for orig, fn in zip(original_fields, fieldnames):
            if fn == "url":
                github_col = orig
                break

    if not report_col:
        for orig, fn in zip(original_fields, fieldnames):
            if fn == "url":
                report_col = orig
                break

    category_title = "CSV Batch"
    for r in rows:
        c_name = r.get("category_name") or r.get("title")
        if c_name and c_name.strip():
            category_title = c_name.strip()
            break

    code_tasks = {}
    report_tasks = {}
    submissions_dict = {}

    for idx, row in enumerate(rows, 1):
        raw_id = row.get(name_col, f"student_{idx}").strip()
        student_folder_name = f"student_{normalize_student_name(raw_id)}"

        if student_folder_name not in submissions_dict:
            submissions_dict[student_folder_name] = {
                "student_name": student_folder_name,
                "github_link": "",
                "doc_link": ""
            }

        gh = row.get(github_col, "").strip() if github_col else ""
        url_val = row.get("url", "").strip()
        if not gh and "github.com" in url_val.lower():
            gh = url_val

        if gh and "github.com" in gh.lower():
            submissions_dict[student_folder_name]["github_link"] = gh
            if student_folder_name not in code_tasks:
                code_tasks[student_folder_name] = gh

        rep = row.get(report_col, "").strip() if report_col else ""
        if not rep and ("docs.google.com" in url_val.lower() or "drive.google.com" in url_val.lower()):
            rep = url_val

        if rep and ("docs.google.com" in rep.lower() or "drive.google.com" in rep.lower()):
            submissions_dict[student_folder_name]["doc_link"] = rep
            if student_folder_name not in report_tasks:
                report_tasks[student_folder_name] = rep

    return {
        "rows": rows,
        "code_tasks": code_tasks,
        "report_tasks": report_tasks,
        "submissions_list": list(submissions_dict.values()),
        "detected_title": category_title
    }
