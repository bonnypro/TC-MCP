from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from automation.services.profiles import load_profiles
from automation.services.session import AutomationSession

SERVER_NAME = "TC-AUTO"
SERVER_VERSION = "0.2.0"
PROTOCOL_VERSIONS = {"2024-11-05", "2025-06-18", "2025-11-25", "2026-07-28"}


def _json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _coerce_value(value, value_type: str):
    kind = value_type.upper()
    if kind == "BOOL":
        if isinstance(value, str):
            return value.strip().casefold() in {"1", "true", "yes", "on"}
        return bool(value)
    if kind in {"REAL", "LREAL"}:
        return float(value)
    if kind in {"INT", "DINT", "SINT", "UINT", "UDINT", "USINT", "BYTE", "WORD", "DWORD"}:
        return int(value)
    return value


def resolve_allowed_tools(profile_name: str | None) -> set[str] | None:
    """Return the tool names exposed by ``profile_name``.

    ``None`` means the full surface.  An unknown profile is a startup error:
    silently falling back to all tools would defeat the point of a profile.
    """
    if not profile_name:
        return None
    profiles = load_profiles()
    profile = profiles.get(profile_name)
    if profile is None:
        raise ValueError(f"unknown tool profile: {profile_name}")
    if "*" in profile.tools:
        return None
    known = {str(tool["name"]) for tool in TOOLS}
    unknown = sorted(set(profile.tools) - known)
    if unknown:
        raise ValueError(
            f"tool profile {profile_name!r} references unknown tools: {', '.join(unknown)}"
        )
    return set(profile.tools)


