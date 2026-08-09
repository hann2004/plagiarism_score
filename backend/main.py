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
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from jplag_runner import run_jplag, JPlagError
from storage import Storage
from language_detector import detect_language
from docx_converter import convert_docx_to_txt
from fetcher import fetch_submissions

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
WORK_DIR = BASE_DIR / "work"
FILES_DIR = DATA_DIR / "files"
JPLAG_JAR = BASE_DIR / "jplag" / "jplag.jar"

DATA_DIR.mkdir(exist_ok=True)
WORK_DIR.mkdir(exist_ok=True)
FILES_DIR.mkdir(exist_ok=True)

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
    return batch


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


def _persist_student_files(submissions_dir: Path, cohort_id: str, batch_id: str):
    """Copy student source files to a permanent location so the diff view can read them later."""
    dest = FILES_DIR / f"{cohort_id}__{batch_id}"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for student_dir in submissions_dir.iterdir():
        if student_dir.is_dir():
            student_dest = dest / student_dir.name
            shutil.copytree(student_dir, student_dest, dirs_exist_ok=True)


def _read_student_files(cohort_id: str, batch_id: str, student_name: str) -> dict:
    """Reads ALL source files submitted by a student, returning a files list and combined view."""
    batch_dir = FILES_DIR / f"{cohort_id}__{batch_id}"
    if not batch_dir.exists():
        return {"mainName": None, "content": None, "files": []}

    student_dir = batch_dir / student_name
    if not student_dir.exists():
        # Flexible folder match (e.g. "student 1" vs "student_1_abebe_kebede")
        target_norm = student_name.lower().replace("_", " ").strip()
        matched = None
        for folder in batch_dir.iterdir():
            if folder.is_dir():
                folder_norm = folder.name.lower().replace("_", " ").strip()
                if folder_norm == target_norm or folder_norm in target_norm or target_norm in folder_norm:
                    matched = folder
                    break
        if matched:
            student_dir = matched
        else:
            return {"mainName": None, "content": None, "files": []}

    found_files = []
    for f in sorted(student_dir.rglob("*")):
        if f.is_file() and not f.name.startswith("."):
            try:
                rel_path = str(f.relative_to(student_dir))
                text = f.read_text(errors="replace")
                found_files.append({"path": rel_path, "content": text})
            except Exception:
                pass

    if not found_files:
        return {"mainName": None, "content": None, "files": []}

    if len(found_files) == 1:
        return {
            "mainName": found_files[0]["path"],
            "content": found_files[0]["content"],
            "files": found_files
        }

    combined_blocks = []
    for item in found_files:
        combined_blocks.append(f"// ==========================================\n// File: {item['path']}\n// ==========================================\n\n{item['content']}")
    
    combined_text = "\n\n".join(combined_blocks)
    return {
        "mainName": f"All Submissions ({len(found_files)} files)",
        "content": combined_text,
        "files": found_files
    }


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
    a_data = _read_student_files(cohort_id, batch_id, pair["a"])
    b_data = _read_student_files(cohort_id, batch_id, pair["b"])
    return {
        "aStudent": pair["a"],
        "bStudent": pair["b"],
        "aFileName": a_data["mainName"] or "(no file)",
        "aContent": a_data["content"] or "File not available — run may have been before file persistence was added.",
        "aFiles": a_data["files"],
        "bFileName": b_data["mainName"] or "(no file)",
        "bContent": b_data["content"] or "File not available — run may have been before file persistence was added.",
        "bFiles": b_data["files"],
        "matches": pair.get("matches", []),
    }


def clean_submissions_directory(submissions_dir: Path, mode: str):
    """
    Cleans up submissions directory based on mode:
    - ALWAYS deletes all README*, license*, changelog*, .md, .markdown, .rst files.
    - If mode == 'code': Also deletes non-code text/doc/pdf files so JPlag compares source code only.
    - If mode == 'report': Also deletes all programming files, configs, scripts, leaving only main reports.
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
            # In report mode, remove programming source code and dev/config files
            if suffix in [".py", ".java", ".cpp", ".c", ".h", ".hpp", ".cs", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".go", ".rs", ".php", ".pyc", ".json", ".yml", ".yaml", ".xml", ".sh", ".bash", ".sql"]:
                try:
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

    # Convert DOCX reports if present
    convert_docx_to_txt(submissions_dir)
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
    _persist_student_files(submissions_dir, cohort_id, batch_id)
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

    # Convert DOCX reports if present
    convert_docx_to_txt(submissions_dir)
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
    _persist_student_files(submissions_dir, cohort_id, batch_id)
    
    return {
        "ok": True, 
        "studentCount": saved["studentCount"], 
        "pairCount": len(saved["pairs"]),
        "fetchErrors": fetch_result["errors"]
    }



# Serve the frontend directly so `uvicorn main:app` gives you a fully
# working app at localhost:8000, no separate frontend server needed
# for local use or for demoing this to your boss.
frontend_dir = BASE_DIR.parent / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
