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

def fetch_submissions(submissions: list, run_dir: Path, mode: str) -> dict:
    """
    Downloads code or reports for all submissions into run_dir/submissions.
    Returns a dict with 'success' count and 'errors' list.
    """
    submissions_dir = run_dir / "submissions"
    submissions_dir.mkdir(parents=True, exist_ok=True)
    
    errors = []
    success_count = 0
    
    for sub in submissions:
        student = sub.get("student_name", "unknown_student").replace(" ", "_")
        student_dir = submissions_dir / student
        
        if mode == "code":
            repo_url = sub.get("github_link", "").strip()
            if not repo_url:
                continue
                
            # Clean up repo URL just in case
            if repo_url.endswith(".git"):
                repo_url = repo_url[:-4]
                
            try:
                # Clone with depth 1
                subprocess.run(
                    ["git", "clone", "--depth", "1", repo_url, str(student_dir)],
                    check=True,
                    capture_output=True,
                    timeout=30
                )
                
                # Remove .git directory to avoid confusing JPlag
                git_dir = student_dir / ".git"
                if git_dir.exists():
                    shutil.rmtree(git_dir)
                success_count += 1
            except subprocess.CalledProcessError as e:
                errors.append(f"Failed to clone GitHub repo for {student}: {repo_url}")
            except subprocess.TimeoutExpired:
                errors.append(f"Timed out cloning GitHub repo for {student}")
                
        elif mode == "report":
            doc_url = sub.get("doc_link", "").strip()
            if not doc_url:
                continue
                
            doc_id = get_doc_id(doc_url)
            if not doc_id:
                errors.append(f"Invalid Google Docs link for {student}")
                continue
                
            export_url = f"https://docs.google.com/document/d/{doc_id}/export?format=docx"
            student_dir.mkdir(exist_ok=True)
            doc_path = student_dir / "report.docx"
            
            try:
                resp = requests.get(export_url, timeout=15)
                # Check if it actually returned a docx (if it's private, it returns a 200 HTML login page!)
                if resp.status_code == 200 and "text/html" not in resp.headers.get("Content-Type", ""):
                    with open(doc_path, "wb") as f:
                        f.write(resp.content)
                    success_count += 1
                else:
                    errors.append(f"Google Doc for {student} is private or inaccessible.")
            except Exception as e:
                errors.append(f"Failed to download Google Doc for {student}: {str(e)}")
                
    return {"success_count": success_count, "errors": errors}
