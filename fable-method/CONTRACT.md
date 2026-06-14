# ENGINE CONTRACT (v2)

This is the binding interface. `engine.py` implements it; `mcp_server.py` and
`cli_harness.py` consume it. Code to *this*, not to assumptions. Pure Python 3.10+, standard
library only for the engine (no third-party deps in engine.py or profiles.py).

---

## Concepts

- A **Session** tracks one task through the pipeline. It is a state machine.
- A **Stage** is one of: `frame, research, plan, draft, critique, verify, revise, deliver`.
  (`reflect` is optional and never gates.)
- A **rigor level** is one of: `low, medium, full, adaptive`.
- A **profile** is one of: `universal, ai_builder, entrepreneur`.
- A **gate** validates the artifact submitted for a stage. It returns pass/fail + reasons.
- Sessions persist as JSON so the MCP server (stateless calls) and CLI can reload them.

## Stage order & which stages are required per level

Order is always: frame → research → plan → draft → critique → verify → revise → deliver.

Required (gated) stages by level — others are SKIPPED automatically:
- `low`:    frame, draft, deliver
- `medium`: frame, research(only if task involves factual claims — see flag), plan, draft, critique, deliver
- `full`:   frame, research, plan, draft, critique, verify, revise, deliver
- `adaptive`: engine first requires a `classify` artifact, then maps to low/medium/full.

The engine, not the model, decides what's required. The model cannot self-exempt.

---

## Public API (engine.py)

Implement these as a class `Engine` plus module-level convenience functions. All inputs are
plain dicts/strings (JSON-serializable) so any transport can use them.

```
Engine(store_dir: str = "~/.fable_method")          # where session JSON files live

create_session(goal: str, profile: str = "universal",
               rigor: str = "adaptive",
               involves_facts: bool | None = None,
               mode: str = "headless",
               override_safety: bool = False) -> dict
    # Returns (normal): {session_id, status, current_stage, rigor, profile,
    #           instructions:str, required_artifact:dict, next_action:str}
    # Returns (refused): {refused:true, category:str, reason:str,
    #           session_id:str, status:"refused"}
    #   — returned when the goal matches the safety screen (see PROTOCOL §6).
    #     Subsequent submit/finalize on a refused session always return refused.
    #     override_safety=True bypasses the screen; the bypass is logged in the certificate.
    # mode: "headless" (default) | "interactive"
    #   — in interactive mode, a frame with open questions pauses and returns
    #     needs_user_input:true; see provide_answers below.
    #   — in headless mode, questions are recorded but the session advances; the
    #     certificate is stamped proceeded_without_answers:true.
    # instructions = the profile+stage guidance pulled from profiles.py.
    # If rigor == adaptive, current_stage == "classify" and the first required
    # artifact is the complexity/stakes classification.

provide_answers(session_id: str, answers: list[str] | dict[str, str]) -> dict
    # Advances a session that is in status "awaiting_input".
    # Accepts a list of answer strings OR a {question: answer} dict (the CLI harness and
    # MCP server pass a dict); a dict is normalized to ["<question>: <answer>", ...] so the
    # human's answer text is preserved.
    # Records the answers into the frame artifact and resumes normal stage routing.
    # Returns: {accepted:true, current_stage, instructions, required_artifact, next_action}
    # Error if session is not awaiting_input.

get_state(session_id: str) -> dict
    # Full session snapshot: stages completed, current stage, gate history, artifacts, done: bool.
    # v2 additions: mode, loop_count, iterations:[{stage, reason}], pending_limitations:[str],
    #   escalated_to: str|None, safety:{refused:bool, category:str|None},
    #   awaiting_input:bool, proceeded_without_answers:bool

submit(session_id: str, stage: str, artifact: dict) -> dict
    # The core call. Validates `artifact` for `stage` via that stage's gate.
    # On PASS: advances to the next required stage, returns
    #   {accepted:true, current_stage, instructions, required_artifact, next_action, done:bool,
    #    loop_count:int, escalated_to:str|None}
    #   done=true when no required stages remain.
    #   After a REVISE with real fixes (reverified:true + ≥1 concrete edit), routes BACK to
    #   verify (loop_count increments). After 3 loops, requires a substantive `residual_risk` field in deliver.
    # On PASS with open questions (interactive mode, frame stage):
    #   {accepted:true, needs_user_input:true, questions:[str], next_action:"answer_questions"}
    #   Session status becomes "awaiting_input".
    # On FAIL: does NOT advance, returns
    #   {accepted:false, stage, violations:[{code, message, fix_hint}], retry:true}
    # Submitting a stage out of order -> {accepted:false, violations:[{code:"OUT_OF_ORDER"...}]}

finalize(session_id: str) -> dict
    # Allowed ONLY if every required stage for the level has passed.
    # On success: {finalized:true, certificate:{...audit log...}}
    # v2 certificate additions: loop_count, iterations, escalations:[{stage, from_level, to_level}],
    #   proceeded_without_answers:bool, unanswered_questions:[str],
    #   safety_screen:{ran:bool, refused:bool, override:bool, category:str|None},
    #   evidence_summary:[{stage, check, evidence_token}]
    # Otherwise: {finalized:false, missing_stages:[...], message}

set_rigor(session_id: str, rigor: str) -> dict   # operator override; may only raise, see PROTOCOL §3
```

