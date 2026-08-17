"""
Integrity Observatory backend.

Cohorts (e.g. "KAIM Batch 7") contain batches (e.g. "Week 5"). Each batch
run compares one set of student submissions, either as code (JPlag,
per-language) or as reports (JPlag's text mode, plain .txt only).

Run with:
    uvicorn main:app --reload --port 8000

Requires jplag.jar to exist at ./jplag/jplag.jar, download it from
https://github.com/jplag/JPlag/releases (the *-jar-with-dependencies.jar
build) and place it there. See README.md.
"""
import shutil
import zipfile
import re
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from jplag_runner import run_jplag, JPlagError
from storage import Storage
from language_detector import detect_language
from docx_converter import convert_docx_to_txt
from ipynb_converter import convert_ipynb_to_py
from pdf_converter import convert_pdf_to_txt
from fetcher import fetch_submissions
from csv_processor import parse_csv_submissions, clone_github_repo, download_google_doc_or_drive
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_DIR = Path(__file__).parent
PROJECT_ROOT = BASE_DIR.parent

# Store working files and data outside backend/ so uvicorn --reload does not restart mid-analysis
DATA_DIR = PROJECT_ROOT / "app_data"
WORK_DIR = PROJECT_ROOT / "app_work"
FILES_DIR = DATA_DIR / "files"
JPLAG_JAR = BASE_DIR / "jplag" / "jplag.jar"

DATA_DIR.mkdir(exist_ok=True)
WORK_DIR.mkdir(exist_ok=True)
FILES_DIR.mkdir(exist_ok=True)

# Copy any existing data or work files from legacy backend/ directories if present
old_data = BASE_DIR / "data"
old_work = BASE_DIR / "work"
if old_data.exists():
    for item in old_data.iterdir():
        dest = DATA_DIR / item.name
        if not dest.exists():
            try:
                if item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest)
            except Exception:
                pass

if old_work.exists():
    for item in old_work.iterdir():
        dest = WORK_DIR / item.name
        if not dest.exists():
            try:
                if item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest)
            except Exception:
                pass

storage = Storage(DATA_DIR)

app = FastAPI(title="Integrity Observatory API")

# Wide open for local development. Tighten this before this ever
# touches real student data on a real, internet-facing server.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/cohorts")
def list_cohorts():
    """{cohortId: {label, batches: {batchId: {label, studentCount, flaggedCount}}}}"""
    return storage.list_cohorts()


@app.get("/api/cohorts/{cohort_id}/batches/{batch_id}")
def get_batch(cohort_id: str, batch_id: str):
    batch = storage.get_batch(cohort_id, batch_id)
    if not batch:
        raise HTTPException(404, "No results yet for this batch.")
    # Strip per-pair match details from the list response — they are large (can be
    # tens of thousands of entries for big batches) and the frontend only needs them
    # when a specific pair is opened in the drawer, where they are returned by the
    # /pairs/{pair_id}/files endpoint.  Omitting them here reduces a 125-student
    # batch from ~10 MB to ~460 KB and prevents the request from timing out.
    lightweight_pairs = [
        {k: v for k, v in p.items() if k != "matches"}
        for p in batch.get("pairs", [])
    ]
    return {**batch, "pairs": lightweight_pairs}


@app.get("/api/cohorts/{cohort_id}/batches/{batch_id}/submissions")
def get_submissions(cohort_id: str, batch_id: str):
    subs = storage.get_submissions(cohort_id, batch_id)
    return {"count": len(subs), "submissions": subs}


@app.post("/api/cohorts/{cohort_id}/batches/{batch_id}/submissions")
def submit_link(
    cohort_id: str, batch_id: str,
    student_name: str = Form(...),
    github_link: str = Form(""),
    doc_link: str = Form("")
):
    if not student_name.strip():
        raise HTTPException(400, "Student name is required")
    storage.add_submission(cohort_id, batch_id, student_name.strip(), github_link.strip(), doc_link.strip())
    return {"ok": True}


