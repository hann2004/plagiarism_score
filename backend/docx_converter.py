from pathlib import Path

def convert_docx_to_txt(folder: Path):
    """
    Finds all .docx files in folder recursively and converts their text contents
    into corresponding .txt files so JPlag text mode can analyze them.
    """
    try:
        import docx
    except ImportError:
        return

    # Known extensions that are ZIP-based but are NOT DOCX — never try to open these as Word files
    NON_DOCX_ZIP_EXTENSIONS = {
        '.xlsx', '.xls', '.xlsm', '.xlsb',  # Excel
        '.pptx', '.ppt', '.pptm',            # PowerPoint
        '.csv', '.zip', '.jar', '.odt',      # Other ZIP-based
        '.odf', '.ods', '.odp', '.apk',
    }

    for docx_file in list(folder.rglob("*")):
        if not docx_file.is_file() or docx_file.name.startswith("~$"):
            continue

        ext = docx_file.suffix.lower()
        is_docx = ext == ".docx"

        # Only inspect the magic bytes for truly ambiguous files (e.g. misnamed .txt or no extension)
        # Never treat known non-DOCX formats as DOCX even if they share the PK header
        if not is_docx and ext not in NON_DOCX_ZIP_EXTENSIONS and ext not in {'.py', '.js', '.ts', '.java', '.c', '.cpp', '.h', '.cs', '.rb', '.go', '.rs', '.scala', '.kt', '.r', '.ipynb', '.html', '.md', '.pdf', '.png', '.jpg', '.jpeg', '.gif', '.xml', '.json', '.yaml', '.yml', '.sh', '.bat'}:
            try:
                with open(docx_file, "rb") as f:
                    if f.read(4) == b"PK\x03\x04":
                        is_docx = True
            except Exception:
                continue

        if not is_docx:
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

            txt_path = docx_file.with_suffix(".txt") if docx_file.suffix.lower() == ".docx" else docx_file
            txt_path.write_text("\n".join(full_text) + "\n", encoding="utf-8")
            if docx_file.suffix.lower() == ".docx":
                try:
                    docx_file.unlink()
                except Exception:
                    pass
        except Exception as e:
            print(f"Warning: Failed to convert {docx_file}: {e}")

