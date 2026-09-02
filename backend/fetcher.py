import os
import re
import shutil
import subprocess
import requests
from pathlib import Path

def get_doc_id(url: str) -> str:
    # Matches typical docs.google.com/document/d/<ID>/edit
    match = re.search(r'/document/d/([a-zA-Z0-9-_]+)', url)
    return match.group(1) if match else None

def is_valid_github_url(url: str) -> bool:
    if not url:
        return False
    clean_url = url.strip()
    pattern = r'^(https?://|git@)([\w.-]+)[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$'
    return bool(re.match(pattern, clean_url))

def fetch_submissions(submissions: list, run_dir: Path, mode: str) -> dict:
    """
    Downloads code or reports for all submissions into run_dir/submissions.
    Returns a dict with 'success_count', 'errors' list, and 'skipped_students' list of dicts.
    """
    submissions_dir = run_dir / "submissions"
    submissions_dir.mkdir(parents=True, exist_ok=True)
    
    errors = []
    skipped_students = []
    success_count = 0
    
    for sub in submissions:
        student = sub.get("student_name", "unknown_student").replace(" ", "_")
        student_dir = submissions_dir / student
        
        if mode == "code":
            repo_url = sub.get("github_link", "").strip()
            if not repo_url:
                skipped_students.append({"name": student, "reason": "No GitHub link provided"})
                continue
                
            if not is_valid_github_url(repo_url):
                msg = f"Invalid GitHub repo URL format for {student}: {repo_url}"
                errors.append(msg)
                skipped_students.append({"name": student, "reason": f"Invalid GitHub URL format: {repo_url}"})
                continue
                
            clone_target = repo_url[:-4] if repo_url.endswith(".git") else repo_url
            cloned = False
            last_err = ""
            
            # Retry loop: up to 2 attempts with 60s timeout
            for attempt in range(1, 3):
                if student_dir.exists():
                    shutil.rmtree(student_dir)
                try:
                    subprocess.run(
                        ["git", "clone", "--depth", "1", clone_target, str(student_dir)],
                        check=True,
                        capture_output=True,
                        text=True,
                        timeout=60
                    )
                    cloned = True
                    break
                except subprocess.TimeoutExpired:
                    last_err = f"Timed out cloning GitHub repo (attempt {attempt}/2, 60s limit)"
                except subprocess.CalledProcessError as e:
                    err_detail = e.stderr.strip() if e.stderr else str(e)
                    last_err = f"Failed to clone GitHub repo: {err_detail}"

            if cloned:
                git_dir = student_dir / ".git"
                if git_dir.exists():
                    shutil.rmtree(git_dir)
                success_count += 1
            else:
                errors.append(f"{student}: {last_err}")
                skipped_students.append({"name": student, "reason": last_err})
                
        elif mode == "report":
            doc_url = sub.get("doc_link", "").strip()
            if not doc_url:
                skipped_students.append({"name": student, "reason": "No Google Doc link provided"})
                continue
                
            doc_id = get_doc_id(doc_url)
            if not doc_id:
                msg = f"Invalid Google Docs link for {student}: {doc_url}"
                errors.append(msg)
                skipped_students.append({"name": student, "reason": f"Invalid Google Doc URL: {doc_url}"})
                continue
                
            export_url = f"https://docs.google.com/document/d/{doc_id}/export?format=docx"
            student_dir.mkdir(exist_ok=True)
            doc_path = student_dir / "report.docx"
            
            try:
                resp = requests.get(export_url, timeout=20)
                if resp.status_code == 200 and "text/html" not in resp.headers.get("Content-Type", ""):
                    with open(doc_path, "wb") as f:
                        f.write(resp.content)
                    success_count += 1
                else:
                    msg = f"Google Doc for {student} is private or inaccessible."
                    errors.append(msg)
                    skipped_students.append({"name": student, "reason": "Google Doc is private or inaccessible"})
            except Exception as e:
                msg = f"Failed to download Google Doc for {student}: {str(e)}"
                errors.append(msg)
                skipped_students.append({"name": student, "reason": f"Download failed: {str(e)}"})
                
    return {"success_count": success_count, "errors": errors, "skipped_students": skipped_students}
