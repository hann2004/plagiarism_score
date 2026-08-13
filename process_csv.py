#!/usr/bin/env python3
"""
Fast Multi-threaded CSV Processor for 10 Academy CSV exports.
Creates code_submissions.zip and report_submissions.zip concurrently in parallel.
"""
import csv
import json
import re
import shutil
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import requests


def normalize_student_name(name: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_\-\s]", "", str(name))
    return clean.strip().replace(" ", "_").lower()


def convert_ipynb_in_folder(folder: Path):
    for ipynb_file in list(folder.rglob("*.ipynb")):
        if ipynb_file.name.startswith(".") or ".ipynb_checkpoints" in str(ipynb_file):
            continue
        try:
            with open(ipynb_file, "r", encoding="utf-8", errors="replace") as f:
                nb_data = json.load(f)
            code_lines = []
            for cell in nb_data.get("cells", []):
                if cell.get("cell_type") == "code":
                    source = cell.get("source", [])
                    if isinstance(source, list):
                        code_lines.extend(source)
                    elif isinstance(source, str):
                        code_lines.append(source)
                    code_lines.append("\n\n")
            if code_lines:
                py_file = ipynb_file.with_suffix(".py")
                py_file.write_text("".join(code_lines), encoding="utf-8")
        except Exception:
            pass


def convert_docx_in_folder(folder: Path):
    try:
        import docx
    except ImportError:
        return

    for docx_file in list(folder.rglob("*.docx")):
        if docx_file.name.startswith("~$"):
            continue
        try:
            doc = docx.Document(docx_file)
            full_text = []
            for para in doc.paragraphs:
                if para.text.strip():
                    full_text.append(para.text)
            for table in doc.tables:
                for row in table.rows:
                    row_text = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
                    if row_text:
                        full_text.append(row_text)

            txt_path = docx_file.with_suffix(".txt")
            txt_path.write_text("\n".join(full_text), encoding="utf-8")
        except Exception:
            pass


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


def clone_github_repo(student_folder_name: str, github_url: str, code_dir: Path):
    student_dir = code_dir / student_folder_name
    if student_dir.exists():
        return True
    try:
        res = subprocess.run(
            ["git", "clone", "--depth", "1", github_url, str(student_dir)],
            check=True,
            capture_output=True,
            timeout=20,
        )
        git_folder = student_dir / ".git"
        if git_folder.exists():
            shutil.rmtree(git_folder)

        convert_ipynb_in_folder(student_dir)
        return True
    except Exception:
        if student_dir.exists():
            shutil.rmtree(student_dir)
        return False


