# Flavor: AI-Builder

Read this on top of `pipeline.md` when the task is building or reviewing AI tools, agents,
prompts, pipelines, APIs, software features, or AI-powered products. It *adds* to the base
stages — it does not replace them. Default to FULL rigor for anything touching production.

Two base-mindset emphases for technical work: never invent API names, model capabilities,
or SDK methods to fill a gap (especially dangerous here); and a bad architecture decision
is far cheaper to catch now than after implementation.

## FRAME — add these questions
- Who is the end user, and what is the job-to-be-done?
- What does failure look like in production? (Wrong output? Harmful output? Silent failure?
  Cost blowup?)
- What are the latency, cost, and privacy constraints?
- What model or infrastructure does this run on — must it be model-agnostic?
- What existing systems does it integrate with?

Add to the FRAME artifact: `End user & job-to-be-done`, `Failure modes in production`,
`Constraints (latency / cost / privacy / model / infra)`.

## PLAN — spec the system before building it
- **Spec-first:** define inputs, outputs, and acceptance criteria in writing before any
  code or prompt is drafted.
- **Design for model-agnosticism** where possible; avoid hard-coding vendor behavior
  without a compelling reason.
- **Plan evals/tests before implementation:** the smallest eval set that catches the most
  important failure modes. Define it now.
- Name the riskiest surface — injection? hallucination? cost blowup? irreversible tool
  call? — and address it in the plan.

Add to the PLAN artifact: `Inputs`, `Outputs`, `Acceptance criteria`, `Eval/test strategy`,
`Model-agnosticism notes`.

## DRAFT — note
If code: write it in full. If a prompt: write it in full, not a sketch. If a spec: write
every field. No outlines where the artifact was asked for.

## CRITIQUE — run all of these
- **Security & abuse:** Can this prompt be injected? Are secrets or sensitive data exposed?
  Are tool-call / code-execution paths constrained against misuse?
- **Failure-mode inventory:** model hallucinates; tool returns an unexpected schema;
  context length exceeded; adversarial input.
- **Cost & rate-limit:** could this blow up in cost at scale? Retry loops that spiral?
- **Hallucination surfaces:** where could the model confidently return wrong info with no catch?
- **Reproducibility:** is behavior deterministic enough to test? If not, is that acceptable?

Add to the CRITIQUE artifact: `Security / abuse review`, `Failure-mode inventory`.

## VERIFY — prove it works
- **Actually run it** if execution is available — show the output, not a claim.
- **Run the eval set** from PLAN, even if small. Record results.
- **End-to-end smoke test:** trace one representative input through the full system.
- **Check each acceptance criterion** from PLAN, one by one.
- If you can't run it, say so and describe exactly what a reviewer would do to verify.

Add to the VERIFY artifact: `Eval results`, `Smoke-test trace`.

## DELIVER — add these
- **Cost / latency:** what does this cost per call at scale? Expected latency profile?
- **Model portability:** what's portable vs. vendor-specific?
- **Known limitations:** where it breaks, what inputs produce bad results, what's untested.
- **Recommended next step** before shipping.

## Extra tripwire
- **Untested claim** — "This prompt will reliably do X" with no eval → run even a minimal
  eval, or explicitly caveat the claim.