class ToolServer:
    def __init__(
        self,
        session: AutomationSession | None = None,
        *,
        allowed_tools: set[str] | None = None,
        profile: str | None = None,
    ) -> None:
        self.session = session or AutomationSession()
        self.allowed_tools = set(allowed_tools) if allowed_tools is not None else None
        self.profile = profile
        self._requests: dict[str, tuple[str, object]] = {}

    def allows(self, name: str) -> bool:
        return self.allowed_tools is None or name in self.allowed_tools

    def call(self, name: str, arguments: dict):
        if not self.allows(name):
            raise ValueError(f"tool not allowed by profile: {name}")
        request_key = arguments.get("request_key")
        request_hash = ""
        if request_key:
            payload = {key: value for key, value in arguments.items() if key != "request_key"}
            request_hash = json.dumps(
                {"tool": name, "arguments": payload},
                sort_keys=True,
                ensure_ascii=False,
                default=str,
            )
            cached = self._requests.get(str(request_key))
            if cached:
                if cached[0] != request_hash:
                    raise ValueError("request_key conflict: same key with different arguments")
                return cached[1]
        result = self._call(name, arguments)
        if request_key:
            if len(self._requests) > 1000:
                self._requests.clear()
            self._requests[str(request_key)] = (request_hash, result)
        return result

    def _call(self, name: str, arguments: dict):
        if name == "tc_status":
            return self.session.status()
        if name == "tc_list_templates":
            root = Path(__file__).resolve().parent / "templates"
            return {
                "templates": [
                    {"name": path.parent.name, "solution": str(path)}
                    for path in sorted(root.glob("*/*.sln"))
                ]
            }
        if name == "tc_create":
            return self.session.create(
                name=str(arguments.get("name", "session")),
                template=str(
                    arguments.get(
                        "template",
                        str(Path(__file__).resolve().parent / "templates/minimal/Minimal.sln"),
                    )
                ),
                platform=str(arguments.get("platform", "TwinCAT OS (x64)")),
                configuration=str(arguments.get("configuration", "Release")),
            )
        if name == "tc_write_pou":
            return self.session.write_pou(
                name=str(arguments["name"]),
                declaration=str(arguments["declaration"]),
                implementation=str(arguments.get("implementation", "")),
                kind=str(arguments.get("kind", "function_block")),
                expected_revision=arguments.get("expected_revision"),
                methods=arguments.get("methods"),
            )
        if name == "tc_write_dut":
            return self.session.write_dut(
                str(arguments["name"]),
                str(arguments["declaration"]),
                str(arguments.get("kind", "struct")),
                arguments.get("expected_revision"),
            )
        if name == "tc_write_gvl":
            return self.session.write_gvl(
                str(arguments["name"]),
                str(arguments["declaration"]),
                arguments.get("expected_revision"),
            )
        if name == "tc_project_tree":
            return self.session.project_tree(int(arguments.get("max_depth", 5)))
        if name == "tc_plc_add_library":
            return self.session.add_library(
                str(arguments["name"]),
                str(arguments.get("version", "*")),
                str(arguments.get("company", "Beckhoff Automation GmbH")),
            )
        if name == "tc_plc_list_libraries":
            return self.session.list_libraries()
        if name == "tc_project_info":
            return self.session.project_info(bool(arguments.get("include_symbols", False)))
        if name == "tc_archive_export":
            return self.session.archive_export(
                str(arguments["filename"]),
                str(arguments.get("scope", "solution")),
                bool(arguments.get("overwrite", False)),
            )
        if name == "tc_snapshot":
            return self.session.snapshot(str(arguments.get("label", "snapshot")))
        if name == "tc_restore":
            return self.session.restore(str(arguments["snapshot_path"]))
        if name == "tc_revision":
            return self.session.revision()
        if name == "tc_build":
            return self.session.build()
        if name == "tc_activate":
            return self.session.activate()
        if name == "tc_verify_symbol":
            return self.session.verify_symbol(str(arguments["symbol"]))
        if name == "tc_ads_state":
            return self.session.ads_state()
        if name == "tc_ads_read":
            return self.session.ads_read(
                str(arguments["symbol"]),
                str(arguments.get("type", "BOOL")),
            )
        if name == "tc_ads_write":
            value_type = str(arguments.get("type", "BOOL"))
            return self.session.ads_write(
                str(arguments["symbol"]),
                _coerce_value(arguments["value"], value_type),
                value_type,
            )
        if name == "tc_ads_read_many":
            return self.session.ads_read_many(list(arguments["symbols"]))
        if name == "tc_ads_write_many":
            writes = []
            for item in list(arguments["writes"]):
                value_type = str(item.get("type", "BOOL"))
                writes.append(
                    {
                        "symbol": str(item["symbol"]),
                        "value": _coerce_value(item["value"], value_type),
                        "type": value_type,
                    }
                )
            return self.session.ads_write_many(writes)
        if name == "tc_ads_wait":
            value_type = str(arguments.get("type", "BOOL"))
            return self.session.ads_wait(
                str(arguments["symbol"]),
                _coerce_value(arguments["expected"], value_type),
                value_type,
                float(arguments.get("timeout_s", 10.0)),
                int(arguments.get("interval_ms", 50)),
                float(arguments.get("tolerance", 0.0)),
            )
        if name == "tc_list_pous":
            return self.session.list_pous()
        if name == "tc_read_pou":
            return self.session.read_pou(str(arguments["name"]))
        if name == "tc_list_symbols":
            return self.session.list_symbols()
        if name == "tc_ads_discover":
            return self.session.ads_discover()
        if name == "tc_validate_project":
            return self.session.validate_project()
        if name == "tc_run_loadcontrol":
            return self.session.run_loadcontrol(
                float(arguments.get("setpoint_lbs", 1000.0)),
                float(arguments.get("hold_seconds", 1.0)),
                float(arguments.get("timeout_s", 30.0)),
            )
        if name == "tc_test_run":
            from automation.services.test_runner import TestPlanRunner

            return TestPlanRunner(self.session).run(
                arguments["plan"],
                arguments.get("name"),
                float(arguments.get("timeout_s", 300.0)),
            )
        if name == "tc_close":
            return self.session.close()
        raise ValueError(f"unknown tool: {name}")


