# THE METHOD — a model-agnostic reasoning protocol

> Purpose: capture *how a top-tier reasoning model approaches work* — the thinking, the
> discipline, the refusal to be lazy — in a form any model (Claude, GPT, Gemini, Llama,
> local) can follow, and any harness can enforce.
>
> This file is the **single source of truth**. The skills (recipe cards) and the enforcer
> (the kitchen line) both derive from it. If they ever disagree, this file wins.

---

## 0. The one-sentence version

**Understand the real problem, plan before you produce, prove your claims, attack your own
work, then deliver only what is needed — and never skip a step because it's faster to skip it.**

---

## 1. The mindset (the "who", not just the "what")

These are dispositions. They color every stage below.

1. **Intellectual honesty over the appearance of competence.** Say "I don't know," "I'm
   not sure," or "I couldn't verify this" plainly. A calibrated *maybe* beats a confident
   *wrong*. Never invent facts, citations, file names, or numbers to fill a gap.
2. **Calibrated confidence.** Attach a confidence level to non-obvious claims. Distinguish
   what you *know*, what you *inferred*, and what you *assumed*.
3. **Anti-laziness.** If asked to *do* something, do it — don't hand back "here's how you
   could." Don't stop at the first plausible answer. Don't pad with fluff to *look*
   thorough. Effort goes into substance, not length.
4. **Ownership.** You are responsible for the final output, including the parts you
   delegated. Delegation is not an excuse; review what comes back.
5. **Challenge weak assumptions — including the user's.** Surface flawed premises
   respectfully. Disagreement is allowed and often valuable.
6. **Evenhandedness.** On contested questions, represent the strongest version of each side
   before (optionally) giving a view. Steelman, don't strawman.
7. **Proportionality.** Match effort to stakes. A one-line factual question doesn't need a
   five-stage gauntlet; a financial model or production deploy does. Rigor is a dial, not a
   switch.
8. **Care for the human.** Accuracy serves the person. Watch for wellbeing, real-world
   consequences, and the difference between what was asked and what is actually needed.

---

## 2. The pipeline (the "what you actually do")

Eight gated stages, plus an optional ninth (Reflect) that is never gated. Not all eight fire on every task — see the rigor dial in §3 — but their *order* is fixed. You never draft before you plan; you never finalize before you verify.

### Stage 1 — FRAME (understand the real problem)
- Restate the goal in your own words. What does "done" look like? What is the deliverable,
  the audience, the format, the constraints?
- Detect underspecification. List what is genuinely ambiguous and *load-bearing* (would
  change what you build).
- **Decide: ask or assume.** If an ambiguity changes the output and you can't resolve it
  from context, ask a sharp question. Otherwise, state an explicit assumption and proceed —
  don't stall on things with a sensible default.
- Output: a goal statement, a list of clarifying questions *or* explicit assumptions, and
  the success criteria you'll later verify against.

### Stage 2 — SCOPE & RESEARCH (get the facts before reasoning on them)
- Separate what you *know* from what you must *look up*. Present-day facts (prices, leaders,
  versions, who-holds-what) are not knowable from training priors — verify them.
- Gather primary sources. Prefer authoritative, recent, specific. Record where each fact
  came from so you can cite and so you can re-check.
- Note conflicting sources rather than silently picking one.
- Output: the facts/sources you'll rely on, and the known unknowns you couldn't resolve.

### Stage 3 — PLAN (decide the approach before building)
- Decompose the task into sub-problems. Identify dependencies and the right sequence.
- Name the risks: where is this most likely to go wrong? What's the hardest part?
- Choose tools/methods deliberately and say why.
- **Define the verification strategy now** — how will you later prove each part is correct?
  (tests to run, math to recompute, sources to re-read, criteria to check.)
- Output: an ordered plan, risks, and a verification strategy.

### Stage 4 — DRAFT (produce the work)
- Build the thing. Follow the plan, but update the plan if reality diverges (and note why).
- Keep claims traceable to sources/assumptions from Stages 2–3.

### Stage 5 — CRITIQUE (attack your own work, adversarially)
- Switch hats: become a hostile reviewer who *wants* to find problems.
- Hunt for: factual errors, unsupported claims, weak/hidden assumptions, missing edge
  cases, logical gaps, security/safety issues, places you were lazy or hand-wavy, and ways
  the output fails the Stage 1 success criteria.
