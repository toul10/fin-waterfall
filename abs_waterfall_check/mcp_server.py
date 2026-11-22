"""Stdio transport for the local run status interface."""
import argparse
import asyncio
from functools import partial
import logging
import os

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from .local import status_for


async def serve(read_status):
    server = Server("abs-waterfall-check")

    @server.list_tools()
    async def list_tools():
        return [types.Tool(
            name="verify_distribution",
            description="Return verification status for a locally registered run.",
            inputSchema={"type": "object", "properties": {
                "run_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
            }, "required": ["run_id"], "additionalProperties": False},
        )]

    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        # Handle validation here so SDK schema errors cannot quote private inputs.
        status = "HOLD"
        if name == "verify_distribution" and set(arguments) == {"run_id"}:
            status = read_status(arguments["run_id"])
        return [types.TextContent(type="text", text=status)]

    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


def main():
    parser = argparse.ArgumentParser()
    store = parser.add_mutually_exclusive_group(required=True)
    store.add_argument("--store", help="Local filesystem workspace")
    store.add_argument("--hosted", action="store_true", help="Read the PostgreSQL status view")
    args = parser.parse_args()
    # SDK diagnostics can include raw input before tool validation runs.
    logging.disable(logging.CRITICAL)
    read_status = partial(status_for, args.store)
    if args.hosted:
        from .postgres import Database
        # The MCP role only needs SELECT on wf_mcp_status. Leave schema
        # initialization and migrations to the workspace owner.
        database = Database(os.environ.get("DATABASE_URL"), os.environ.get("WATERFALL_ORGANIZATION"))
        read_status = database.run_status
    asyncio.run(serve(read_status))


if __name__ == "__main__":
    main()
