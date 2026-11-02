"""Configuration discovery for source checkouts and frozen executables."""

import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server  # noqa: E402


class ConfigurationTests(TestCase):
    def test_source_loads_env_next_to_server(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text("BRING_EMAIL=test@example.com\nBRING_PASSWORD=test\n")
            with patch.dict(os.environ, {}, clear=True), patch.object(server, "__file__", str(root / "server.py")), patch.object(sys, "frozen", False, create=True):
                server._load_environment()
                self.assertEqual(os.environ["BRING_EMAIL"], "test@example.com")
                self.assertEqual(os.environ["BRING_PASSWORD"], "test")

    def test_frozen_loads_env_next_to_executable_not_extracted_source(self):
        with TemporaryDirectory(prefix="bring config ") as directory:
            root = Path(directory)
            install = root / "installed app"
            extracted = root / "extracted"
            install.mkdir()
            extracted.mkdir()
            (install / ".env").write_text("BRING_EMAIL=test@example.com\nBRING_PASSWORD=test\n")
            (extracted / ".env").write_text("BRING_EMAIL=wrong@example.com\n")
            with patch.dict(os.environ, {}, clear=True), patch.object(server, "__file__", str(extracted / "server.py")), patch.object(sys, "executable", str(install / "bring-mcp-server")), patch.object(sys, "frozen", True, create=True):
                server._load_environment()
                self.assertEqual(os.environ["BRING_EMAIL"], "test@example.com")
                self.assertEqual(os.environ["BRING_PASSWORD"], "test")

    def test_existing_environment_takes_precedence(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text("BRING_EMAIL=file@example.com\nBRING_PASSWORD=file\n")
            with patch.dict(os.environ, {"BRING_EMAIL": "env@example.com", "BRING_PASSWORD": "env"}, clear=True), patch.object(server, "__file__", str(root / "server.py")), patch.object(sys, "frozen", False, create=True):
                server._load_environment()
                self.assertEqual(os.environ["BRING_EMAIL"], "env@example.com")
                self.assertEqual(os.environ["BRING_PASSWORD"], "env")

    def test_missing_env_file_is_allowed(self):
        with TemporaryDirectory() as directory:
            with patch.dict(os.environ, {}, clear=True), patch.object(server, "__file__", str(Path(directory) / "server.py")), patch.object(sys, "frozen", False, create=True):
                server._load_environment()
                self.assertNotIn("BRING_EMAIL", os.environ)
