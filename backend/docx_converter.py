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

    for docx_file in list(folder.rglob("*.docx")):
        if docx_file.name.startswith("~$"):
            continue  # Skip temporary Word lock files
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
        except Exception as e:
            print(f"Warning: Failed to convert {docx_file}: {e}")