## Artifact shapes per stage (what `artifact` must contain)

Gates check for presence, minimum counts, and tripwire patterns (PROTOCOL §4). Counts shown
are FULL-level minimums; MEDIUM uses the same shapes with relaxed minimums; LOW barely gates.

- `classify`: `{complexity:"low|medium|high", stakes:"low|medium|high",
   reversibility:"easy|hard", selected_level:"low|medium|full", justification:str}`
   Gate: justification non-trivial AND shares ≥1 content token with the goal (else
   `EMPTY_OR_TRIVIAL` — "justify against THIS goal, not generically");
   selected_level consistent (high stakes or hard reversibility cannot map to "low");
   if the goal contains risk signals (money, irreversibility, legal/medical/security/auth/
   deploy/production) the engine rejects `selected_level:"low"` with code `RISK_FLOOR`
   listing the matched signals — the model must re-classify at medium or full.
- `frame`: `{goal_restatement:str, success_criteria:[str], 
   questions:[str] OR assumptions:[{assumption, why_safe}]}`
   Gate: goal_restatement present; ≥1 success_criterion; at least one of questions/assumptions
   non-empty. Tripwire: if `goal_restatement` (lowercased/stripped) equals the goal or has ≥ 0.9
   token-overlap with it → `EMPTY_OR_TRIVIAL` ("restatement echoes the goal; restate in your own words").
   v2 interactive mode: if `questions` is non-empty and mode is "interactive", the gate passes
   structurally but the engine sets status `awaiting_input` and returns `needs_user_input:true`
   with `next_action:"answer_questions"`. The session does not advance until `provide_answers`
   is called. In headless mode the session advances and the certificate records
   `proceeded_without_answers:true`.
- `research`: `{facts:[{claim, source, type?:"url|file|tool_output|assumed"}], unknowns:[str]}`
   Gate (when required): ≥1 fact with a non-empty source OR an explicit
   `{no_research_needed:true, why:str}`.
   Source `type` is optional; if omitted the engine infers it (http prefix → url, path-like → file,
   else assumed). `assumed` is allowed (honest) but any assumed-source claim is added to
   `pending_limitations` and must be disclosed in deliver.limitations (`UNCOVERED_LIMITATION` if
   missing). Fabricated-looking url/tool_output sources (empty, `example.com`, `todo`, `xxx`, `...`)
   are flagged `FABRICATION_RISK`. The old blanket penalty for "training data" as a source is
   removed; honest `assumed` is not the harshest failure.
   Research unknowns are also auto-added to `pending_limitations`.
- `plan`: `{steps:[str], risks:[str], verification_strategy:[str]}`
   Gate: ≥2 steps; ≥1 risk; ≥1 verification_strategy item. (FULL: ≥3 steps.)
- `draft`: `{content:str}`  Gate: non-empty, above a small min length; no leftover
   "TODO"/"you could"/"as an AI" hand-waving patterns when work was requested.
- `critique`: `{findings:[{severity:"blocker|major|minor", issue, location}],
   steelman:str}`
   Gate: ≥1 finding (FULL: ≥2, and at least one must be blocker/major severity OR an explicit
   `{no_issues_found:true, why:str}` with `why` ≥ 80 chars — two `minor` findings alone fail
   at FULL → `HOLLOW_CRITIQUE`); steelman present.
