"""Run the distributed executable through a real MCP stdio client, offline."""

import argparse
import asyncio
import os
import sys
from pathlib import Path
import tarfile
import tempfile
import zipfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError


async def smoke_test(executable: Path) -> None:
    env = dict(os.environ)
    env.update(BRING_EMAIL="", BRING_PASSWORD="", PYTHONPATH="")
    env.pop("PYTHONHOME", None)
    with tempfile.TemporaryDirectory(prefix="bring smoke cwd ") as cwd, tempfile.TemporaryFile(mode="w+") as errors:
        params = StdioServerParameters(command=str(executable.resolve()), env=env, cwd=cwd)
        try:
            async with asyncio.timeout(60):
                async with stdio_client(params, errlog=errors) as (read, write):
                    async with ClientSession(read, write) as client:
                        await client.initialize()
                        tools = await client.list_tools()
                        assert {t.name for t in tools.tools} == {"get_lists", "get_list", "search_items", "add_item", "remove_item", "complete_item", "batch_update"}
                        schemas = {tool.name: tool.inputSchema for tool in tools.tools}
                        assert "format" in schemas["get_lists"]["properties"]
                        assert "filter" in schemas["search_items"]["properties"]
                        for name in ("add_item", "remove_item", "complete_item", "batch_update"):
                            assert "dry_run" in schemas[name]["properties"]
                        assert schemas["add_item"]["properties"]["attributes"]["maxItems"] == 0
                        assert "ATTRIBUTE_UPDATE" not in schemas["batch_update"]["properties"]["operation"]["enum"]
                        assert "operation" in schemas["batch_update"]["properties"]["items"]["items"]["properties"]
                        resources = await client.list_resources()
                        assert [str(r.uri) for r in resources.resources] == ["bring://lists"]
                        templates = await client.list_resource_templates()
                        assert len(templates.resourceTemplates) == 1
                        prompts = await client.list_prompts()
                        assert {p.name for p in prompts.prompts} == {"meal_plan", "weekly_groceries"}
                        for name, args in (("meal_plan", {"meal": "Test"}), ("weekly_groceries", {})):
                            assert (await client.get_prompt(name, args)).messages
                        failure = await client.call_tool("get_lists", {})
                        assert failure.isError is True
                        assert failure.content[0].text.startswith("Configuration error:")
                        for name, args in (("get_lists", {"format": "json"}),
                                           ("search_items", {"filter": "Milk"}),
                                           ("batch_update", {"list_uuid": "offline", "items": [{"item_id": "Milk"}], "dry_run": True})):
                            result = await client.call_tool(name, args)
                            assert result.isError and result.content[0].text.startswith("Configuration error:")
                        for name in ("add_item", "remove_item", "complete_item"):
                            result = await client.call_tool(name, {"list_uuid": "offline", "item_name": "Offline"})
                            assert result.isError and result.content[0].text.startswith("Configuration error:")
                        for name, args in (
                            ("add_item", {"list_uuid": "offline", "item_id": "Milk", "attributes": [{"type": "urgent", "content": {"urgent": True}}]}),
                            ("batch_update", {"list_uuid": "offline", "operation": "ATTRIBUTE_UPDATE", "items": [{"item_id": "Milk"}], "dry_run": True}),
                        ):
                            result = await client.call_tool(name, args)
                            assert result.isError and "attribute writes are not supported" in result.content[0].text
                        try:
                            await client.read_resource("bring://unknown")
                        except McpError:
                            pass
                        else:
                            raise AssertionError("Unknown resource was accepted")
        except Exception:
            errors.seek(0)
            print(errors.read(), file=sys.stderr)
            raise
    print("PASS: packaged MCP initialization, tools, resources, prompts, aliases and error flags (offline)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--binary", type=Path)
    group.add_argument("--archive", type=Path)
    args = parser.parse_args()
    if args.binary:
        asyncio.run(smoke_test(args.binary))
    else:
        with tempfile.TemporaryDirectory(prefix="bring extracted archive ") as directory:
            root = Path(directory)
            if args.archive.suffix == ".zip":
                with zipfile.ZipFile(args.archive) as archive:
                    archive.extractall(root)
            else:
                with tarfile.open(args.archive) as archive:
                    archive.extractall(root, filter="data")
            executable = root / "bring-mcp-server" / ("bring-mcp-server.exe" if os.name == "nt" else "bring-mcp-server")
            assert not (executable.parent / ".env").exists(), "Archive contains credentials"
            assert (executable.parent / ".env.example").is_file()
            assert (executable.parent / "THIRD_PARTY_LICENSES" / "Python-LICENSE.txt").is_file()
            asyncio.run(smoke_test(executable))