@app.delete("/api/cohorts/{cohort_id}/batches/{batch_id}")
def delete_batch(cohort_id: str, batch_id: str):
    ok = storage.delete_batch(cohort_id, batch_id)
    if not ok:
        raise HTTPException(404, "Batch not found.")
    
    # Also clean up work directories if they exist
    for mode in ["code", "report"]:
        run_dir = WORK_DIR / f"{cohort_id}__{batch_id}__{mode}"
        if run_dir.exists():
            shutil.rmtree(run_dir)
            
    return {"ok": True}


@app.post("/api/cohorts/{cohort_id}/batches/{batch_id}/status")
def set_status(cohort_id: str, batch_id: str, pair_id: str = Form(...), status: str = Form(...)):
    ok = storage.set_pair_status(cohort_id, batch_id, pair_id, status)
    if not ok:
        raise HTTPException(404, "Pair not found.")
    return {"ok": True}


def _persist_student_files(submissions_dir: Path, cohort_id: str, batch_id: str, mode: str = "code"):
    """Copy student source files to a permanent location so the diff view can read them later.
    Saves to mode-specific path (e.g., cohort_batch_code vs cohort_batch_report) so running
    Report comparison does not wipe out saved Code files.
    """
    dest_mode = FILES_DIR / f"{cohort_id}__{batch_id}__{mode}"
    if dest_mode.exists():
        shutil.rmtree(dest_mode)
    dest_mode.mkdir(parents=True, exist_ok=True)
    
    dest_generic = FILES_DIR / f"{cohort_id}__{batch_id}"
    dest_generic.mkdir(parents=True, exist_ok=True)

    for student_dir in submissions_dir.iterdir():
        if student_dir.is_dir():
            shutil.copytree(student_dir, dest_mode / student_dir.name, dirs_exist_ok=True)
            shutil.copytree(student_dir, dest_generic / student_dir.name, dirs_exist_ok=True)


def _normalize_name(name: str) -> str:
    s = name.lower().replace("_", " ").replace("-", " ").strip()
    return re.sub(r'\b0+(\d+)', r'\1', s)


def _read_student_files(cohort_id: str, batch_id: str, student_name: str, mode: str = "code") -> dict:
    """Reads source files submitted by a student, filtered by mode.
    
    mode='code'   → returns source code files (excludes converted .txt reports)
    mode='report' → returns .txt files (the converted report text)
    """
    # Look first in mode-specific directory (FILES_DIR or WORK_DIR), then fallback to generic batch directory
    batch_dirs = [
        FILES_DIR / f"{cohort_id}__{batch_id}__{mode}",
        WORK_DIR / f"{cohort_id}__{batch_id}__{mode}" / "submissions",
        FILES_DIR / f"{cohort_id}__{batch_id}",
    ]
    
    batch_dir = None
    for bd in batch_dirs:
        if bd.exists():
            batch_dir = bd
            break

    if not batch_dir:
        return {"mainName": None, "content": None, "files": []}

    student_dir = batch_dir / student_name
    if not student_dir.exists():
        # Flexible folder match (e.g. "student 1" vs "student_01" vs "student_1_abebe_kebede")
        target_norm = _normalize_name(student_name)
        matched = None
        for folder in batch_dir.iterdir():
            if folder.is_dir():
                folder_norm = _normalize_name(folder.name)
                if folder_norm == target_norm or folder_norm in target_norm or target_norm in folder_norm:
                    matched = folder
                    break
        if matched:
            student_dir = matched
        else:
            return {"mainName": None, "content": None, "files": []}

    all_file_objects = []
    for f in sorted(student_dir.rglob("*")):
        if f.is_file() and not f.name.startswith("."):
            try:
                rel_path = str(f.relative_to(student_dir))
                text = f.read_text(errors="replace")
                all_file_objects.append({"path": rel_path, "content": text, "suffix": f.suffix.lower()})
            except Exception:
                pass

    if not all_file_objects:
        return {"mainName": None, "content": None, "files": []}

    found_files = []
    if mode == "report":
        # Report mode: prefer .txt / report files
        found_files = [item for item in all_file_objects if item["suffix"] == ".txt"]
        if not found_files:
            found_files = all_file_objects
    else:
        # Code mode: filter out report text files (.txt files that are converted reports)
        code_files = [
            item for item in all_file_objects 
            if item["suffix"] != ".txt" or not item["path"].lower().startswith("report")
        ]
        # Further refine: if code files exist (e.g. .py, .js, .java, etc.), exclude all .txt files
        strict_code = [item for item in all_file_objects if item["suffix"] != ".txt"]
        if strict_code:
            found_files = strict_code
        elif code_files:
            found_files = code_files
        else:
            return {
                "mainName": "No code files found",
                "content": "// No source code files (.py, .js, .java, etc.) found in this submission.\n// Only text/report files were uploaded for this batch.",
                "files": []
            }

    if len(found_files) == 1:
        return {
            "mainName": found_files[0]["path"],
            "content": found_files[0]["content"],
            "files": [{"path": item["path"], "content": item["content"]} for item in found_files]
        }

    combined_blocks = []
    clean_files = []
    for item in found_files:
        clean_files.append({"path": item["path"], "content": item["content"]})
        combined_blocks.append(f"// ==========================================\n// File: {item['path']}\n// ==========================================\n\n{item['content']}")
    
    combined_text = "\n\n".join(combined_blocks)
    return {
        "mainName": f"All Submissions ({len(found_files)} files)",
        "content": combined_text,
        "files": clean_files
    }


