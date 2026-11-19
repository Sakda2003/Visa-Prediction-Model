"""Create a distributable source bundle without local case data or credentials."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT.parent / "output" / "Visa_Platform_Integrated.zip"
SOURCE_DIRS = ("backend", "ai_service", "frontend", "docs", "scripts", "templates")
ROOT_FILES = ("README.md", "PRODUCT.md", "DESIGN.md", ".gitignore", ".env.example", "start.ps1", "EXTRACTION.json", "package.json", "docker-compose.yml", "ocr_api.py")
EXCLUDE_DIRS = {"node_modules", ".venv", ".runtime", ".git", ".vscode", "__pycache__", ".pytest_cache", "storage", "results", "ocr_results", "models", "model_artifacts", "cache", ".cache"}
ALLOWED_SUFFIXES = {".woff2", ".py", ".jsx", ".js", ".css", ".html", ".md", ".json", ".toml", ".csv", ".txt", ".svg", ".png", ".jpg", ".jpeg", ".webmanifest", ".yml", ".yaml", ".ps1"}


def source_files():
    for name in ROOT_FILES:
        path = ROOT / name
        if path.is_file():
            yield path
    design = ROOT / ".impeccable" / "design.json"
    if design.is_file():
        yield design
    for directory in SOURCE_DIRS:
        for parent, directories, files in os.walk(ROOT / directory):
            directories[:] = sorted(d for d in directories if d not in EXCLUDE_DIRS)
            for name in sorted(files):
                path = Path(parent) / name
                # Include the built SPA, but never PDFs, trained artifacts, environment
                # files, databases, OCR output, or bundled private sample images.
                if name.startswith(".env") or name in {"passport.jpg", "bank_statement.pdf"}:
                    continue
                if path.suffix.lower() not in ALLOWED_SUFFIXES:
                    continue
                if path.is_symlink() or not path.resolve().is_relative_to(ROOT.resolve()):
                    raise RuntimeError(f"Unsafe source path: {path.relative_to(ROOT)}")
                yield path


def main():
    OUTPUT.parent.mkdir(exist_ok=True)
    entries = sorted(set(source_files()), key=lambda p: p.relative_to(ROOT).as_posix())
    manifest = []
    with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in entries:
            relative = path.relative_to(ROOT).as_posix()
            data = path.read_bytes()
            archive.writestr(f"visa-platform/{relative}", data)
            manifest.append({"path": relative, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
        archive.writestr("visa-platform/SOURCE_MANIFEST.json", json.dumps({"privateDataIncluded": False, "files": manifest}, indent=2))
    with zipfile.ZipFile(OUTPUT) as archive:
        if archive.testzip():
            raise RuntimeError("Source ZIP verification failed")
        assert not any("/.env" in name and not name.endswith("/.env.example") for name in archive.namelist())
        assert not any("/.runtime/" in name or "/storage/" in name or name.lower().endswith(".pdf") for name in archive.namelist())
    print(f"Created {OUTPUT}: {len(entries)} files, {OUTPUT.stat().st_size:,} bytes; no local case data or credentials.")


if __name__ == "__main__":
    main()
