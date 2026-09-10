"""Build the distributable release ZIP."""

from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def get_version() -> str:
    init_file = ROOT / "src" / "__init__.py"
    content = init_file.read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
    if not match:
        raise RuntimeError("Cannot read __version__ from src/__init__.py")
    return match.group(1)


def main() -> int:
    version = get_version()
    exe_path = ROOT / "dist" / "MC_Hanhua_Tool.exe"
    if not exe_path.exists():
        print("[ERROR] dist/MC_Hanhua_Tool.exe not found. Run tools/build.bat first.")
        return 1

    files_to_copy = {
        exe_path: "MC_Hanhua_Tool.exe",
        ROOT / "README.md": "README.md",
        ROOT / "CHANGELOG.md": "CHANGELOG.md",
        ROOT / "config_template.json": "config_template.json",
        ROOT / "LICENSE": "LICENSE.txt",
    }

    missing = [str(path) for path in files_to_copy if not path.exists()]
    if missing:
        print("[ERROR] Missing release files:")
        for path in missing:
            print(f"  - {path}")
        return 1

    zip_path = ROOT / "dist" / f"MC_Hanhua_Tool_v{version}.zip"
    zip_path.unlink(missing_ok=True)

    print("=" * 60)
    print(f"Creating MC_Hanhua_Tool v{version} release")
    print("=" * 60)

    with tempfile.TemporaryDirectory(prefix="mc_hanhua_release_") as temp_dir:
        release_dir = Path(temp_dir) / "MC_Hanhua_Tool"
        release_dir.mkdir()

        for source, destination in files_to_copy.items():
            shutil.copy2(source, release_dir / destination)
            print(f"[COPY] {destination}")

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for file_path in sorted(release_dir.rglob("*")):
                if file_path.is_file():
                    archive.write(file_path, file_path.relative_to(release_dir.parent))
            archive.writestr("MC_Hanhua_Tool/cache/", "")

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print()
    print("[SUCCESS] Release package created")
    print(f"Output: {zip_path.relative_to(ROOT)}")
    print(f"Size: {size_mb:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
