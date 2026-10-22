#!/usr/bin/env python3
"""Build a local .deb without installing packages or modifying user settings."""
from __future__ import annotations

import argparse
from email.parser import Parser
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile


def build(wheel: Path, output_dir: Path) -> Path:
    source = Path(__file__).resolve().parent.parent
    wheel = wheel.resolve(strict=True)
    if not shutil.which("dpkg-deb"):
        raise RuntimeError("dpkg-deb is required (Ubuntu package: dpkg).")
    with zipfile.ZipFile(wheel) as archive:
        names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(names) != 1:
            raise ValueError("Expected exactly one wheel metadata file.")
        metadata = Parser().parsestr(archive.read(names[0]).decode("utf-8"))
        version = metadata["Version"]
        if metadata["Name"] != "quota-widget" or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){2}", version or ""):
            raise ValueError("Expected a quota-widget wheel with a stable X.Y.Z version.")
        if "quota_widget/__main__.py" not in archive.namelist():
            raise ValueError("The wheel does not contain the app entry point.")
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir.resolve() / f"quota-dock_{version}_all.deb"
    with tempfile.TemporaryDirectory(prefix="quota-dock-deb-") as temporary:
        stage = Path(temporary) / "root"
        files = [
            (source / "packaging/quota-widget", "usr/bin/quota-widget"),
            (source / "packaging/gui_launcher.py", "usr/lib/quota-widget/gui_launcher.py"),
            (source / "packaging/quota-dock.desktop", "usr/share/applications/quota-dock.desktop"),
            (source / "quota_widget/assets/icon.svg", "usr/share/icons/hicolor/scalable/apps/quota-dock.svg"),
            (source / "LICENSE", "usr/share/doc/quota-dock/copyright"),
            (source / "README.md", "usr/share/doc/quota-dock/README.md"),
            (source / "docs/TROUBLESHOOTING.md", "usr/share/doc/quota-dock/TROUBLESHOOTING.md"),
            (wheel, f"usr/share/quota-widget/{wheel.name}"),
        ]
        # Preserve README and nested documentation links in the installed tree.
        for filename in ("LICENSE", "CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md"):
            files.append((source / filename, f"usr/share/doc/quota-dock/{filename}"))
        for document in sorted((source / "docs").rglob("*")):
            if document.is_file() and not any(part.startswith(".") for part in document.relative_to(source).parts):
                relative = document.relative_to(source).as_posix()
                files.append((document, f"usr/share/doc/quota-dock/{relative}"))
        for original, relative in files:
            destination = stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, destination)
            destination.chmod(0o755 if relative == "usr/bin/quota-widget" else 0o644)
        release = {"version": version, "wheel": wheel.name,
                   "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}
        (stage / "usr/share/quota-widget/release.json").write_text(
            json.dumps(release, indent=2) + "\n", encoding="utf-8")
        control_dir = stage / "DEBIAN"
        control_dir.mkdir()
        installed_size = sum((item.stat().st_size + 1023) // 1024 for item in stage.rglob("*") if item.is_file())
        control = (source / "packaging/control.in").read_text(encoding="utf-8")
        (control_dir / "control").write_text(
            control.replace("@VERSION@", version).replace("@SIZE@", str(installed_size)), encoding="utf-8")
        checksums = []
        for item in sorted(stage.rglob("*")):
            if item.is_file() and control_dir not in item.parents:
                checksums.append(hashlib.md5(item.read_bytes()).hexdigest() + "  " + str(item.relative_to(stage)))
        (control_dir / "md5sums").write_text("\n".join(checksums) + "\n", encoding="utf-8")
        # No maintainer scripts: installation/removal never touches user homes.
        environment = os.environ.copy()
        environment.setdefault("SOURCE_DATE_EPOCH", "1792540800")
        subprocess.run(["dpkg-deb", "--root-owner-group", "-Zxz", "--build", str(stage), str(target)],
                       env=environment, check=True)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path, help="Current quota_widget-X.Y.Z-py3-none-any.whl")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent.parent / "dist")
    args = parser.parse_args()
    print(build(args.wheel, args.output_dir))


if __name__ == "__main__":
    main()
