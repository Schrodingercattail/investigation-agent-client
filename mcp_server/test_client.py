import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    backend_dir = os.path.join(project_root, "backend")

    server_params = StdioServerParameters(
        command=sys.executable,
        args=[
            os.path.join(project_root, "mcp_server", "server.py"),
        ],
        env={
            "PYTHONPATH": backend_dir,
        },
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            print("=== Server Info ===")
            print(session.server_info)

            print("\n=== Available Tools ===")
            tools = await session.list_tools()

            for tool in tools.tools:
                print(f"- {tool.name}")
                print(f"  description: {tool.description}")
                print(f"  input_schema: {tool.input_schema}")

            print("\n=== Calling risk_case_fetch(U00299) ===")
            result = await session.call_tool(
                "risk_case_fetch",
                arguments={"case_id": "U00299"},
            )

            print("\n=== MCP Tool Result ===")
            print("content:")
            for item in result.content:
                print(item)

            print("\nstructured_content:")
            print(json.dumps(result.structured_content, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
