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

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
WORK_DIR = BASE_DIR / "work"
JPLAG_JAR = BASE_DIR / "jplag" / "jplag.jar"

DATA_DIR.mkdir(exist_ok=True)
WORK_DIR.mkdir(exist_ok=True)

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


@app.post("/api/cohorts/{cohort_id}/batches/{batch_id}/status")
def set_status(cohort_id: str, batch_id: str, pair_id: str = Form(...), status: str = Form(...)):
    ok = storage.set_pair_status(cohort_id, batch_id, pair_id, status)
    if not ok:
        raise HTTPException(404, "Pair not found.")
    return {"ok": True}


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
    return {"ok": True, "studentCount": saved["studentCount"], "pairCount": len(saved["pairs"])}


# Serve the frontend directly so `uvicorn main:app` gives you a fully
# working app at localhost:8000, no separate frontend server needed
# for local use or for demoing this to your boss.
frontend_dir = BASE_DIR.parent / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
