from pathlib import Path
from collections import Counter

EXTENSIONS = {
    ".py": "python3",
    ".java": "java",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".c": "c",
    ".cs": "csharp",
    ".js": "javascript",
    ".ts": "typescript",
    ".txt": "text"
}

def detect_language(folder: Path) -> str:
    counter = Counter()

    for file in folder.rglob("*"):
        if file.is_file():
            ext = file.suffix.lower()
            if ext in EXTENSIONS:
                counter[EXTENSIONS[ext]] += 1

    if not counter:
        raise Exception("No supported files found")

    return counter.most_common(1)[0][0]
