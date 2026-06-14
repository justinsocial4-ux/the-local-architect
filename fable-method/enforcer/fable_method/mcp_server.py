"""
mcp_server.py — Fable Method stdio MCP server.

Exposes the Engine as six MCP tools:

    begin_task        → Engine.create_session
    get_state         → Engine.get_state
    submit_stage      → Engine.submit
    answer_questions  → Engine.provide_answers   (V9 — interactive mode)
    finalize          → Engine.finalize
    set_rigor         → Engine.set_rigor

Run with:
    python -m fable_method.mcp_server

Or register it in your MCP client config (see README).

The pipeline is MANDATORY.  Every stage must pass its gate before the next
stage is accepted.  ``finalize`` is refused until all required stages pass.

v2 behaviour notes
------------------
- begin_task may return {refused:true, category, reason, session_id, status:"refused"}
  when the goal matches a prohibited safety category (V10).  Use override_safety=true
  to proceed with an audit log of the bypass.
- submit_stage may return needs_user_input:true + status:"awaiting_input" when the
  frame stage has questions and mode="interactive" (V9). Call answer_questions to advance.
- verify artifacts must include a concrete evidence field on at least one check (V5).
  evidence must contain a digit, PASS/FAIL token, file:line, or quoted/backtick snippet.
- After a passing revise the engine may route BACK to verify (backtracking loop, V7).
  follow current_stage in the submit response — it may be "verify" again.
  loop_count is included in every submit response.
- Sessions can auto-escalate to FULL rigor when critique has a blocker finding (V2).
  escalated_to is included in submit/get_state responses when raised.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

# The mcp package is an optional dependency — give a clear error if missing.
try:
    import mcp.server.stdio  # type: ignore
    from mcp.server import Server  # type: ignore
    from mcp.types import (  # type: ignore
        CallToolResult,
        ListToolsResult,
        TextContent,
        Tool,
    )
except ImportError as _mcp_err:
    # This module constructs Server/Tool objects at import time, so it genuinely cannot be
    # imported without the 'mcp' SDK. Raise a CATCHABLE ImportError (rather than sys.exit,
    # which would kill the whole interpreter / any test collector that merely touches this
    # module). The clean install hint is still printed for someone running the server directly.
    print(
        "ERROR: The 'mcp' package is required to run the MCP server.\n"
        "Install it with:  pip install mcp\n"
        f"Detail: {_mcp_err}",
        file=sys.stderr,
    )
    raise ImportError(
        "fable_method.mcp_server requires the 'mcp' package (pip install mcp)"
    ) from _mcp_err

from .engine import Engine

# ---------------------------------------------------------------------------
# Engine instance — store_dir can be overridden via env var
# ---------------------------------------------------------------------------

_store_dir = os.environ.get("FABLE_STORE_DIR", os.path.expanduser("~/.fable_method"))
_engine = Engine(store_dir=_store_dir)

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

_TOOLS: list[Tool] = [
    Tool(
        name="begin_task",
        description=(
            "Start a new Fable Method session for a goal. "
            "This creates the session and returns the first stage's instructions "
            "and required artifact schema. "
            "The pipeline is MANDATORY — you must complete every required stage "
            "in order before finalize will be accepted. "
            "\n\nv2 NOTES:\n"
            "- Sessions can be REFUSED for safety (V10): if the goal matches a prohibited "
            "category (weapons, malware, fraud/phishing, CSAM, etc.) the response is "
            "{refused:true, category, reason, session_id, status:'refused'}. "
            "No stages can be submitted on a refused session. "
            "Set override_safety=true to proceed with operator-accountability logging.\n"
            "- Use mode='interactive' (V9) so that frame-stage questions pause for "
            "human answers via answer_questions; default 'headless' stamps "
            "proceeded_without_answers:true in the certificate.\n"
            "- Returns: {session_id, current_stage, rigor_level, mode, status, "
            "instructions, required_artifact, next_action}."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "The task or goal to work through rigorously.",
                },
                "profile": {
                    "type": "string",
                    "enum": ["universal", "ai_builder", "entrepreneur"],
                    "default": "universal",
                    "description": "Reasoning profile that adds domain-specific guidance.",
                },
                "rigor": {
                    "type": "string",
                    "enum": ["low", "medium", "full", "adaptive"],
                    "default": "adaptive",
                    "description": "Rigor level controlling which stages are required.",
                },
                "involves_facts": {
                    "type": "boolean",
                    "description": (
                        "Set true if the task involves present-day factual claims "
                        "(activates the research gate in medium rigor)."
                    ),
                },
                "mode": {
                    "type": "string",
                    "enum": ["headless", "interactive"],
                    "default": "headless",
                    "description": (
                        "Session mode (V9). 'interactive': frame questions pause for "
                        "human answers via answer_questions. "
                        "'headless' (default): questions are allowed but the certificate "
                        "is stamped proceeded_without_answers:true."
                    ),
                },
                "override_safety": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Override the safety screen (V10). If the goal would normally be "
                        "refused, setting this to true allows the session to proceed. "
                        "The bypass is LOGGED in the certificate for audit accountability."
                    ),
                },
            },
            "required": ["goal"],
        },
    ),
    Tool(
        name="get_state",
        description=(
            "Retrieve the full current state of a session: completed stages, "
            "current stage, gate history, and all submitted artifacts. "
            "Use this to inspect progress or resume a session. "
            "\n\nv2 additions in response: mode, loop_count, iterations, "
            "pending_limitations, escalated_to, safety (refused/category), awaiting_input."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "The session ID returned by begin_task.",
                }
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="submit_stage",
        description=(
            "Submit an artifact for the current pipeline stage. "
            "The engine validates the artifact against the stage's gate. "
            "On PASS: advances to the next required stage and returns its instructions. "
            "On FAIL: returns violations with fix hints — you must fix them and resubmit. "
            "You cannot skip stages. Submitting out of order is rejected. "
            "\n\nv2 NOTES:\n"
            "- verify artifacts MUST include a concrete evidence field on at least one "
            "check (V5). Evidence must contain a digit, PASS/FAIL token, file:line, or "
            "quoted/backtick snippet. Missing evidence → NO_EVIDENCE violation.\n"
            "- After a passing revise the engine may return current_stage='verify' "
            "instead of 'deliver' — this is a backtracking loop (V7). Follow "
            "current_stage in the response; loop_count increments each loop.\n"
            "- In interactive mode, a passing frame with questions returns "
            "needs_user_input:true + status:'awaiting_input'. Call answer_questions "
            "before submitting the next stage.\n"
            "- loop_count and (when raised) escalated_to are included in every response.\n"
            "- pending_limitations from research unknowns and assumed sources must be "
            "covered in deliver.limitations (V8)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "The session ID returned by begin_task.",
                },
                "stage": {
                    "type": "string",
                    "description": "The stage name (e.g. 'frame', 'plan', 'draft').",
                },
                "artifact": {
                    "type": "object",
                    "description": (
                        "The artifact dict for this stage. "
                        "Shape depends on the stage — see the required_artifact field "
                        "returned by begin_task or the previous submit_stage call."
                    ),
                },
            },
            "required": ["session_id", "stage", "artifact"],
        },
    ),
    Tool(
        name="answer_questions",
        description=(
            "Provide answers to frame-stage questions in interactive mode (V9). "
            "Call this when submit_stage returns needs_user_input:true and "
            "status:'awaiting_input'. Records the answers into the session and "
            "advances to the next stage. "
            "\n\nThe answers parameter is a dict mapping each question string to its "
            "answer string (e.g. {\"What is the target audience?\": \"Small businesses\"}). "
            "Returns the next stage's instructions and required artifact schema."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "The session ID that is awaiting input.",
                },
                "answers": {
                    "type": "object",
                    "description": (
                        "Dict mapping question strings to answer strings. "
                        "Each key should be a question from the frame artifact's "
                        "'questions' list; the value is the human's answer."
                    ),
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["session_id", "answers"],
        },
    ),
    Tool(
        name="finalize",
        description=(
            "Finalize the session and generate the audit certificate. "
            "REFUSED until every required stage for the session's rigor level has passed. "
            "REFUSED if the session was refused by the safety screen. "
            "On success: returns {finalized: true, certificate: {...}}. "
            "On refusal: returns {finalized: false, missing_stages: [...], message}. "
            "\n\nv2 certificate additions: loop_count, iterations, escalations, "
            "proceeded_without_answers, safety_screen, evidence_summary."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "The session ID to finalize.",
                }
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="set_rigor",
        description=(
            "Operator override to raise the rigor level of a session. "
            "Rigor can only be raised, never lowered (per PROTOCOL §3). "
            "Returns the updated session state."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "The session ID to update.",
                },
                "rigor": {
                    "type": "string",
                    "enum": ["low", "medium", "full", "adaptive"],
                    "description": "The new (higher) rigor level. 'adaptive' is "
                                   "treated as 'full' by the raise-only override.",
                },
            },
            "required": ["session_id", "rigor"],
        },
    ),
]


# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

server = Server("fable-method")


@server.list_tools()  # type: ignore[arg-type]
async def list_tools() -> ListToolsResult:
    return ListToolsResult(tools=_TOOLS)


@server.call_tool()  # type: ignore[arg-type]
async def call_tool(name: str, arguments: dict) -> CallToolResult:
    """Dispatch MCP tool calls to the appropriate Engine method."""
    try:
        result = _dispatch(name, arguments)
    except Exception as exc:
        result = {"error": str(exc), "tool": name}

    text = json.dumps(result, indent=2, default=str)
    return CallToolResult(content=[TextContent(type="text", text=text)])


def _dispatch(name: str, args: dict) -> dict:
    """Map tool name to Engine method and call it."""
    if name == "begin_task":
        kwargs: dict = {
            "goal": args["goal"],
            "profile": args.get("profile", "universal"),
            "rigor": args.get("rigor", "adaptive"),
            "mode": args.get("mode", "headless"),
            "override_safety": bool(args.get("override_safety", False)),
        }
        if "involves_facts" in args:
            kwargs["involves_facts"] = args["involves_facts"]
        return _engine.create_session(**kwargs)

    elif name == "get_state":
        return _engine.get_state(args["session_id"])

    elif name == "submit_stage":
        return _engine.submit(
            session_id=args["session_id"],
            stage=args["stage"],
            artifact=args["artifact"],
        )

    elif name == "answer_questions":
        # V9: answers is a dict {question: answer}
        answers = args.get("answers", {})
        if not isinstance(answers, dict):
            return {"accepted": False, "error": "answers must be a dict mapping questions to answers."}
        return _engine.provide_answers(
            session_id=args["session_id"],
            answers=answers,
        )

    elif name == "finalize":
        return _engine.finalize(args["session_id"])

    elif name == "set_rigor":
        return _engine.set_rigor(
            session_id=args["session_id"],
            rigor=args["rigor"],
        )

    else:
        raise ValueError(f"Unknown tool: {name!r}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def _main() -> None:
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
