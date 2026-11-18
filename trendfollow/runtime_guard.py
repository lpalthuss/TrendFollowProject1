"""Detect source changes that require a clean UI process restart."""

import hashlib
from pathlib import Path


def source_fingerprint(root):
    root = Path(root)
    files = sorted((root / "trendfollow").rglob("*.py"))
    files.extend(root / name for name in ("streamlit_app.py", "pyproject.toml",
                                          "requirements.txt", ".streamlit/config.toml"))
    digest = hashlib.sha256()
    for path in files:
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()
