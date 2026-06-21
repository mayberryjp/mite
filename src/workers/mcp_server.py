import json
import logging
import time

from bottle import Bottle, request, response, run

from src.core.ai_discovery import classify_patterns
from src.core.config import MITE_MCP_HOST, MITE_MCP_PORT, VERSION
from src.core.db import (
    create_action,
    delete_action,
    get_action_by_id,
    get_actions,
    get_alerts,
    get_all_patterns,
    get_all_settings,
    get_logs,
    get_logs_by_pattern,
    get_pattern_by_id,
    get_pending_patterns,
    get_stats,
    init_database,
    update_action,
    update_pattern_user_override,
)
from src.utils.locallogging import log_error, log_info

logger = logging.getLogger(__name__)

VALID_CLASSIFICATIONS = {"critical", "high", "medium", "low", "noise"}

# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

TOOLS = {}


def mcp_tool(name, description, parameters=None):
    """Decorator to register a function as an MCP tool."""
    if parameters is None:
        parameters = {"type": "object", "properties": {}}

    def decorator(func):
        TOOLS[name] = {
            "name": name,
            "description": description,
            "inputSchema": parameters,
            "function": func,
        }
        return func

    return decorator


def _as_int(value, field_name, default=None, min_value=None):
    if value is None:
        if default is not None:
            return default
        raise ValueError(f"Missing required parameter: {field_name}")

    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid integer for {field_name}") from exc

    if min_value is not None and parsed < min_value:
        raise ValueError(f"{field_name} must be >= {min_value}")

    return parsed


def _as_bool(value, field_name):
    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        if value in (0, 1):
            return bool(value)

    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("true", "1", "yes", "on"):
            return True
        if normalized in ("false", "0", "no", "off"):
            return False

    raise ValueError(f"Invalid boolean for {field_name}")


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------


@mcp_tool(
    "list_patterns",
    "List learned patterns with optional pagination and classification filter.",
    {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Maximum number of patterns to return. Use null for no limit.",
                "default": 100,
            },
            "offset": {
                "type": "integer",
                "description": "Pagination offset.",
                "default": 0,
            },
            "classification": {
                "type": "string",
                "description": "Filter by AI classification (critical/high/medium/low/noise/pending).",
            },
        },
    },
)
def list_patterns(arguments):
    limit = arguments.get("limit", 100)
    offset = _as_int(arguments.get("offset", 0), "offset", default=0, min_value=0)
    classification = arguments.get("classification")

    parsed_limit = None
    if limit is not None:
        parsed_limit = _as_int(limit, "limit", default=100, min_value=1)

    items, total = get_all_patterns(
        limit=parsed_limit,
        offset=offset,
        classification=classification,
    )
    return {
        "items": items,
        "total": total,
        "limit": parsed_limit,
        "offset": offset,
        "classification": classification,
    }


@mcp_tool(
    "get_pattern",
    "Get a single pattern by id.",
    {
        "type": "object",
        "properties": {
            "pattern_id": {
                "type": "integer",
                "description": "Pattern id.",
            }
        },
        "required": ["pattern_id"],
    },
)
def get_pattern(arguments):
    pattern_id = _as_int(arguments.get("pattern_id"), "pattern_id", min_value=1)
    pattern = get_pattern_by_id(pattern_id)
    if not pattern:
        raise ValueError(f"Pattern not found: {pattern_id}")
    return pattern


@mcp_tool(
    "set_pattern_override",
    "Set or clear user override classification on a pattern.",
    {
        "type": "object",
        "properties": {
            "pattern_id": {
                "type": "integer",
                "description": "Pattern id.",
            },
            "classification": {
                "type": ["string", "null"],
                "description": "One of critical/high/medium/low/noise, or null to clear override.",
            },
        },
        "required": ["pattern_id", "classification"],
    },
)
def set_pattern_override(arguments):
    pattern_id = _as_int(arguments.get("pattern_id"), "pattern_id", min_value=1)
    classification = arguments.get("classification")

    pattern = get_pattern_by_id(pattern_id)
    if not pattern:
        raise ValueError(f"Pattern not found: {pattern_id}")

    if classification is not None and classification not in VALID_CLASSIFICATIONS:
        raise ValueError(f"Invalid classification: {classification}")

    update_pattern_user_override(pattern_id, classification)
    return {
        "status": "ok",
        "pattern_id": pattern_id,
        "user_override": classification,
    }


