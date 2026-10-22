#!/usr/bin/env python3
"""Verify local release files and optionally rebuild from the sdist, without installing the app."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import runpy
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from email.parser import Parser


ROOT = Path(__file__).resolve().parent.parent


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def source_files(root: Path) -> list[Path]:
    files = [root / name for name in (
        "README.md", "LICENSE", "pyproject.toml", "MANIFEST.in", "Makefile",
        "requirements-dev.txt", "install.sh", "uninstall.sh", "CONTRIBUTING.md",
        "SECURITY.md", "CHANGELOG.md",
    )]
    for directory in ("quota_widget", "tests", "docs", "packaging", ".github/workflows"):
        for path in sorted((root / directory).rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix in (".pyc", ".pyo"):
                continue
            require(not path.is_symlink(), f"Source symlink is not supported: {path}")
            files.append(path)
    return files


def check_wheel(wheel: Path, source: Path, version: str) -> None:
    with zipfile.ZipFile(wheel) as archive:
        require(archive.testzip() is None, "Wheel CRC check failed")
        names = archive.namelist()
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        require(len(metadata_names) == 1, "Expected one wheel metadata file")
        metadata = Parser().parsestr(archive.read(metadata_names[0]).decode("utf-8"))
        require(metadata["Name"] == "quota-widget" and metadata["Version"] == version,
                "Wheel name or version does not match source")
        for path in sorted((source / "quota_widget").rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix not in (".pyc", ".pyo"):
                name = path.relative_to(source).as_posix()
                require(archive.read(name) == path.read_bytes(), f"Wheel differs from source: {name}")


def check_deb(deb: Path, wheel: Path, source: Path, version: str) -> None:
    with tempfile.TemporaryDirectory(prefix="quota-dock-deb-check-") as temporary:
        data, control = Path(temporary) / "data", Path(temporary) / "control"
        subprocess.run(["dpkg-deb", "-x", str(deb), str(data)], check=True)
        subprocess.run(["dpkg-deb", "-e", str(deb), str(control)], check=True)
        actual_version = subprocess.check_output(["dpkg-deb", "-f", str(deb), "Version"], text=True).strip()
        require(actual_version == version, "Debian package version does not match source")
        architecture = subprocess.check_output(["dpkg-deb", "-f", str(deb), "Architecture"], text=True).strip()
        require(architecture == "all", "Expected an architecture-independent Debian package")
        require({path.name for path in control.iterdir()} == {"control", "md5sums"},
                "Unexpected Debian control file or maintainer script")
        release = json.loads((data / "usr/share/quota-widget/release.json").read_text())
        expected = {"version": version, "wheel": wheel.name,
                    "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}
        require(release == expected, "Debian release manifest does not match wheel")
        require((data / "usr/share/quota-widget" / wheel.name).read_bytes() == wheel.read_bytes(),
                "Bundled wheel differs from the verified wheel")
        checked_files = set()
        for line in (control / "md5sums").read_text().splitlines():
            digest, relative = line.split("  ", 1)
            require(hashlib.md5((data / relative).read_bytes()).hexdigest() == digest,
                    f"Debian data checksum failed: {relative}")
            checked_files.add(relative)
        data_files = {path.relative_to(data).as_posix() for path in data.rglob("*") if path.is_file()}
        require(checked_files == data_files, "Debian data files are missing from the checksum manifest")
        for path in source_files(source):
            relative = path.relative_to(source)
            if relative.parts[0] == "docs" or path.name in {
                "README.md", "CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md", "LICENSE",
            }:
                installed = data / "usr/share/doc/quota-dock" / relative
                require(installed.read_bytes() == path.read_bytes(), f"Missing or stale installed document: {relative}")
        require((data / "usr/bin/quota-widget").stat().st_mode & 0o111 != 0,
                "Debian launcher is not executable")


def unpack_sdist(sdist: Path, destination: Path, version: str) -> Path:
    prefix = f"quota_widget-{version}"
    with tarfile.open(sdist, "r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            name = PurePosixPath(member.name)
            require(not name.is_absolute() and ".." not in name.parts and name.parts[0] == prefix,
                    f"Unsafe or unexpected source archive path: {member.name}")
            require(member.isfile() or member.isdir(), f"Unsupported source archive entry: {member.name}")
        # Members were checked above; this also works on the supported Python 3.10.
        archive.extractall(destination, members=members)
    source = destination / prefix
    for path in source_files(ROOT):
        relative = path.relative_to(ROOT)
        require((source / relative).read_bytes() == path.read_bytes(), f"Source archive missing or stale: {relative}")
    require(not (source / "dist").exists(), "Built files must not be included in the source archive")
    return source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / "dist")
    parser.add_argument("--rebuild-sdist", action="store_true")
    parser.add_argument("--no-isolation", action="store_true",
                        help="Reuse prepared build tools for the optional sdist rebuild")
    args = parser.parse_args()
    version = runpy.run_path(str(ROOT / "quota_widget/__init__.py"))["__version__"]
    directory = args.directory.resolve()
    wheel = directory / f"quota_widget-{version}-py3-none-any.whl"
    sdist = directory / f"quota_widget-{version}.tar.gz"
    deb = directory / f"quota-dock_{version}_all.deb"
    try:
        for path in (wheel, sdist, deb):
            require(path.is_file(), f"Missing artifact; run make deb first: {path}")
        check_wheel(wheel, ROOT, version)
        check_deb(deb, wheel, ROOT, version)
        with tempfile.TemporaryDirectory(prefix="quota-dock-sdist-check-") as temporary:
            source = unpack_sdist(sdist, Path(temporary), version)
            if args.rebuild_sdist:
                output = Path(temporary) / "rebuilt"
                command = [sys.executable, "-m", "build", "--wheel", "--outdir", str(output)]
                if args.no_isolation:
                    command.append("--no-isolation")
                subprocess.run(command, cwd=source, check=True)
                rebuilt_wheel = output / wheel.name
                check_wheel(rebuilt_wheel, source, version)
                subprocess.run([sys.executable, "packaging/build_deb.py", str(rebuilt_wheel),
                                "--output-dir", str(output)], cwd=source, check=True)
                check_deb(output / deb.name, rebuilt_wheel, source, version)
        checksums = "".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
                            for path in (wheel, sdist, deb))
        (directory / "SHA256SUMS").write_text(checksums, encoding="utf-8")
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError, zipfile.BadZipFile, tarfile.TarError) as exc:
        print(f"Package verification failed: {exc}", file=sys.stderr)
        return 1
    print(f"Verified wheel, sdist and Debian package for {version}; wrote {directory / 'SHA256SUMS'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
