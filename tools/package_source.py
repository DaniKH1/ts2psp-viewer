"""Package readable sources, assets and tests without game data or build files."""

from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import argparse

ROOT = Path(__file__).resolve().parent.parent
TOP_FILES = {
    "README.md",
    "pyproject.toml",
    "requirements.txt",
    "requirements-build.txt",
    ".gitignore",
    "run_viewer.py",
    "run.bat",
    "build.bat",
}
FOLDERS = ("src", "docs", "tools", "tests")


def package(target):
    files = [ROOT / name for name in TOP_FILES]
    files += [
        p
        for name in FOLDERS
        for p in (ROOT / name).rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    ]
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(target, "w", ZIP_DEFLATED, compresslevel=7) as archive:
        for path in sorted(files):
            archive.write(path, path.relative_to(ROOT))
    with ZipFile(target) as archive:
        assert archive.testzip() is None
    print(f"{target}: {len(files)} files")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output", nargs="?", default=str(ROOT / "dist/TS2_PSP_Viewer_source.zip")
    )
    package(parser.parse_args().output)