@mcp_tool(
    "list_pending_patterns",
    "Return pending patterns waiting for AI classification.",
    {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Maximum pending patterns to return.",
                "default": 50,
            }
        },
    },
)
def list_pending_patterns(arguments):
    limit = _as_int(arguments.get("limit", 50), "limit", default=50, min_value=1)
    pending = get_pending_patterns(limit=limit)
    return {"items": pending, "count": len(pending), "limit": limit}


@mcp_tool(
    "classify_pending_patterns",
    "Trigger one AI classification pass for pending patterns.",
    {
        "type": "object",
        "properties": {
            "batch_size": {
                "type": "integer",
                "description": "Number of pending patterns to classify this run.",
                "default": 20,
            }
        },
    },
)
def classify_pending_patterns(arguments):
    batch_size = _as_int(
        arguments.get("batch_size", 20),
        "batch_size",
        default=20,
        min_value=1,
    )
    pending = get_pending_patterns(limit=batch_size)
    if not pending:
        return {
            "status": "ok",
            "message": "No pending patterns to classify",
            "classified": 0,
        }

    result = classify_patterns(pending)
    return {
        "status": "ok",
        "pending_count": len(pending),
        "result": result,
    }


@mcp_tool(
    "list_logs",
    "List processed logs with optional filters.",
    {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "default": 100},
            "offset": {"type": "integer", "default": 0},
            "host": {"type": "string"},
            "source_ip": {"type": "string"},
            "program": {"type": "string"},
            "severity": {"type": "string"},
            "search": {"type": "string"},
            "start": {
                "type": "string",
                "description": "Start datetime (SQLite datetime-compatible text).",
            },
            "end": {
                "type": "string",
                "description": "End datetime (SQLite datetime-compatible text).",
            },
        },
    },
)
def list_logs(arguments):
    limit = _as_int(arguments.get("limit", 100), "limit", default=100, min_value=1)
    offset = _as_int(arguments.get("offset", 0), "offset", default=0, min_value=0)

    items, total = get_logs(
        limit=limit,
        offset=offset,
        host=arguments.get("host"),
        source_ip=arguments.get("source_ip"),
        program=arguments.get("program"),
        severity=arguments.get("severity"),
        search=arguments.get("search"),
        start=arguments.get("start"),
        end=arguments.get("end"),
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@mcp_tool(
    "list_alerts",
    "List alerts with optional filters.",
    {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "default": 100},
            "offset": {"type": "integer", "default": 0},
            "severity": {"type": "string"},
            "host": {"type": "string"},
            "source_ip": {"type": "string"},
            "pattern_id": {"type": "integer"},
            "search": {"type": "string"},
        },
    },
)
def list_alerts(arguments):
    limit = _as_int(arguments.get("limit", 100), "limit", default=100, min_value=1)
    offset = _as_int(arguments.get("offset", 0), "offset", default=0, min_value=0)

    pattern_id = arguments.get("pattern_id")
    if pattern_id is not None:
        pattern_id = _as_int(pattern_id, "pattern_id", min_value=1)

    items, total = get_alerts(
        limit=limit,
        offset=offset,
        severity=arguments.get("severity"),
        host=arguments.get("host"),
        source_ip=arguments.get("source_ip"),
        pattern_id=pattern_id,
        search=arguments.get("search"),
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@mcp_tool(
    "get_stats",
    "Return dashboard stats snapshot.",
)
def list_stats(arguments):
    del arguments
    return get_stats()


@mcp_tool(
    "list_settings",
    "Return all runtime settings.",
)
def list_settings(arguments):
    del arguments
    return get_all_settings()


@mcp_tool(
    "get_pattern_logs",
    "List logs mapped to a specific pattern.",
    {
        "type": "object",
        "properties": {
            "pattern_id": {"type": "integer", "description": "Pattern id."},
            "limit": {"type": "integer", "default": 100},
            "offset": {"type": "integer", "default": 0},
        },
        "required": ["pattern_id"],
    },
)
def get_pattern_logs(arguments):
    pattern_id = _as_int(arguments.get("pattern_id"), "pattern_id", min_value=1)
    limit = _as_int(arguments.get("limit", 100), "limit", default=100, min_value=1)
    offset = _as_int(arguments.get("offset", 0), "offset", default=0, min_value=0)

    items, total = get_logs_by_pattern(pattern_id, limit=limit, offset=offset)
    return {
        "pattern_id": pattern_id,
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@mcp_tool(
    "list_actions",
    "List actions with optional pagination and filters.",
    {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "default": 100},
            "offset": {"type": "integer", "default": 0},
            "acknowledged": {"type": "boolean"},
            "search": {"type": "string"},
        },
    },
)
def list_actions(arguments):
    limit = _as_int(arguments.get("limit", 100), "limit", default=100, min_value=1)
    offset = _as_int(arguments.get("offset", 0), "offset", default=0, min_value=0)

    acknowledged = arguments.get("acknowledged")
    if acknowledged is not None:
        acknowledged = _as_bool(acknowledged, "acknowledged")

    items, total = get_actions(
        limit=limit,
        offset=offset,
        acknowledged=acknowledged,
        search=arguments.get("search"),
    )
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "acknowledged": acknowledged,
    }


