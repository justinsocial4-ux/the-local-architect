---
name: fable-method
description: Apply the Fable Method — a disciplined, gated reasoning pipeline (Frame → Research → Plan → Draft → Critique → Verify → Revise → Deliver, plus optional Reflect) with a rigor dial that scales to the stakes. Use this skill whenever the user wants careful, thorough, high-stakes, or mistake-averse work; OR is building or reviewing AI systems, prompts, agents, pipelines, or software; OR is evaluating a business idea, market, pricing, or venture decision. Trigger on phrases like "think carefully", "be rigorous", "don't skip anything", "take your time", "use the method", "full pipeline", "build/design/spec this", "review this prompt", "is this safe to deploy", "should I pursue this idea", "validate this market", "is this worth building" — and on any task where being wrong has real cost (irreversible decisions, financial or legal analysis, production code, research synthesis). Pick the flavor — general, AI-builder, or entrepreneur — from the task and load the matching reference file. Err toward triggering.
---

# Fable Method

Force careful, staged reasoning instead of a fast guess. Work moves through gated stages **in order** — each stage must be done before the next — and effort scales to the stakes. This file is the map; open the reference files as you need them (that is the point of the method: load detail only when the step calls for it).

## 1. Enforced or self-enforced?

- **If the enforcer connector tools are available** (`begin_task`, `submit_stage`, `finalize`, `get_state`, `set_rigor`, `answer_questions`) — **use them** and follow their output exactly; they will hand you stage instructions and reject artifacts that fail a gate. Read `references/enforcer.md` for the behaviors to expect (real evidence, verify↔revise loops, refusals, source typing).
- **If not**, self-enforce: produce each stage's artifact inline before moving to the next. Honest caveat: self-enforced mode is best-effort — only the CLI harness makes skipping a stage architecturally impossible.

## 2. Pick the rigor level (before you start)

| Level | Stages | Use when |
|---|---|---|
| **LOW** | Frame (light) → Draft → Deliver | trivial / low-stakes / quick |
| **MEDIUM** | Frame → Research → Plan → Draft → Critique → Deliver | normal work, moderate cost if wrong |
| **FULL** | all 8 gated stages | high-stakes, irreversible, production, or explicitly requested |
| **ADAPTIVE** *(default)* | classify stakes first, then auto-pick LOW/MEDIUM/FULL | avoids over- and under-engineering |

Rules: stakes can only **raise** the level, never lower it. In ADAPTIVE, state your classification (complexity / stakes / reversibility / chosen level + one sentence why) before proceeding. If the enforcer sets the level, do not override it downward.

## 3. Pick the flavor → load its reference

Every run: read `references/mindset-and-tripwires.md` (the mindset and self-checks apply at every stage) and `references/pipeline.md` (the stage templates). Then, based on the task:

- **Building AI / software / agents / prompts / pipelines** → also read `references/profile-ai-builder.md`
- **A business idea, market, pricing, or venture decision** → also read `references/profile-entrepreneur.md`
- **Anything else** → the base pipeline is enough.

The flavor files only *add* a few questions and checks to specific stages — they don't replace the base pipeline.

## 4. The pipeline at a glance

1. **FRAME** — understand the real problem; restate the goal, set success criteria, ask or assume.
2. **RESEARCH** — gather real facts with sources; flag risks.
3. **PLAN** — decompose, name risks, decide how you'll verify.
4. **DRAFT** — do the actual work; no hand-waving.
5. **CRITIQUE** — attack your own work; find the real problems.
6. **VERIFY** — prove it with concrete evidence, not assertions.
7. **REVISE** — fix every blocker/major; re-verify.
8. **DELIVER** — lead with the answer; state limits, assumptions, sources.
9. **REFLECT** *(optional)* — capture what's reusable.

Full "do this" guidance + artifact templates for each stage live in `references/pipeline.md`.

## Reference files

| File | Read it for |
|---|---|
| `references/pipeline.md` | the 8 stage templates (+ Reflect) — **every run** |
| `references/mindset-and-tripwires.md` | the 8 mindset principles, anti-laziness tripwires, quick-start checklist — **every run** |
| `references/enforcer.md` | how to behave when the enforcer connector is driving the loop |
| `references/profile-ai-builder.md` | overlays for building AI / software |
| `references/profile-entrepreneur.md` | overlays for venture / market decisions |
