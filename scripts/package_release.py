"""Package only explicitly allowed release files, never local credentials."""

import argparse
import hashlib
from importlib import metadata
from pathlib import Path
import platform
import re
import shutil
import sys
import sysconfig
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def write_licenses(destination: Path) -> None:
    destination.mkdir()
    packages = sorted(metadata.distributions(), key=lambda d: d.metadata["Name"].lower())
    manifest = [f"Python {platform.python_version()}"]
    for distribution in packages:
        name = distribution.metadata["Name"]
        manifest.append(f"{name}=={distribution.version}")
        directory = destination / re.sub(r"[^A-Za-z0-9_.-]", "_", name)
        for file in distribution.files or []:
            if ".dist-info/" not in str(file) or not any(word in file.name.lower() for word in ("license", "copying", "notice")):
                continue
            source = Path(distribution.locate_file(file))
            if source.is_file():
                directory.mkdir(exist_ok=True)
                # Flatten paths without losing different vendor license files.
                metadata_index = next(i for i, part in enumerate(file.parts) if part.endswith(".dist-info"))
                filename = "__".join(file.parts[metadata_index + 1:])
                shutil.copyfile(source, directory / filename)
    python_license = next((p for p in (
        Path(sys.base_prefix) / "LICENSE.txt",
        Path(sysconfig.get_path("stdlib")) / "LICENSE.txt",
    ) if p.is_file()), None)
    if python_license is None:
        raise RuntimeError("Python LICENSE.txt not found; refusing to package without it")
    shutil.copyfile(python_license, destination / "Python-LICENSE.txt")
    (destination / "PACKAGES.txt").write_text("\n".join(manifest) + "\n", encoding="utf-8")


def build_archive(binary: Path, version: str, system: str, arch: str, output: Path) -> Path:
    if not re.fullmatch(r"v\d+\.\d+\.\d+(?:[-.][A-Za-z0-9.-]+)?", version):
        raise ValueError("Expected a version tag such as v1.1.1")
    if system not in {"linux", "macos", "windows"} or arch not in {"x86_64", "arm64"}:
        raise ValueError("Unsupported platform or architecture")
    binary = binary.resolve(strict=True)
    output.mkdir(parents=True, exist_ok=True)
    basename = f"bring-mcp-server-{version}-{system}-{arch}"
    archive = output / (basename + (".zip" if system == "windows" else ".tar.gz"))
    with tempfile.TemporaryDirectory() as temporary:
        bundle = Path(temporary) / "bring-mcp-server"
        bundle.mkdir()
        executable = bundle / ("bring-mcp-server.exe" if system == "windows" else "bring-mcp-server")
        shutil.copy2(binary, executable)
        executable.chmod(0o755)
        for name in ("README.md", "LICENSE", ".env.example"):
            shutil.copyfile(ROOT / name, bundle / name)
        write_licenses(bundle / "THIRD_PARTY_LICENSES")
        if system == "windows":
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as packed:
                for file in sorted(bundle.rglob("*")):
                    if file.is_file():
                        packed.write(file, file.relative_to(bundle.parent))
        else:
            def executable_mode(member: tarfile.TarInfo) -> tarfile.TarInfo:
                if member.name == f"{bundle.name}/{executable.name}":
                    member.mode = 0o755
                return member

            with tarfile.open(archive, "w:gz") as packed:
                packed.add(bundle, arcname=bundle.name, filter=executable_mode)
    with archive.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    archive.with_name(archive.name + ".sha256").write_text(f"{digest}  {archive.name}\n", encoding="ascii", newline="\n")
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--system", choices=["linux", "macos", "windows"], required=True)
    parser.add_argument("--arch", choices=["x86_64", "arm64"], required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "release")
    args = parser.parse_args()
    print(build_archive(args.binary, args.version, args.system, args.arch, args.output))