@mcp_tool(
    "get_action",
    "Get a single action by id.",
    {
        "type": "object",
        "properties": {
            "action_id": {"type": "integer", "description": "Action id."}
        },
        "required": ["action_id"],
    },
)
def get_action(arguments):
    action_id = _as_int(arguments.get("action_id"), "action_id", min_value=1)
    item = get_action_by_id(action_id)
    if not item:
        raise ValueError(f"Action not found: {action_id}")
    return item


@mcp_tool(
    "create_action",
    "Create a new action.",
    {
        "type": "object",
        "properties": {
            "action_text": {
                "type": "string",
                "description": "Action text.",
            },
            "acknowledged": {
                "type": "boolean",
                "description": "Whether the action is acknowledged.",
                "default": False,
            },
        },
        "required": ["action_text"],
    },
)
def create_action_tool(arguments):
    action_text = arguments.get("action_text")
    if not isinstance(action_text, str) or not action_text.strip():
        raise ValueError("action_text must be a non-empty string")

    acknowledged = arguments.get("acknowledged", False)
    acknowledged = _as_bool(acknowledged, "acknowledged")

    action_id = create_action(action_text.strip(), acknowledged)
    return get_action_by_id(action_id)


@mcp_tool(
    "update_action",
    "Update action text and/or acknowledged flag.",
    {
        "type": "object",
        "properties": {
            "action_id": {"type": "integer", "description": "Action id."},
            "action_text": {"type": "string", "description": "Action text."},
            "acknowledged": {
                "type": "boolean",
                "description": "Whether the action is acknowledged.",
            },
        },
        "required": ["action_id"],
    },
)
def update_action_tool(arguments):
    action_id = _as_int(arguments.get("action_id"), "action_id", min_value=1)
    action_text = arguments.get("action_text") if "action_text" in arguments else None
    acknowledged = (
        _as_bool(arguments.get("acknowledged"), "acknowledged")
        if "acknowledged" in arguments
        else None
    )

    if action_text is None and acknowledged is None:
        raise ValueError("At least one of action_text or acknowledged is required")

    if action_text is not None:
        if not isinstance(action_text, str) or not action_text.strip():
            raise ValueError("action_text must be a non-empty string")
        action_text = action_text.strip()

    updated = update_action(
        action_id,
        action_text=action_text,
        acknowledged=acknowledged,
    )
    if not updated:
        if not get_action_by_id(action_id):
            raise ValueError(f"Action not found: {action_id}")
        raise ValueError("No valid fields provided")

    return get_action_by_id(action_id)


