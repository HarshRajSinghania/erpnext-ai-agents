"""The agents' side of MCP: connect to a tool server, list its tools, call them.

By default the tool server is our own (mcp_server.py) running inside this process and spoken to through MCP's in-memory transport, so an agent really is an MCP client: it
learns the tools from the server and calls them with MCP messages. `tools_url` points it at any other MCP server instead (Streamable HTTP, with a bearer token).
"""
from __future__ import annotations

import copy
import json
from typing import Any, Optional

from mcp import Client

_DROP = ("title", "default", "additionalProperties")


def compact_schema(schema: Any) -> Any:
    """A tool's input schema as a small model likes it: no titles or defaults, and 'string or null' is just 'string' (an optional argument is one that is not in 'required')."""
    if isinstance(schema, list):
        return [compact_schema(x) for x in schema]
    if not isinstance(schema, dict):
        return schema
    any_of = schema.get("anyOf")
    if isinstance(any_of, list):
        kinds = [x for x in any_of if not (isinstance(x, dict) and x.get("type") == "null")]
        if len(kinds) == 1 and isinstance(kinds[0], dict):
            merged = {**{k: v for k, v in schema.items() if k != "anyOf"}, **kinds[0]}
            return compact_schema(merged)
    return {k: compact_schema(v) for k, v in schema.items() if k not in _DROP}


class ToolBridge:
    def __init__(self, server: Any = None, url: str = "", token: str = "") -> None:  # an empty token means "send none", not a password  # nosec B107
        """server: an MCPServer to call in-process. url (+ token): a remote MCP server over Streamable HTTP."""
        if server is None and not url:
            raise ValueError("a tool server or a tools_url is needed")
        self.server, self.url, self.token = server, url, token
        self._client: Optional[Client] = None
        self._transport_cm: Any = None

    async def __aenter__(self) -> "ToolBridge":
        target: Any = self.server
        if target is None:
            from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
            http = create_mcp_http_client(headers={"Authorization": f"Bearer {self.token}"} if self.token else None)
            target = streamable_http_client(self.url, http_client=http)
        self._client = Client(target)
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client is not None:
            await self._client.__aexit__(*exc)
            self._client = None

    def _connected(self) -> Client:
        if self._client is None:
            raise RuntimeError("the tool bridge is not open: use it with 'async with'")
        return self._client

    async def tools(self, allow: Optional[tuple[str, ...]] = None) -> list[dict[str, Any]]:
        """[{name, description, parameters}] for the tools the agent may use (in the allowlist's order), as the model should see them."""
        self._connected()
        listed = {t.name: t for t in (await self._connected().list_tools()).tools}
        names = [n for n in (allow if allow is not None else tuple(listed)) if n in listed]
        return [{"name": n, "description": listed[n].description or "", "parameters": compact_schema(copy.deepcopy(listed[n].input_schema))} for n in names]

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """The tool's result, or {"error": "..."} - a failed tool is something the model should read, not an exception."""
        client = self._connected()
        try:
            res = await client.call_tool(name, arguments)
        except Exception as exc:  # noqa: BLE001 - a broken tool server must not take the task down
            return {"error": f"the tool server could not run '{name}' ({type(exc).__name__})"}
        if res.is_error:
            text = " ".join(getattr(c, "text", "") for c in res.content).strip()
            return {"error": text.split(": ", 1)[1] if text.startswith("Error executing tool") and ": " in text else text or "the tool failed"}
        if isinstance(res.structured_content, dict):
            return res.structured_content
        try:
            data = json.loads(" ".join(getattr(c, "text", "") for c in res.content))
        except ValueError:
            return {"error": "the tool sent a reply that is not data"}
        return data if isinstance(data, dict) else {"result": data}
