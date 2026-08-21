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


def _regex_fallback_extract_code(text: str) -> list:
    """
    Fallback method when JSON parsing fails completely.
    Extracts code cell content directly using regex matching on cell sources.
    """
    code_lines = []
    cell_pattern = re.compile(
        r'"cell_type"\s*:\s*"code"[^}]*?"source"\s*:\s*(\[[^\]]*?\]|"(?:\\.|[^"\\])*")',
        re.DOTALL
    )
    matches = cell_pattern.findall(text)
    for m in matches:
        m_str = m.strip()
        if m_str.startswith("["):
            items = re.findall(r'"((?:\\.|[^"\\])*)"', m_str)
            for item in items:
                try:
                    decoded = item.encode("utf-8").decode("unicode_escape", errors="replace")
                    code_lines.append(decoded)
                except Exception:
                    code_lines.append(item)
            code_lines.append("\n\n")
        elif m_str.startswith('"'):
            inner = m_str[1:-1]
            try:
                decoded = inner.encode("utf-8").decode("unicode_escape", errors="replace")
                code_lines.append(decoded)
            except Exception:
                code_lines.append(inner)
            code_lines.append("\n\n")
    return code_lines


def convert_ipynb_to_py(folder: Path):
    """
    Finds all .ipynb files in folder recursively, extracts python code cells,
    and writes them into corresponding .py files so JPlag can analyze Jupyter
    Notebooks.
    """
    for ipynb_file in list(folder.rglob("*.ipynb")):
        if ipynb_file.name.startswith(".") or ".ipynb_checkpoints" in str(ipynb_file):
            continue

        try:
            raw = ipynb_file.read_bytes()
        except OSError:
            continue

        if not raw.strip():
            continue

        text = raw.decode("utf-8", errors="replace")

        # Attempt 1: Parse standard JSON with strict=False
        nb_data = None
        try:
            nb_data = json.loads(text, strict=False)
        except Exception:
            # Attempt 2: Sanitize control characters and parse with strict=False
            try:
                nb_data = json.loads(_sanitize_notebook_json(text), strict=False)
            except Exception:
                nb_data = None

        code_lines = []

        if nb_data and isinstance(nb_data, dict):
            try:
                for cell in nb_data.get("cells", []):
                    if cell.get("cell_type") == "code":
                        source = cell.get("source", [])
                        if isinstance(source, list):
                            code_lines.extend(source)
                        elif isinstance(source, str):
                            code_lines.append(source)
                        code_lines.append("\n\n")
            except Exception:
                pass

        # Attempt 3: Regex fallback if json.loads failed or produced no lines
        if not code_lines:
            code_lines = _regex_fallback_extract_code(text)

        if code_lines:
            try:
                py_file = ipynb_file.with_suffix(".py")
                py_file.write_text("".join(code_lines), encoding="utf-8")
                try:
                    ipynb_file.unlink()
                except Exception:
                    pass
            except Exception:
                pass