- `verify`: `{checks:[{what, how, result, evidence?:str, status?:"pass|fail|inconclusive"}]}`
   Gate: ≥1 check; each check's `how` must be ≥ 15 chars AND contain a concrete-method keyword
   (ran/tested/recomputed/re-read/measured/confirmed…); `result` must differ from `what` (not
   echo the claim); else → `UNVERIFIED_CLAIM`.
   v2: `evidence` is required on at least one check and must contain a concrete artifact token —
   a digit, an explicit PASS/FAIL tied to a named criterion, a `file:line` pattern, or a quoted
   output snippet (`"..."` or triple-backtick block). Checks where `evidence` is absent on ALL
   checks → `NO_EVIDENCE`. A check whose `status:"pass"` contradicts its evidence (the LAST exit
   code in the evidence is non-zero) → `FABRICATION_RISK`. Scope, stated honestly: in CLI
   `--exec` mode the harness runs the commands attached to each check in a subprocess, injects the
   real stdout/stderr/exit-code as that check's evidence, AND sets that check's `status` from the
   real exit code — there the model genuinely cannot fabricate the evidence or the verdict of a
   check it backs with a command, and a check with no command is forced to `inconclusive` rather
   than a model-asserted `pass` (the harness checks the exit code, not whether the command truly
   tests the claim). OUTSIDE `--exec` (model-supplied
   evidence), the `FABRICATION_RISK` check is a narrow backstop on explicit "exit code N" phrasing
   only; a model can still phrase a failure to avoid it (shape-not-substance — see §honest limits).
   `status` (optional) is the structured signal that drives the backtracking loop (see revise):
   on a RE-verify (after a revise loop-back), `fail`/`inconclusive` routes back to revise for
   another cycle; the first verify advances to revise by normal ordering regardless. The loop
   reads this structured field, NOT free-text in `result`.
- `revise`: `{fixes:[{finding_ref, change}], reverified:bool, reopen?:"plan|draft"}`
   Gate: one fix per blocker/major finding from the critique (mapping completeness);
   reverified == true if anything changed.
   v2 additions:
   - `change` must contain at least one concrete-edit verb (changed/added/removed/replaced/
     rewrote/recomputed/corrected/refactored/renamed/set/updated + an object). Vague intent
     phrases ("will address later", "noted", "TBD", "recommend adding") are rejected: `NOOP_FIX`.
   - `finding_ref` must have token-overlap ≥ 0.4 (Jaccard on word tokens) with the finding's
     `issue` text. Unrelated refs fail: `UNMAPPED_FIX`.
   - After a passing REVISE with real fixes, the engine routes BACK to verify (incrementing
     `loop_count`). The loop is driven by a STRUCTURED signal: if a re-verify marks any check
     `status:"fail"` or `"inconclusive"`, the engine routes BACK to revise for another cycle.
     A re-verify with no failing/inconclusive status routes forward to deliver. (The trigger is
     the structured `status` field, not keyword-scanning of free text — prose scanning misfired
     on benign phrasings and missed real failures.) Loop cap: 3 — at the cap the engine stops
     looping and `deliver` must include a substantive `residual_risk` field (else `MISSING_FIELD`).
     A residual keyword inside `limitations` no longer suffices — the field must be consciously
     filled and is surfaced in the certificate for human review (the engine cannot judge whether
     the disclosure is truthful or complete).
   - Optional `reopen:"plan|draft"` triggers a major replan: resets that stage and all later
     completed stages, routes back to the reopened stage, and records an `iteration` entry.
     Loop count and iterations are positive rigor signals in the certificate.
- `deliver`: `{summary:str, limitations:[str], sources:[{text:str, type?:"url|file|tool_output|assumed"}], residual_risk?:str}`
   Gate: summary present; limitations present (may be explicit "none, because …"). `residual_risk`
   is required (substantive free text) only when the backtracking loop reached its cap.
   v2: each item in the session's `pending_limitations` list (accumulated from research unknowns
   and assumed-source claims) must be addressed/sufficiently overlapping in `deliver.limitations`
   — missing items → `UNCOVERED_LIMITATION` ("unresolved unknowns must be disclosed at delivery").
   Source `type` on deliver.sources follows the same rules as research.facts.source.type.
   v11 anti-hollow: when unresolved/assumed items remain (pending_limitations non-empty, or the
   loop reached its cap), the `summary` may not make a blatant unqualified certainty/completeness
   claim → `OVERCLAIMED_SUMMARY`. This is a conservative phrase check (a specific factual claim
   like "all 12 tests passed" is fine); it surfaces the failure mode, it cannot prove the
   summary is fully honest — a model can rephrase to dodge it (shape, not substance).

## Violation codes (stable, the harness branches on these)

