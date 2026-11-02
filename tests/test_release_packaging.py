import hashlib
from pathlib import Path
import sys
import tarfile
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.package_release import build_archive  # noqa: E402


class ReleasePackagingTests(TestCase):
    def test_archives_exclude_credentials_and_include_required_files(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("README.md", "LICENSE", ".env.example"):
                (root / name).write_text("Public example\n")
            for name in (".env", "credentials.json", "debug.log"):
                (root / name).write_text("PRIVATE_TEST_SENTINEL")
            binary = root / "fake-executable"
            binary.write_bytes(b"test executable")
            # Windows ignores POSIX executable bits when chmod is called.
            with patch("scripts.package_release.ROOT", root), patch.object(Path, "chmod"):
                for system in ("linux", "macos", "windows"):
                    with self.subTest(system=system):
                        archive = build_archive(binary, "v1.1.1", system, "x86_64", root / "output")
                        if system == "windows":
                            with zipfile.ZipFile(archive) as packed:
                                names = packed.namelist()
                                content = [packed.read(name) for name in names]
                        else:
                            with tarfile.open(archive) as packed:
                                files = [m for m in packed.getmembers() if m.isfile()]
                                names = [m.name for m in files]
                                content = [packed.extractfile(m).read() for m in files]
                                executable = packed.getmember("bring-mcp-server/bring-mcp-server")
                                self.assertTrue(executable.mode & 0o111)
                        relative = [name.removeprefix("bring-mcp-server/") for name in names]
                        self.assertIn(".env.example", relative)
                        self.assertIn("THIRD_PARTY_LICENSES/Python-LICENSE.txt", relative)
                        self.assertIn("THIRD_PARTY_LICENSES/PACKAGES.txt", relative)
                        self.assertNotIn(".env", relative)
                        self.assertNotIn("credentials.json", relative)
                        self.assertNotIn("debug.log", relative)
                        self.assertTrue(all(b"PRIVATE_TEST_SENTINEL" not in data for data in content))
                        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
                        self.assertEqual(archive.with_name(archive.name + ".sha256").read_text(), f"{digest}  {archive.name}\n")

    def test_invalid_release_tag_is_rejected(self):
        with self.assertRaises(ValueError):
            build_archive(Path("unused"), "../../private", "linux", "x86_64", Path("unused"))
