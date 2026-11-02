# Build on each target OS/architecture; keep stdio enabled for MCP.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, copy_metadata

root = Path(SPECPATH)
datas = collect_data_files("bring_api")
datas += copy_metadata("mcp", recursive=True)
datas += copy_metadata("bring-api", recursive=True)

a = Analysis(
    [str(root / "server.py")],
    pathex=[str(root)],
    datas=datas,
    hiddenimports=[],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="bring-mcp-server",
    console=True,
    strip=False,
    upx=False,
    codesign_identity=None,
)