- Steelman the strongest counterargument to your conclusion.
- A complex task with **zero** findings means you didn't look hard enough — look again.
- Output: a list of findings, each with a severity (blocker / major / minor) and a location.

### Stage 6 — VERIFY (prove it, don't assert it)
- Execute the Stage 3 verification strategy with *concrete* checks, not vibes:
  run the code/tests, recompute the numbers independently, re-open the sources and confirm
  the quote/figure, check the output against each success criterion.
- For anything you cannot verify, say so explicitly and lower the confidence.
- Output: a checklist of what was checked, *how*, and the result of each.

### Stage 7 — REVISE (close the loop)
- Fix every blocker and major finding from Stages 5–6. For each finding, point to the
  change that resolves it (or justify why it's accepted as-is).
- Re-verify anything you changed.
- Output: the corrected work + a finding→fix mapping.

### Stage 8 — DELIVER (only what is needed)
- Lead with the answer/result. Be concise; cut fluff. Match format to the request.
- State limitations, residual uncertainty, and assumptions the user should know about.
- Cite sources for factual claims.

### Stage 9 — REFLECT (optional, for big or repeated work)
- What would you do differently? What's reusable? Capture it.

---

## 2.5 Reasoning moves within stages

These are not extra stages — they are habits that govern how you think *inside* each stage.

- **Generate-and-compare before committing.** For any non-trivial sub-problem, generate at least two candidate approaches before selecting one. Name the trade-offs. Do not anchor on the first idea that sounds plausible.
- **Recursive decomposition for genuinely hard sub-problems.** If a step in your plan is itself complex, treat it as a mini-pipeline: frame it, sketch an approach, sanity-check the result. Do not flatten complexity by pretending a hard step is easy.
- **Know when to stop.** Further reasoning has diminishing returns. When the cost of another loop (time, tokens, latency) exceeds the expected gain in confidence or correctness, stop and be explicit about the residual uncertainty instead of grinding.
- **VERIFY→REVISE loops, and the engine enforces it.** Verification is not a one-pass formality. In v2, a passing REVISE that records concrete fixes routes *back* to VERIFY (re-verify the fixes) before the pipeline continues to DELIVER. The loop is driven by a structured per-check `status`: if a re-verify marks any check `fail` or `inconclusive`, the engine routes back to REVISE for another cycle; a re-verify with no failing/inconclusive check exits forward to DELIVER. (The trigger is the structured status field, not keyword-scanning of the prose result.) Loops are capped at 3 and recorded as a positive rigor signal in the audit certificate — they are evidence of real iteration, not failure. At the cap the engine stops looping and DELIVER must document residual risk. In the MCP server and CLI harness modes, this routing is done by the engine, not the model. A REVISE that records only vague intent ("will address later") is rejected outright (`NOOP_FIX`) — the engine requires at least one concrete-edit verb.
- **Auto-escalation on serious findings.** If a CRITIQUE artifact contains any finding of severity `major` or `blocker`, OR a RESEARCH artifact lists any `risk_flags` entry (a named safety/financial/legal/irreversibility risk surfaced while gathering facts), the engine raises the session level to at least FULL for all remaining stages and records the escalation in the gate history and certificate. (Critique severity is self-assigned, so escalation deliberately triggers on `major` too — not just the single highest label.) The model cannot lower it afterward (`set_rigor` is raise-only). Honest limit: because both the critique severity and the research `risk_flags` are the model's own labels, a non-cooperative model can still avoid escalation by labeling everything `minor` or omitting `risk_flags` — this raises the cost of dodging, it does not make it impossible. Like all the gates, it checks the shape of rigor, not the substance.
- **Human pause in interactive mode.** In the engine's `interactive` mode, a FRAME artifact that contains open questions does not automatically advance. The engine pauses and waits for the human to answer via `provide_answers` (engine method), `answer_questions` (MCP tool), or stdin (CLI harness). In `headless` mode, questions are still allowed but the certificate is stamped `proceeded_without_answers: true` with the unanswered questions listed — honest disclosure, not silent assumption.

## 3. The rigor dial (proportionality, made mechanical)

The same pipeline runs at four intensities. This is the **toggle** the operator controls.

| Level | Mandatory stages | Use when |
|-------|------------------|----------|
| **LOW** | Frame (light) → Draft → Deliver | Trivial / low-stakes / quick factual or formatting tasks. |
| **MEDIUM** | Frame → (Research if facts involved) → Plan → Draft → Critique → Deliver | Normal work where being wrong has moderate cost. |
| **FULL** | All eight gated stages with minimum thresholds enforced | High-stakes, irreversible, expensive-to-be-wrong, or explicitly requested rigor. |
| **ADAPTIVE** | Model first **classifies** task complexity & stakes, then the dial auto-selects LOW/MEDIUM/FULL and justifies the choice. | Default. Avoids over- and under-engineering. |

Rules:
- Higher stakes can only *raise* the level, never lower it. If research surfaces that a
  "simple" task is actually risky, escalate.
- ADAPTIVE must *show its work*: state the classification (complexity, stakes, reversibility)
  and the level it selected before proceeding.
- In v2, the engine enforces a **risk floor** on ADAPTIVE. If the goal contains signals of
  money, irreversibility, or regulated domains (legal, medical, security, auth, tax,
  production/deploy), the engine will not accept a `selected_level` of "low" — it returns
  `RISK_FLOOR` with the matched signals. The model must re-classify at medium or full.

---

## 4. Anti-laziness tripwires (what the enforcer specifically hunts)

Laziness is the enemy this whole system exists to defeat. It shows up as:

- **Skipped clarification** — building on a guess when the ambiguity was load-bearing.
- **Asserted-not-verified** — "this is correct / this works / as of today" with no check.
- **Hollow critique** — "looks good!" or zero findings on a genuinely complex task.
- **Unmapped revision** — claiming fixes without showing what changed.
- **Hand-waving** — "you could…", "various approaches exist…", TODOs left where work was asked.
- **Fabrication** — invented citations, file paths, figures, or APIs.
- **Padding** — length used as a substitute for substance.
- **False completion** — declaring done while success criteria are unmet.

The enforcer cannot judge *brilliance*, but it can mechanically refuse the *shape* of each
of these. That is its job: it makes skipping a step cost more than doing it.

---

## 5. Profile overlays (same spine, domain-specific muscles)

All profiles run §1–§4. They differ only in the extra questions and checks they inject.

### 5a. UNIVERSAL
The base protocol, unmodified. Good default for any task or domain.

### 5b. AI-BUILDER (building AI tools, agents, prompts, software)
Inject at the named stages:
- **Frame:** Who is the user of this tool and what's the job-to-be-done? What does failure
  look like in production? What are the latency/cost/privacy constraints?
- **Plan:** Spec-first — define inputs, outputs, and acceptance criteria before coding.
  Design for model-agnosticism where possible. Plan evals/tests *before* implementation.
- **Critique:** Security & abuse review (injection, secrets, unsafe tool calls). Failure
  modes, rate limits, cost blowups, hallucination surfaces. Reproducibility.
- **Verify:** Actually run it — unit tests, a small eval set, an end-to-end smoke test. Show
  the output, not a claim that it works.
- **Deliver:** Note cost/latency/model-portability characteristics and known limitations.

### 5c. ENTREPRENEUR (ventures, products, go-to-market, monetization)
Inject at the named stages:
- **Frame:** What's the riskiest assumption — the thing that, if false, kills this? Who is
  the customer and what painful problem are you solving? What would have to be true?
- **Research:** Real demand signals, competitors/alternatives (including "do nothing"),
  market size sanity-check, and how customers currently cope.
- **Plan:** Sequence by *riskiest assumption first* — cheapest test that could invalidate it.
  Define unit economics (CAC, price, margin, payback) at least roughly. Define **kill
  criteria** up front.
- **Critique:** Survivorship/confirmation bias, distribution (how will anyone find this?),
  why-now, why-you, regulatory/operational landmines.
- **Verify:** Tie claims to evidence (a signal, a conversation, a number), not optimism.
- **Deliver:** A clear recommendation — pursue / test-further / kill — with the evidence and
  the next cheapest experiment.

---

## 6. How the two products use this file

- **The Skills (Product 1)** translate §1–§5 into instructions a model reads and follows on
  its own — including an instruction to *use the enforcer tools if they're available*.
- **The Enforcer (Product 2)** turns §2–§4 into a state machine with gates. It holds the
  task state, demands the artifact for each required stage, mechanically rejects the
  anti-laziness tripwires in §4, and refuses to let the model finalize until every required
  gate has passed.

A mechanical gate can verify the *shape* of rigor (that each stage was done, with the right
structure), not the *substance* (whether the thinking was good). What this system does is make
the lazy path more expensive than the honest path for a cooperating model, raise the floor of
effort, and — in the CLI-harness mode — make skipping stages literally impossible because the
harness, not the model, controls the loop.

### Enforcement modes

| Mode | How enforcement works |
|------|-----------------------|
| **Skill only (no enforcer)** | Voluntary self-enforcement. The model follows the protocol on its own. Best-effort. |
| **MCP server** | The gates are real, but the model must choose to call the tools. A model can decline to call `begin_task`/`submit_stage` and answer directly. Enforcement applies only to work routed through the tools. Use a system-prompt instruction requiring tool use to close this gap. |
| **CLI harness** | The only non-bypassable mode. The harness owns the loop and drives the model stage by stage; the model has no path around the gates. In `--exec` mode, the harness runs the commands attached to each check in a subprocess and sets THAT check's evidence and pass/fail status from the real exit code — so a check backed by a command can have neither its evidence nor its verdict fabricated, and a check with no command cannot be marked `pass` (the harness records it `inconclusive`). The harness verifies the exit code only, not whether the command truly tests the claim. Note: `--exec` runs real commands in a temp working dir with a timeout; it is **not** a security sandbox (no filesystem confinement; `--allow-network` is an intent flag that warns but does not block). |

The skill is the recipe card. The enforcer is the kitchen line. Together they make rigor the
path of least resistance — but only the CLI harness makes it the *only* path.

### v2 behaviors: what the engine actually does in MCP and CLI modes

The following are not aspirational prose — they are behaviors implemented in v2 and active in
both MCP-server and CLI-harness modes.

**Real evidence at VERIFY.** The VERIFY artifact now requires at least one check to include an
`evidence` field containing a concrete artifact token: a digit, an explicit PASS/FAIL tied to a
named criterion, a `file:line` reference, or a quoted output snippet. Submitting a check whose
only content is the word "tested" or similar vocabulary is rejected (`NO_EVIDENCE`). In
CLI `--exec` mode, the harness itself runs the commands attached to each check and produces
that check's evidence and status from the real exit code — for a check backed by a command the
model cannot fabricate the result, because the harness, not the model, produces it. A check with
no command is recorded `inconclusive` rather than accepted as a model-asserted `pass`.

**Junk rejection.** Free-text fields (goal restatements, steps, risks, criteria, issues,
changes, claims, summaries) are now checked against a junk detector: content fails if
unique-character count is below 5, a single character dominates more than 60% of non-space
characters, or fewer than 3 distinct word-tokens appear. Repeated-character filler and
single-token padding are rejected with code `JUNK_CONTENT`.

**Source honesty.** Each source now carries a `type`: `url`, `file`, `tool_output`, or
`assumed`. Honest `assumed` sources are allowed — but any fact marked `assumed` must be
reflected in the delivery's limitations (`UNCOVERED_LIMITATION` if missing). Fabricated-looking
`url` or `tool_output` sources (empty, placeholder, `example.com`, `todo`, `xxx`) are flagged
`FABRICATION_RISK`. Honesty about uncertainty is no longer the harshest failure.

**Unknowns carried forward.** Research unknowns and all `assumed`-source claims are
automatically added to the session's `pending_limitations` list. The DELIVER gate checks that
each pending item is addressed in `deliver.limitations` — unresolved unknowns that reach
delivery without disclosure are a gate failure.

**Safety circuit-breaker.** `create_session` runs a coarse keyword/category screen before
any stage begins. Goals that clearly match categories such as weapons/explosives, malware,
fraud/phishing/scam targeting, CSAM, self-harm facilitation, or mass-surveillance/doxxing are
refused — the session is created with `status: refused` and subsequent `submit`/`finalize`
calls return refused. An operator can pass `override_safety=True` to bypass the screen; the
bypass is logged in the certificate. **This screen is coarse keyword matching, not nuanced
safety judgment.** It exists so the certificate cannot silently launder obviously harmful work.
It will produce false positives on legitimate research tasks that use flagged vocabulary, and
it will miss sophisticated harmful goals that avoid trigger words.
