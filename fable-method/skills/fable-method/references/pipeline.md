# The Pipeline — base stage templates

The eight gated stages (plus optional Reflect). They always run in this order: never
draft before planning; never finalize before verifying. At lower rigor levels, skipped
stages are truly skipped — not silently rushed. Each stage below gives its purpose, what
to do, and an artifact template.

> If your task is AI/software building or a venture/market decision, also read the
> matching flavor file — it adds extra questions and checks to some of these stages.

---

## STAGE 1 — FRAME
**Purpose:** Understand the real problem before touching the work.

- Restate the goal in your own words. What does "done" look like? Who is the audience?
  What is the format and the constraints?
- Detect underspecification. List what is genuinely ambiguous and load-bearing (would
  change what you build).
- Decide: ask or assume. If an ambiguity changes the output and you can't resolve it from
  context, ask one sharp question. Otherwise, state an explicit assumption and proceed —
  don't stall on things with a sensible default.

```
FRAME
Goal restatement: [your own words, not a quote of the request]
Success criteria: [how will you know you're done and right?]
Clarifying questions: [only the truly load-bearing ones]
  OR
Explicit assumptions: [each assumption + why it's a safe default]
```

---

## STAGE 2 — RESEARCH
**Purpose:** Get the facts before reasoning on them.

- Separate what you *know* from what you must verify. Present-day facts (prices, versions,
  who holds a role, current events) are not safely knowable from training alone — verify
  them if they matter.
- If web search or tools are available, use them for any factual claim the output depends on.
- Prefer authoritative, recent, specific sources. Record where each fact came from.
- Note conflicting sources rather than silently picking one.

```
RESEARCH
Facts relied on:
  - [claim] — source: [URL, document, or explicit "training data / unverified"]
Known unknowns (couldn't resolve): [list or "none"]
Risk flags (safety/financial/legal/irreversibility risks surfaced): [list or omit]
```
> Any non-empty `risk_flags` entry auto-escalates the session to FULL rigor. Flag honestly.

---

## STAGE 3 — PLAN
**Purpose:** Decide the approach before building anything.

- Decompose the task into sub-problems. Identify dependencies and the right sequence.
- Name the risks: where is this most likely to go wrong? What's the hardest part?
- Choose tools and methods deliberately and say why.
- Define the verification strategy now — how will you later prove each part is correct?
  (Tests to run, math to recompute, sources to re-read, criteria to check.)

```
PLAN
Steps (in order):
  1. ...
Risks:
  - [risk] — mitigation: [...]
Verification strategy:
  - [what will be checked, how]
```

---

## STAGE 4 — DRAFT
**Purpose:** Build the thing.

- Follow the plan. If reality diverges, update the plan and note why.
- Keep claims traceable to sources and assumptions from Stages 2–3.
- No hand-waving. No TODOs where work was asked for. No "you could…" when asked to do it.

```
DRAFT
[The actual work product — code, document, analysis, plan, etc. — not an outline]
```

---

## STAGE 5 — CRITIQUE
**Purpose:** Attack your own work adversarially. Switch hats.

- Become a hostile reviewer who *wants* to find problems.
- Hunt for: factual errors, unsupported claims, weak or hidden assumptions, missing edge
  cases, logical gaps, security or safety issues, places you were lazy, and ways the
  output fails the Stage-1 success criteria.
- Steelman the strongest counterargument to your conclusion.
- **If you find zero issues on a complex task, you didn't look hard enough — look again.**
  Justify any "no issues" claim in at least 3 sentences.

```
CRITIQUE
Findings:
  - [BLOCKER/MAJOR/MINOR] — Issue: [...] — Location: [...]
Steelman of the strongest counterargument: [...]
```

---

## STAGE 6 — VERIFY
**Purpose:** Prove it, don't assert it.

- Execute the Stage-3 verification strategy with *concrete* checks, not vibes.
- If code: run it (or state clearly you cannot, and lower confidence accordingly).
- If numbers: recompute them independently. If sources: re-open them and confirm.
- Check the output against each Stage-1 success criterion.
- For anything you cannot verify, say so explicitly and lower your stated confidence.

```
VERIFY
Checks:
  - What: [...] — How: [ran / recomputed / re-read / measured — be specific] — Result: [pass/fail/partial + detail]
Unverifiable items: [list with explicit confidence downgrade, or "none"]
```

---

## STAGE 7 — REVISE
**Purpose:** Close the loop on every blocker and major finding.

- Fix every BLOCKER and MAJOR finding from Stages 5–6.
- For each finding, point to the specific change that resolves it (or justify in writing
  why it is accepted as-is).
- Re-verify anything you changed.

```
REVISE
Finding → Fix mapping:
  - [Finding ref] → [What changed, or: accepted as-is because ...]
Re-verified: [yes/no — what was re-checked]
```

---

## STAGE 8 — DELIVER
**Purpose:** Give the user only what they need, clearly.

- Lead with the answer or result. Be concise. Cut fluff. Match format to the request.
- State limitations, residual uncertainty, and assumptions the user should know about.
- Cite sources for factual claims.

```
DELIVER
[Answer / result — lead with it]
Limitations & residual uncertainty: [...]
Key assumptions: [...]
Sources: [...]
```

---

## STAGE 9 — REFLECT (optional)
**Purpose:** Capture what's reusable for big or repeated work.

```
REFLECT
What I'd do differently: [...]
What's reusable: [...]
```
