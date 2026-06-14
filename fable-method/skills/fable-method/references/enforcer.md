# Enforcer Integration

Read this when the Fable Method enforcer connector is available. Its tools are
`begin_task`, `submit_stage`, `finalize`, `get_state`, `set_rigor`, `answer_questions`.

**If those tools are present, USE THEM.** Do not self-enforce when the harness can
enforce for you. It will give you stage instructions and reject artifacts that fail a
gate — follow its output exactly. If the tools are NOT present, self-enforce by
producing each stage's artifact inline before proceeding (see `pipeline.md`).

## Behaviors to expect when the enforcer is driving

- **VERIFY requires real evidence.** At least one check in your VERIFY artifact must
  include an `evidence` field with a concrete artifact token: a number, a PASS/FAIL tied
  to a named criterion, a `file:line` reference, or a quoted output snippet. "Tested" or
  "ran it" with no supporting artifact is rejected (`NO_EVIDENCE`). In `--exec` mode,
  attach a `commands` list to each check you want machine-verified — the harness runs
  them and sets pass/fail from the real exit code (you do not supply `evidence`; it does).
  A check with no command, or one that only prints a literal (e.g. `echo "PASS"`), is
  recorded as `inconclusive`, not passed.

- **Expect to loop back to VERIFY after REVISE.** A passing REVISE that records real
  fixes routes back to VERIFY, not forward to DELIVER. Mark each verify check with a
  `status` of `pass`, `fail`, or `inconclusive`. If a re-verify marks any check `fail` or
  `inconclusive`, the engine routes you back to REVISE — up to 3 loops; otherwise it
  continues to DELIVER (and at the loop cap, DELIVER must document residual risk). Each
  loop is a positive rigor signal in the certificate. Your "fix" text must contain a
  concrete-edit verb (changed/added/removed/replaced/rewrote/recomputed/corrected/
  refactored/renamed/set/updated + an object) — vague intent ("will address later",
  "noted", "TBD") is rejected as `NOOP_FIX`.

- **Answer clarifying questions via `answer_questions`.** In interactive mode, if your
  FRAME artifact contains open questions, the engine pauses (`awaiting_input`). Do not
  proceed yourself — wait for the human to call `answer_questions` (or the CLI harness to
  prompt on stdin) before the session resumes.

- **Obviously harmful tasks may be refused.** Session creation runs a coarse
  keyword/category screen (weapons, malware, fraud/phishing, CSAM, self-harm
  facilitation). It is a coarse filter, not nuanced judgment — it will occasionally refuse
  legitimate research using flagged vocabulary. An operator can pass `override_safety=True`;
  the override is logged.

- **Sources must be typed.** In research and deliver artifacts, mark each source with a
  `type`: `url`, `file`, `tool_output`, or `assumed`. Honest `assumed` is allowed — but any
  assumed-source claim must appear in the delivery's `limitations`. Do not label a source
  `url` or `tool_output` if it is actually inferred from training or reasoning.

- **`risk_flags` escalate rigor.** Any non-empty `risk_flags` entry in RESEARCH
  auto-escalates the session to FULL. Flag safety / financial / legal / irreversibility
  risks honestly rather than under-report.

**Honesty note.** Without the CLI harness driving the loop, enforcement is self-enforced
— the gates exist, but you are the one choosing to respect them. The CLI harness is the
only mode where skipping a stage is architecturally impossible.
