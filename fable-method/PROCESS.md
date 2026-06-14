# Working on Fable Method — Process & Discipline

Fable Method enforces reasoning rigor on a model. It only has standing if it holds itself to
the same bar. The lessons below have been re-learned across multiple sessions (see
`logs/session-logs/`); they live here so they don't have to be re-discovered each time.

## Core principles

1. **Reproduce before claiming.** Before asserting a bug or a fix works, demonstrate it with a
   small script and cite `file:line`. If you cannot reproduce it, tag the claim `[UNVERIFIED]`
   and say what you'd need.
2. **Don't grade your own homework.** The author of a fix is the worst judge of whether it
   works. Before marking a fix *done*, run an **independent adversarial pass** — ideally a
   fresh reviewer (a different subagent or session) that did not write the fix and is trying
   to break it.
3. **Don't co-design tests with the heuristic they test.** Write test inputs from realistic,
   adversarial phrasings you invent independently. Never lift strings out of the regex /
   keyword lists in the implementation — a test built from the heuristic validates the
   assumption instead of stress-testing it. (This exact mistake hid a real bug; see
   session-02.)
4. **Honest limits over clean claims.** Every gate has a boundary. State what it does *not*
   catch, in the code and the docs. Gates check the **shape** of rigor, not the substance —
   say so rather than implying more.
5. **Tightenings update affected tests; they never relax a gate.** When a change makes a gate
   stricter, fix the tests that depended on the looser behavior — don't loosen the gate to fit
   the tests.
6. **Suspect your own setup before blaming the engine.** When a gate rejects something you
   expected to pass, print every prior step's acceptance first. (Weak verbs, empty
   assumptions, wrong field names, and short list items have all masqueraded as engine bugs.)

## Before you mark a fix DONE — checklist

- [ ] Reproduced the original problem with an independent script (cited `file:line`).
- [ ] Fix covered by a test whose phrasings are **not** taken from the implementation.
- [ ] An **independent** adversarial pass (fresh eyes / subagent) tried to break the fix.
- [ ] Full suite green **and** the six bypass probes: `./run_tests.sh` then `./run_tests.sh -k bypass`.
- [ ] `--exec` still finalizes end-to-end: `python -m fable_method.cli_harness --provider echo --exec --allow-network --rigor full --goal "..."`.
- [ ] Docs reconciled to the code — grep **all** occurrences, including model-facing strings (system prompts, help text), not just human docs.
- [ ] New or changed limits documented honestly (what it does **not** catch).
- [ ] Session log updated (`logs/session-logs/`).

## Tests

- Run with `./run_tests.sh` (from `enforcer/`). `basetemp` is auto-assigned by `conftest.py`,
  so a bare `pytest` works too — you do **not** need `--basetemp`. (A naive run on a fresh
  machine used to show a flood of false "errors" from stale temp dirs; that's handled now.)
- The six bypass probes (`./run_tests.sh -k bypass`) are the five in `TestV2AdversarialBypasses` plus `test_adversarial_noop_bypass_blocked`.
- The test count should grow with each fix, never shrink.

## Known limits — keep stating these honestly; do NOT re-flag them as new bugs

- Gates check the **shape** of rigor, not the substance.
- Only the **CLI harness** is non-bypassable. MCP gates apply only to work routed through the
  tools; the skill alone is voluntary discipline.
- `--exec` is plain `subprocess.run` + a timeout — **not** a security sandbox (no filesystem
  confinement). `--allow-network` is an intent flag that warns but does **not** block the
  network.
- `--exec` is **per-check**: every `pass` is backed by a real command's exit code; a check with
  no command (or only a literal-echo no-op) is `inconclusive`. The harness checks the exit
  code, **not** whether the command truly tests the claim — a determined model can still write
  a running-but-trivial command.
- Outside `--exec`, the `FABRICATION_RISK` prose check is a best-effort backstop on explicit
  "exit code N" phrasing; a model can phrase a failure another way to avoid it.
