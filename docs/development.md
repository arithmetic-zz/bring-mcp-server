# Development

Run these commands from the repository root after completing the [source setup](../README.md#1-install-the-server).

Dependencies are declared in [requirements.txt](../requirements.txt). Keep MCP SDK 1.x (`mcp>=1.30.0,<2`): MCP 2 removes the handler decorators used by this server.

```bash
.venv/bin/python -m py_compile server.py
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m pip check
```

GitHub Actions runs dependency, compile, and test checks on Python 3.11, 3.12, and 3.13 for pushes and pull requests targeting `main`.

Tests mock Bring API access and require no credentials. `tests/test_server.py` covers server behavior; `tests/test_mcp_protocol.py` connects an MCP client and server over in-memory streams to test schema validation, error flags, and authentication recovery. These tests do not verify live login or changes to actual shopping lists.

For interactive MCP testing, use the Inspector (requires Node.js/npm):

```bash
npx @modelcontextprotocol/inspector .venv/bin/python server.py
```

With valid credentials, verify `get_lists` and `get_list` first. On a chosen list, add uniquely named test items using `add_item` or `batch_update` (`ADD`), read them back, then delete them with `remove_item` and confirm they are absent. These calls modify real Bring data; clean up only the items created for the test.

Contributor conventions and architecture details are in [AGENTS.md](../AGENTS.md).

## Building release downloads

Build on the target OS and architecture:

```bash
.venv/bin/python -m pip install -r requirements-build.txt
.venv/bin/python -m PyInstaller --noconfirm --clean bring-mcp-server.spec
.venv/bin/python scripts/package_release.py --binary dist/bring-mcp-server --version v1.4.0 --system macos --arch arm64
.venv/bin/python scripts/smoke_test.py --archive dist/release/bring-mcp-server-v1.4.0-macos-arm64.tar.gz
```

Adjust the version, system (`macos`, `linux`, `windows`), architecture and executable suffix for your target. The package contains the executable, this README, `.env.example`, the project license and third-party license texts. The real `.env` is never included.

The `Release binaries` workflow builds all four targets when a `v*` tag is pushed. It can also be run manually for an existing version tag. Every target runs the source tests and an MCP smoke test against its extracted archive. The release is published with downloads and checksums only after all targets succeed. Publishing expects that the version has no existing GitHub Release; it does not overwrite releases or move tags.
