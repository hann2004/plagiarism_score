import subprocess
from pathlib import Path

def convert_pdf_to_txt(folder: Path):
    """
    Finds all .pdf files or misnamed .txt files (containing %PDF header) in folder recursively
    and converts their text contents into corresponding .txt files so JPlag text mode can analyze them.
    Removes original .pdf binary files after conversion.
    """
    for file_path in list(folder.rglob("*")):
        if not file_path.is_file():
            continue

        is_pdf = file_path.suffix.lower() == ".pdf"
        
        # Check if file has %PDF header even if extension is not .pdf
        if not is_pdf:
            try:
                with open(file_path, "rb") as f:
                    header = f.read(4)
                    if header == b"%PDF":
                        is_pdf = True
            except Exception:
                continue

        if not is_pdf:
            continue

        # Target txt file path
        if file_path.suffix.lower() == ".pdf":
            txt_path = file_path.with_suffix(".txt")
        else:
            txt_path = file_path

        temp_txt = file_path.parent / f"temp_{file_path.stem}.txt"

        converted = False
        # 1. Try system pdftotext first
        try:
            res = subprocess.run(
                ["pdftotext", str(file_path), str(temp_txt)],
                capture_output=True,
                text=True,
                timeout=30
            )
            if res.returncode == 0 and temp_txt.exists():
                text_content = temp_txt.read_text(errors="replace")
                if text_content.strip():
                    txt_path.write_text(text_content, encoding="utf-8")
                    converted = True
                temp_txt.unlink(missing_ok=True)
        except Exception as e:
            print(f"Warning: pdftotext failed for {file_path}: {e}")
            if temp_txt.exists():
                temp_txt.unlink(missing_ok=True)

        # 2. Fallback to pypdf if pdftotext didn't extract text
        if not converted:
            try:
                import pypdf
                reader = pypdf.PdfReader(str(file_path))
                extracted_pages = []
                for page in reader.pages:
                    text = page.extract_text()
                    if text:
                        extracted_pages.append(text)
                full_text = "\n".join(extracted_pages)
                if full_text.strip():
                    txt_path.write_text(full_text, encoding="utf-8")
                    converted = True
            except Exception as e:
                print(f"Warning: pypdf fallback failed for {file_path}: {e}")

        # Remove original .pdf file if it had a .pdf extension
        if converted and file_path.suffix.lower() == ".pdf":
            try:
                file_path.unlink()
            except Exception:
                pass
