"""
profiles.py — Profile overlays for the fable_method enforcement engine.

Profiles add stage-specific guidance and advisory checklist items.
They do NOT modify gate pass/fail logic; that is exclusively in engine.py.

Exported symbols:
    PROFILES          dict[str, dict]  — profile metadata (currently descriptive)
    get_instructions  (profile, stage, level) -> str
    get_overlay_checks(profile, stage) -> list[str]
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Base stage guidance (shared by every profile)
# ---------------------------------------------------------------------------

_BASE_GUIDANCE: dict[str, str] = {
    "classify": (
        "CLASSIFY — Adaptive rigor selection.\n"
        "Assess the task on three dimensions:\n"
        "  • complexity: low | medium | high\n"
        "  • stakes: low | medium | high  (cost of being wrong)\n"
        "  • reversibility: easy | hard  (can a mistake be undone cheaply?)\n"
        "Then choose selected_level (low / medium / full) consistent with those ratings.\n"
        "High stakes or hard reversibility MUST map to medium or full.\n"
        "Write a justification that explains the choice — not a one-liner."
    ),
    "frame": (
        "FRAME — Understand the real problem before building anything.\n"
        "1. Restate the goal in your own words (not a paraphrase of the request).\n"
        "2. List success criteria — concrete, checkable conditions for 'done'.\n"
        "3. Either list genuinely load-bearing ambiguities as clarifying questions,\n"
        "   OR document explicit assumptions with reasons they're safe.\n"
        "Do NOT echo the goal verbatim. Do NOT leave success_criteria empty."
    ),
    "research": (
        "RESEARCH — Gather facts before reasoning on them.\n"
        "1. Separate what you know from what you must verify externally.\n"
        "2. For present-day factual claims (prices, versions, leadership, events)\n"
        "   you MUST cite a real, specific source — not 'training data'.\n"
        "3. Note conflicting sources rather than silently picking one.\n"
        "4. List known unknowns you could not resolve.\n"
        "5. If the research surfaces a safety, financial, legal, or irreversibility\n"
        "   risk, name it in `risk_flags` (a list). ANY non-empty entry auto-escalates\n"
        "   the session to FULL rigor — so flag honestly rather than under-report.\n"
        "If no external research is needed, say so explicitly with `no_research_needed:true`\n"
        "and explain why."
    ),
    "plan": (
        "PLAN — Decide the approach before you build.\n"
        "1. Decompose the task into ordered steps. At FULL rigor: ≥3 steps.\n"
        "2. Name at least one risk — where is this most likely to go wrong?\n"
        "3. Define the verification strategy NOW — how will you prove each part\n"
        "   is correct? (tests to run, numbers to recompute, sources to re-read)"
    ),
    "draft": (
        "DRAFT — Build the thing. Follow the plan.\n"
        "• Keep claims traceable to research sources and plan assumptions.\n"
        "• Do NOT leave TODOs, 'you could…' hand-offs, or 'as an AI…' disclaimers\n"
        "  in place of actual work.\n"
        "• Substance over length. Do not pad."
    ),
    "critique": (
        "CRITIQUE — Attack your own work adversarially.\n"
        "Switch hats: become a hostile reviewer who wants to find problems.\n"
        "Hunt for: factual errors, unsupported claims, hidden assumptions, missing\n"
        "edge cases, logical gaps, security/safety issues, lazy hand-waving.\n"
        "Steelman the strongest counterargument to your conclusion.\n"
        "A complex task with ZERO findings means you didn't look hard enough."
    ),
    "verify": (
        "VERIFY — Prove it; don't assert it.\n"
        "Execute your Stage 3 verification strategy with CONCRETE checks:\n"
        "  • ran the code / tests\n"
        "  • recomputed the numbers independently\n"
        "  • re-read the source and confirmed the quote/figure\n"
        "  • checked output against each success criterion\n"
        "For anything you cannot verify, say so and lower confidence. Every check\n"
        "must state HOW it was done, not merely WHAT was checked."
    ),
    "revise": (
        "REVISE — Close the loop on every blocker/major finding.\n"
        "For each finding of severity blocker or major from critique/verify:\n"
        "  • provide a fix entry whose `finding_ref` matches the finding's description\n"
        "  • describe the concrete change made\n"
        "  • set reverified=true if anything was changed\n"
        "Do NOT mark fixes without mapping them to specific findings."
    ),
    "deliver": (
        "DELIVER — Lead with the answer. Be concise.\n"
        "1. summary: the result / answer directly.\n"
        "2. limitations: residual uncertainty, caveats the user must know.\n"
        "   May be 'none, because…' but must be present and non-empty.\n"
        "3. sources: cite factual claims. May be empty list only if no facts asserted."
    ),
    "reflect": (
        "REFLECT (optional) — What would you do differently?\n"
        "What is reusable? Capture lessons for repeated work."
    ),
}

# ---------------------------------------------------------------------------
# Profile overlay guidance (appended to base; advisory only)
# ---------------------------------------------------------------------------

_OVERLAY_GUIDANCE: dict[str, dict[str, str]] = {
    "universal": {},  # No overlays — pure base protocol

    "ai_builder": {
        "frame": (
            "\n[AI-BUILDER] Also address:\n"
            "  • Who is the user of this tool and what's the job-to-be-done?\n"
            "  • What does failure look like in production?\n"
            "  • What are the latency / cost / privacy constraints?"
        ),
        "plan": (
            "\n[AI-BUILDER] Also:\n"
            "  • Spec-first: define inputs, outputs, and acceptance criteria before coding.\n"
            "  • Design for model-agnosticism where possible.\n"
            "  • Plan evals/tests BEFORE implementation."
        ),
        "critique": (
            "\n[AI-BUILDER] Extra attack vectors:\n"
            "  • Security & abuse review (injection, secrets, unsafe tool calls).\n"
            "  • Failure modes, rate limits, cost blowups, hallucination surfaces.\n"
            "  • Reproducibility."
        ),
        "verify": (
            "\n[AI-BUILDER] Concrete verification required:\n"
            "  • Actually run it — unit tests, a small eval set, end-to-end smoke test.\n"
            "  • Show the output, not a claim that it works."
        ),
        "deliver": (
            "\n[AI-BUILDER] Also document:\n"
            "  • Cost / latency / model-portability characteristics.\n"
            "  • Known limitations for production use."
        ),
    },

    "entrepreneur": {
        "frame": (
            "\n[ENTREPRENEUR] Also address:\n"
            "  • What's the riskiest assumption — the thing that, if false, kills this?\n"
            "  • Who is the customer and what painful problem are you solving?\n"
            "  • What would have to be true for this to work?"
        ),
        "research": (
            "\n[ENTREPRENEUR] Gather:\n"
            "  • Real demand signals (conversations, search volume, waitlists).\n"
            "  • Competitors / alternatives including 'do nothing'.\n"
            "  • Market size sanity-check with sources.\n"
            "  • How customers currently cope without your solution."
        ),
        "plan": (
            "\n[ENTREPRENEUR] Sequence by riskiest assumption first:\n"
            "  • Cheapest test that could invalidate it.\n"
            "  • Define unit economics (CAC, price, margin, payback) at least roughly.\n"
            "  • Define kill criteria up front."
        ),
        "critique": (
            "\n[ENTREPRENEUR] Extra attack vectors:\n"
            "  • Survivorship / confirmation bias.\n"
            "  • Distribution: how will anyone find this?\n"
            "  • Why now? Why you? Regulatory / operational landmines."
        ),
        "verify": (
            "\n[ENTREPRENEUR] Tie claims to evidence:\n"
            "  • A signal, a conversation, a number — not optimism.\n"
            "  • Every market-size or demand claim needs a traceable source."
        ),
        "deliver": (
            "\n[ENTREPRENEUR] End with a clear recommendation:\n"
            "  pursue / test-further / kill — with supporting evidence and the\n"
            "  next cheapest experiment to run."
        ),
    },
}

# ---------------------------------------------------------------------------
# Advisory overlay checks (gate appends these as fix_hints / reminders)
# ---------------------------------------------------------------------------

_OVERLAY_CHECKS: dict[str, dict[str, list[str]]] = {
    "universal": {},

    "ai_builder": {
        "frame": [
            "Specify the end-user of the tool and their job-to-be-done.",
            "Describe what production failure looks like.",
            "State latency, cost, and privacy constraints.",
        ],
        "plan": [
            "Define inputs, outputs, and acceptance criteria before any code.",
            "Plan tests/evals before implementation.",
        ],
        "critique": [
            "Review for prompt injection and secret leakage.",
            "Check for cost-blowup and rate-limit failure modes.",
            "Assess reproducibility of the approach.",
        ],
        "verify": [
            "Provide actual run output, not assertions.",
            "Include at least one end-to-end smoke test result.",
        ],
        "deliver": [
            "Document cost, latency, and model-portability characteristics.",
        ],
    },

    "entrepreneur": {
        "frame": [
            "State the single riskiest assumption.",
            "Identify the customer and the painful problem they have.",
        ],
        "research": [
            "Include real demand signals (not just market reports).",
            "Cover competitors AND the 'do nothing' alternative.",
        ],
        "plan": [
            "Sequence by riskiest assumption first.",
            "Include rough unit economics (CAC, price, margin).",
            "Define explicit kill criteria.",
        ],
        "critique": [
            "Check for survivorship and confirmation bias.",
            "Address distribution: how will customers find this?",
            "Assess why-now and why-you.",
        ],
        "verify": [
            "Every market-size or demand claim needs a traceable evidence source.",
        ],
        "deliver": [
            "End with pursue / test-further / kill recommendation.",
            "State the next cheapest experiment.",
        ],
    },
}

# ---------------------------------------------------------------------------
# PROFILES registry (metadata only; logic is above)
# ---------------------------------------------------------------------------

PROFILES: dict[str, dict] = {
    "universal": {
        "name": "Universal",
        "description": "Base protocol, unmodified. Good default for any task or domain.",
    },
    "ai_builder": {
        "name": "AI Builder",
        "description": (
            "For building AI tools, agents, prompts, and software. "
            "Adds security, eval, and production-readiness checks."
        ),
    },
    "entrepreneur": {
        "name": "Entrepreneur",
        "description": (
            "For ventures, products, go-to-market, and monetization. "
            "Adds market validation, unit economics, and kill-criteria checks."
        ),
    },
}

_VALID_PROFILES = set(PROFILES.keys())


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def get_instructions(profile: str, stage: str, level: str) -> str:
    """
    Return the human-readable guidance string for a given profile + stage + level.

    = base stage guidance + any profile overlay for that stage.

    Falls back gracefully if the profile or stage is unknown.
    """
    base = _BASE_GUIDANCE.get(stage, f"Stage: {stage}\nProceed according to the method.")

    if profile not in _VALID_PROFILES:
        # Unknown profile — return base only
        return base

    overlay = _OVERLAY_GUIDANCE.get(profile, {}).get(stage, "")

    level_note = ""
    if level == "low" and stage == "frame":
        level_note = "\n[LOW RIGOR] Keep this light — brief restatement and key assumptions only."
    elif level == "medium" and stage == "critique":
        level_note = "\n[MEDIUM RIGOR] At least one finding required; two or more preferred."
    elif level == "full":
        level_note = "\n[FULL RIGOR] All minimums are enforced at their strictest. Do not cut corners."

    return base + overlay + level_note


def get_overlay_checks(profile: str, stage: str) -> list[str]:
    """
    Return advisory checklist items for the gate to append as fix_hints / reminders.

    These are ADVISORY only; they do not affect gate pass/fail logic.
    Returns an empty list if the profile or stage has no overlays.
    """
    if profile not in _VALID_PROFILES:
        return []
    return list(_OVERLAY_CHECKS.get(profile, {}).get(stage, []))