def _find_student_links(cohort_id: str, batch_id: str, student_name: str, student_data: dict) -> dict:
    github_link = ""
    doc_link = ""
    
    subs = storage.get_submissions(cohort_id, batch_id)
    target_norm = _normalize_name(student_name)
    for s in subs:
        if _normalize_name(s.get("student_name", "")) == target_norm:
            github_link = s.get("github_link", "").strip()
            doc_link = s.get("doc_link", "").strip()
            break
            
    content = student_data.get("content") or ""
    if not github_link:
        gh_match = re.search(r'https?://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', content)
        if gh_match:
            github_link = gh_match.group(0)
            
    if not doc_link:
        doc_match = re.search(r'https?://(?:docs|drive)\.google\.com/[^\s\'"]+', content)
        if doc_match:
            doc_link = doc_match.group(0)
            
    return {"github_link": github_link, "doc_link": doc_link}


@app.get("/api/cohorts/{cohort_id}/batches/{batch_id}/pairs/{pair_id}/files")
def get_pair_files(cohort_id: str, batch_id: str, pair_id: str):
    batch = storage.get_batch(cohort_id, batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found.")
    pair = None
    for p in batch.get("pairs", []):
        if p["id"] == pair_id:
            pair = p
            break
    if not pair:
        raise HTTPException(404, "Pair not found.")
    # Determine which file type to show based on the pair's comparison type
    mode = "report" if pair.get("type", "Code") == "Report" else "code"
    a_data = _read_student_files(cohort_id, batch_id, pair["a"], mode)
    b_data = _read_student_files(cohort_id, batch_id, pair["b"], mode)
    a_links = _find_student_links(cohort_id, batch_id, pair["a"], a_data)
    b_links = _find_student_links(cohort_id, batch_id, pair["b"], b_data)

    return {
        "aStudent": pair["a"],
        "bStudent": pair["b"],
        "aFileName": a_data["mainName"] or "(no file)",
        "aContent": a_data["content"] or "File not available — run may have been before file persistence was added.",
        "aFiles": a_data["files"],
        "aGithubLink": a_links["github_link"] if mode == "code" else "",
        "aDocLink": a_links["doc_link"] if mode == "report" else "",
        "bFileName": b_data["mainName"] or "(no file)",
        "bContent": b_data["content"] or "File not available — run may have been before file persistence was added.",
        "bFiles": b_data["files"],
        "bGithubLink": b_links["github_link"] if mode == "code" else "",
        "bDocLink": b_links["doc_link"] if mode == "report" else "",
        "matches": pair.get("matches", []),
        "pairType": pair.get("type", "Code"),
    }



def clean_submissions_directory(submissions_dir: Path, mode: str):
    """
    Cleans up submissions directory based on mode:
    - ALWAYS deletes all README*, license*, changelog*, .md, .markdown, .rst files.
    - If mode == 'code': Also deletes non-code text/doc/pdf files so JPlag compares source code only.
    - If mode == 'report': Deletes programming files, configs, scripts, and remaining binary files (.docx, .pdf, images), ensuring only valid plain text files remain.
    """
    for file_path in list(submissions_dir.rglob("*")):
        if not file_path.is_file():
            continue
        name_lower = file_path.name.lower()
        suffix = file_path.suffix.lower()

        # ALWAYS strip READMEs, licenses, changelogs, and markdown documentation in ALL modes
        if name_lower.startswith("readme") or name_lower.startswith("license") or name_lower.startswith("changelog") or suffix in [".md", ".markdown", ".rst"]:
            try:
                file_path.unlink()
            except Exception:
                pass
            continue

        if mode == "code":
            # In code mode, remove text/doc/pdf files that aren't source code
            if suffix in [".txt", ".docx", ".doc", ".pdf", ".rtf", ".odt"]:
                try:
                    file_path.unlink()
                except Exception:
                    pass
        elif mode == "report":
            # In report mode, remove programming files, dev/config files, AND binary documents/media
            binary_and_code_exts = [
                ".py", ".java", ".cpp", ".c", ".h", ".hpp", ".cs", ".js", ".ts", ".jsx", ".tsx",
                ".html", ".css", ".go", ".rs", ".php", ".pyc", ".json", ".yml", ".yaml", ".xml",
                ".sh", ".bash", ".sql", ".docx", ".doc", ".pdf", ".rtf", ".odt", ".png", ".jpg",
                ".jpeg", ".gif", ".zip", ".tar", ".gz", ".7z", ".rar", ".exe", ".bin"
            ]
            if suffix in binary_and_code_exts:
                try:
                    file_path.unlink()
                except Exception:
                    pass
                continue

            # Verify that any remaining text file is valid text and not corrupted binary
            try:
                with open(file_path, "rb") as f:
                    sample = f.read(2048)
                if b"\x00" in sample or sample.startswith(b"%PDF"):
                    file_path.unlink()
            except Exception:
                pass


@app.post("/api/run")
async def run_comparison(
    cohort_id: str = Form(...),
    cohort_label: str = Form(...),
    batch_id: str = Form(...),
    batch_label: str = Form(...),
    mode: str = Form(...),          # "code" or "report"
    submissions_zip: UploadFile = File(...),
):
    """
    Unzips the uploaded submissions (expects one folder per student inside),
    runs JPlag once in the requested mode, and stores the result under
    this cohort/batch, merging with whatever's already there (running
    "code" doesn't erase a prior "report" run for the same batch).
    """
    run_dir = WORK_DIR / f"{cohort_id}__{batch_id}__{mode}"
    if run_dir.exists():
        shutil.rmtree(run_dir)
    submissions_dir = run_dir / "submissions"
    submissions_dir.mkdir(parents=True)

    zip_path = run_dir / "upload.zip"
    with open(zip_path, "wb") as f:
        shutil.copyfileobj(submissions_zip.file, f)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(submissions_dir)
    zip_path.unlink()

    # Flatten a single wrapping folder, e.g. if the zip contains one
    # top-level "submissions/" folder rather than student folders directly.
    entries = list(submissions_dir.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        inner = entries[0]
        for item in inner.iterdir():
            shutil.move(str(item), str(submissions_dir / item.name))
        inner.rmdir()

    student_folders = [p.name for p in submissions_dir.iterdir() if p.is_dir()]
    if len(student_folders) < 2:
        raise HTTPException(
            400,
            f"Found {len(student_folders)} student folder(s) inside the zip. "
            f"Need at least 2 to compare anything. Expected structure: "
            f"one folder per student inside the zip.",
        )

    # Convert DOCX, IPYNB, and PDF files if present
    convert_docx_to_txt(submissions_dir)
    convert_ipynb_to_py(submissions_dir)
    convert_pdf_to_txt(submissions_dir)
    _persist_student_files(submissions_dir, cohort_id, batch_id, mode)
    clean_submissions_directory(submissions_dir, mode)

    if mode == "report":
        jplag_language = "text"
    else:
        try:
            jplag_language = detect_language(submissions_dir)
        except Exception as e:
            raise HTTPException(400, f"Language detection failed: {str(e)}")

    try:
        result = run_jplag(JPLAG_JAR, submissions_dir, jplag_language, run_dir / "result")
    except JPlagError as e:
        raise HTTPException(500, str(e))

    saved = storage.save_run(
        cohort_id=cohort_id, cohort_label=cohort_label,
        batch_id=batch_id, batch_label=batch_label,
        mode=mode, student_folders=student_folders,
        comparisons=result["comparisons"],
    )
    _persist_student_files(submissions_dir, cohort_id, batch_id, mode)
    return {"ok": True, "studentCount": saved["studentCount"], "pairCount": len(saved["pairs"])}


@app.post("/api/run-fetched")
async def run_fetched_comparison(
    cohort_id: str = Form(...),
    cohort_label: str = Form(...),
    batch_id: str = Form(...),
    batch_label: str = Form(...),
    mode: str = Form(...),
):
    submissions = storage.get_submissions(cohort_id, batch_id)
    if not submissions:
        raise HTTPException(400, "No submissions collected for this batch yet.")

    run_dir = WORK_DIR / f"{cohort_id}__{batch_id}__{mode}"
    if run_dir.exists():
        shutil.rmtree(run_dir)
        
    fetch_result = fetch_submissions(submissions, run_dir, mode)
    submissions_dir = run_dir / "submissions"
    
    student_folders = [p.name for p in submissions_dir.iterdir() if p.is_dir()]
    if len(student_folders) < 2:
        raise HTTPException(
            400,
            f"Successfully fetched {fetch_result['success_count']} folders, but need at least 2 to compare. Errors: {fetch_result['errors']}"
        )

    # Convert DOCX, IPYNB, and PDF files if present
    convert_docx_to_txt(submissions_dir)
    convert_ipynb_to_py(submissions_dir)
    convert_pdf_to_txt(submissions_dir)
    _persist_student_files(submissions_dir, cohort_id, batch_id, mode)
    clean_submissions_directory(submissions_dir, mode)

    if mode == "report":
        jplag_language = "text"
    else:
        try:
            jplag_language = detect_language(submissions_dir)
        except Exception as e:
            raise HTTPException(400, f"Language detection failed: {str(e)}")

    try:
        result = run_jplag(JPLAG_JAR, submissions_dir, jplag_language, run_dir / "result")
    except JPlagError as e:
        raise HTTPException(500, str(e))

    saved = storage.save_run(
        cohort_id=cohort_id, cohort_label=cohort_label,
        batch_id=batch_id, batch_label=batch_label,
        mode=mode, student_folders=student_folders,
        comparisons=result["comparisons"],
    )
    _persist_student_files(submissions_dir, cohort_id, batch_id, mode)
    
    return {
        "ok": True, 
        "studentCount": saved["studentCount"], 
        "pairCount": len(saved["pairs"]),
        "fetchErrors": fetch_result["errors"]
    }


@app.post("/api/run-csv")
async def run_csv_comparison(
    cohort_id: str = Form("kaim_cohort"),
    cohort_label: str = Form("KAIM Cohort"),
    batch_id: str = Form("csv_batch"),
    batch_label: str = Form("CSV Batch Submissions"),
    csv_file: UploadFile = File(...),
):
    """
    Accepts a 10 Academy CSV export file directly (e.g. batch9_week11.csv),
    parses GitHub repos and Google Doc/Drive links, clones/downloads them in parallel,
    runs JPlag plagiarism comparisons for Code and Report modes, and saves results.
    """
    try:
        content_bytes = await csv_file.read()
        csv_text = content_bytes.decode("utf-8-sig", errors="replace")
    except Exception as e:
        raise HTTPException(400, f"Could not read CSV file: {str(e)}")

    parsed = parse_csv_submissions(csv_text)
    code_tasks = parsed["code_tasks"]
    report_tasks = parsed["report_tasks"]

    if not code_tasks and not report_tasks:
        raise HTTPException(400, "No valid GitHub URLs or Google Doc/Drive links found in the uploaded CSV file.")

    if parsed["detected_title"] and parsed["detected_title"] != "CSV Batch":
        batch_label = parsed["detected_title"]
        # Generate clean IDs if default
        clean_batch_id = re.sub(r'[^a-zA-Z0-9_]', '_', batch_label.lower()).strip('_')
        if clean_batch_id:
            batch_id = clean_batch_id[:32]

    results = {"code": None, "report": None, "cohortId": cohort_id, "batchId": batch_id}

    for sub in parsed["submissions_list"]:
        storage.add_submission(cohort_id, batch_id, sub["student_name"], sub["github_link"], sub["doc_link"])

    # 1. PROCESS CODE SUBMISSIONS IF AVAILABLE
    if code_tasks:
        run_dir_code = WORK_DIR / f"{cohort_id}__{batch_id}__code"
        if run_dir_code.exists():
            shutil.rmtree(run_dir_code)
        submissions_dir_code = run_dir_code / "submissions"
        submissions_dir_code.mkdir(parents=True, exist_ok=True)

        with ThreadPoolExecutor(max_workers=12) as executor:
            futures = [
                executor.submit(clone_github_repo, s_name, url, submissions_dir_code)
                for s_name, url in code_tasks.items()
            ]
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception:
                    pass

        student_folders_code = [p.name for p in submissions_dir_code.iterdir() if p.is_dir()]
        if len(student_folders_code) >= 2:
            convert_docx_to_txt(submissions_dir_code)
            convert_ipynb_to_py(submissions_dir_code)
            convert_pdf_to_txt(submissions_dir_code)
            _persist_student_files(submissions_dir_code, cohort_id, batch_id, "code")
            clean_submissions_directory(submissions_dir_code, "code")

            try:
                jplag_lang = detect_language(submissions_dir_code)
                jplag_res = run_jplag(JPLAG_JAR, submissions_dir_code, jplag_lang, run_dir_code / "result")
                saved_code = storage.save_run(
                    cohort_id=cohort_id, cohort_label=cohort_label,
                    batch_id=batch_id, batch_label=batch_label,
                    mode="code", student_folders=student_folders_code,
                    comparisons=jplag_res["comparisons"]
                )
                _persist_student_files(submissions_dir_code, cohort_id, batch_id, "code")
                results["code"] = {"studentCount": saved_code["studentCount"], "pairCount": len(saved_code["pairs"])}
            except Exception as e:
                results["code_error"] = str(e)

    # 2. PROCESS REPORT SUBMISSIONS IF AVAILABLE
    if report_tasks:
        run_dir_report = WORK_DIR / f"{cohort_id}__{batch_id}__report"
        if run_dir_report.exists():
            shutil.rmtree(run_dir_report)
        submissions_dir_report = run_dir_report / "submissions"
        submissions_dir_report.mkdir(parents=True, exist_ok=True)

        def download_job(s_name, url):
            s_dir = submissions_dir_report / s_name
            ok = download_google_doc_or_drive(url, s_dir, s_name)
            if ok:
                convert_docx_to_txt(s_dir)
                return True
            return False

        with ThreadPoolExecutor(max_workers=12) as executor:
            futures = [
                executor.submit(download_job, s_name, url)
                for s_name, url in report_tasks.items()
            ]
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception:
                    pass

        student_folders_report = [p.name for p in submissions_dir_report.iterdir() if p.is_dir()]
        if len(student_folders_report) >= 2:
            convert_docx_to_txt(submissions_dir_report)
            convert_ipynb_to_py(submissions_dir_report)
            convert_pdf_to_txt(submissions_dir_report)
            _persist_student_files(submissions_dir_report, cohort_id, batch_id, "report")
            clean_submissions_directory(submissions_dir_report, "report")

            try:
                jplag_res = run_jplag(JPLAG_JAR, submissions_dir_report, "text", run_dir_report / "result")
                saved_report = storage.save_run(
                    cohort_id=cohort_id, cohort_label=cohort_label,
                    batch_id=batch_id, batch_label=batch_label,
                    mode="report", student_folders=student_folders_report,
                    comparisons=jplag_res["comparisons"]
                )
                _persist_student_files(submissions_dir_report, cohort_id, batch_id, "report")
                results["report"] = {"studentCount": saved_report["studentCount"], "pairCount": len(saved_report["pairs"])}
            except Exception as e:
                results["report_error"] = str(e)

    return {"ok": True, "results": results, "detectedTitle": parsed["detected_title"]}



# Serve the frontend directly so `uvicorn main:app` gives you a fully
# working app at localhost:8000, no separate frontend server needed
# for local use or for demoing this to your boss.
frontend_dir = BASE_DIR.parent / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