TOOLS = [
    {
        "name": "tc_status",
        "description": "Return the current TC-AUTO session, XAE/ADS target, and workspace status.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "tc_list_templates",
        "description": "List local TwinCAT solution templates available to the automation session.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "tc_create",
        "description": "Copy a local TwinCAT template and open the copied solution in TcXaeShell. Only templates/ sources are allowed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "default": "session"},
                "template": {"type": "string", "default": "templates/minimal/Minimal.sln"},
                "platform": {"type": "string", "default": "TwinCAT OS (x64)"},
                "configuration": {"type": "string", "default": "Release"},
            },
        },
    },
    {
        "name": "tc_write_pou",
        "description": "Create or update a PLC POU through the XAE Automation Interface after static ST review.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "declaration": {"type": "string"},
                "implementation": {"type": "string", "default": ""},
                "kind": {"type": "string", "enum": ["program", "function", "function_block"], "default": "function_block"},
                "expected_revision": {"type": "string"},
                "methods": {
                    "type": "array",
                    "description": "Optional POU methods written as real <Method> children.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "declaration": {"type": "string"},
                            "implementation": {"type": "string", "default": ""},
                        },
                        "required": ["name", "declaration"],
                    },
                },
            },
            "required": ["name", "declaration"],
        },
    },
    {
        "name": "tc_write_dut",
        "description": "Create or update a DUT (enum/struct/union) in the open PLC project.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "declaration": {"type": "string"},
                "kind": {"type": "string", "enum": ["enum", "struct", "union"], "default": "struct"},
                "expected_revision": {"type": "string"},
            },
            "required": ["name", "declaration"],
        },
    },
    {
        "name": "tc_write_gvl",
        "description": "Create or update a global variable list in the open PLC project.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "declaration": {"type": "string"},
                "expected_revision": {"type": "string"},
            },
            "required": ["name", "declaration"],
        },
    },
    {
        "name": "tc_plc_add_library",
        "description": "Add a PLC library reference (name, version, company).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "version": {"type": "string", "default": "*"},
                "company": {"type": "string", "default": "Beckhoff Automation GmbH"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "tc_plc_list_libraries",
        "description": "List PLC library references in the open project.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "tc_project_info",
        "description": "Return project status, tree, libraries, and POU list (optionally symbols).",
        "inputSchema": {
            "type": "object",
            "properties": {"include_symbols": {"type": "boolean", "default": False}},
        },
    },
    {
        "name": "tc_project_tree",
        "description": "Return a bounded tree of the open PLC project.",
        "inputSchema": {
            "type": "object",
            "properties": {"max_depth": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5}},
        },
    },
    {
        "name": "tc_archive_export",
        "description": "Export a .tnzip/.tszip solution archive or .tpzip PLC archive under runtime/archive.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string"},
                "scope": {"type": "string", "enum": ["solution", "plc"], "default": "solution"},
                "overwrite": {"type": "boolean", "default": False},
            },
            "required": ["filename"],
        },
    },
    {
        "name": "tc_snapshot",
        "description": "Create a portable zip snapshot of the open project before a risky change.",
        "inputSchema": {
            "type": "object",
            "properties": {"label": {"type": "string", "default": "snapshot"}},
        },
    },
    {
        "name": "tc_restore",
        "description": "Restore a project snapshot from runtime/snapshots and reopen it in XAE.",
        "inputSchema": {
            "type": "object",
            "properties": {"snapshot_path": {"type": "string"}},
            "required": ["snapshot_path"],
        },
    },
    {
        "name": "tc_revision",
        "description": "Return the content revision and source-file count of the open project.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "tc_build",
        "description": "Build the open solution for the configured TwinCAT OS platform.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "tc_activate",
        "description": "Activate the open solution on the local simulation runtime and ensure the PLC is running.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "tc_verify_symbol",
        "description": "Verify that a symbol exists in the built TMC before activation.",
        "inputSchema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
    {
        "name": "tc_ads_state",
        "description": "Return the local ADS connection and PLC run-state.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "tc_ads_read",
        "description": "Read a PLC symbol from the local simulation runtime. ADS reads are read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "type": {"type": "string", "default": "BOOL"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "tc_ads_write",
        "description": "Write a PLC symbol on the local simulation runtime. Restricted to 127.0.0.1.1.1:851.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "value": {},
                "type": {"type": "string", "default": "BOOL"},
            },
            "required": ["symbol", "value"],
        },
    },
    {
        "name": "tc_ads_read_many",
        "description": "Read multiple PLC symbols in one serialized ADS operation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbols": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string"},
                            "type": {"type": "string", "default": "BOOL"},
                        },
                        "required": ["symbol"],
                    },
                }
            },
            "required": ["symbols"],
        },
    },
    {
        "name": "tc_ads_write_many",
        "description": "Write multiple PLC symbols in one serialized ADS operation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "writes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string"},
                            "value": {},
                            "type": {"type": "string", "default": "BOOL"},
                        },
                        "required": ["symbol", "value"],
                    },
                }
            },
            "required": ["writes"],
        },
    },
    {
        "name": "tc_ads_wait",
        "description": "Poll a PLC symbol until it equals the expected value or the timeout elapses.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "expected": {},
                "type": {"type": "string", "default": "BOOL"},
                "timeout_s": {"type": "number", "default": 10.0},
                "interval_ms": {"type": "integer", "default": 50},
                "tolerance": {"type": "number", "default": 0.0},
            },
            "required": ["symbol", "expected"],
        },
    },
    {
        "name": "tc_list_pous",
        "description": "List the POUs in the open PLC project.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "tc_read_pou",
        "description": "Read one POU declaration and implementation from the open PLC project.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "tc_list_symbols",
        "description": "List symbols from the compiled TMC of the open project.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "tc_ads_discover",
        "description": "Discover live ADS symbols from the running local PLC (symbol upload).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "tc_validate_project",
        "description": "Run the local TcPOU/TcDUT/TcGVL/plcproj validator on the open project.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "tc_test_run",
        "description": "Run a declarative tcauto.testplan/1 test plan over the open session and write report.json + junit.xml.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "plan": {"type": "object"},
                "name": {"type": "string"},
                "timeout_s": {"type": "number", "default": 300.0},
            },
            "required": ["plan"],
        },
    },
    {
        "name": "tc_run_loadcontrol",
        "description": "Create a new project, write FB_LoadControl + MAIN, build, activate, hold a simulated 1000 lb load for 1 s, release, and return an ADS test report.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "setpoint_lbs": {"type": "number", "default": 1000.0},
                "hold_seconds": {"type": "number", "default": 1.0},
                "timeout_s": {"type": "number", "default": 30.0},
            },
        },
    },
    {
        "name": "tc_close",
        "description": "Close the XAE/ADS session and release the simulation runner lock.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _result(value) -> str:
    return _json_text(
        {
            "content": [{"type": "text", "text": _json_text(value)}],
            "isError": False,
        }
    )


def _error(message: str) -> str:
    return _json_text({"content": [{"type": "text", "text": message}], "isError": True})


def handle(server: ToolServer, request: dict) -> dict | None:
    method = request.get("method")
    request_id = request.get("id")
    params = request.get("params") or {}

    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in PROTOCOL_VERSIONS else "2024-11-05"
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }

    if method == "notifications/initialized":
        return None

    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}

    if method in {"server/discover", "server/discover/request"}:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "capabilities": {"tools": {"listChanged": False}},
            },
        }

    if method == "tools/list":
        tools = TOOLS if server.allowed_tools is None else [
            tool for tool in TOOLS if tool["name"] in server.allowed_tools
        ]
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": tools}}

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        try:
            value = server.call(str(name), arguments)
            return {"jsonrpc": "2.0", "id": request_id, "result": json.loads(_result(value))}
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": request_id, "result": json.loads(_error(str(exc)))}

    if method == "resources/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "resources": [
                    {
                        "uri": "tcauto://session/current",
                        "name": "Current TC-AUTO session",
                        "mimeType": "application/json",
                    },
                    {
                        "uri": "tcauto://templates",
                        "name": "TwinCAT templates",
                        "mimeType": "application/json",
                    },
                ]
            },
        }

    if method == "resources/templates/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"resourceTemplates": []}}

    if method == "resources/read":
        uri = str(params.get("uri", ""))
        if uri == "tcauto://session/current":
            value = server.call("tc_status", {})
        elif uri == "tcauto://templates":
            value = server.call("tc_list_templates", {})
        else:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32602, "message": f"unknown resource: {uri}"},
            }
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "contents": [{"uri": uri, "mimeType": "application/json", "text": _json_text(value)}]
            },
        }

    if method == "prompts/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"prompts": []}}

    if request_id is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def serve() -> int:
    try:
        from scripts.mcp_lifecycle import install as install_lifecycle

        install_lifecycle()
    except Exception:
        pass
    profile = os.environ.get("TC_MCP_TOOL_PROFILE") or None
    try:
        allowed = resolve_allowed_tools(profile)
    except Exception as exc:
        sys.stderr.write(f"TC-AUTO tool profile error: {exc}\n")
        sys.stderr.flush()
        return 2
    server = ToolServer(allowed_tools=allowed, profile=profile)
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError as exc:
                sys.stderr.write(f"TC-AUTO invalid json: {exc}\n")
                sys.stderr.flush()
                continue
            response = handle(server, request)
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()
    finally:
        server.session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(serve())