def process_csv(csv_path: str):
    path = Path(csv_path)
    if not path.exists():
        print(f"Error: File '{csv_path}' not found.")
        sys.exit(1)

    with open(path, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        print("Error: CSV file is empty.")
        sys.exit(1)

    print(f"📄 Loaded {len(rows)} submission records from '{csv_path}'.\n")

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

    print(f"Detected Columns:")
    print(f" - Trainee ID Column:   '{name_col}'")
    print(f" - GitHub Link Column:  '{github_col}'")
    print(f" - Report Link Column:  '{report_col}'\n")

    # Collect tasks
    code_tasks = {}
    report_tasks = {}

    for idx, row in enumerate(rows, 1):
        raw_id = row.get(name_col, f"student_{idx}").strip()
        student_folder_name = f"student_{normalize_student_name(raw_id)}"

        gh = row.get(github_col, "").strip() if github_col else ""
        if gh and "github.com" in gh.lower() and student_folder_name not in code_tasks:
            code_tasks[student_folder_name] = gh

        rep = row.get(report_col, "").strip() if report_col else ""
        if rep and ("docs.google.com" in rep.lower() or "drive.google.com" in rep.lower()) and student_folder_name not in report_tasks:
            report_tasks[student_folder_name] = rep

    print(f"Found {len(code_tasks)} unique GitHub code repositories to clone.")
    print(f"Found {len(report_tasks)} unique Google Doc/Drive reports to download.\n")

    # 1. PROCESS CODE SUBMISSIONS (PARALLEL 12 WORKERS)
    code_dir = Path("temp_code_submissions")
    if code_dir.exists():
        shutil.rmtree(code_dir)
    code_dir.mkdir(parents=True)

    success_code = 0
    print("⚡ [1/2] Parallel cloning GitHub repos (12 workers)...")
    with ThreadPoolExecutor(max_workers=12) as executor:
        future_to_student = {
            executor.submit(clone_github_repo, s_name, url, code_dir): s_name
            for s_name, url in code_tasks.items()
        }
        for future in as_completed(future_to_student):
            s_name = future_to_student[future]
            try:
                ok = future.result()
                if ok:
                    success_code += 1
                    print(f"  ✅ Cloned {s_name} ({success_code}/{len(code_tasks)})")
                else:
                    print(f"  ⚠️ Skipping {s_name} (private or deleted repo)")
            except Exception:
                pass

    if success_code > 0:
        code_zip = Path("code_submissions.zip")
        # Snapshot file list BEFORE opening the zip to avoid race conditions
        # where git may still be renaming files in another thread.
        code_files = [(item, item.relative_to(code_dir)) for item in sorted(code_dir.rglob("*")) if item.is_file()]
        with zipfile.ZipFile(code_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for item, arcname in code_files:
                try:
                    zf.write(str(item), str(arcname))
                except Exception as e:
                    print(f"  ⚠️ Skipping {arcname}: {e}")
        shutil.rmtree(code_dir)
        print(f"\n🎉 Created '{code_zip.resolve()}' with {success_code} student code folders.\n")
    else:
        print("\n⚠️ No valid GitHub code repositories were cloned.\n")

    # 2. PROCESS REPORT SUBMISSIONS (PARALLEL 12 WORKERS)
    report_dir = Path("temp_report_submissions")
    if report_dir.exists():
        shutil.rmtree(report_dir)
    report_dir.mkdir(parents=True)

    success_report = 0
    print("⚡ [2/2] Parallel downloading Google Doc/Drive reports (12 workers)...")

    def download_job(s_name, url):
        s_dir = report_dir / s_name
        ok = download_google_doc_or_drive(url, s_dir, s_name)
        if ok:
            convert_docx_in_folder(s_dir)
            return True
        return False

    with ThreadPoolExecutor(max_workers=12) as executor:
        future_to_student = {
            executor.submit(download_job, s_name, url): s_name
            for s_name, url in report_tasks.items()
        }
        for future in as_completed(future_to_student):
            s_name = future_to_student[future]
            try:
                ok = future.result()
                if ok:
                    success_report += 1
                    print(f"  ✅ Downloaded report for {s_name} ({success_report}/{len(report_tasks)})")
            except Exception:
                pass

    if success_report > 0:
        report_zip = Path("report_submissions.zip")
        # Snapshot file list BEFORE opening the zip to avoid race conditions.
        report_files = [(item, item.relative_to(report_dir)) for item in sorted(report_dir.rglob("*")) if item.is_file()]
        with zipfile.ZipFile(report_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for item, arcname in report_files:
                try:
                    zf.write(str(item), str(arcname))
                except Exception as e:
                    print(f"  ⚠️ Skipping {arcname}: {e}")
        shutil.rmtree(report_dir)
        print(f"\n🎉 Created '{report_zip.resolve()}' with {success_report} student report folders.\n")
    else:
        print("\n⚠️ No valid Google Doc/Drive reports were downloaded.\n")

    print("==================================================")
    print("🎉 ALL DONE!")
    print(f" • Upload 'code_submissions.zip' ({success_code} students) for Code mode")
    print(f" • Upload 'report_submissions.zip' ({success_report} students) for Report mode")
    print("==================================================")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python process_csv.py <path_to_instructor_csv>")
        sys.exit(1)
    process_csv(sys.argv[1])
