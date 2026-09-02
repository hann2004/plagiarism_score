from pathlib import Path
from collections import Counter
from typing import Optional

EXTENSIONS = {
    ".py": "python3",
    ".java": "java",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".h": "cpp",
    ".c": "c",
    ".cs": "csharp",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".swift": "swift",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".r": "rlang",
    ".R": "rlang",
    ".txt": "text",
}

def detect_language(folder: Path) -> Optional[str]:
    counter = Counter()

    for file in folder.rglob("*"):
        if file.is_file():
            ext = file.suffix.lower()
            if ext in EXTENSIONS:
                counter[EXTENSIONS[ext]] += 1

    if not counter:
        return None

    return counter.most_common(1)[0][0]