@mcp_tool(
    "delete_action",
    "Delete an action by id.",
    {
        "type": "object",
        "properties": {
            "action_id": {"type": "integer", "description": "Action id."}
        },
        "required": ["action_id"],
    },
)
def delete_action_tool(arguments):
    action_id = _as_int(arguments.get("action_id"), "action_id", min_value=1)
    deleted = delete_action(action_id)
    if not deleted:
        raise ValueError(f"Action not found: {action_id}")

    return {"status": "ok", "action_id": action_id}


# ---------------------------------------------------------------------------
# JSON-RPC helpers
# ---------------------------------------------------------------------------

SERVER_INFO = {
    "name": "mite-mcp",
    "version": VERSION,
}

CAPABILITIES = {
    "tools": {},
}


def jsonrpc_success(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def jsonrpc_error(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


# ---------------------------------------------------------------------------
# Bottle app and MCP endpoint
# ---------------------------------------------------------------------------

app = Bottle()


@app.route("/mcp", method=["POST"])
def mcp_endpoint():
    """MCP Streamable HTTP transport endpoint for JSON-RPC 2.0."""
    response.content_type = "application/json"

    try:
        body = request.json
    except Exception:
        return json.dumps(jsonrpc_error(None, -32700, "Parse error"))

    if not body or "method" not in body:
        req_id = body.get("id") if isinstance(body, dict) else None
        return json.dumps(jsonrpc_error(req_id, -32600, "Invalid request"))

    method = body.get("method")
    params = body.get("params", {})
    req_id = body.get("id")

    log_info(logger, f"[INFO] MCP request: method={method} id={req_id}")

    if method == "initialize":
        result = {
            "protocolVersion": "2025-03-26",
            "serverInfo": SERVER_INFO,
            "capabilities": CAPABILITIES,
        }
        return json.dumps(jsonrpc_success(req_id, result))

    if method == "notifications/initialized":
        return json.dumps(jsonrpc_success(req_id, {}))

    if method == "tools/list":
        tool_list = []
        for tool in TOOLS.values():
            tool_list.append(
                {
                    "name": tool["name"],
                    "description": tool["description"],
                    "inputSchema": tool["inputSchema"],
                }
            )
        return json.dumps(jsonrpc_success(req_id, {"tools": tool_list}))

    if method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        if tool_name not in TOOLS:
            return json.dumps(
                jsonrpc_error(req_id, -32602, f"Unknown tool: {tool_name}")
            )

        try:
            result = TOOLS[tool_name]["function"](arguments)
            content = [{"type": "text", "text": json.dumps(result, default=str)}]
            return json.dumps(jsonrpc_success(req_id, {"content": content}))
        except Exception as e:
            log_error(logger, f"[ERROR] MCP tool {tool_name} failed: {e}")
            content = [{"type": "text", "text": json.dumps({"error": str(e)})}]
            return json.dumps(
                jsonrpc_success(req_id, {"content": content, "isError": True})
            )

    return json.dumps(jsonrpc_error(req_id, -32601, f"Method not found: {method}"))


def main():
    log_info(
        logger, "[INFO] MCP server waiting 5 seconds for database initialization..."
    )
    time.sleep(5)
    init_database()
    log_info(logger, f"[INFO] MCP server starting on {MITE_MCP_HOST}:{MITE_MCP_PORT}")
    run(app, host=MITE_MCP_HOST, port=MITE_MCP_PORT, server="waitress", quiet=True)


if __name__ == "__main__":
    main()
