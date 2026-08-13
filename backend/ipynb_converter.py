import json
import re
from pathlib import Path


def _sanitize_notebook_json(raw: str) -> str:
    """
    Remove ASCII control characters (0x00–0x1F, excluding standard whitespace
    \\t \\n \\r) that make json.loads raise 'Invalid control character'.
    These often appear as embedded null bytes or terminal escape codes in
    notebook output cells.
    """
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", raw)


def convert_ipynb_to_py(folder: Path):
    """
    Finds all .ipynb files in folder recursively, extracts python code cells,
    and writes them into corresponding .py files so JPlag can analyze Jupyter
    Notebooks.

    Robustness guarantees:
    - Empty files are skipped silently.
    - Notebooks with embedded control characters are sanitised and retried
      before giving up, so they are converted rather than dropped.
    - Any remaining parse or I/O failures emit a warning and are skipped
      without crashing the overall run.
    """
    for ipynb_file in list(folder.rglob("*.ipynb")):
        if ipynb_file.name.startswith(".") or ".ipynb_checkpoints" in str(ipynb_file):
            continue

        try:
            raw = ipynb_file.read_bytes()
        except OSError as e:
            print(f"Warning: Could not read notebook {ipynb_file}: {e}")
            continue

        # Skip completely empty files – nothing to convert.
        if not raw.strip():
            print(f"Warning: Skipping empty notebook {ipynb_file}")
            continue

        text = raw.decode("utf-8", errors="replace")

        # First attempt: parse as-is.
        nb_data = None
        try:
            nb_data = json.loads(text)
        except json.JSONDecodeError:
            # Second attempt: strip embedded control characters and retry.
            try:
                nb_data = json.loads(_sanitize_notebook_json(text))
            except json.JSONDecodeError as e:
                print(f"Warning: Failed to convert notebook {ipynb_file}: {e}")
                continue

        try:
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
        except Exception as e:
            print(f"Warning: Failed to extract code from notebook {ipynb_file}: {e}")
