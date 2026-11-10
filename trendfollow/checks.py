"""Run the same offline checks locally and in CI: python -m trendfollow.checks."""

from pathlib import Path
import shutil
import subprocess
import sys

from .assets import check


def main():
    root = Path(__file__).resolve().parent.parent
    node = shutil.which("node")
    if node is None:
        raise SystemExit(
            "검증에는 Node.js 22 이상이 필요합니다. UI 실행에는 필요하지 않습니다."
        )
    check()
    commands = [
        [sys.executable, "-m", "pytest", "-q"],
        [node, "--check", str(root / "trendfollow/web/app.js")],
        [node, "--test", *map(str, sorted((root / "tests").glob("*.test.cjs")))],
    ]
    for command in commands:
        print("Running: " + " ".join(command), flush=True)
        result = subprocess.run(command, cwd=root)
        if result.returncode:
            raise SystemExit(result.returncode)
    print("Python, JavaScript and UI bundle checks passed.")


if __name__ == "__main__":
    main()
