"""
cli_harness.py — Drive an external LLM through the Fable Method pipeline.

The harness fully controls the loop.  The model cannot skip, reorder, or
shortcut stages — it must produce a passing artifact for each gate before
the next stage is unlocked.

Usage examples
--------------
Offline (no API key needed):
    python -m fable_method.cli_harness \\
        --provider echo \\
        --goal "Design a REST API for a task tracker"

Full run with OpenAI:
    python -m fable_method.cli_harness \\
        --provider openai \\
        --model gpt-4o \\
        --profile ai_builder \\
        --rigor full \\
        --goal "Design a REST API for a task tracker"

Exec-evidence mode (harness runs the model's commands and injects real output):
    python -m fable_method.cli_harness \\
        --provider echo \\
        --exec \\
        --goal "Compute and verify 2+2"

WARNING: --exec runs real subprocess commands. Use only with trusted models.
Requires --allow-network to permit network access intent (network cannot be
truly sandboxed with stdlib subprocess — the flag documents the intent and
warns loudly; commands always run in a temp working directory).

Flags
-----
    --provider        openai | anthropic | google | echo
    --model           model name passed to the provider (default varies per provider)
    --profile         universal | ai_builder | entrepreneur  (default: universal)
    --rigor           low | medium | full | adaptive         (default: adaptive)
    --goal            The task/goal string (required)
    --involves-facts  Pass if the task involves present-day factual claims
    --max-retries     Max times to retry a failed stage before stopping (default: 3)
    --store-dir       Where session JSON files are saved (default: ~/.fable_method)
    --exec            Exec-evidence mode: harness runs verify commands and injects
                      real stdout/stderr/exit-code as evidence (V6).
    --interactive     Interactive mode: harness prompts stdin when the frame stage
                      returns questions (V9).
    --allow-network   Required intent flag when using --exec with network commands.
                      NOTE: stdlib subprocess cannot truly sandbox the network;
                      this flag documents operator intent only and prints a warning.
    --override-safety Override the safety screen for this session. The bypass is
                      logged in the certificate for audit accountability.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
from typing import Any

from .engine import Engine, _MAX_LOOP_COUNT
from .providers import get_provider


# ---------------------------------------------------------------------------
# JSON extraction helpers
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)


def _strip_fences(text: str) -> str:
    """Remove markdown code fences if present."""
    m = _FENCE_RE.search(text)
    if m:
        return m.group(1)
    return text


def _iter_json_candidates(text: str):
    """Yield candidate strings to parse, in priority order: each fenced code-block body
    (in document order), then the whole raw text.

    Falling through to the raw text is what fixes finding #8: when a code fence precedes
    the real JSON (e.g. a ```python example block first), the earlier code used only the
    FIRST fence body and never recovered the actual object. Trying every fence body and
    then the raw text means a leading non-JSON fence can no longer hide the answer.
    """
    seen: set[str] = set()
    for m in _FENCE_RE.finditer(text):
        body = m.group(1).strip()
        if body and body not in seen:
            seen.add(body)
            yield body
    raw = text.strip()
    if raw and raw not in seen:
        yield raw


def _try_parse_json_object(candidate: str) -> dict | None:
    """Run three escalating extraction attempts on one candidate string and return a dict,
    or None if none parse. RecursionError (deeply-nested input) is caught alongside
    JSONDecodeError (finding #2) so a pathological response degrades to None instead of
    crashing the harness with an uncaught exception."""
    # Attempt 1 — whole string
    try:
        obj = json.loads(candidate)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, RecursionError):
        pass

    # Attempt 2 — first '{' to last '}'
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            obj = json.loads(candidate[start : end + 1])
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, RecursionError):
            pass

    # Attempt 3 — scan for balanced braces
    for i, ch in enumerate(candidate):
        if ch == "{":
            depth = 0
            for j, c2 in enumerate(candidate[i:], start=i):
                if c2 == "{":
                    depth += 1
                elif c2 == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            obj = json.loads(candidate[i : j + 1])
                            if isinstance(obj, dict):
                                return obj
                        except (json.JSONDecodeError, RecursionError):
                            break
    return None


def _extract_json(text: str) -> dict:
    """
    Robustly extract the first valid JSON object from a string.

    Strategy: try each fenced code-block body in order, then the raw text (so a leading
    non-JSON fence can't mask the real object — finding #8). For each candidate, try the
    whole string, then the first-'{'-to-last-'}' slice, then a balanced-brace scan.
    Deeply-nested input that would raise RecursionError degrades cleanly to a ValueError
    (finding #2) rather than crashing the caller.
    """
    for candidate in _iter_json_candidates(text):
        obj = _try_parse_json_object(candidate)
        if obj is not None:
            return obj

    raise ValueError(
        "Could not extract a JSON object from the model's response.\n"
        f"Response was:\n{text[:800]}"
    )


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = textwrap.dedent(
    """\
    You are running inside the Fable Method pipeline — a rigor-enforcement system.
    You will be asked to produce one JSON artifact at a time.  Each artifact must
    exactly match the required schema shown to you.  Respond ONLY with the JSON
    object — no prose, no explanation, no markdown commentary outside the JSON.
    Do NOT add extra keys unless the schema permits them.
    """
)

_SYSTEM_PROMPT_EXEC = textwrap.dedent(
    """\
    You are running inside the Fable Method pipeline — a rigor-enforcement system
    operating in EXEC-EVIDENCE mode.

    At the VERIFY stage, attach a "commands" list to EACH check you want machine-verified:
    {"what": ..., "how": ..., "result": ...,
     "commands": [{"lang": "bash"|"python", "code": "<shell or python -c code>"}]}.
    The HARNESS runs each check's commands in a subprocess (temp working dir + timeout;
    NOT a security sandbox) and sets THAT check's evidence and pass/fail status from the
    real exit code — you cannot fabricate the evidence or the verdict for a check you back
    with a command. A check you do NOT back with a command cannot be marked 'pass': the
    harness records it as 'inconclusive' (not machine-verified) and routes it back for
    revision. A command that only emits a literal (e.g. `echo "PASS"` or printing a
    hardcoded result) is treated as NO work — the check stays 'inconclusive'. The harness
    checks only the exit code, not whether the command actually tests the claim, so back each
    check with a command that genuinely exercises it (run the code, assert the result).

    For all other stages: produce one JSON artifact at a time exactly matching
    the required schema. Respond ONLY with the JSON object — no prose, no explanation.
    """
)

_STAGE_USER_TEMPLATE = textwrap.dedent(
    """\
    === FABLE METHOD — STAGE: {stage} ===

    GOAL:
    {goal}

    INSTRUCTIONS FOR THIS STAGE:
    {instructions}

    REQUIRED ARTIFACT SCHEMA:
    {schema_json}

    Produce the artifact now.  Respond with ONLY the JSON object.
    """
)

_STAGE_USER_TEMPLATE_EXEC_VERIFY = textwrap.dedent(
    """\
    === FABLE METHOD — STAGE: verify (EXEC-EVIDENCE MODE) ===

    GOAL:
    {goal}

    INSTRUCTIONS FOR THIS STAGE:
    {instructions}

    REQUIRED ARTIFACT SCHEMA:
    {schema_json}

    EXEC-EVIDENCE MODE: attach a "commands" list to EACH check you want verified.
    Each command: {{"lang": "bash"|"python", "code": "<command string>"}}.
    The harness runs each check's commands in a subprocess and sets that check's
    evidence and pass/fail status from the real exit code. A check with no command
    is recorded as 'inconclusive' (not machine-verified) — you cannot mark it 'pass'.
    Do NOT fabricate evidence values. Commands run in a temporary directory with a
    {timeout}s timeout.
    {network_note}

    Produce the artifact now.  Respond with ONLY the JSON object.
    """
)

_RETRY_USER_TEMPLATE = textwrap.dedent(
    """\
    Your previous artifact for stage "{stage}" was REJECTED by the gate.

    VIOLATIONS:
    {violations_text}

    Fix every violation and resubmit the artifact.  Respond with ONLY the
    corrected JSON object.
    """
)


def _format_violations(violations: list[dict]) -> str:
    lines = []
    for i, v in enumerate(violations, 1):
        code = v.get("code", "UNKNOWN")
        msg = v.get("message", "")
        hint = v.get("fix_hint", "")
        field = v.get("field", "")
        field_str = f" (field: {field})" if field else ""
        lines.append(f"{i}. [{code}]{field_str} {msg}")
        if hint:
            lines.append(f"   Fix hint: {hint}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# V6: Exec-evidence helpers
# ---------------------------------------------------------------------------

_EXEC_TIMEOUT_DEFAULT = 30  # seconds


def _run_command(lang: str, code: str, workdir: str, timeout: int, allow_network: bool) -> str:
    """
    Run a single command in a subprocess and return a formatted evidence string.
    lang: "bash" or "python".
    Returns: formatted string with exit_code, stdout, stderr.

    NOTE: --allow-network gates the intent only. stdlib subprocess cannot
    truly block network access. When allow_network=False, we warn but still
    run (the flag is an operator intent declaration, not a kernel sandbox).
    """
    if lang == "python":
        cmd = [sys.executable, "-c", code]
    elif lang == "bash":
        cmd = ["/bin/bash", "-c", code]
    else:
        return f"[exec] SKIPPED: unknown lang={lang!r}"

    if not allow_network:
        print(
            f"[exec]   WARNING: --allow-network not set. Running command anyway "
            f"(stdlib subprocess cannot truly block network). "
            f"Set --allow-network to suppress this warning.",
            file=sys.stderr,
        )

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=workdir,
        )
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()
        exit_code = proc.returncode
        parts = [f"exit_code={exit_code}"]
        if stdout:
            parts.append(f"stdout: `{stdout[:400]}`")
        if stderr:
            parts.append(f"stderr: `{stderr[:200]}`")
        result = "; ".join(parts)
        status = "PASS" if exit_code == 0 else "FAIL"
        return f"{status} {result}"
    except subprocess.TimeoutExpired:
        return f"FAIL exit_code=timeout (>{timeout}s)"
    except Exception as exc:
        return f"FAIL exec_error: {exc}"


def _command_is_noop(lang: str, code: str) -> bool:
    """Shape-level anti-laundering check. Return True if the command does no real work —
    it only emits literal text (echo / print of constants) or is an unconditional success
    (``true`` / ``:``). Such a command cannot meaningfully back a verify check, so the
    harness refuses to let it stamp a 'pass' (the most common fabrication is laundering the
    claim through a print so the gate sees the 'PASS' token it wants).

    This is deliberately CONSERVATIVE: it flags only blatant no-ops, not every trivial
    command. It is a SHAPE check ('does this command do anything beyond emit a literal?'),
    NOT a judgement of whether the command truly tests the claim — that remains outside what
    any harness can mechanically decide, and a determined model can still write a command
    that runs without really testing anything.
    """
    code = code.strip()
    if not code:
        return True
    lang = (lang or "bash").lower()
    if lang == "python":
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return False  # can't parse it -> don't presume it's a no-op; let it run
        if not tree.body:
            return True
        for node in tree.body:
            if isinstance(node, ast.Pass):
                continue
            if (isinstance(node, ast.Expr)
                    and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name)
                    and node.value.func.id == "print"
                    and not node.value.keywords
                    and all(isinstance(a, ast.Constant) for a in node.value.args)):
                continue  # print() of constants only — emits a literal, does nothing
            return False  # any real statement (assign, assert, call, loop, …) -> not a no-op
        return True
    # bash / shell (default): a no-op iff every non-comment line is true / : / a bare
    # echo|printf of a literal (no command substitution, pipe, chaining, or redirect).
    lines = [ln.strip() for ln in code.splitlines()]
    lines = [ln for ln in lines if ln and not ln.startswith("#")]
    if not lines:
        return True
    noop_re = re.compile(
        r"^(?:true|:|echo(?:\s+[^|`$();&<>]*)?|printf(?:\s+[^|`$();&<>]*)?)$",
        re.IGNORECASE,
    )
    return all(noop_re.match(ln) for ln in lines)


def _run_command_list(
    cmd_list: list,
    workdir: str,
    timeout: int,
    allow_network: bool,
    label: str,
) -> tuple:
    """Run a list of {lang, code} commands in workdir.

    No-op / literal-echo commands (see ``_command_is_noop``) are SKIPPED — they cannot back
    a check. Returns (evidence_str, any_failed, had_noop) where any_failed reflects the REAL
    exit codes and had_noop is True if a no-op command was skipped; (None, None, had_noop) if
    nothing runnable remained.
    """
    parts: list[str] = []
    any_failed = False
    ran = False
    had_noop = False
    for j, cmd_spec in enumerate(cmd_list):
        if not isinstance(cmd_spec, dict):
            continue
        lang = str(cmd_spec.get("lang", "bash"))
        code = str(cmd_spec.get("code", ""))
        if not code.strip():
            continue
        if _command_is_noop(lang, code):
            print(f"[exec]   {label} cmd[{j}] SKIPPED (no-op / literal only): {code[:60]!r}")
            had_noop = True
            continue
        print(f"[exec]   {label} cmd[{j}] lang={lang}: {code[:80]!r}")
        result_str = _run_command(lang, code, workdir, timeout, allow_network)
        print(f"[exec]   result: {result_str[:120]}")
        if result_str.startswith("FAIL"):
            any_failed = True
        parts.append(f"cmd[{j}]({lang}): {result_str}")
        ran = True
    if not ran:
        return None, None, had_noop
    return " | ".join(parts), any_failed, had_noop


def _inject_exec_evidence(artifact: dict, allow_network: bool, timeout: int = _EXEC_TIMEOUT_DEFAULT) -> dict:
    """
    V6 / V10: In --exec mode, bind EACH check's pass/fail status to the REAL exit code of
    the command(s) attached to THAT check — so the model cannot stamp a fabricated 'pass'
    on a check the harness never executed. Returns a modified copy (does NOT mutate input).

    Each check carries its own ``"commands": [{"lang","code"}, ...]``; the harness runs them
    and sets THAT check's evidence + status from the real exit code. There is NO top-level/
    global commands list — a command must be attached to the specific check it verifies (a
    stray top-level "commands" is stripped and ignored).

    V10 enforcement (the fix): a check the harness did NOT back with a real command cannot be
    treated as verified. Any unbacked check whose status is missing or 'pass' is downgraded to
    'inconclusive' with an injected note, and routed back through the loop. A command that only
    emits a literal (echo/print of a constant, ``true``/``:``) is a no-op and does NOT back the
    check. The harness sets pass/fail from the exit code ONLY; it does not (and cannot) judge
    whether the command actually tests the claim — so a trivial command still yields a
    'shape-level' pass.
    """
    artifact = dict(artifact)  # shallow copy
    stray = artifact.pop("commands", None)  # top-level commands are no longer used — strip them
    if stray:
        print("[exec]   NOTE: a top-level 'commands' list is ignored — attach commands to each "
              "check ('checks[i].commands'). Unbacked checks are marked 'inconclusive'.")

    checks = artifact.get("checks", [])
    if not isinstance(checks, list) or not checks:
        return artifact

    new_checks = [dict(c) if isinstance(c, dict) else c for c in checks]
    backed = [False] * len(new_checks)
    noop_only = [False] * len(new_checks)  # check supplied commands, but all were no-ops

    with tempfile.TemporaryDirectory(prefix="fable_exec_") as workdir:
        # Per-check commands: each backs its OWN check (no cross-check binding).
        for i, check in enumerate(new_checks):
            if not isinstance(check, dict):
                continue
            own = check.get("commands")
            if isinstance(own, list) and own:
                evidence, any_failed, had_noop = _run_command_list(
                    own, workdir, timeout, allow_network, f"check[{i}]"
                )
                if evidence is not None:
                    check["evidence"] = evidence
                    check["status"] = "fail" if any_failed else "pass"
                    backed[i] = True
                elif had_noop:
                    noop_only[i] = True  # only no-op commands -> not a real backing

    # Downgrade rule: strip leftover command specs, and refuse to let an UNBACKED check
    #    carry a verified verdict. A missing or 'pass' status on an unbacked check becomes
    #    'inconclusive' — the model cannot self-certify a check the harness did not run, nor
    #    launder a pass through a no-op command that only emits a literal.
    NOTE_UNBACKED = "[harness: no command bound to this check; not machine-verified in --exec mode]"
    NOTE_NOOP = ("[harness: backing command does no real work (only emits a literal); "
                 "not machine-verified in --exec mode]")
    for i, check in enumerate(new_checks):
        if not isinstance(check, dict):
            continue
        check.pop("commands", None)  # never forward command specs to the engine
        if backed[i]:
            continue
        status = str(check.get("status", "")).strip().lower()
        if status in ("", "pass"):
            check["status"] = "inconclusive"
            note = NOTE_NOOP if noop_only[i] else NOTE_UNBACKED
            existing = str(check.get("evidence", "")).strip()
            check["evidence"] = f"{existing} {note}".strip() if existing else note

    artifact["checks"] = new_checks
    return artifact


# ---------------------------------------------------------------------------
# V9: Interactive stdin prompting
# ---------------------------------------------------------------------------

def _prompt_answers(questions: list) -> dict:
    """
    Prompt the user on stdin for answers to frame questions.
    Returns a dict mapping question -> answer (as engine.provide_answers expects a dict).
    """
    print("\n[interactive] The pipeline has questions that require your input:")
    answers: dict = {}
    for i, q in enumerate(questions, 1):
        q_str = str(q)
        print(f"  Q{i}: {q_str}")
        try:
            answer = input(f"  A{i}: ").strip()
        except (EOFError, KeyboardInterrupt):
            answer = "(no answer provided)"
        answers[q_str] = answer
    print()
    return answers


# ---------------------------------------------------------------------------
# Main harness loop
# ---------------------------------------------------------------------------


def run_harness(
    goal: str,
    provider_name: str,
    model: str | None,
    profile: str = "universal",
    rigor: str = "adaptive",
    involves_facts: bool | None = None,
    max_retries: int = 3,
    store_dir: str = "~/.fable_method",
    exec_mode: bool = False,
    interactive: bool = False,
    allow_network: bool = False,
    override_safety: bool = False,
    exec_timeout: int = _EXEC_TIMEOUT_DEFAULT,
) -> None:
    """
    Drive a complete Fable Method session using an external model.

    Raises SystemExit on unrecoverable errors so the CLI exits cleanly.
    """
    # ------------------------------------------------------------------
    # Exec-mode safety warning
    # ------------------------------------------------------------------
    if exec_mode:
        print(
            "\n[harness] WARNING: --exec mode is active. The harness will run "
            "subprocess commands produced by the model. Commands execute in a "
            f"temporary directory with a {exec_timeout}s timeout.",
            file=sys.stderr,
        )
        if not allow_network:
            print(
                "[harness] NOTE: --allow-network not set. Network access is NOT "
                "truly blocked (stdlib subprocess cannot sandbox network), but "
                "the intent is documented. Pass --allow-network to suppress warnings.",
                file=sys.stderr,
            )

    # ------------------------------------------------------------------
    # Provider setup
    # ------------------------------------------------------------------
    print(f"\n[harness] Initialising provider: {provider_name}")
    try:
        provider = get_provider(provider_name)
    except (ValueError, RuntimeError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    provider_opts: dict[str, Any] = {}
    if model:
        provider_opts["model"] = model

    # ------------------------------------------------------------------
    # Engine setup
    # ------------------------------------------------------------------
    expanded_store = os.path.expanduser(store_dir)
    engine = Engine(store_dir=expanded_store)

    # ------------------------------------------------------------------
    # Create session
    # ------------------------------------------------------------------
    mode = "interactive" if interactive else "headless"
    print(f"[harness] Creating session — profile={profile}, rigor={rigor}, mode={mode}")
    create_kwargs: dict[str, Any] = {
        "goal": goal,
        "profile": profile,
        "rigor": rigor,
        "mode": mode,
        "override_safety": override_safety,
    }
    if involves_facts is not None:
        create_kwargs["involves_facts"] = involves_facts

    session = engine.create_session(**create_kwargs)

    # V10: Handle refusal
    if session.get("refused"):
        category = session.get("category", "unknown")
        reason = session.get("reason", "Goal was refused by safety screen.")
        print(f"\n[harness] REFUSED — category: {category}", file=sys.stderr)
        print(f"[harness] Reason: {reason}", file=sys.stderr)
        print(
            "\n[harness] The goal matched a prohibited safety category. "
            "The session has not been started.\n"
            "If this is a legitimate operator use case, re-run with --override-safety "
            "(the bypass will be logged in the certificate for audit accountability).",
            file=sys.stderr,
        )
        sys.exit(2)

    session_id: str = session["session_id"]
    print(f"[harness] Session created: {session_id}")

    # Choose system prompt based on mode
    system_prompt = _SYSTEM_PROMPT_EXEC if exec_mode else _SYSTEM_PROMPT

    # ------------------------------------------------------------------
    # Stage loop
    # ------------------------------------------------------------------
    current = session  # first iteration uses the create_session response

    while True:
        # Primary exit — engine's explicit `done` bool (submit success on final stage)
        if current.get("done") is True:
            break

        status = current.get("status", "")
        next_action = current.get("next_action", "")

        # current_stage is None means all stages exhausted
        if current.get("current_stage") is None and status in ("ready_to_finalize", "complete"):
            break

        # Legacy fallback: next_action string signals finalize
        if next_action.startswith("Call finalize") and status in ("ready_to_finalize", "complete"):
            break

        # If there's no current stage info, fetch state
        if "current_stage" not in current:
            current = engine.get_state(session_id)

        current_stage: str = current.get("current_stage", "")
        instructions: str = current.get("instructions", "")
        required_artifact: dict = current.get("required_artifact", {})

        if not current_stage or current_stage in ("done", "finalized"):
            break

        print(f"\n[harness] ─── Stage: {current_stage.upper()} ───")
        loop_count = current.get("loop_count", 0)
        if loop_count > 0:
            print(f"[harness]   (backtrack loop #{loop_count})")

        schema_json = json.dumps(required_artifact, indent=2)

        # Build the initial user message for this stage
        if exec_mode and current_stage == "verify":
            network_note = (
                "Network is PERMITTED (--allow-network set)."
                if allow_network
                else "Network commands are NOT recommended (--allow-network not set)."
            )
            user_message = _STAGE_USER_TEMPLATE_EXEC_VERIFY.format(
                goal=goal,
                instructions=instructions,
                schema_json=schema_json,
                timeout=exec_timeout,
                network_note=network_note,
            )
        else:
            user_message = _STAGE_USER_TEMPLATE.format(
                stage=current_stage,
                goal=goal,
                instructions=instructions,
                schema_json=schema_json,
            )

        conversation: list[dict] = [{"role": "user", "content": user_message}]

        attempt = 0
        stage_passed = False

        while attempt <= max_retries:
            attempt += 1
            print(f"[harness]   Attempt {attempt}/{max_retries + 1} ...")

            # Call the model
            try:
                raw_response = provider.complete(
                    system=system_prompt,
                    messages=conversation,
                    **provider_opts,
                )
            except Exception as exc:
                print(f"\nERROR calling provider: {exc}", file=sys.stderr)
                sys.exit(1)

            # Add model response to conversation history
            conversation.append({"role": "assistant", "content": raw_response})

            # Parse the artifact
            try:
                artifact = _extract_json(raw_response)
            except ValueError as exc:
                print(f"[harness]   Could not parse JSON: {exc}")
                if attempt > max_retries:
                    _human_intervention(session_id, current_stage, raw_response)
                    sys.exit(1)
                parse_error_msg = (
                    "Your response could not be parsed as JSON. "
                    "Reply with ONLY a valid JSON object — no prose, "
                    "no markdown, no explanation.\n\n"
                    f"Parse error: {exc}"
                )
                conversation.append({"role": "user", "content": parse_error_msg})
                continue

            # V6: exec-evidence injection at VERIFY stage
            if exec_mode and current_stage == "verify":
                artifact = _inject_exec_evidence(artifact, allow_network, exec_timeout)

            # Submit to engine
            result = engine.submit(
                session_id=session_id,
                stage=current_stage,
                artifact=artifact,
            )

            # V9: Interactive mode — handle awaiting_input
            if result.get("needs_user_input") and result.get("status") == "awaiting_input":
                questions = result.get("questions", [])
                print(f"\n[interactive] Frame stage has {len(questions)} question(s).")
                answers = _prompt_answers(questions)
                # Call provide_answers to advance the session
                pa_result = engine.provide_answers(session_id, answers)
                print(f"[interactive] Answers recorded. Advancing to next stage ...")
                current = pa_result
                stage_passed = True
                break

            if result.get("accepted"):
                print(f"[harness]   Gate PASSED.")
                if result.get("loop_back"):
                    print(
                        f"[harness]   Backtracking to {result.get('current_stage', '?')} — "
                        f"loop {result.get('loop_count', '?')}/{_MAX_LOOP_COUNT}"
                    )
                elif result.get("iteration_recorded"):
                    print(f"[harness]   Major replan recorded — routing to {result.get('current_stage')}.")
                stage_passed = True
                current = result
                break
            else:
                violations = result.get("violations", [])
                print(
                    f"[harness]   Gate FAILED — {len(violations)} violation(s):"
                )
                for v in violations:
                    print(f"             [{v.get('code')}] {v.get('message', '')}")

                if attempt > max_retries:
                    _human_intervention(
                        session_id,
                        current_stage,
                        raw_response,
                        violations,
                    )
                    sys.exit(1)

                # Feed violations back to model
                retry_message = _RETRY_USER_TEMPLATE.format(
                    stage=current_stage,
                    violations_text=_format_violations(violations),
                )
                conversation.append({"role": "user", "content": retry_message})

        if not stage_passed:
            print(
                f"\n[harness] Stage '{current_stage}' could not be passed after "
                f"{max_retries + 1} attempts.",
                file=sys.stderr,
            )
            sys.exit(1)

        # Check done flag on the submit result (primary signal)
        if current.get("done") is True:
            break
        # Fallback: next_action string or current_stage None
        next_action = current.get("next_action", "")
        if current.get("current_stage") is None or next_action.startswith("Call finalize"):
            break

    # ------------------------------------------------------------------
    # Finalize
    # ------------------------------------------------------------------
    print("\n[harness] All required stages passed. Finalizing session ...")
    final = engine.finalize(session_id)

    if final.get("finalized"):
        cert = final.get("certificate", {})
        print("\n" + "=" * 60)
        print("  FABLE METHOD — SESSION COMPLETE")
        print("=" * 60)
        print(json.dumps(cert, indent=2, default=str))
        print("=" * 60)
        loop_count = cert.get("loop_count", 0)
        if loop_count > 0:
            print(f"\n[harness] Backtracking loop count: {loop_count} (rigor signal)")
        escalations = cert.get("escalations", [])
        if escalations:
            print(f"[harness] Auto-escalations recorded: {len(escalations)}")
        pwa = cert.get("proceeded_without_answers", False)
        if pwa:
            print(
                "[harness] NOTE: Session proceeded without answers to frame questions "
                "(headless mode — certificate stamped proceeded_without_answers=true)."
            )
    else:
        missing = final.get("missing_stages", [])
        msg = final.get("message", "Finalize refused.")
        print(f"\n[harness] Finalize refused: {msg}", file=sys.stderr)
        if missing:
            print(f"[harness] Missing stages: {missing}", file=sys.stderr)
        sys.exit(1)


def _human_intervention(
    session_id: str,
    stage: str,
    last_response: str,
    violations: list[dict] | None = None,
) -> None:
    """Print a clear message when human intervention is needed."""
    print("\n" + "!" * 60, file=sys.stderr)
    print("  HUMAN INTERVENTION REQUIRED", file=sys.stderr)
    print("!" * 60, file=sys.stderr)
    print(f"Session: {session_id}", file=sys.stderr)
    print(f"Stage:   {stage}", file=sys.stderr)
    if violations:
        print("\nUnresolved violations:", file=sys.stderr)
        for v in violations:
            print(
                f"  [{v.get('code')}] {v.get('message', '')}",
                file=sys.stderr,
            )
            hint = v.get("fix_hint", "")
            if hint:
                print(f"    Hint: {hint}", file=sys.stderr)
    print("\nLast model response (truncated):", file=sys.stderr)
    print(last_response[:1000], file=sys.stderr)
    print("!" * 60, file=sys.stderr)
    print(
        "\nTo resume this session later, use the same session_id with the MCP server\n"
        "or call engine.get_state() to inspect progress.",
        file=sys.stderr,
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m fable_method.cli_harness",
        description=(
            "Drive an external LLM through the Fable Method pipeline. "
            "The model cannot skip or reorder stages — every gate must pass."
        ),
    )
    p.add_argument(
        "--provider",
        required=True,
        choices=["openai", "anthropic", "google", "echo"],
        help="LLM provider to use. Use 'echo' for offline testing.",
    )
    p.add_argument(
        "--model",
        default=None,
        help=(
            "Model name to pass to the provider "
            "(e.g. gpt-4o, claude-3-5-sonnet-latest, gemini-1.5-pro). "
            "Defaults to the provider's built-in default."
        ),
    )
    p.add_argument(
        "--profile",
        default="universal",
        choices=["universal", "ai_builder", "entrepreneur"],
        help="Reasoning profile (default: universal).",
    )
    p.add_argument(
        "--rigor",
        default="adaptive",
        choices=["low", "medium", "full", "adaptive"],
        help="Rigor level (default: adaptive).",
    )
    p.add_argument(
        "--goal",
        required=True,
        help="The task or goal to work through rigorously.",
    )
    p.add_argument(
        "--involves-facts",
        action="store_true",
        default=None,
        dest="involves_facts",
        help=(
            "Declare that the task involves present-day factual claims. "
            "Activates the research gate in medium rigor."
        ),
    )
    p.add_argument(
        "--max-retries",
        type=int,
        default=3,
        dest="max_retries",
        help="Max retry attempts per failed stage (default: 3).",
    )
    p.add_argument(
        "--store-dir",
        default=os.path.expanduser("~/.fable_method"),
        dest="store_dir",
        help="Directory for session JSON files (default: ~/.fable_method).",
    )
    # V6: exec-evidence mode
    p.add_argument(
        "--exec",
        action="store_true",
        default=False,
        dest="exec_mode",
        help=(
            "Exec-evidence mode (V6): at the VERIFY stage each check carries its own "
            "'commands'; the harness runs them via subprocess with a timeout and sets that "
            "check's evidence and pass/fail from the real exit code. A check with no command "
            "(or only a literal-echo no-op) is marked 'inconclusive'. The model cannot "
            "fabricate the verdict of a check it backs with a command. "
            "WARNING: runs real subprocess commands — use only with trusted models/goals."
        ),
    )
    # V9: interactive mode
    p.add_argument(
        "--interactive",
        action="store_true",
        default=False,
        help=(
            "Interactive mode (V9): when the frame stage returns questions, the harness "
            "prompts you on stdin for answers and calls engine.provide_answers() before "
            "continuing. In headless mode (default), questions are logged in the "
            "certificate as proceeded_without_answers=true."
        ),
    )
    # V6: allow-network intent flag
    p.add_argument(
        "--allow-network",
        action="store_true",
        default=False,
        dest="allow_network",
        help=(
            "Intent flag required when using --exec with commands that access the network. "
            "NOTE: stdlib subprocess cannot truly sandbox network access — this flag "
            "documents operator intent and suppresses the network-access warning. "
            "Without this flag, a warning is printed for every exec command."
        ),
    )
    # V10: override-safety
    p.add_argument(
        "--override-safety",
        action="store_true",
        default=False,
        dest="override_safety",
        help=(
            "Override the safety screen for goals that match a prohibited category. "
            "The bypass is LOGGED in the session certificate for audit accountability. "
            "Use only for legitimate operator testing — the bypass is not silent."
        ),
    )
    return p


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    run_harness(
        goal=args.goal,
        provider_name=args.provider,
        model=args.model,
        profile=args.profile,
        rigor=args.rigor,
        involves_facts=args.involves_facts,
        max_retries=args.max_retries,
        store_dir=args.store_dir,
        exec_mode=args.exec_mode,
        interactive=args.interactive,
        allow_network=args.allow_network,
        override_safety=args.override_safety,
    )


if __name__ == "__main__":
    main()