`OUT_OF_ORDER, MISSING_FIELD, TOO_FEW_ITEMS, EMPTY_OR_TRIVIAL, UNVERIFIED_CLAIM,
HOLLOW_CRITIQUE, UNMAPPED_FIX, HANDWAVING, FABRICATION_RISK, LEVEL_INCONSISTENT,
NOT_ENOUGH_RIGOR`

v2 additions: `JUNK_CONTENT` (repeated-char / single-token filler in free-text fields),
`RISK_FLOOR` (adaptive selected "low" on a goal with risk signals), `NOOP_FIX` (revise change
is pure intent with no concrete-edit verb), `NO_EVIDENCE` (verify has no check with a concrete
evidence token), `UNCOVERED_LIMITATION` (an assumed-source claim or research unknown was not
disclosed in deliver.limitations), `MISSING_GOAL_TOKEN` (classify justification shares no
content tokens with the goal), `OVERCLAIMED_SUMMARY` (deliver summary claims unqualified
certainty/completeness while unresolved or assumed items remain).

Session statuses (v2 additions): `awaiting_input` (interactive mode, frame has open questions),
`refused` (safety screen matched; no stages can run).
Response flags (v2 additions): `needs_user_input:bool`, `refused:bool`, `loop_count:int`,
`escalated_to:str|None`.

Each violation: `{code, message, fix_hint, stage, field?}`.

---

## profiles.py contract

```
PROFILES: dict[str, Profile]   # keys: "universal", "ai_builder", "entrepreneur"

get_instructions(profile: str, stage: str, level: str) -> str
    # Returns the human-readable guidance string injected into engine responses,
    # = base stage guidance + any profile overlay for that stage (PROTOCOL §5).

get_overlay_checks(profile: str, stage: str) -> list[str]
    # Extra checklist items the gate appends as fix_hints / reminders (advisory).
```

Profiles must NOT change the gate's hard pass/fail logic (that lives in engine.py and is
uniform); they add guidance and advisory checklist items only. This keeps enforcement
consistent across domains while letting the *thinking prompts* differ.

---

## mcp_server.py contract

- Expose the engine as MCP tools using the official `mcp` Python SDK (stdio server).
- One tool per engine method: `begin_task`, `get_state`, `submit_stage`, `finalize`,
  `set_rigor`, `answer_questions`. Tool descriptions must tell the model the pipeline is
  mandatory, that `finalize` will be refused until gates pass, that obviously harmful goals may
  be refused before any stage runs, and that interactive sessions may pause for human input.
- Tools return the engine dicts as JSON text.
- Must run with: `python -m fable_method.mcp_server` and via an `mcp.json`/README snippet.

## cli_harness.py contract

- Drives an EXTERNAL model through the engine in a loop, fully controlling the loop so the
  model cannot skip gates:
  1. `create_session`; 2. loop: send the model the stage `instructions` + required artifact
  schema, get a response, parse it into the artifact dict, call `submit`; on FAIL, feed the
  violations back and retry (cap retries, then surface to the human); on PASS, continue;
  3. `finalize`; print the certificate.
- Model access via `providers.py` adapters; provider/model/key chosen by flags or env.
- Must run headless: `python -m fable_method.cli_harness --provider openai --model gpt-4o-mini
  --profile ai_builder --rigor full --goal "..."`.
- v2 flags: `--exec` (harness runs verify commands in a subprocess and injects real output as
  evidence), `--interactive` (session pauses at frame if questions are present; harness prompts
  via stdin and calls provide_answers), `--allow-network` (with --exec, permits network-touching
  commands in subprocess; default is no network), `--override-safety` (bypasses the safety
  screen; logged in certificate).
- Include a `--provider echo` mock that needs no API key, for offline testing.

## providers.py contract

```
get_provider(name: str) -> Provider
Provider.complete(system: str, messages: list[dict], **opts) -> str
```
Adapters: `openai`, `anthropic`, `google`, and `echo` (mock). Read API keys from env
(`OPENAI_API_KEY`, etc.). Network/SDK calls isolated here so the engine stays pure.

---

## Tests (tests/test_engine.py, pytest)

Must cover, at minimum:
- Stage ordering is enforced (OUT_OF_ORDER on skip).
- `finalize` refused until all required stages pass; allowed after.
- Each gate: a passing artifact and at least one failing artifact per major violation code.
- Adaptive flow requires `classify` first and high-stakes cannot select "low".
- LOW/MEDIUM/FULL require the correct stage sets.
- Anti-laziness tripwires fire: empty critique, unverified claim, unmapped fix, handwaving draft.
- Round-trip persistence (create → reload from disk → continue).
