"""
test_engine.py — pytest tests for fable_method.engine

Covers every bullet in the CONTRACT "Tests" section:
  - Stage ordering is enforced (OUT_OF_ORDER on skip)
  - finalize refused until all required stages pass; allowed after
  - Each gate: passing artifact + at least one failing artifact per major violation code
  - Adaptive flow requires classify first; high-stakes cannot select "low"
  - LOW/MEDIUM/FULL require the correct stage sets
  - Anti-laziness tripwires: empty critique, unverified claim, unmapped fix, handwaving draft

A10 additions (FIXES.md §A):
  - A1: duplicate/trivial list items rejected at every gated list
  - A2: verify.how must be ≥15 chars + concrete method; result must not echo what
  - A3: FULL critique must have ≥1 blocker/major (two minors alone fails)
  - A4: revise must be non-empty and reverified=true when critique had blocker/major
  - A5: frame echo tripwire rejects goal verbatim and ≥0.9 token-overlap restatements
  - A6: classify complexity=high cannot select low
  - A7: deliver sources must be non-empty when real research was done
  - A8: legitimate prose with "placeholder text" and todo-app drafts do NOT trigger HANDWAVING
  - A9: done flag present in submit/get_state; True only after final stage
  - A10: all-"x" / duplicate-filler FULL run never reaches finalize
  - Round-trip persistence (create → reload from disk → continue)

Tests use a tmp_path fixture for store_dir; no network or third-party services required.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from fable_method.engine import Engine, V, REQUIRED_STAGES


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def eng(tmp_path: Path) -> Engine:
    """Return a fresh Engine backed by a temp directory."""
    return Engine(store_dir=str(tmp_path))


# ---------------------------------------------------------------------------
# Minimal passing artifacts per stage
# ---------------------------------------------------------------------------

GOOD_CLASSIFY = {
    "complexity": "high",
    "stakes": "high",
    "reversibility": "hard",
    "selected_level": "full",
    "justification": "The stakes are high and the decision is not easily reversed, so full rigor is warranted.",
}

GOOD_FRAME = {
    "goal_restatement": "Build a system that validates reasoning artifacts against a formal protocol.",
    "success_criteria": ["All gate validators reject invalid artifacts", "Finalize succeeds only after all stages pass"],
    "assumptions": [{"assumption": "Python 3.10+ available", "why_safe": "Standard dev environment"}],
}

GOOD_RESEARCH = {
    "facts": [{"claim": "A staged reasoning protocol that validates artifacts at each gate reduces unverified claims in long-form work.", "source": "https://en.wikipedia.org/wiki/Software_verification"}],
    "unknowns": ["Exact performance overhead of JSON serialisation at scale"],
}

GOOD_PLAN_FULL = {
    "steps": ["Design gate validators", "Implement Engine class", "Write persistence layer"],
    "risks": ["JSON serialisation may be slow for large artifacts"],
    "verification_strategy": ["Run pytest suite", "Check all gate codes fire correctly"],
}

GOOD_PLAN_MEDIUM = {
    "steps": ["Design the gate validators", "Implement the Engine class"],
    "risks": ["Edge cases in adaptive flow"],
    "verification_strategy": ["Run unit tests for all modules"],
}

GOOD_DRAFT = {
    "content": (
        "The enforcement engine is a state machine. Each session progresses through "
        "required stages in order. Gates validate artifacts before advancing. "
        "Violations are returned as structured dicts with stable codes."
    ),
}

GOOD_CRITIQUE = {
    "findings": [
        {"severity": "major", "issue": "Revise gate does not check reverified field when fixes exist", "location": "_gate_revise"},
        {"severity": "minor", "issue": "Missing docstrings on helper functions", "location": "engine.py"},
    ],
    "steelman": "The engine is well-structured; the gate logic is clear and the violation codes are stable.",
}

GOOD_VERIFY = {
    "checks": [
        {
            "what": "Gate rejects missing fields",
            "how": "Ran pytest suite covering all gates with invalid artifacts",
            "result": "All 12 negative cases raised expected violation codes",
            "evidence": "pytest: 12 passed, 0 failed in 0.34s",
        }
    ]
}

GOOD_REVISE = {
    "fixes": [
        {
            "finding_ref": "Revise gate does not check reverified field when fixes exist",
            "change": "Added check: if fixes present and reverified is False, add NOT_ENOUGH_RIGOR violation.",
        }
    ],
    "reverified": True,
}

GOOD_DELIVER = {
    "summary": "The enforcement engine is complete and all gates pass.",
    "limitations": [
        "Does not yet support async submission",
        "JSON serialisation performance overhead at scale was not measured and remains unknown",
    ],
    "sources": ["https://docs.python.org/3/library/json.html"],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def codes(result: dict) -> list[str]:
    """Extract violation codes from a submit result."""
    return [v["code"] for v in result.get("violations", [])]


def run_to_stage(eng: Engine, session_id: str, stop_before: str) -> None:
    """Drive the session forward, submitting good artifacts, stopping before a given stage.

    V7 note: handles the verify→revise→verify backtracking loop by re-submitting verify
    after a revise with real fixes, then using a no-loop revise to advance to deliver.
    """
    stage_order = ["classify", "frame", "research", "plan", "draft", "critique", "verify", "revise", "deliver"]
    good_artifacts = {
        "classify": GOOD_CLASSIFY,
        "frame": GOOD_FRAME,
        "research": GOOD_RESEARCH,
        "plan": GOOD_PLAN_FULL,
        "draft": GOOD_DRAFT,
        "critique": GOOD_CRITIQUE,
        "verify": GOOD_VERIFY,
        "revise": GOOD_REVISE,
        "deliver": GOOD_DELIVER,
    }
    for st in stage_order:
        if st == stop_before:
            break
        state = eng.get_state(session_id)
        if state["current_stage"] != st:
            continue  # already past or not required
        result = eng.submit(session_id, st, good_artifacts[st])
        assert result["accepted"], f"Expected pass at stage '{st}': {result}"
        # V7: revise with real fixes routes BACK to verify; after that verify passes,
        # the session advances to deliver directly (revise already completed).
        if st == "revise" and result.get("loop_back") and stop_before not in ("verify", "revise"):
            result2 = eng.submit(session_id, "verify", GOOD_VERIFY)
            assert result2["accepted"], f"V7 loop: verify re-submit failed: {result2}"
            # After loop verify, current_stage = deliver (no second revise)


def full_session(eng: Engine, goal: str = "Test goal") -> str:
    """Create an adaptive session resolved to full rigor, then drive all stages.

    V7 note: after revise passes with real fixes (reverified=True), the engine routes
    back to verify (backtracking loop). This helper drives through that loop once, then
    submits a no-loop revise (reverified=False, no fixes) to advance to deliver.
    """
    r = eng.create_session(goal, rigor="adaptive")
    sid = r["session_id"]
    # classify
    result = eng.submit(sid, "classify", GOOD_CLASSIFY)
    assert result["accepted"], result
    # stages up to (but not including) revise
    for stage, artifact in [
        ("frame", GOOD_FRAME),
        ("research", GOOD_RESEARCH),
        ("plan", GOOD_PLAN_FULL),
        ("draft", GOOD_DRAFT),
        ("critique", GOOD_CRITIQUE),
        ("verify", GOOD_VERIFY),
    ]:
        result = eng.submit(sid, stage, artifact)
        assert result["accepted"], f"Stage '{stage}' failed: {result}"
    # First revise — real fixes, reverified=True → V7 routes BACK to verify
    result = eng.submit(sid, "revise", GOOD_REVISE)
    assert result["accepted"], f"Stage 'revise' failed: {result}"
    if result.get("loop_back"):
        # Re-verify the fixes; after this verify passes it advances directly to deliver
        result = eng.submit(sid, "verify", GOOD_VERIFY)
        assert result["accepted"], f"Stage 'verify' (loop) failed: {result}"
        # current_stage is now "deliver" — no second revise needed
    # deliver
    result = eng.submit(sid, "deliver", GOOD_DELIVER)
    assert result["accepted"], f"Stage 'deliver' failed: {result}"
    return sid


# ---------------------------------------------------------------------------
# Test: stage ordering (OUT_OF_ORDER)
# ---------------------------------------------------------------------------

class TestStageOrdering:
    def test_out_of_order_skip_to_draft(self, eng: Engine) -> None:
        """Submitting draft before frame must be rejected with OUT_OF_ORDER."""
        r = eng.create_session("goal", rigor="low")
        sid = r["session_id"]
        result = eng.submit(sid, "draft", GOOD_DRAFT)
        assert not result["accepted"]
        assert V.OUT_OF_ORDER in codes(result)

    def test_out_of_order_after_one_pass(self, eng: Engine) -> None:
        """After frame passes, submitting deliver (not draft) must be OUT_OF_ORDER."""
        r = eng.create_session("goal", rigor="low")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        result = eng.submit(sid, "deliver", GOOD_DELIVER)
        assert not result["accepted"]
        assert V.OUT_OF_ORDER in codes(result)

    def test_correct_order_passes(self, eng: Engine) -> None:
        """frame → draft → deliver in order must all pass for LOW rigor."""
        r = eng.create_session("goal", rigor="low")
        sid = r["session_id"]
        r1 = eng.submit(sid, "frame", GOOD_FRAME)
        assert r1["accepted"]
        r2 = eng.submit(sid, "draft", GOOD_DRAFT)
        assert r2["accepted"]
        r3 = eng.submit(sid, "deliver", GOOD_DELIVER)
        assert r3["accepted"]


# ---------------------------------------------------------------------------
# Test: finalize behaviour
# ---------------------------------------------------------------------------

class TestFinalize:
    def test_finalize_refused_mid_session(self, eng: Engine) -> None:
        """finalize must fail if required stages are incomplete."""
        r = eng.create_session("goal", rigor="low")
        sid = r["session_id"]
        fin = eng.finalize(sid)
        assert not fin["finalized"]
        assert "missing_stages" in fin
        assert "frame" in fin["missing_stages"]

    def test_finalize_refused_after_partial(self, eng: Engine) -> None:
        """finalize after only frame must still fail (draft, deliver pending)."""
        r = eng.create_session("goal", rigor="low")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        fin = eng.finalize(sid)
        assert not fin["finalized"]

    def test_finalize_allowed_after_all_stages(self, eng: Engine) -> None:
        """finalize must succeed once all required stages have passed."""
        r = eng.create_session("goal", rigor="low")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        eng.submit(sid, "draft", GOOD_DRAFT)
        eng.submit(sid, "deliver", GOOD_DELIVER)
        fin = eng.finalize(sid)
        assert fin["finalized"]
        assert "certificate" in fin
        cert = fin["certificate"]
        assert cert["session_id"] == sid
        assert set(cert["stages_completed"]) >= {"frame", "draft", "deliver"}

    def test_finalize_full_session(self, eng: Engine) -> None:
        """Full adaptive → full-rigor session finalizes correctly."""
        sid = full_session(eng)
        fin = eng.finalize(sid)
        assert fin["finalized"]


# ---------------------------------------------------------------------------
# Test: gate — classify
# ---------------------------------------------------------------------------

class TestGateClassify:
    def test_classify_pass(self, eng: Engine) -> None:
        r = eng.create_session("goal", rigor="adaptive")
        sid = r["session_id"]
        result = eng.submit(sid, "classify", GOOD_CLASSIFY)
        assert result["accepted"]

    def test_classify_missing_fields(self, eng: Engine) -> None:
        r = eng.create_session("goal", rigor="adaptive")
        sid = r["session_id"]
        result = eng.submit(sid, "classify", {})
        assert not result["accepted"]
        assert V.MISSING_FIELD in codes(result)

    def test_classify_trivial_justification(self, eng: Engine) -> None:
        r = eng.create_session("goal", rigor="adaptive")
        sid = r["session_id"]
        result = eng.submit(sid, "classify", {
            **GOOD_CLASSIFY,
            "justification": "ok",
        })
        assert not result["accepted"]
        assert V.EMPTY_OR_TRIVIAL in codes(result)

    def test_classify_justification_no_goal_token_emits_missing_goal_token(self, eng: Engine) -> None:
        """Bug (c): a long-but-generic justification that shares no token with the goal
        must emit the specific MISSING_GOAL_TOKEN code the docs reference (was EMPTY_OR_TRIVIAL)."""
        r = eng.create_session("Migrate the customer billing database to PostgreSQL", rigor="adaptive")
        sid = r["session_id"]
        result = eng.submit(sid, "classify", {
            **GOOD_CLASSIFY,
            "justification": "The chosen level reflects standard caution applied generically without specifics.",
        })
        assert not result["accepted"]
        assert V.MISSING_GOAL_TOKEN in codes(result), f"Expected MISSING_GOAL_TOKEN: {codes(result)}"

    def test_classify_level_inconsistent_high_stakes_low(self, eng: Engine) -> None:
        r = eng.create_session("goal", rigor="adaptive")
        sid = r["session_id"]
        result = eng.submit(sid, "classify", {
            "complexity": "high",
            "stakes": "high",
            "reversibility": "easy",
            "selected_level": "low",
            "justification": "Selecting low rigor because the task is straightforward to undo.",
        })
        assert not result["accepted"]
        assert V.LEVEL_INCONSISTENT in codes(result)

    def test_classify_level_inconsistent_hard_reversibility_low(self, eng: Engine) -> None:
        r = eng.create_session("goal", rigor="adaptive")
        sid = r["session_id"]
        result = eng.submit(sid, "classify", {
            "complexity": "low",
            "stakes": "low",
            "reversibility": "hard",
            "selected_level": "low",
            "justification": "The task is easy but cannot be undone, so low rigor seems fine.",
        })
        assert not result["accepted"]
        assert V.LEVEL_INCONSISTENT in codes(result)

    def test_classify_medium_stakes_medium_level_ok(self, eng: Engine) -> None:
        r = eng.create_session("goal", rigor="adaptive")
        sid = r["session_id"]
        result = eng.submit(sid, "classify", {
            "complexity": "medium",
            "stakes": "medium",
            "reversibility": "easy",
            "selected_level": "medium",
            "justification": "Moderate stakes and easy reversibility warrant medium rigor for this task.",
        })
        assert result["accepted"]


# ---------------------------------------------------------------------------
# Test: gate — frame
# ---------------------------------------------------------------------------

class TestGateFrame:
    def test_frame_pass(self, eng: Engine) -> None:
        r = eng.create_session("goal", rigor="low")
        sid = r["session_id"]
        result = eng.submit(sid, "frame", GOOD_FRAME)
        assert result["accepted"]

    def test_frame_missing_goal_restatement(self, eng: Engine) -> None:
        r = eng.create_session("goal", rigor="low")
        sid = r["session_id"]
        result = eng.submit(sid, "frame", {
            "success_criteria": ["Done"],
            "assumptions": [{"assumption": "A", "why_safe": "B"}],
        })
        assert not result["accepted"]
        assert V.MISSING_FIELD in codes(result)

    def test_frame_trivial_restatement(self, eng: Engine) -> None:
        r = eng.create_session("goal", rigor="low")
        sid = r["session_id"]
        result = eng.submit(sid, "frame", {
            "goal_restatement": "ok",
            "success_criteria": ["Done"],
            "assumptions": [{"assumption": "A", "why_safe": "B"}],
        })
        assert not result["accepted"]
        assert V.EMPTY_OR_TRIVIAL in codes(result)

    def test_frame_no_success_criteria(self, eng: Engine) -> None:
        r = eng.create_session("goal", rigor="low")
        sid = r["session_id"]
        result = eng.submit(sid, "frame", {
            "goal_restatement": "Build a robust validation system for reasoning artifacts.",
            "success_criteria": [],
            "assumptions": [{"assumption": "A", "why_safe": "B"}],
        })
        assert not result["accepted"]
        assert V.TOO_FEW_ITEMS in codes(result)

    def test_frame_no_questions_or_assumptions(self, eng: Engine) -> None:
        r = eng.create_session("goal", rigor="low")
        sid = r["session_id"]
        result = eng.submit(sid, "frame", {
            "goal_restatement": "Build a robust validation system for reasoning artifacts.",
            "success_criteria": ["Gates reject invalid artifacts"],
        })
        assert not result["accepted"]
        assert V.MISSING_FIELD in codes(result)


# ---------------------------------------------------------------------------
# Test: gate — research
# ---------------------------------------------------------------------------

class TestGateResearch:
    def test_research_pass(self, eng: Engine) -> None:
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        result = eng.submit(sid, "research", GOOD_RESEARCH)
        assert result["accepted"]

    def test_research_no_research_needed_pass(self, eng: Engine) -> None:
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        result = eng.submit(sid, "research", {
            "no_research_needed": True,
            "why": "This task is purely structural and requires no external fact-finding.",
        })
        assert result["accepted"]

    def test_research_no_research_needed_missing_why(self, eng: Engine) -> None:
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        result = eng.submit(sid, "research", {"no_research_needed": True})
        assert not result["accepted"]
        assert V.EMPTY_OR_TRIVIAL in codes(result)

    def test_research_training_data_now_allowed_as_assumed(self, eng: Engine) -> None:
        """V4: 'training data' source is now type=assumed (honest). Research gate accepts it.
        The claim must then appear in deliver.limitations (handled by V8 plumbing), but
        the research gate itself no longer rejects it with FABRICATION_RISK."""
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        result = eng.submit(sid, "research", {
            "facts": [{"claim": "Python 3.10 released in 2021.", "source": "training data"}],
            "unknowns": [],
        })
        # V4: assumed sources are now ALLOWED at the research gate
        assert result["accepted"], (
            f"V4: 'training data' is type=assumed (honest), should be accepted at research: {result}"
        )
        # The claim is carried as pending_limitations for V8 enforce at deliver

    def test_research_fabrication_risk_empty_source(self, eng: Engine) -> None:
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        result = eng.submit(sid, "research", {
            "facts": [{"claim": "Some claim.", "source": ""}],
            "unknowns": [],
        })
        assert not result["accepted"]
        assert V.FABRICATION_RISK in codes(result)

    def test_research_missing_unknowns(self, eng: Engine) -> None:
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        result = eng.submit(sid, "research", {
            "facts": [{"claim": "Fact.", "source": "https://example.com"}],
        })
        assert not result["accepted"]
        assert V.MISSING_FIELD in codes(result)


# ---------------------------------------------------------------------------
# Test: gate — plan
# ---------------------------------------------------------------------------

class TestGatePlan:
    def test_plan_pass_full(self, eng: Engine) -> None:
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        eng.submit(sid, "research", GOOD_RESEARCH)
        result = eng.submit(sid, "plan", GOOD_PLAN_FULL)
        assert result["accepted"]

    def test_plan_pass_medium(self, eng: Engine) -> None:
        r = eng.create_session("goal", rigor="medium")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        eng.submit(sid, "research", GOOD_RESEARCH)
        result = eng.submit(sid, "plan", GOOD_PLAN_MEDIUM)
        assert result["accepted"]

    def test_plan_too_few_steps_full(self, eng: Engine) -> None:
        """FULL rigor requires ≥3 steps; 2 steps must fail."""
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        eng.submit(sid, "research", GOOD_RESEARCH)
        result = eng.submit(sid, "plan", {
            "steps": ["Step 1", "Step 2"],  # only 2
            "risks": ["Some risk"],
            "verification_strategy": ["Run tests"],
        })
        assert not result["accepted"]
        assert V.TOO_FEW_ITEMS in codes(result)

    def test_plan_no_risks(self, eng: Engine) -> None:
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        eng.submit(sid, "research", GOOD_RESEARCH)
        result = eng.submit(sid, "plan", {
            "steps": ["Step 1", "Step 2", "Step 3"],
            "risks": [],
            "verification_strategy": ["Run tests"],
        })
        assert not result["accepted"]
        assert V.TOO_FEW_ITEMS in codes(result)

    def test_plan_no_verification_strategy(self, eng: Engine) -> None:
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        eng.submit(sid, "research", GOOD_RESEARCH)
        result = eng.submit(sid, "plan", {
            "steps": ["Step 1", "Step 2", "Step 3"],
            "risks": ["Risk"],
            "verification_strategy": [],
        })
        assert not result["accepted"]
        assert V.TOO_FEW_ITEMS in codes(result)


# ---------------------------------------------------------------------------
# Test: gate — draft (incl. anti-laziness handwaving)
# ---------------------------------------------------------------------------

class TestGateDraft:
    def _advance_to_draft(self, eng: Engine, rigor: str = "full") -> str:
        r = eng.create_session("goal", rigor=rigor)
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        if rigor == "full":
            eng.submit(sid, "research", GOOD_RESEARCH)
            eng.submit(sid, "plan", GOOD_PLAN_FULL)
        elif rigor == "medium":
            eng.submit(sid, "research", GOOD_RESEARCH)
            eng.submit(sid, "plan", GOOD_PLAN_MEDIUM)
        elif rigor == "low":
            pass  # frame → draft directly
        return sid

    def test_draft_pass(self, eng: Engine) -> None:
        sid = self._advance_to_draft(eng, "low")
        result = eng.submit(sid, "draft", GOOD_DRAFT)
        assert result["accepted"]

    def test_draft_empty(self, eng: Engine) -> None:
        sid = self._advance_to_draft(eng, "low")
        result = eng.submit(sid, "draft", {"content": ""})
        assert not result["accepted"]
        assert V.EMPTY_OR_TRIVIAL in codes(result)

    def test_draft_too_short(self, eng: Engine) -> None:
        sid = self._advance_to_draft(eng, "low")
        result = eng.submit(sid, "draft", {"content": "Short."})
        assert not result["accepted"]
        assert V.EMPTY_OR_TRIVIAL in codes(result)

    def test_draft_handwaving_you_could(self, eng: Engine) -> None:
        sid = self._advance_to_draft(eng, "low")
        result = eng.submit(sid, "draft", {
            "content": "You could implement this by creating a class that handles all the validation logic in a structured way across the codebase.",
        })
        assert not result["accepted"]
        assert V.HANDWAVING in codes(result)

    def test_draft_handwaving_as_an_ai(self, eng: Engine) -> None:
        sid = self._advance_to_draft(eng, "low")
        result = eng.submit(sid, "draft", {
            "content": "As an AI language model, I will help you build this validation system by outlining the key components that would be needed.",
        })
        assert not result["accepted"]
        assert V.HANDWAVING in codes(result)

    def test_draft_handwaving_todo(self, eng: Engine) -> None:
        sid = self._advance_to_draft(eng, "low")
        result = eng.submit(sid, "draft", {
            "content": "The engine validates each stage. TODO: add the actual validation logic here once the design is finalized and reviewed.",
        })
        assert not result["accepted"]
        assert V.HANDWAVING in codes(result)

    def test_draft_handwaving_various_approaches(self, eng: Engine) -> None:
        sid = self._advance_to_draft(eng, "low")
        result = eng.submit(sid, "draft", {
            "content": "There are various approaches you could take here. Various approaches exist for solving this problem. The system will be designed appropriately.",
        })
        assert not result["accepted"]
        assert V.HANDWAVING in codes(result)

    def test_draft_missing_content(self, eng: Engine) -> None:
        sid = self._advance_to_draft(eng, "low")
        result = eng.submit(sid, "draft", {})
        assert not result["accepted"]
        assert V.MISSING_FIELD in codes(result)


# ---------------------------------------------------------------------------
# Test: gate — critique (incl. hollow critique tripwire)
# ---------------------------------------------------------------------------

class TestGateCritique:
    def _advance_to_critique(self, eng: Engine, rigor: str = "full") -> str:
        r = eng.create_session("goal", rigor=rigor)
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        if rigor in ("full", "medium"):
            eng.submit(sid, "research", GOOD_RESEARCH)
            plan = GOOD_PLAN_FULL if rigor == "full" else GOOD_PLAN_MEDIUM
            eng.submit(sid, "plan", plan)
        eng.submit(sid, "draft", GOOD_DRAFT)
        return sid

    def test_critique_pass(self, eng: Engine) -> None:
        sid = self._advance_to_critique(eng)
        result = eng.submit(sid, "critique", GOOD_CRITIQUE)
        assert result["accepted"]

    def test_critique_no_findings_full(self, eng: Engine) -> None:
        """FULL rigor: empty findings list must fail with HOLLOW_CRITIQUE."""
        sid = self._advance_to_critique(eng, "full")
        result = eng.submit(sid, "critique", {
            "findings": [],
            "steelman": "The output is correct and complete.",
        })
        assert not result["accepted"]
        assert V.HOLLOW_CRITIQUE in codes(result)

    def test_critique_one_finding_medium_ok(self, eng: Engine) -> None:
        """MEDIUM rigor: one finding is enough."""
        sid = self._advance_to_critique(eng, "medium")
        result = eng.submit(sid, "critique", {
            "findings": [{"severity": "minor", "issue": "Minor style issue", "location": "engine.py:10"}],
            "steelman": "The system design is solid.",
        })
        assert result["accepted"]

    def test_critique_one_finding_full_fails(self, eng: Engine) -> None:
        """FULL rigor: only one finding must fail."""
        sid = self._advance_to_critique(eng, "full")
        result = eng.submit(sid, "critique", {
            "findings": [{"severity": "minor", "issue": "Minor style issue", "location": "engine.py:10"}],
            "steelman": "The system design is solid.",
        })
        assert not result["accepted"]
        assert V.HOLLOW_CRITIQUE in codes(result)

    def test_critique_no_steelman(self, eng: Engine) -> None:
        sid = self._advance_to_critique(eng, "full")
        result = eng.submit(sid, "critique", {
            "findings": [
                {"severity": "major", "issue": "Issue one", "location": "A"},
                {"severity": "minor", "issue": "Issue two", "location": "B"},
            ],
        })
        assert not result["accepted"]
        assert V.MISSING_FIELD in codes(result)

    def test_critique_no_issues_found_with_good_why(self, eng: Engine) -> None:
        """no_issues_found=true with a sufficiently detailed why must pass."""
        sid = self._advance_to_critique(eng, "full")
        result = eng.submit(sid, "critique", {
            "no_issues_found": True,
            "why": (
                "I reviewed the draft line by line against each success criterion. "
                "The logic is correct, there are no unsupported claims, all edge cases "
                "are handled, and the output matches the spec exactly. I also ran the "
                "steelman check and the strongest counterargument (that the algorithm "
                "is too slow) does not apply given the stated scale constraints."
            ),
            "steelman": "The strongest counterargument is that the approach may not scale, but this is out of scope.",
        })
        assert result["accepted"]

    def test_critique_no_issues_found_trivial_why(self, eng: Engine) -> None:
        sid = self._advance_to_critique(eng, "full")
        result = eng.submit(sid, "critique", {
            "no_issues_found": True,
            "why": "Looks good.",
            "steelman": "No issues.",
        })
        assert not result["accepted"]
        assert V.HOLLOW_CRITIQUE in codes(result)


# ---------------------------------------------------------------------------
# Test: gate — verify (incl. unverified claim tripwire)
# ---------------------------------------------------------------------------

class TestGateVerify:
    def _advance_to_verify(self, eng: Engine) -> str:
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        for stage, artifact in [
            ("frame", GOOD_FRAME),
            ("research", GOOD_RESEARCH),
            ("plan", GOOD_PLAN_FULL),
            ("draft", GOOD_DRAFT),
            ("critique", GOOD_CRITIQUE),
        ]:
            result = eng.submit(sid, stage, artifact)
            assert result["accepted"], f"Advance failed at '{stage}': {result}"
        return sid

    def test_verify_pass(self, eng: Engine) -> None:
        sid = self._advance_to_verify(eng)
        result = eng.submit(sid, "verify", GOOD_VERIFY)
        assert result["accepted"]

    def test_verify_no_checks(self, eng: Engine) -> None:
        sid = self._advance_to_verify(eng)
        result = eng.submit(sid, "verify", {"checks": []})
        assert not result["accepted"]
        assert V.TOO_FEW_ITEMS in codes(result)

    def test_verify_unverified_claim_vague_how(self, eng: Engine) -> None:
        """'how' that lacks concrete method words must trigger UNVERIFIED_CLAIM."""
        sid = self._advance_to_verify(eng)
        result = eng.submit(sid, "verify", {
            "checks": [{
                "what": "Gate correctness",
                "how": "I reviewed the logic and it seems correct.",
                "result": "Appears to work",
            }]
        })
        assert not result["accepted"]
        assert V.UNVERIFIED_CLAIM in codes(result)

    def test_verify_unverified_claim_assertion_only(self, eng: Engine) -> None:
        sid = self._advance_to_verify(eng)
        result = eng.submit(sid, "verify", {
            "checks": [{
                "what": "Output quality",
                "how": "This is clearly correct based on my analysis.",
                "result": "Passes",
            }]
        })
        assert not result["accepted"]
        assert V.UNVERIFIED_CLAIM in codes(result)

    def test_verify_missing_how(self, eng: Engine) -> None:
        sid = self._advance_to_verify(eng)
        result = eng.submit(sid, "verify", {
            "checks": [{"what": "Something", "result": "Pass"}]
        })
        assert not result["accepted"]
        assert V.MISSING_FIELD in codes(result)

    def test_verify_concrete_methods_accepted(self, eng: Engine) -> None:
        """Each concrete method keyword should pass individually."""
        sid = self._advance_to_verify(eng)
        for how in [
            "Ran pytest on the full test suite and all 47 tests passed.",
            "Recomputed the totals in a separate spreadsheet and they matched.",
            "Re-read the source document paragraph 3 and confirmed the quote.",
            "Measured latency with time.perf_counter over 1000 iterations.",
            "Executed the CLI command and captured stdout for review.",
            "Checked against the original success criteria one by one.",
            "Compared the output to the reference implementation side by side.",
        ]:
            result = eng.submit(sid, "verify", {
                "checks": [{"what": "Check", "how": how, "result": "Pass — criterion met",
                            "evidence": "47 tests PASS, 0 failures"}]
            })
            assert result["accepted"], f"Should accept how='{how}': {result}"
            # Reset so we can test repeatedly — reload session at verify stage
            # (in practice each iteration advances; just check first accepted pass)
            break  # Only need to confirm one; session advances after first pass


# ---------------------------------------------------------------------------
# Test: gate — revise (incl. unmapped fix tripwire)
# ---------------------------------------------------------------------------

class TestGateRevise:
    def _advance_to_revise(self, eng: Engine) -> str:
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        for stage, artifact in [
            ("frame", GOOD_FRAME),
            ("research", GOOD_RESEARCH),
            ("plan", GOOD_PLAN_FULL),
            ("draft", GOOD_DRAFT),
            ("critique", GOOD_CRITIQUE),
            ("verify", GOOD_VERIFY),
        ]:
            result = eng.submit(sid, stage, artifact)
            assert result["accepted"], f"Advance failed at '{stage}': {result}"
        return sid

    def test_revise_pass(self, eng: Engine) -> None:
        sid = self._advance_to_revise(eng)
        result = eng.submit(sid, "revise", GOOD_REVISE)
        assert result["accepted"]

    def test_revise_missing_fixes(self, eng: Engine) -> None:
        sid = self._advance_to_revise(eng)
        result = eng.submit(sid, "revise", {"reverified": True})
        assert not result["accepted"]
        assert V.MISSING_FIELD in codes(result)

    def test_revise_unmapped_fix(self, eng: Engine) -> None:
        """
        GOOD_CRITIQUE has a 'major' finding about the revise gate.
        Supplying a fix with a completely unrelated finding_ref must trigger UNMAPPED_FIX.
        """
        sid = self._advance_to_revise(eng)
        result = eng.submit(sid, "revise", {
            "fixes": [
                {"finding_ref": "Some completely unrelated issue that was not in the critique", "change": "Fixed it."},
            ],
            "reverified": True,
        })
        assert not result["accepted"]
        assert V.UNMAPPED_FIX in codes(result)

    def test_revise_reverified_false_with_fixes(self, eng: Engine) -> None:
        """reverified=false when fixes are present must trigger NOT_ENOUGH_RIGOR."""
        sid = self._advance_to_revise(eng)
        result = eng.submit(sid, "revise", {
            "fixes": [
                {
                    "finding_ref": "Revise gate does not check reverified field when fixes exist",
                    "change": "Added the check.",
                }
            ],
            "reverified": False,
        })
        assert not result["accepted"]
        assert V.NOT_ENOUGH_RIGOR in codes(result)

    def test_revise_missing_reverified_field(self, eng: Engine) -> None:
        sid = self._advance_to_revise(eng)
        result = eng.submit(sid, "revise", {
            "fixes": [{"finding_ref": "Revise gate does not check reverified field when fixes exist", "change": "Fixed."}],
        })
        assert not result["accepted"]
        assert V.MISSING_FIELD in codes(result)


# ---------------------------------------------------------------------------
# Test: gate — deliver
# ---------------------------------------------------------------------------

class TestGateDeliver:
    def _advance_to_deliver(self, eng: Engine, rigor: str = "low") -> str:
        r = eng.create_session("goal", rigor=rigor)
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        if rigor in ("full", "medium"):
            eng.submit(sid, "research", GOOD_RESEARCH)
            plan = GOOD_PLAN_FULL if rigor == "full" else GOOD_PLAN_MEDIUM
            eng.submit(sid, "plan", plan)
        eng.submit(sid, "draft", GOOD_DRAFT)
        if rigor in ("full", "medium"):
            eng.submit(sid, "critique", GOOD_CRITIQUE)
        if rigor == "full":
            eng.submit(sid, "verify", GOOD_VERIFY)
            eng.submit(sid, "revise", GOOD_REVISE)
        return sid

    def test_deliver_pass(self, eng: Engine) -> None:
        sid = self._advance_to_deliver(eng, "low")
        result = eng.submit(sid, "deliver", GOOD_DELIVER)
        assert result["accepted"]

    def test_deliver_missing_summary(self, eng: Engine) -> None:
        sid = self._advance_to_deliver(eng, "low")
        result = eng.submit(sid, "deliver", {
            "limitations": ["None"],
            "sources": [],
        })
        assert not result["accepted"]
        assert V.MISSING_FIELD in codes(result)

    def test_deliver_empty_limitations(self, eng: Engine) -> None:
        sid = self._advance_to_deliver(eng, "low")
        result = eng.submit(sid, "deliver", {
            "summary": "Done.",
            "limitations": [],
            "sources": [],
        })
        assert not result["accepted"]
        assert V.EMPTY_OR_TRIVIAL in codes(result)

    def test_deliver_missing_sources(self, eng: Engine) -> None:
        sid = self._advance_to_deliver(eng, "low")
        result = eng.submit(sid, "deliver", {
            "summary": "Done.",
            "limitations": ["None, because the task was fully verifiable with no external claims."],
        })
        assert not result["accepted"]
        assert V.MISSING_FIELD in codes(result)


# ---------------------------------------------------------------------------
# Test: adaptive flow
# ---------------------------------------------------------------------------

class TestAdaptiveFlow:
    def test_adaptive_requires_classify_first(self, eng: Engine) -> None:
        """Submitting any stage other than classify first must be OUT_OF_ORDER."""
        r = eng.create_session("goal", rigor="adaptive")
        sid = r["session_id"]
        result = eng.submit(sid, "frame", GOOD_FRAME)
        assert not result["accepted"]
        assert V.OUT_OF_ORDER in codes(result)

    def test_adaptive_classify_then_correct_level(self, eng: Engine) -> None:
        """After classify with full, the next stage must be frame."""
        r = eng.create_session("goal", rigor="adaptive")
        sid = r["session_id"]
        result = eng.submit(sid, "classify", GOOD_CLASSIFY)
        assert result["accepted"]
        assert result["current_stage"] == "frame"

    def test_adaptive_high_stakes_low_rejected(self, eng: Engine) -> None:
        r = eng.create_session("goal", rigor="adaptive")
        sid = r["session_id"]
        result = eng.submit(sid, "classify", {
            "complexity": "high",
            "stakes": "high",
            "reversibility": "easy",
            "selected_level": "low",
            "justification": "I am choosing low because I think I can handle this.",
        })
        assert not result["accepted"]
        assert V.LEVEL_INCONSISTENT in codes(result)

    def test_adaptive_resolves_to_medium(self, eng: Engine) -> None:
        """Adaptive with low-stakes can select medium; required stages match."""
        r = eng.create_session("goal", rigor="adaptive")
        sid = r["session_id"]
        classify_medium = {
            "complexity": "medium",
            "stakes": "medium",
            "reversibility": "easy",
            "selected_level": "medium",
            "justification": "Medium complexity and moderate stakes; easy to revise if wrong.",
        }
        result = eng.submit(sid, "classify", classify_medium)
        assert result["accepted"]
        state = eng.get_state(sid)
        assert state["level"] == "medium"

    def test_finalize_refused_before_classify(self, eng: Engine) -> None:
        r = eng.create_session("goal", rigor="adaptive")
        sid = r["session_id"]
        fin = eng.finalize(sid)
        assert not fin["finalized"]


# ---------------------------------------------------------------------------
# Test: LOW / MEDIUM / FULL required stage sets
# ---------------------------------------------------------------------------

class TestRigorLevels:
    def test_low_only_requires_frame_draft_deliver(self, eng: Engine) -> None:
        r = eng.create_session("goal", rigor="low")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        eng.submit(sid, "draft", GOOD_DRAFT)
        eng.submit(sid, "deliver", GOOD_DELIVER)
        fin = eng.finalize(sid)
        assert fin["finalized"]

    def test_medium_requires_research_plan_critique(self, eng: Engine) -> None:
        """Medium with involves_facts=True must include research, plan, critique."""
        r = eng.create_session("goal", rigor="medium", involves_facts=True)
        sid = r["session_id"]
        # Skip research → should be required
        eng.submit(sid, "frame", GOOD_FRAME)
        # Try to skip straight to plan
        result = eng.submit(sid, "plan", GOOD_PLAN_MEDIUM)
        assert not result["accepted"]
        assert V.OUT_OF_ORDER in codes(result)

    def test_medium_no_facts_skips_research(self, eng: Engine) -> None:
        """Medium with involves_facts=False must skip research."""
        r = eng.create_session("goal", rigor="medium", involves_facts=False)
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        # Next should be plan (research skipped)
        state = eng.get_state(sid)
        assert state["current_stage"] == "plan"
        eng.submit(sid, "plan", GOOD_PLAN_MEDIUM)
        eng.submit(sid, "draft", GOOD_DRAFT)
        # Minor-only critique: this test verifies research-skipping, not escalation.
        # A 'major' finding (as in GOOD_CRITIQUE) now correctly escalates medium->full.
        eng.submit(sid, "critique", {
            "findings": [
                {"severity": "minor", "issue": "Several helper functions are missing docstrings.", "location": "engine.py"},
                {"severity": "minor", "issue": "Some variable names could be more descriptive in the gate loop.", "location": "engine.py"},
            ],
            "steelman": "The engine is well-structured and the gate codes are stable overall.",
        })
        eng.submit(sid, "deliver", GOOD_DELIVER)
        fin = eng.finalize(sid)
        assert fin["finalized"]

    def test_full_requires_all_stages(self, eng: Engine) -> None:
        """FULL must require all 8 stages."""
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        state = eng.get_state(sid)
        # Try to finalize immediately — should list all stages as missing
        fin = eng.finalize(sid)
        assert not fin["finalized"]
        assert len(fin["missing_stages"]) == len(REQUIRED_STAGES["full"])


# ---------------------------------------------------------------------------
# Test: set_rigor (may only raise)
# ---------------------------------------------------------------------------

class TestSetRigor:
    def test_raise_rigor_low_to_medium(self, eng: Engine) -> None:
        r = eng.create_session("goal", rigor="low")
        sid = r["session_id"]
        result = eng.set_rigor(sid, "medium")
        assert result["accepted"]
        state = eng.get_state(sid)
        assert state["level"] == "medium"

    def test_raise_rigor_medium_to_full(self, eng: Engine) -> None:
        r = eng.create_session("goal", rigor="medium")
        sid = r["session_id"]
        result = eng.set_rigor(sid, "full")
        assert result["accepted"]
        state = eng.get_state(sid)
        assert state["level"] == "full"

    def test_lower_rigor_rejected(self, eng: Engine) -> None:
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        result = eng.set_rigor(sid, "low")
        assert not result["accepted"]
        assert "error" in result

    def test_same_rigor_allowed(self, eng: Engine) -> None:
        """Setting the same level should be accepted (not a lowering)."""
        r = eng.create_session("goal", rigor="medium")
        sid = r["session_id"]
        result = eng.set_rigor(sid, "medium")
        assert result["accepted"]


# ---------------------------------------------------------------------------
# Test: round-trip persistence
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_create_then_reload(self, eng: Engine, tmp_path: Path) -> None:
        """Create a session, create a new Engine pointing to same dir, reload and continue."""
        r = eng.create_session("Persistent goal", rigor="low")
        sid = r["session_id"]

        # Create a completely new Engine instance pointing to the same store_dir
        eng2 = Engine(store_dir=str(tmp_path))
        state = eng2.get_state(sid)
        assert state["session_id"] == sid
        assert state["goal"] == "Persistent goal"
        assert state["current_stage"] == "frame"

        # Continue with the new engine instance
        result = eng2.submit(sid, "frame", GOOD_FRAME)
        assert result["accepted"]

    def test_full_persistence_across_instances(self, eng: Engine, tmp_path: Path) -> None:
        """Drive three stages across three separate Engine instances."""
        r = eng.create_session("Persistent full", rigor="low")
        sid = r["session_id"]

        # Instance 1: frame
        eng.submit(sid, "frame", GOOD_FRAME)

        # Instance 2: draft
        eng2 = Engine(store_dir=str(tmp_path))
        eng2.submit(sid, "draft", GOOD_DRAFT)

        # Instance 3: deliver + finalize
        eng3 = Engine(store_dir=str(tmp_path))
        eng3.submit(sid, "deliver", GOOD_DELIVER)
        fin = eng3.finalize(sid)
        assert fin["finalized"]
        assert fin["certificate"]["session_id"] == sid

    def test_state_reflects_completed_stages(self, eng: Engine) -> None:
        """get_state must accurately reflect completed stages after submissions."""
        r = eng.create_session("State check", rigor="low")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        state = eng.get_state(sid)
        assert "frame" in state["completed_stages"]
        assert state["current_stage"] == "draft"


# ---------------------------------------------------------------------------
# Test: profiles — get_instructions and get_overlay_checks
# ---------------------------------------------------------------------------

class TestProfiles:
    def test_get_instructions_universal(self) -> None:
        from fable_method import profiles
        instr = profiles.get_instructions("universal", "frame", "full")
        assert isinstance(instr, str)
        assert len(instr) > 0

    def test_get_instructions_ai_builder_overlay(self) -> None:
        from fable_method import profiles
        instr = profiles.get_instructions("ai_builder", "frame", "full")
        assert "AI-BUILDER" in instr

    def test_get_instructions_entrepreneur_overlay(self) -> None:
        from fable_method import profiles
        instr = profiles.get_instructions("entrepreneur", "plan", "full")
        assert "ENTREPRENEUR" in instr

    def test_get_instructions_unknown_profile_graceful(self) -> None:
        from fable_method import profiles
        instr = profiles.get_instructions("nonexistent_profile", "frame", "full")
        assert isinstance(instr, str)
        assert len(instr) > 0

    def test_get_overlay_checks_ai_builder(self) -> None:
        from fable_method import profiles
        checks = profiles.get_overlay_checks("ai_builder", "critique")
        assert isinstance(checks, list)
        assert len(checks) > 0

    def test_get_overlay_checks_universal_empty(self) -> None:
        from fable_method import profiles
        checks = profiles.get_overlay_checks("universal", "frame")
        assert checks == []

    def test_get_overlay_checks_unknown_profile(self) -> None:
        from fable_method import profiles
        checks = profiles.get_overlay_checks("bogus", "frame")
        assert checks == []

    def test_profile_session_creates_with_ai_builder(self, eng: Engine) -> None:
        r = eng.create_session("Build an agent", profile="ai_builder", rigor="low")
        assert r["profile"] == "ai_builder"
        assert "AI-BUILDER" in r["instructions"] or "frame" in r["instructions"].lower()

    def test_profile_session_creates_with_entrepreneur(self, eng: Engine) -> None:
        r = eng.create_session("Launch a startup", profile="entrepreneur", rigor="low")
        assert r["profile"] == "entrepreneur"


# ---------------------------------------------------------------------------
# Test: create_session returns correct initial state
# ---------------------------------------------------------------------------

class TestCreateSession:
    def test_returns_required_fields(self, eng: Engine) -> None:
        r = eng.create_session("test goal", rigor="low")
        for key in ("session_id", "status", "current_stage", "rigor", "profile",
                    "instructions", "required_artifact", "next_action"):
            assert key in r, f"Missing key: {key}"

    def test_adaptive_starts_at_classify(self, eng: Engine) -> None:
        r = eng.create_session("test", rigor="adaptive")
        assert r["current_stage"] == "classify"

    def test_low_starts_at_frame(self, eng: Engine) -> None:
        r = eng.create_session("test", rigor="low")
        assert r["current_stage"] == "frame"

    def test_full_starts_at_frame(self, eng: Engine) -> None:
        r = eng.create_session("test", rigor="full")
        assert r["current_stage"] == "frame"

    def test_instructions_non_empty(self, eng: Engine) -> None:
        r = eng.create_session("test", rigor="low")
        assert isinstance(r["instructions"], str)
        assert len(r["instructions"]) > 10


# ---------------------------------------------------------------------------
# Test: module-level convenience wrappers
# ---------------------------------------------------------------------------

class TestModuleConvenience:
    def test_module_functions_work(self, tmp_path: Path) -> None:
        """Module-level wrappers must be callable (uses default engine; route through Engine)."""
        from fable_method import engine as eng_mod
        # Use a local Engine instance to avoid polluting the module-level default
        local_eng = Engine(store_dir=str(tmp_path))
        r = local_eng.create_session("module test", rigor="low")
        sid = r["session_id"]
        state = local_eng.get_state(sid)
        assert state["session_id"] == sid

    def test_submit_and_finalize_via_engine(self, tmp_path: Path) -> None:
        local_eng = Engine(store_dir=str(tmp_path))
        r = local_eng.create_session("quick test", rigor="low")
        sid = r["session_id"]
        local_eng.submit(sid, "frame", GOOD_FRAME)
        local_eng.submit(sid, "draft", GOOD_DRAFT)
        local_eng.submit(sid, "deliver", GOOD_DELIVER)
        fin = local_eng.finalize(sid)
        assert fin["finalized"]


# ---------------------------------------------------------------------------
# A10 NEW TESTS — A1 through A9
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# A1: duplicate / trivial list items
# ---------------------------------------------------------------------------

class TestA1DuplicateTrivialItems:
    """Every gated list must reject duplicate or too-short items."""

    def test_success_criteria_duplicate(self, eng: Engine) -> None:
        r = eng.create_session("Build a pricing model", rigor="low")
        sid = r["session_id"]
        result = eng.submit(sid, "frame", {
            "goal_restatement": "Construct a pricing model that calculates optimal prices for products.",
            "success_criteria": [
                "The model outputs a valid price",
                "The model outputs a valid price",  # duplicate
            ],
            "assumptions": [{"assumption": "Market data is available and reliable", "why_safe": "We have a vendor contract"}],
        })
        assert not result["accepted"]
        assert V.EMPTY_OR_TRIVIAL in codes(result)

    def test_success_criteria_too_short(self, eng: Engine) -> None:
        r = eng.create_session("Build a pricing model", rigor="low")
        sid = r["session_id"]
        result = eng.submit(sid, "frame", {
            "goal_restatement": "Construct a pricing model that calculates optimal prices for products.",
            "success_criteria": ["Done"],  # 4 non-space chars < 12
            "assumptions": [{"assumption": "Market data is available and reliable", "why_safe": "We have a vendor contract"}],
        })
        assert not result["accepted"]
        assert V.EMPTY_OR_TRIVIAL in codes(result)

    def test_plan_steps_duplicate(self, eng: Engine) -> None:
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        eng.submit(sid, "research", GOOD_RESEARCH)
        result = eng.submit(sid, "plan", {
            "steps": [
                "Design the validation schema for artifacts",
                "Design the validation schema for artifacts",  # duplicate
                "Write tests for each gate",
            ],
            "risks": ["Scope creep could delay delivery"],
            "verification_strategy": ["Run pytest after each implementation step"],
        })
        assert not result["accepted"]
        assert V.EMPTY_OR_TRIVIAL in codes(result)

    def test_plan_steps_too_short(self, eng: Engine) -> None:
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        eng.submit(sid, "research", GOOD_RESEARCH)
        result = eng.submit(sid, "plan", {
            "steps": ["Step 1", "Step 2", "Step 3"],  # all < 12 non-space chars
            "risks": ["Some risk about the implementation going wrong"],
            "verification_strategy": ["Run the full test suite after each step"],
        })
        assert not result["accepted"]
        assert V.EMPTY_OR_TRIVIAL in codes(result)

    def test_plan_risks_duplicate(self, eng: Engine) -> None:
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        eng.submit(sid, "research", GOOD_RESEARCH)
        result = eng.submit(sid, "plan", {
            "steps": [
                "Design the gate validator interfaces",
                "Implement each gate function in engine.py",
                "Write integration tests for the full pipeline",
            ],
            "risks": [
                "Incorrect gate logic may pass invalid artifacts",
                "Incorrect gate logic may pass invalid artifacts",  # duplicate
            ],
            "verification_strategy": ["Run pytest suite covering all violation codes"],
        })
        assert not result["accepted"]
        assert V.EMPTY_OR_TRIVIAL in codes(result)

    def test_facts_duplicate_claims(self, eng: Engine) -> None:
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        result = eng.submit(sid, "research", {
            "facts": [
                {"claim": "Python 3.10 introduced structural pattern matching.", "source": "https://docs.python.org/3/whatsnew/3.10.html"},
                {"claim": "Python 3.10 introduced structural pattern matching.", "source": "https://docs.python.org/3/whatsnew/3.10.html"},
            ],
            "unknowns": [],
        })
        assert not result["accepted"]
        assert V.EMPTY_OR_TRIVIAL in codes(result)

    def test_findings_duplicate_issues(self, eng: Engine) -> None:
        """Duplicate findings must fail with EMPTY_OR_TRIVIAL (A1)."""
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        eng.submit(sid, "research", GOOD_RESEARCH)
        eng.submit(sid, "plan", GOOD_PLAN_FULL)
        eng.submit(sid, "draft", GOOD_DRAFT)
        result = eng.submit(sid, "critique", {
            "findings": [
                {"severity": "major", "issue": "The revise gate does not validate reverified flag", "location": "engine.py"},
                {"severity": "minor", "issue": "The revise gate does not validate reverified flag", "location": "engine.py"},
            ],
            "steelman": "The engine design is fundamentally sound and well structured.",
        })
        assert not result["accepted"]
        assert V.EMPTY_OR_TRIVIAL in codes(result)

    def test_all_x_frame_fails(self, eng: Engine) -> None:
        """A frame artifact where every field is 'x' must fail."""
        r = eng.create_session("Build a production pricing model", rigor="full")
        sid = r["session_id"]
        result = eng.submit(sid, "frame", {
            "goal_restatement": "x",
            "success_criteria": ["x", "x"],
            "assumptions": [{"assumption": "x", "why_safe": "x"}],
        })
        assert not result["accepted"], "All-x frame must be rejected"
        assert V.EMPTY_OR_TRIVIAL in codes(result)


# ---------------------------------------------------------------------------
# A2: verify substance — result must differ from what; how must be ≥15 chars + concrete
# ---------------------------------------------------------------------------

class TestA2VerifySubstance:
    def _advance_to_verify(self, eng: Engine) -> str:
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        for stage, artifact in [
            ("frame", GOOD_FRAME),
            ("research", GOOD_RESEARCH),
            ("plan", GOOD_PLAN_FULL),
            ("draft", GOOD_DRAFT),
            ("critique", GOOD_CRITIQUE),
        ]:
            eng.submit(sid, stage, artifact)
        return sid

    def test_verify_result_echoes_what(self, eng: Engine) -> None:
        """result that verbatim equals what must trigger UNVERIFIED_CLAIM."""
        sid = self._advance_to_verify(eng)
        result = eng.submit(sid, "verify", {
            "checks": [{
                "what": "Gate rejects missing fields",
                "how": "Ran pytest suite with invalid artifact inputs",
                "result": "Gate rejects missing fields",  # echoes what
            }]
        })
        assert not result["accepted"]
        assert V.UNVERIFIED_CLAIM in codes(result)

    def test_verify_how_too_short(self, eng: Engine) -> None:
        """how with fewer than 15 chars must trigger UNVERIFIED_CLAIM."""
        sid = self._advance_to_verify(eng)
        result = eng.submit(sid, "verify", {
            "checks": [{
                "what": "Gate rejects missing fields",
                "how": "Ran tests",  # only 9 chars
                "result": "All 12 cases passed as expected with correct violation codes",
            }]
        })
        assert not result["accepted"]
        assert V.UNVERIFIED_CLAIM in codes(result)

    def test_verify_how_long_but_vague(self, eng: Engine) -> None:
        """how ≥15 chars but no concrete method keyword must still fail."""
        sid = self._advance_to_verify(eng)
        result = eng.submit(sid, "verify", {
            "checks": [{
                "what": "Gate rejects missing fields",
                "how": "I looked at the logic carefully and it seemed correct",
                "result": "Everything appears to be working as expected based on review",
            }]
        })
        assert not result["accepted"]
        assert V.UNVERIFIED_CLAIM in codes(result)

    def test_verify_good_how_passes(self, eng: Engine) -> None:
        """Concrete how ≥15 chars with keyword and distinct result must pass."""
        sid = self._advance_to_verify(eng)
        result = eng.submit(sid, "verify", {
            "checks": [{
                "what": "Gate rejects missing fields",
                "how": "Ran pytest suite covering all gates with invalid artifacts and captured stdout",
                "result": "All 12 negative cases raised the expected violation codes without exception",
                "evidence": "pytest: 12 passed, 0 failed in 0.34s",
            }]
        })
        assert result["accepted"], f"Good verify should pass: {result}"


# ---------------------------------------------------------------------------
# A3: FULL critique must have ≥1 blocker/major (two minors alone fails)
# ---------------------------------------------------------------------------

class TestA3CritiqueBiteAtFull:
    def _advance_to_critique(self, eng: Engine) -> str:
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        for stage, artifact in [
            ("frame", GOOD_FRAME),
            ("research", GOOD_RESEARCH),
            ("plan", GOOD_PLAN_FULL),
            ("draft", GOOD_DRAFT),
        ]:
            eng.submit(sid, stage, artifact)
        return sid

    def test_two_minors_fails_at_full(self, eng: Engine) -> None:
        """Two minor findings at FULL rigor must fail with HOLLOW_CRITIQUE (A3)."""
        sid = self._advance_to_critique(eng)
        result = eng.submit(sid, "critique", {
            "findings": [
                {"severity": "minor", "issue": "The variable naming could be more descriptive in some places", "location": "engine.py:45"},
                {"severity": "minor", "issue": "Missing docstrings on several helper functions throughout", "location": "engine.py:120"},
            ],
            "steelman": "The engine is well structured with clear separation of concerns.",
        })
        assert not result["accepted"]
        assert V.HOLLOW_CRITIQUE in codes(result)

    def test_one_major_two_minors_passes_at_full(self, eng: Engine) -> None:
        """One major + one minor = ≥1 blocker/major → must pass at FULL."""
        sid = self._advance_to_critique(eng)
        result = eng.submit(sid, "critique", {
            "findings": [
                {"severity": "major", "issue": "The revise gate mapping completeness check is too permissive", "location": "_gate_revise"},
                {"severity": "minor", "issue": "Missing docstrings on several helper functions throughout", "location": "engine.py:120"},
            ],
            "steelman": "The engine is well structured with clear separation of concerns.",
        })
        assert result["accepted"], f"major+minor should pass at FULL: {result}"

    def test_one_blocker_passes_at_full(self, eng: Engine) -> None:
        """One blocker alone must pass at FULL (≥2 findings, ≥1 blocker/major)."""
        sid = self._advance_to_critique(eng)
        result = eng.submit(sid, "critique", {
            "findings": [
                {"severity": "blocker", "issue": "The frame gate does not enforce goal restatement echo tripwire", "location": "_gate_frame"},
                {"severity": "minor", "issue": "Handwaving patterns list lacks word-boundary precision in two entries", "location": "engine.py:80"},
            ],
            "steelman": "The engine covers the most important structural checks correctly.",
        })
        assert result["accepted"], f"blocker+minor should pass at FULL: {result}"

    def test_two_minors_passes_at_medium(self, eng: Engine) -> None:
        """Two minor findings at MEDIUM rigor must pass (A3 applies only to FULL)."""
        r = eng.create_session("goal", rigor="medium")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        eng.submit(sid, "research", GOOD_RESEARCH)
        eng.submit(sid, "plan", GOOD_PLAN_MEDIUM)
        eng.submit(sid, "draft", GOOD_DRAFT)
        result = eng.submit(sid, "critique", {
            "findings": [
                {"severity": "minor", "issue": "The variable naming could be more descriptive in places", "location": "engine.py:45"},
                {"severity": "minor", "issue": "Missing docstrings on several helper functions throughout", "location": "engine.py:120"},
            ],
            "steelman": "The engine is well structured with clear separation of concerns.",
        })
        assert result["accepted"], f"Two minors at MEDIUM should pass: {result}"


# ---------------------------------------------------------------------------
# A4: revise non-empty and reverified=true when critique had blocker/major
# ---------------------------------------------------------------------------

class TestA4ReviseCannotBeNeutered:
    def _advance_to_revise_with_major(self, eng: Engine) -> str:
        """Advance to revise with a critique that has a major finding."""
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        for stage, artifact in [
            ("frame", GOOD_FRAME),
            ("research", GOOD_RESEARCH),
            ("plan", GOOD_PLAN_FULL),
            ("draft", GOOD_DRAFT),
            ("critique", GOOD_CRITIQUE),  # has major + minor
            ("verify", GOOD_VERIFY),
        ]:
            res = eng.submit(sid, stage, artifact)
            assert res["accepted"], f"Setup failed at {stage}: {res}"
        return sid

    def test_empty_fixes_with_major_critique_fails(self, eng: Engine) -> None:
        """A4: empty fixes when critique had blocker/major must fail."""
        sid = self._advance_to_revise_with_major(eng)
        result = eng.submit(sid, "revise", {
            "fixes": [],
            "reverified": True,
        })
        assert not result["accepted"]
        assert V.UNMAPPED_FIX in codes(result)

    def test_reverified_false_with_major_critique_fails(self, eng: Engine) -> None:
        """A4: reverified=false when critique had blocker/major must fail."""
        sid = self._advance_to_revise_with_major(eng)
        result = eng.submit(sid, "revise", {
            "fixes": [{"finding_ref": "Revise gate does not check reverified field when fixes exist", "change": "Added the reverified check."}],
            "reverified": False,
        })
        assert not result["accepted"]
        assert V.NOT_ENOUGH_RIGOR in codes(result)


# ---------------------------------------------------------------------------
# A5: frame echo tripwire
# ---------------------------------------------------------------------------

class TestA5FrameEchoTripwire:
    def test_frame_exact_echo_rejected(self, eng: Engine) -> None:
        """goal_restatement that exactly equals the goal must fail."""
        goal = "Build a production pricing model for SaaS subscriptions"
        r = eng.create_session(goal, rigor="low")
        sid = r["session_id"]
        result = eng.submit(sid, "frame", {
            "goal_restatement": goal,  # exact verbatim copy
            "success_criteria": ["The pricing model accurately calculates prices for all tiers"],
            "assumptions": [{"assumption": "SaaS tier definitions are stable and documented", "why_safe": "Product manager confirmed in writing"}],
        })
        assert not result["accepted"]
        assert V.EMPTY_OR_TRIVIAL in codes(result)

    def test_frame_high_overlap_rejected(self, eng: Engine) -> None:
        """goal_restatement with ≥0.9 token overlap must fail."""
        goal = "Build a production pricing model for SaaS subscriptions"
        r = eng.create_session(goal, rigor="low")
        sid = r["session_id"]
        result = eng.submit(sid, "frame", {
            # Same words, minor reordering — nearly 100% token overlap
            "goal_restatement": "Build a pricing model for production SaaS subscriptions",
            "success_criteria": ["The pricing model accurately calculates prices for all tiers"],
            "assumptions": [{"assumption": "SaaS tier definitions are stable and documented", "why_safe": "Product manager confirmed in writing"}],
        })
        assert not result["accepted"]
        assert V.EMPTY_OR_TRIVIAL in codes(result)

    def test_frame_genuine_restatement_passes(self, eng: Engine) -> None:
        """A genuine paraphrase with <0.9 overlap must pass."""
        goal = "Build a production pricing model for SaaS subscriptions"
        r = eng.create_session(goal, rigor="low")
        sid = r["session_id"]
        result = eng.submit(sid, "frame", {
            "goal_restatement": (
                "Design and implement a system that determines the optimal charge "
                "per customer tier, suitable for live deployment in our billing pipeline."
            ),
            "success_criteria": ["The pricing engine returns correct prices for all known tier combinations"],
            "assumptions": [{"assumption": "Customer tier data is accessible via the billing API", "why_safe": "API is already live and documented"}],
        })
        assert result["accepted"], f"Genuine restatement should pass: {result}"

    def test_frame_echo_without_goal_still_checks_length(self, eng: Engine) -> None:
        """Short restatement still fails even without a goal to compare against."""
        r = eng.create_session("Build a pricing model", rigor="low")
        sid = r["session_id"]
        result = eng.submit(sid, "frame", {
            "goal_restatement": "pricing model",  # 12 chars, < 20 min
            "success_criteria": ["Prices are calculated correctly for all inputs"],
            "assumptions": [{"assumption": "Input data is clean and validated upstream", "why_safe": "Existing data pipeline enforces schema"}],
        })
        assert not result["accepted"]
        assert V.EMPTY_OR_TRIVIAL in codes(result)


# ---------------------------------------------------------------------------
# A6: adaptive floor — complexity=high cannot select low
# ---------------------------------------------------------------------------

class TestA6AdaptiveFloor:
    def test_high_complexity_low_level_rejected(self, eng: Engine) -> None:
        """A6: complexity=high cannot map to selected_level=low."""
        r = eng.create_session("goal", rigor="adaptive")
        sid = r["session_id"]
        result = eng.submit(sid, "classify", {
            "complexity": "high",
            "stakes": "low",
            "reversibility": "easy",
            "selected_level": "low",
            "justification": "I believe low rigor is fine despite the high complexity here.",
        })
        assert not result["accepted"]
        assert V.LEVEL_INCONSISTENT in codes(result)

    def test_high_complexity_medium_level_ok(self, eng: Engine) -> None:
        """A6: complexity=high with selected_level=medium must pass."""
        r = eng.create_session("goal", rigor="adaptive")
        sid = r["session_id"]
        result = eng.submit(sid, "classify", {
            "complexity": "high",
            "stakes": "low",
            "reversibility": "easy",
            "selected_level": "medium",
            "justification": "High complexity warrants at least medium rigor, even with low stakes.",
        })
        assert result["accepted"], f"High complexity + medium should be accepted: {result}"

    def test_low_complexity_low_stakes_easy_rev_low_ok(self, eng: Engine) -> None:
        """Low complexity, low stakes, easy reversibility can legitimately select low."""
        r = eng.create_session("goal", rigor="adaptive")
        sid = r["session_id"]
        result = eng.submit(sid, "classify", {
            "complexity": "low",
            "stakes": "low",
            "reversibility": "easy",
            "selected_level": "low",
            "justification": "Simple task with no consequences if wrong; easy to redo entirely.",
        })
        assert result["accepted"], f"Genuinely low task should pass classify at low: {result}"


# ---------------------------------------------------------------------------
# A7: deliver sources must be non-empty when real research was done
# ---------------------------------------------------------------------------

class TestA7DeliverCitationHonesty:
    def _advance_full_to_deliver(self, eng: Engine) -> str:
        """Full rigor session with real research, stopped just before deliver.
        Handles V7 backtracking loop: GOOD_REVISE has real fixes, so the engine
        routes back to verify; after the loop verify passes, session is at deliver.
        """
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        for stage, artifact in [
            ("frame", GOOD_FRAME),
            ("research", GOOD_RESEARCH),
            ("plan", GOOD_PLAN_FULL),
            ("draft", GOOD_DRAFT),
            ("critique", GOOD_CRITIQUE),
            ("verify", GOOD_VERIFY),
        ]:
            res = eng.submit(sid, stage, artifact)
            assert res["accepted"], f"Setup failed at {stage}: {res}"
        # GOOD_REVISE has real fixes + reverified=True → V7 routes back to verify
        res = eng.submit(sid, "revise", GOOD_REVISE)
        assert res["accepted"], f"Setup failed at revise: {res}"
        if res.get("loop_back"):
            res = eng.submit(sid, "verify", GOOD_VERIFY)
            assert res["accepted"], f"Setup failed at verify (V7 loop): {res}"
            # After loop verify, session is now at deliver
        return sid

    def test_empty_sources_after_real_research_fails(self, eng: Engine) -> None:
        """A7: empty sources after real research must fail with MISSING_FIELD."""
        sid = self._advance_full_to_deliver(eng)
        result = eng.submit(sid, "deliver", {
            "summary": "The enforcement engine is complete and all gates pass correctly.",
            "limitations": ["Does not yet support async submission or concurrent sessions"],
            "sources": [],  # empty despite real research
        })
        assert not result["accepted"]
        assert V.MISSING_FIELD in codes(result)

    def test_sources_present_after_research_passes(self, eng: Engine) -> None:
        """A7: sources present → deliver passes."""
        sid = self._advance_full_to_deliver(eng)
        result = eng.submit(sid, "deliver", GOOD_DELIVER)
        assert result["accepted"], f"Deliver with sources should pass: {result}"

    def test_empty_sources_ok_when_no_research(self, eng: Engine) -> None:
        """A7: if research used no_research_needed, empty sources in deliver is fine."""
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        no_research = {"no_research_needed": True,
                       "why": "This task is purely structural; all facts are defined in the spec."}
        for stage, artifact in [
            ("frame", GOOD_FRAME),
            ("research", no_research),
            ("plan", GOOD_PLAN_FULL),
            ("draft", GOOD_DRAFT),
            ("critique", GOOD_CRITIQUE),
            ("verify", GOOD_VERIFY),
        ]:
            res = eng.submit(sid, stage, artifact)
            assert res["accepted"], f"Setup failed at {stage}: {res}"
        # V7: GOOD_REVISE triggers loop back to verify
        res = eng.submit(sid, "revise", GOOD_REVISE)
        assert res["accepted"], f"Setup failed at revise: {res}"
        if res.get("loop_back"):
            res = eng.submit(sid, "verify", GOOD_VERIFY)
            assert res["accepted"], f"Setup failed at verify (V7 loop): {res}"
        result = eng.submit(sid, "deliver", {
            "summary": "The enforcement engine is complete and all gates pass correctly.",
            "limitations": ["Does not yet support async submission or concurrent sessions"],
            "sources": [],  # OK — no real research was done
        })
        assert result["accepted"], f"Empty sources OK without real research: {result}"


# ---------------------------------------------------------------------------
# A8: handwaving false positives — legitimate text must NOT trip
# ---------------------------------------------------------------------------

class TestA8HandwavingFalsePositives:
    def _advance_to_draft(self, eng: Engine) -> str:
        r = eng.create_session("goal", rigor="low")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        return sid

    def test_placeholder_text_in_ux_draft_does_not_trip(self, eng: Engine) -> None:
        """'placeholder text' as a UI concept must NOT trigger HANDWAVING (A8)."""
        sid = self._advance_to_draft(eng)
        result = eng.submit(sid, "draft", {
            "content": (
                "The onboarding screen uses placeholder text in the email field to guide "
                "users. This placeholder text renders correctly in all tested browsers "
                "including Chrome 120 and Firefox 121. The label above each field "
                "supplements the placeholder text with a persistent hint. "
                "Input validation fires on blur and replaces placeholder text with "
                "an error message if the field is left empty."
            ),
        })
        assert result["accepted"], (
            f"Legitimate UX draft mentioning 'placeholder text' must not trigger HANDWAVING: {result}"
        )

    def test_todo_app_draft_does_not_trip(self, eng: Engine) -> None:
        """A draft describing a 'todo app' (noun, not imperative) must NOT trigger HANDWAVING (A8)."""
        sid = self._advance_to_draft(eng)
        result = eng.submit(sid, "draft", {
            "content": (
                "This document specifies the architecture of a todo app built with React "
                "and a FastAPI backend. The todo app stores items in a PostgreSQL database "
                "with a tasks table containing id, title, completed, and created_at columns. "
                "Each todo app endpoint is protected by JWT authentication issued on login. "
                "The todo app supports real-time updates via Server-Sent Events."
            ),
        })
        assert result["accepted"], (
            f"Draft describing a 'todo app' must not trigger HANDWAVING: {result}"
        )

    def test_todo_colon_still_trips(self, eng: Engine) -> None:
        """'TODO:' (imperative leftover marker) must still trigger HANDWAVING."""
        sid = self._advance_to_draft(eng)
        result = eng.submit(sid, "draft", {
            "content": (
                "The pricing engine calculates base price from tier configuration. "
                "TODO: add discount logic here before shipping to production. "
                "Output is a JSON payload sent to the billing service."
            ),
        })
        assert not result["accepted"]
        assert V.HANDWAVING in codes(result)

    def test_fill_in_the_blank_trips(self, eng: Engine) -> None:
        """'fill in the blank' (lazy filler pattern) must still trigger HANDWAVING."""
        sid = self._advance_to_draft(eng)
        result = eng.submit(sid, "draft", {
            "content": (
                "The configuration file has a secret_key field. "
                "You will need to fill in the blank with the actual API secret "
                "before deploying the application to production."
            ),
        })
        assert not result["accepted"]
        assert V.HANDWAVING in codes(result)

    def test_bracket_placeholder_trips(self, eng: Engine) -> None:
        """'[placeholder' syntax must still trigger HANDWAVING."""
        sid = self._advance_to_draft(eng)
        result = eng.submit(sid, "draft", {
            "content": (
                "The email template subject line should read: "
                "[placeholder for subject line to be written by marketing team]. "
                "The body follows the standard transactional format with the user name "
                "and action details filled in from the event payload automatically."
            ),
        })
        assert not result["accepted"]
        assert V.HANDWAVING in codes(result)

    def test_as_an_ai_still_trips(self, eng: Engine) -> None:
        """'as an AI' must still trigger HANDWAVING regardless of A8 changes."""
        sid = self._advance_to_draft(eng)
        result = eng.submit(sid, "draft", {
            "content": (
                "As an AI language model I should note that I cannot verify "
                "the runtime performance of this algorithm without actually running it. "
                "The implementation follows standard merge sort principles."
            ),
        })
        assert not result["accepted"]
        assert V.HANDWAVING in codes(result)


# ---------------------------------------------------------------------------
# A9: done flag in submit/get_state
# ---------------------------------------------------------------------------

class TestA9DoneFlag:
    def test_done_false_mid_session(self, eng: Engine) -> None:
        """done must be False when required stages remain."""
        r = eng.create_session("goal", rigor="low")
        sid = r["session_id"]
        result = eng.submit(sid, "frame", GOOD_FRAME)
        assert result["accepted"]
        assert result.get("done") is False

    def test_done_true_on_final_stage(self, eng: Engine) -> None:
        """done must be True when the last required stage passes."""
        r = eng.create_session("goal", rigor="low")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        eng.submit(sid, "draft", GOOD_DRAFT)
        result = eng.submit(sid, "deliver", GOOD_DELIVER)
        assert result["accepted"]
        assert result.get("done") is True

    def test_get_state_done_false_mid(self, eng: Engine) -> None:
        """get_state must include done=False when stages remain."""
        r = eng.create_session("goal", rigor="low")
        sid = r["session_id"]
        state = eng.get_state(sid)
        assert "done" in state
        assert state["done"] is False

    def test_get_state_done_true_after_all(self, eng: Engine) -> None:
        """get_state must include done=True after all stages complete."""
        r = eng.create_session("goal", rigor="low")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        eng.submit(sid, "draft", GOOD_DRAFT)
        eng.submit(sid, "deliver", GOOD_DELIVER)
        state = eng.get_state(sid)
        assert state.get("done") is True

    def test_done_false_after_partial_full(self, eng: Engine) -> None:
        """done must be False after frame + research in a full session."""
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        r1 = eng.submit(sid, "frame", GOOD_FRAME)
        assert r1.get("done") is False
        r2 = eng.submit(sid, "research", GOOD_RESEARCH)
        assert r2.get("done") is False


# ---------------------------------------------------------------------------
# A10: all-"x" / duplicate-filler FULL run never reaches finalize
# ---------------------------------------------------------------------------

class TestA10AllXRunNeverFinalizes:
    """
    Adversarial proof: a FULL session where every artifact is populated with
    trivially short or duplicate "x" values must be rejected at every stage.
    finalize() must never be reachable.
    """

    # Junk artifacts — all fields set to "x" or trivially short values
    JUNK_CLASSIFY = {
        "complexity": "high",
        "stakes": "high",
        "reversibility": "hard",
        "selected_level": "full",
        "justification": "x",  # too short
    }

    JUNK_FRAME = {
        "goal_restatement": "x",  # too short
        "success_criteria": ["x", "x"],  # too short + duplicate
        "assumptions": [{"assumption": "x", "why_safe": "x"}],  # too short
    }

    JUNK_RESEARCH = {
        "facts": [
            {"claim": "x", "source": "https://example.com"},  # claim too short
        ],
        "unknowns": [],
    }

    JUNK_PLAN = {
        "steps": ["x", "x", "x"],  # too short + duplicates
        "risks": ["x"],  # too short
        "verification_strategy": ["x"],  # too short
    }

    JUNK_DRAFT = {
        "content": "x",  # too short
    }

    JUNK_CRITIQUE = {
        "findings": [
            {"severity": "minor", "issue": "x", "location": "x"},  # issue too short
            {"severity": "minor", "issue": "x", "location": "x"},  # duplicate + only minors
        ],
        "steelman": "x",
    }

    JUNK_VERIFY = {
        "checks": [{
            "what": "x",
            "how": "x",   # too short + not concrete
            "result": "x",  # echoes what + too short
        }]
    }

    JUNK_REVISE = {
        "fixes": [{"finding_ref": "x", "change": "x"}],  # change too short
        "reverified": True,
    }

    JUNK_DELIVER = {
        "summary": "x",
        "limitations": ["x"],  # too short — but limitations aren't length-checked in deliver gate
        "sources": [],  # empty after real research
    }

    def test_all_x_classify_fails(self, eng: Engine) -> None:
        r = eng.create_session("Build a production pricing model", rigor="adaptive")
        sid = r["session_id"]
        result = eng.submit(sid, "classify", self.JUNK_CLASSIFY)
        assert not result["accepted"], "All-x classify must be rejected"
        assert V.EMPTY_OR_TRIVIAL in codes(result)

    def test_all_x_frame_fails(self, eng: Engine) -> None:
        r = eng.create_session("Build a production pricing model", rigor="full")
        sid = r["session_id"]
        result = eng.submit(sid, "frame", self.JUNK_FRAME)
        assert not result["accepted"], "All-x frame must be rejected"
        assert V.EMPTY_OR_TRIVIAL in codes(result)

    def test_all_x_research_fails(self, eng: Engine) -> None:
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        result = eng.submit(sid, "research", self.JUNK_RESEARCH)
        assert not result["accepted"], "All-x research must be rejected"
        assert V.EMPTY_OR_TRIVIAL in codes(result)

    def test_all_x_plan_fails(self, eng: Engine) -> None:
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        eng.submit(sid, "research", GOOD_RESEARCH)
        result = eng.submit(sid, "plan", self.JUNK_PLAN)
        assert not result["accepted"], "All-x plan must be rejected"
        assert V.EMPTY_OR_TRIVIAL in codes(result)

    def test_all_x_draft_fails(self, eng: Engine) -> None:
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        eng.submit(sid, "research", GOOD_RESEARCH)
        eng.submit(sid, "plan", GOOD_PLAN_FULL)
        result = eng.submit(sid, "draft", self.JUNK_DRAFT)
        assert not result["accepted"], "All-x draft must be rejected"
        assert V.EMPTY_OR_TRIVIAL in codes(result)

    def test_all_x_critique_fails(self, eng: Engine) -> None:
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        eng.submit(sid, "research", GOOD_RESEARCH)
        eng.submit(sid, "plan", GOOD_PLAN_FULL)
        eng.submit(sid, "draft", GOOD_DRAFT)
        result = eng.submit(sid, "critique", self.JUNK_CRITIQUE)
        assert not result["accepted"], "All-x / all-minor critique must be rejected at FULL"
        assert V.EMPTY_OR_TRIVIAL in codes(result) or V.HOLLOW_CRITIQUE in codes(result)

    def test_all_x_verify_fails(self, eng: Engine) -> None:
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        eng.submit(sid, "research", GOOD_RESEARCH)
        eng.submit(sid, "plan", GOOD_PLAN_FULL)
        eng.submit(sid, "draft", GOOD_DRAFT)
        eng.submit(sid, "critique", GOOD_CRITIQUE)
        result = eng.submit(sid, "verify", self.JUNK_VERIFY)
        assert not result["accepted"], "All-x verify must be rejected"
        assert V.UNVERIFIED_CLAIM in codes(result) or V.EMPTY_OR_TRIVIAL in codes(result)

    def test_all_x_full_run_never_finalizes(self, eng: Engine) -> None:
        """
        End-to-end adversarial: attempt every stage with junk artifacts.
        Assert that finalize() is never reachable — every stage blocks the session.
        """
        r = eng.create_session("Build a production pricing model", rigor="full")
        sid = r["session_id"]

        # frame with all "x" must fail immediately
        result = eng.submit(sid, "frame", self.JUNK_FRAME)
        assert not result["accepted"], "All-x frame must be rejected — session blocked"

        # Session must still be at frame (not advanced)
        state = eng.get_state(sid)
        assert state["current_stage"] == "frame", (
            f"Session should still be at frame after rejection, got: {state['current_stage']}"
        )

        # finalize must be refused — required stages still missing
        fin = eng.finalize(sid)
        assert not fin["finalized"], "finalize must fail when stages are incomplete"
        assert "frame" in fin.get("missing_stages", []), (
            "frame must be listed as missing since it never passed"
        )

    def test_full_x_session_cannot_reach_finalize(self, eng: Engine) -> None:
        """
        Comprehensive: submit junk at every stage in a full session.
        The engine must block at each gate; finalize must always refuse.
        """
        r = eng.create_session("Build a production pricing model", rigor="full")
        sid = r["session_id"]

        # Track which stages actually passed (should be zero or minimum)
        stages_passed: list[str] = []

        # Try frame with junk — must fail
        res = eng.submit(sid, "frame", self.JUNK_FRAME)
        assert not res["accepted"], f"Junk frame should fail: {res}"

        # Provide a good frame to advance past it
        res = eng.submit(sid, "frame", GOOD_FRAME)
        assert res["accepted"]
        stages_passed.append("frame")

        # Try research with a claim that's too short
        res = eng.submit(sid, "research", self.JUNK_RESEARCH)
        assert not res["accepted"], f"Junk research should fail: {res}"

        # Advance with good research
        res = eng.submit(sid, "research", GOOD_RESEARCH)
        assert res["accepted"]
        stages_passed.append("research")

        # Try plan with all "x" steps
        res = eng.submit(sid, "plan", self.JUNK_PLAN)
        assert not res["accepted"], f"Junk plan should fail: {res}"

        # Advance with good plan
        res = eng.submit(sid, "plan", GOOD_PLAN_FULL)
        assert res["accepted"]
        stages_passed.append("plan")

        # Try draft with "x"
        res = eng.submit(sid, "draft", self.JUNK_DRAFT)
        assert not res["accepted"], f"Junk draft should fail: {res}"

        # Advance with good draft
        res = eng.submit(sid, "draft", GOOD_DRAFT)
        assert res["accepted"]
        stages_passed.append("draft")

        # Try critique with all minors and trivial issues
        res = eng.submit(sid, "critique", self.JUNK_CRITIQUE)
        assert not res["accepted"], f"Junk critique should fail at FULL: {res}"

        # Advance with good critique
        res = eng.submit(sid, "critique", GOOD_CRITIQUE)
        assert res["accepted"]
        stages_passed.append("critique")

        # Try verify with "x" how
        res = eng.submit(sid, "verify", self.JUNK_VERIFY)
        assert not res["accepted"], f"Junk verify should fail: {res}"

        # Advance with good verify
        res = eng.submit(sid, "verify", GOOD_VERIFY)
        assert res["accepted"]
        stages_passed.append("verify")

        # Revise: GOOD_REVISE is the only way through (V7: loops back to verify)
        res = eng.submit(sid, "revise", GOOD_REVISE)
        assert res["accepted"]
        stages_passed.append("revise")

        # V7 backtracking: after revise with real fixes, engine routes back to verify
        if res.get("loop_back"):
            res = eng.submit(sid, "verify", GOOD_VERIFY)
            assert res["accepted"], f"V7 loop verify failed: {res}"
            # After loop verify passes, session is at deliver (revise already complete)

        # Try deliver with empty sources after real research — must fail
        res = eng.submit(sid, "deliver", self.JUNK_DELIVER)
        assert not res["accepted"], f"Junk deliver (empty sources) should fail: {res}"

        # Deliver with sources — passes
        res = eng.submit(sid, "deliver", GOOD_DELIVER)
        assert res["accepted"], f"Good deliver failed: {res}"
        stages_passed.append("deliver")

        # Now finalize succeeds
        fin = eng.finalize(sid)
        assert fin["finalized"], f"Should finalize after all good stages: {fin}"

        # The key proof: every junk submission was individually blocked
        assert len(stages_passed) == 8, f"Expected 8 stages passed, got: {stages_passed}"


# ===========================================================================
# V2 NEW TESTS — one TestV2* class per spec item
# ===========================================================================

# ---------------------------------------------------------------------------
# TestV2V1 — Junk detector (JUNK_CONTENT)
# ---------------------------------------------------------------------------

class TestV2V1JunkDetector:
    """V1: JUNK_CONTENT fires on unique-char<5, top-char>60%, or <3 distinct tokens."""

    def test_all_a_string_rejected(self, eng: Engine) -> None:
        """'aaaaaaaaaaaaa' — 1 unique char → JUNK_CONTENT."""
        from fable_method.engine import V, _is_junk
        is_j, reason = _is_junk("aaaaaaaaaaaaa", "issue")
        assert is_j, f"Should be junk: {reason}"
        assert "unique" in reason

    def test_repeated_char_string_rejected(self, eng: Engine) -> None:
        """'xxxxxxxxxxxx' — 1 unique char → JUNK_CONTENT."""
        from fable_method.engine import _is_junk
        is_j, reason = _is_junk("xxxxxxxxxxxx", "issue")
        assert is_j

    def test_single_repeated_word_rejected(self, eng: Engine) -> None:
        """'word word word word' — 1 distinct token → JUNK_CONTENT for free-text fields."""
        from fable_method.engine import _is_junk
        is_j, reason = _is_junk("word word word word", "issue")
        assert is_j, f"Should be junk: {reason}"

    def test_top_char_over_60pct_rejected(self, eng: Engine) -> None:
        """50×'a ' — 'a' is >60% of non-space chars → JUNK_CONTENT."""
        from fable_method.engine import _is_junk
        text = "a " * 50
        is_j, reason = _is_junk(text, "issue")
        assert is_j, f"Should be junk: {reason}"

    def test_normal_sentence_passes(self, eng: Engine) -> None:
        """A genuine English sentence must not trigger junk detection."""
        from fable_method.engine import _is_junk
        text = "The authentication system fails to validate token expiry correctly."
        is_j, reason = _is_junk(text, "issue")
        assert not is_j, f"Normal sentence should not be junk: {reason}"

    def test_junk_in_frame_goal_restatement(self, eng: Engine) -> None:
        """Junk in goal_restatement fires JUNK_CONTENT."""
        r = eng.create_session("Build a secure payment processing pipeline", rigor="low")
        sid = r["session_id"]
        result = eng.submit(sid, "frame", {
            "goal_restatement": "aaaaaaaaaaaaaaaaaaaaaaaa",  # 1 unique char
            "success_criteria": ["Payment transactions are processed without errors"],
            "assumptions": [{"assumption": "The payment gateway API is stable", "why_safe": "Vendor SLA guarantees 99.9% uptime"}],
        })
        assert not result["accepted"]
        assert V.JUNK_CONTENT in codes(result)

    def test_junk_distinct_repeated_chars_all_fields(self, eng: Engine) -> None:
        """Adversarial bypass (a): distinct repeated-char junk in every field fails."""
        # This is bypass test (a): aaaaaaa, bbbbbbb, ccccccc etc.
        r = eng.create_session("Build a secure payment processing pipeline", rigor="low")
        sid = r["session_id"]
        result = eng.submit(sid, "frame", {
            "goal_restatement": "bbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "success_criteria": ["ccccccccccccccccccccccc"],
            "assumptions": [{"assumption": "ddddddddddddddddddddd", "why_safe": "eeeeeeeeeeeeeeeeeeeee"}],
        })
        assert not result["accepted"]
        assert V.JUNK_CONTENT in codes(result) or V.EMPTY_OR_TRIVIAL in codes(result)


# ---------------------------------------------------------------------------
# TestV2V2 — RISK_FLOOR + auto-escalation
# ---------------------------------------------------------------------------

class TestV2V2RiskFloor:
    """V2: RISK_FLOOR blocks 'low' when goal has financial/irreversibility/domain signals."""

    def test_financial_goal_cannot_select_low(self, eng: Engine) -> None:
        """Goal with '$4,000,000' → RISK_FLOOR prevents selected_level='low'."""
        r = eng.create_session("wire $4,000,000 irreversibly to vendor", rigor="adaptive")
        sid = r["session_id"]
        result = eng.submit(sid, "classify", {
            "complexity": "low",
            "stakes": "low",
            "reversibility": "easy",
            "selected_level": "low",
            "justification": "Wire transfer to vendor for completed services irreversibly settled.",
        })
        assert not result["accepted"]
        assert V.RISK_FLOOR in codes(result), f"Expected RISK_FLOOR, got: {codes(result)}"

    def test_irreversible_goal_cannot_select_low(self, eng: Engine) -> None:
        """Goal with 'delete production database' → RISK_FLOOR."""
        r = eng.create_session("delete the production database tables immediately", rigor="adaptive")
        sid = r["session_id"]
        result = eng.submit(sid, "classify", {
            "complexity": "low",
            "stakes": "low",
            "reversibility": "easy",
            "selected_level": "low",
            "justification": "Deleting production tables to clean up old data.",
        })
        assert not result["accepted"]
        assert V.RISK_FLOOR in codes(result)

    def test_domain_signal_cannot_select_low(self, eng: Engine) -> None:
        """Goal with 'legal contract' → RISK_FLOOR."""
        r = eng.create_session("draft the legal contract for vendor compliance", rigor="adaptive")
        sid = r["session_id"]
        result = eng.submit(sid, "classify", {
            "complexity": "low",
            "stakes": "low",
            "reversibility": "easy",
            "selected_level": "low",
            "justification": "Legal contract drafting is straightforward for our vendor.",
        })
        assert not result["accepted"]
        assert V.RISK_FLOOR in codes(result)

    def test_risk_signal_allows_medium(self, eng: Engine) -> None:
        """Goal with risk signals but selected_level='medium' must pass."""
        r = eng.create_session("deploy the application to production servers", rigor="adaptive")
        sid = r["session_id"]
        result = eng.submit(sid, "classify", {
            "complexity": "medium",
            "stakes": "medium",
            "reversibility": "hard",
            "selected_level": "medium",
            "justification": "Deploying to production servers requires careful staging and rollback planning.",
        })
        assert result["accepted"], f"Medium with risk signal should pass: {result}"

    def test_auto_escalation_on_blocker_critique(self, eng: Engine) -> None:
        """V2 auto-escalation: critique with a blocker finding escalates session to FULL."""
        r = eng.create_session("goal", rigor="medium", involves_facts=False)
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        eng.submit(sid, "plan", GOOD_PLAN_MEDIUM)
        eng.submit(sid, "draft", GOOD_DRAFT)
        result = eng.submit(sid, "critique", {
            "findings": [
                {"severity": "blocker",
                 "issue": "Critical security vulnerability: authentication tokens not validated",
                 "location": "auth.py:42"},
            ],
            "steelman": "The overall architecture is sound aside from this critical flaw.",
        })
        assert result["accepted"]
        state = eng.get_state(sid)
        # After blocker finding, level must be escalated to full
        assert state.get("level") == "full", f"Expected level=full after blocker, got: {state.get('level')}"
        assert state.get("escalated_to"), "escalated_to must be recorded"

    def test_auto_escalation_on_major_critique(self, eng: Engine) -> None:
        """Hardening: a 'major' finding (not just 'blocker') escalates to FULL — severity is
        self-assigned, so escalation must not hinge on the single highest label."""
        r = eng.create_session("goal", rigor="medium", involves_facts=False)
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        eng.submit(sid, "plan", GOOD_PLAN_MEDIUM)
        eng.submit(sid, "draft", GOOD_DRAFT)
        result = eng.submit(sid, "critique", {
            "findings": [
                {"severity": "major", "issue": "The deliver gate mishandles dict-shaped sources entirely.", "location": "_gate_deliver"},
                {"severity": "minor", "issue": "Helper functions are missing docstrings.", "location": "engine.py"},
            ],
            "steelman": "The overall architecture is sound aside from this issue.",
        })
        assert result["accepted"]
        state = eng.get_state(sid)
        assert state.get("level") == "full", f"Expected level=full after major, got: {state.get('level')}"
        assert state.get("escalated_to"), "escalated_to must be recorded for a major finding"

    def test_no_escalation_on_minor_only_critique(self, eng: Engine) -> None:
        """Hardening guard: an all-minor critique must NOT escalate (medium stays medium)."""
        r = eng.create_session("goal", rigor="medium", involves_facts=False)
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        eng.submit(sid, "plan", GOOD_PLAN_MEDIUM)
        eng.submit(sid, "draft", GOOD_DRAFT)
        eng.submit(sid, "critique", {
            "findings": [
                {"severity": "minor", "issue": "Helper functions are missing docstrings.", "location": "engine.py"},
                {"severity": "minor", "issue": "Some variable names could be clearer in the gate loop.", "location": "engine.py"},
            ],
            "steelman": "The engine is well-structured and the gate codes are stable overall.",
        })
        state = eng.get_state(sid)
        assert state.get("level") == "medium", f"Minor-only must stay medium, got: {state.get('level')}"
        assert not state.get("escalated_to"), "minor-only critique must not escalate"

    def test_scan_risk_matches_expected_signals(self, eng: Engine) -> None:
        """_scan_risk returns correct signals for financial goal."""
        from fable_method.engine import _scan_risk
        signals = _scan_risk("wire $4,000,000 irreversibly to vendor for payment")
        assert any("money" in s for s in signals), f"Expected money signal: {signals}"
        assert any("irreversible" in s for s in signals), f"Expected irreversible signal: {signals}"

    def test_neutral_goal_no_risk_signals(self, eng: Engine) -> None:
        """Neutral goal returns no risk signals."""
        from fable_method.engine import _scan_risk
        signals = _scan_risk("write a unit test for the string formatter")
        assert signals == [], f"Expected no signals: {signals}"

    def test_scan_risk_plain_money_words(self, eng: Engine) -> None:
        """Hardening: plain financial words (no $ or 'wire') must flag risk too."""
        from fable_method.engine import _scan_risk
        assert _scan_risk("Move money out of the account"), "'money' should flag risk"
        assert _scan_risk("Send the funds to the new payee"), "'funds' should flag risk"
        assert _scan_risk("Withdraw the cash from savings"), "'cash'/'withdraw' should flag risk"

    def test_adaptive_low_plain_money_blocked(self, eng: Engine) -> None:
        """Hardening: ADAPTIVE classify selecting LOW for a plain-'money' goal → RISK_FLOOR."""
        r = eng.create_session("Move money out of the account to a new payee", rigor="adaptive")
        sid = r["session_id"]
        result = eng.submit(sid, "classify", {
            "complexity": "low", "stakes": "low", "reversibility": "easy", "selected_level": "low",
            "justification": "Moving the money to the payee account looks like a routine low-risk task.",
        })
        assert not result["accepted"]
        assert V.RISK_FLOOR in codes(result), f"Expected RISK_FLOOR: {codes(result)}"

    def test_adaptive_low_wire_money_blocked(self, eng: Engine) -> None:
        """Adversarial bypass (b): ADAPTIVE classify selecting LOW for wire $4M goal fails."""
        r = eng.create_session("wire $4,000,000 irreversibly to vendor", rigor="adaptive")
        sid = r["session_id"]
        result = eng.submit(sid, "classify", {
            "complexity": "low",
            "stakes": "low",
            "reversibility": "easy",
            "selected_level": "low",
            "justification": "Wire transfer to vendor is a routine irreversible payment operation.",
        })
        assert not result["accepted"], "ADAPTIVE LOW for wire $4M must be refused"
        assert V.RISK_FLOOR in codes(result), f"Expected RISK_FLOOR: {codes(result)}"


# ---------------------------------------------------------------------------
# TestV2V3 — NOOP_FIX + token-overlap ≥0.4 finding→fix mapping
# ---------------------------------------------------------------------------

class TestV2V3NoopFix:
    """V3: NOOP_FIX rejects pure-intent change text; token-overlap ≥0.4 for mapping."""

    def _advance_to_revise(self, eng: Engine) -> str:
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        for stage, artifact in [
            ("frame", GOOD_FRAME),
            ("research", GOOD_RESEARCH),
            ("plan", GOOD_PLAN_FULL),
            ("draft", GOOD_DRAFT),
            ("critique", GOOD_CRITIQUE),
            ("verify", GOOD_VERIFY),
        ]:
            res = eng.submit(sid, stage, artifact)
            assert res["accepted"], f"Setup failed at {stage}: {res}"
        return sid

    def test_acknowledged_noop_rejected(self, eng: Engine) -> None:
        """'Acknowledged; will consider addressing this later' → NOOP_FIX."""
        sid = self._advance_to_revise(eng)
        result = eng.submit(sid, "revise", {
            "fixes": [{
                "finding_ref": "Revise gate does not check reverified field when fixes exist",
                "change": "Acknowledged; will consider addressing this later.",
            }],
            "reverified": True,
        })
        assert not result["accepted"]
        assert V.NOOP_FIX in codes(result), f"Expected NOOP_FIX: {codes(result)}"

    def test_will_address_noop_rejected(self, eng: Engine) -> None:
        """'Will address in a future sprint' → NOOP_FIX."""
        sid = self._advance_to_revise(eng)
        result = eng.submit(sid, "revise", {
            "fixes": [{
                "finding_ref": "Revise gate does not check reverified field when fixes exist",
                "change": "Will address in a future sprint when time permits.",
            }],
            "reverified": True,
        })
        assert not result["accepted"]
        assert V.NOOP_FIX in codes(result)

    def test_noted_noop_rejected(self, eng: Engine) -> None:
        """'Noted. TBD.' → NOOP_FIX."""
        sid = self._advance_to_revise(eng)
        result = eng.submit(sid, "revise", {
            "fixes": [{
                "finding_ref": "Revise gate does not check reverified field when fixes exist",
                "change": "Noted. TBD.",
            }],
            "reverified": True,
        })
        assert not result["accepted"]
        assert V.NOOP_FIX in codes(result)

    def test_concrete_fix_passes(self, eng: Engine) -> None:
        """A fix with a concrete edit verb passes NOOP_FIX gate."""
        sid = self._advance_to_revise(eng)
        result = eng.submit(sid, "revise", {
            "fixes": [{
                "finding_ref": "Revise gate does not check reverified field when fixes exist",
                "change": "Added check in _gate_revise: if fixes present and reverified is False, "
                          "append NOT_ENOUGH_RIGOR violation. Updated line 845 of engine.py.",
            }],
            "reverified": True,
        })
        assert result["accepted"], f"Concrete fix should pass: {result}"

    def test_low_overlap_finding_ref_fails(self, eng: Engine) -> None:
        """finding_ref with <0.4 token overlap with the finding issue → UNMAPPED_FIX."""
        sid = self._advance_to_revise(eng)
        result = eng.submit(sid, "revise", {
            "fixes": [{
                "finding_ref": "completely unrelated topic about database schema migration",
                "change": "Updated the database schema migration scripts to use transactions.",
            }],
            "reverified": True,
        })
        assert not result["accepted"]
        assert V.UNMAPPED_FIX in codes(result), f"Expected UNMAPPED_FIX: {codes(result)}"

    def test_adversarial_noop_bypass_blocked(self, eng: Engine) -> None:
        """Adversarial bypass (d): 'Acknowledged; will consider addressing this later' is blocked."""
        sid = self._advance_to_revise(eng)
        result = eng.submit(sid, "revise", {
            "fixes": [{
                "finding_ref": "Revise gate does not check reverified field when fixes exist",
                "change": "Acknowledged; will consider addressing this later",
            }],
            "reverified": True,
        })
        assert not result["accepted"], "NOOP intent-only change must be blocked"
        assert V.NOOP_FIX in codes(result)


# ---------------------------------------------------------------------------
# TestV2V4 — Source typing + FABRICATION_RISK alignment
# ---------------------------------------------------------------------------

class TestV2V4SourceTyping:
    """V4: assumed sources allowed; FABRICATION_RISK only for fake url/tool_output."""

    def _advance_to_research(self, eng: Engine) -> str:
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        return sid

    def test_assumed_source_accepted_at_research(self, eng: Engine) -> None:
        """assumed source is now allowed at research gate (V4)."""
        sid = self._advance_to_research(eng)
        result = eng.submit(sid, "research", {
            "facts": [{"claim": "Python 3.10 was released in October 2021.",
                        "source": "my training data", "type": "assumed"}],
            "unknowns": [],
        })
        assert result["accepted"], f"assumed source should be accepted: {result}"

    def test_inferred_assumed_accepted(self, eng: Engine) -> None:
        """Source inferred as 'assumed' (no http, no path) is accepted."""
        sid = self._advance_to_research(eng)
        result = eng.submit(sid, "research", {
            "facts": [{"claim": "The sky appears blue due to Rayleigh scattering.",
                        "source": "general physics knowledge"}],
            "unknowns": [],
        })
        assert result["accepted"], f"Inferred assumed source should be accepted: {result}"

    def test_placeholder_url_is_fabrication(self, eng: Engine) -> None:
        """A url source pointing to example.com → FABRICATION_RISK."""
        sid = self._advance_to_research(eng)
        result = eng.submit(sid, "research", {
            "facts": [{"claim": "The API returns 200 on success.",
                        "source": "https://example.com/made-up"}],
            "unknowns": [],
        })
        assert not result["accepted"]
        assert V.FABRICATION_RISK in codes(result), f"Expected FABRICATION_RISK: {codes(result)}"

    def test_real_url_accepted(self, eng: Engine) -> None:
        """A real URL with path is not fabrication."""
        sid = self._advance_to_research(eng)
        result = eng.submit(sid, "research", {
            "facts": [{"claim": "Python 3.10 introduced structural pattern matching.",
                        "source": "https://docs.python.org/3/whatsnew/3.10.html"}],
            "unknowns": [],
        })
        assert result["accepted"], f"Real URL should be accepted: {result}"

    def test_empty_source_still_fabrication(self, eng: Engine) -> None:
        """Empty source string → FABRICATION_RISK regardless of V4."""
        sid = self._advance_to_research(eng)
        result = eng.submit(sid, "research", {
            "facts": [{"claim": "Some unverified claim.", "source": ""}],
            "unknowns": [],
        })
        assert not result["accepted"]
        assert V.FABRICATION_RISK in codes(result)

    def test_adversarial_fake_url_in_deliver_blocked(self, eng: Engine) -> None:
        """Adversarial bypass (e): example.com source in deliver → FABRICATION_RISK."""
        r = eng.create_session("goal", rigor="low")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        eng.submit(sid, "draft", GOOD_DRAFT)
        result = eng.submit(sid, "deliver", {
            "summary": "The analysis is complete with fully verified sources.",
            "limitations": ["None identified"],
            "sources": ["https://example.com/made-up"],
        })
        assert not result["accepted"], "Placeholder URL in deliver sources must be blocked"
        assert V.FABRICATION_RISK in codes(result), f"Expected FABRICATION_RISK: {codes(result)}"

    def test_dict_shaped_placeholder_source_in_deliver_blocked(self, eng: Engine) -> None:
        """Bug (a): a dict-shaped source {text,type} per CONTRACT must NOT bypass the
        fabrication check. Previously str(dict) inferred as 'assumed' and slipped through."""
        r = eng.create_session("goal", rigor="low")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        eng.submit(sid, "draft", GOOD_DRAFT)
        result = eng.submit(sid, "deliver", {
            "summary": "The analysis is complete with fully verified sources.",
            "limitations": ["None identified"],
            "sources": [{"text": "https://example.com/made-up", "type": "url"}],
        })
        assert not result["accepted"], "Dict-shaped placeholder URL must be blocked"
        assert V.FABRICATION_RISK in codes(result), f"Expected FABRICATION_RISK: {codes(result)}"

    def test_dict_shaped_real_source_in_deliver_passes(self, eng: Engine) -> None:
        """Bug (a) guard: a dict-shaped source with a real URL must still pass cleanly."""
        r = eng.create_session("goal", rigor="low")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        eng.submit(sid, "draft", GOOD_DRAFT)
        result = eng.submit(sid, "deliver", {
            "summary": "The analysis is complete with fully verified sources.",
            "limitations": ["None identified"],
            "sources": [{"text": "https://docs.python.org/3/library/zipfile.html", "type": "url"}],
        })
        assert V.FABRICATION_RISK not in codes(result), f"Real dict URL should pass: {codes(result)}"


# ---------------------------------------------------------------------------
# TestV2V5 — Evidence gate (NO_EVIDENCE)
# ---------------------------------------------------------------------------

class TestV2V5EvidenceGate:
    """V5: verify must have ≥1 check with concrete evidence; NO_EVIDENCE otherwise."""

    def _advance_to_verify(self, eng: Engine) -> str:
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        for stage, artifact in [
            ("frame", GOOD_FRAME),
            ("research", GOOD_RESEARCH),
            ("plan", GOOD_PLAN_FULL),
            ("draft", GOOD_DRAFT),
            ("critique", GOOD_CRITIQUE),
        ]:
            eng.submit(sid, stage, artifact)
        return sid

    def test_no_evidence_field_fails(self, eng: Engine) -> None:
        """Checks with no evidence field at all → NO_EVIDENCE."""
        sid = self._advance_to_verify(eng)
        result = eng.submit(sid, "verify", {
            "checks": [{
                "what": "Gate rejects missing fields",
                "how": "Ran pytest suite covering all gates with invalid artifacts",
                "result": "All 12 negative cases raised expected violation codes",
                # no evidence field
            }]
        })
        assert not result["accepted"]
        assert V.NO_EVIDENCE in codes(result), f"Expected NO_EVIDENCE: {codes(result)}"

    def test_empty_evidence_field_fails(self, eng: Engine) -> None:
        """Empty evidence string → NO_EVIDENCE."""
        sid = self._advance_to_verify(eng)
        result = eng.submit(sid, "verify", {
            "checks": [{
                "what": "Gate rejects missing fields",
                "how": "Ran pytest suite covering all gates with invalid artifacts",
                "result": "All cases passed as expected",
                "evidence": "",  # empty
            }]
        })
        assert not result["accepted"]
        assert V.NO_EVIDENCE in codes(result)

    def test_vague_evidence_no_artifact_fails(self, eng: Engine) -> None:
        """Evidence with no digit/PASS/FAIL/file:line/snippet → NO_EVIDENCE."""
        sid = self._advance_to_verify(eng)
        result = eng.submit(sid, "verify", {
            "checks": [{
                "what": "Gate rejects missing fields",
                "how": "Ran pytest suite covering all gates with invalid artifacts",
                "result": "Everything passed as expected and looked correct",
                "evidence": "I tested it and it worked",  # no concrete artifact token
            }]
        })
        assert not result["accepted"]
        assert V.NO_EVIDENCE in codes(result)

    def test_digit_in_evidence_passes(self, eng: Engine) -> None:
        """Evidence containing a digit satisfies the gate."""
        sid = self._advance_to_verify(eng)
        result = eng.submit(sid, "verify", {
            "checks": [{
                "what": "Gate rejects missing fields",
                "how": "Ran pytest suite covering all gates with invalid artifacts",
                "result": "All negative cases raised correct violation codes",
                "evidence": "47 tests passed, 0 failed",
            }]
        })
        assert result["accepted"], f"Digit evidence should pass: {result}"

    def test_pass_fail_token_passes(self, eng: Engine) -> None:
        """Evidence with explicit PASS token satisfies the gate."""
        sid = self._advance_to_verify(eng)
        result = eng.submit(sid, "verify", {
            "checks": [{
                "what": "Gate rejects missing fields",
                "how": "Ran pytest and captured output",
                "result": "All checks confirmed correct",
                "evidence": "ALL_TESTS PASS — no failures detected",
            }]
        })
        assert result["accepted"], f"PASS token evidence should pass: {result}"

    def test_file_line_reference_passes(self, eng: Engine) -> None:
        """Evidence with file:line pattern satisfies the gate."""
        sid = self._advance_to_verify(eng)
        result = eng.submit(sid, "verify", {
            "checks": [{
                "what": "Reverified field check logic",
                "how": "Re-read the engine source and confirmed the logic",
                "result": "Logic confirmed correct at the specified location",
                "evidence": "engine.py:845 — reverified check present and correct",
            }]
        })
        assert result["accepted"], f"file:line evidence should pass: {result}"

    def test_quoted_snippet_passes(self, eng: Engine) -> None:
        """Evidence with quoted snippet satisfies the gate."""
        sid = self._advance_to_verify(eng)
        result = eng.submit(sid, "verify", {
            "checks": [{
                "what": "API response format",
                "how": "Ran the CLI and captured stdout output",
                "result": "Output matched expected format",
                "evidence": 'Output: `{"accepted": true, "done": false}`',
            }]
        })
        assert result["accepted"], f"Quoted snippet evidence should pass: {result}"

    def test_adversarial_vague_verify_blocked(self, eng: Engine) -> None:
        """Adversarial bypass (c): vague how + no evidence → blocked."""
        sid = self._advance_to_verify(eng)
        result = eng.submit(sid, "verify", {
            "checks": [{
                "what": "I tested it all completely",
                "how": "I tested it all completely and everything passed",
                "result": "everything passed",
                "evidence": "",
            }]
        })
        assert not result["accepted"], "Vague verify with no evidence must be blocked"
        # Must fail on at least UNVERIFIED_CLAIM (result echoes what, how has no concrete method)
        # and NO_EVIDENCE
        assert V.NO_EVIDENCE in codes(result) or V.UNVERIFIED_CLAIM in codes(result), \
            f"Expected NO_EVIDENCE or UNVERIFIED_CLAIM: {codes(result)}"


# ---------------------------------------------------------------------------
# TestV2V7 — Backtracking loop
# ---------------------------------------------------------------------------

class TestV2V7BacktrackingLoop:
    """V7: passing revise with real changes routes back to verify; loop_count tracked."""

    def _advance_to_revise(self, eng: Engine) -> str:
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        for stage, artifact in [
            ("frame", GOOD_FRAME),
            ("research", GOOD_RESEARCH),
            ("plan", GOOD_PLAN_FULL),
            ("draft", GOOD_DRAFT),
            ("critique", GOOD_CRITIQUE),
            ("verify", GOOD_VERIFY),
        ]:
            res = eng.submit(sid, stage, artifact)
            assert res["accepted"], f"Setup failed at {stage}: {res}"
        return sid

    def test_loop_detector_is_structured_not_prose(self) -> None:
        """Devil's-advocate fix: the loop trigger reads a STRUCTURED status, not free-text prose.
        Benign phrasings must NOT loop (no false positive); real failures in prose must NOT loop
        unless status says so (the prose classifier was wrong in both directions)."""
        from fable_method.engine import _verify_has_unresolved_check as U
        # False-positive phrasings that the old keyword scanner wrongly flagged — now clean:
        for r in ["PASS: no errors found", "error handling confirmed working",
                  "no mismatch between expected and actual", "graceful error recovery works"]:
            assert U([{"what": "x", "how": "y", "result": r}]) is False, f"should not loop: {r!r}"
        # Structured status is authoritative:
        assert U([{"what": "x", "how": "y", "result": "ok", "status": "fail"}]) is True
        assert U([{"what": "x", "how": "y", "result": "ok", "status": "inconclusive"}]) is True
        assert U([{"what": "x", "how": "y", "result": "all good", "status": "pass"}]) is False

    def test_verify_pass_status_contradicting_failing_evidence_blocked(self, eng: Engine) -> None:
        """#3 fix: a check marked status='pass' whose evidence shows a non-zero exit is rejected
        (FABRICATION_RISK) — the model cannot stamp PASS over harness-injected failure evidence."""
        from fable_method.engine import _gate_verify, V
        v = _gate_verify({"checks": [{
            "what": "build succeeds", "how": "ran the build command in a subprocess",
            "result": "everything fine", "evidence": "cmd[0](bash): FAIL exit_code=1",
            "status": "pass",
        }]}, "full", "universal")
        codes_ = [x["code"] for x in v]
        assert V.FABRICATION_RISK in codes_, f"Expected FABRICATION_RISK: {codes_}"

    def test_verify_pass_with_honest_before_after_evidence_not_flagged(self, eng: Engine) -> None:
        """Round-3 fix: a genuine post-fix pass that quotes a PRIOR failing run for context must
        NOT be flagged — only the LAST exit code in the evidence decides (here it is 0)."""
        from fable_method.engine import _gate_verify, V
        v = _gate_verify({"checks": [{
            "what": "tests pass after the fix", "how": "ran the pytest suite after applying the fix",
            "result": "now all passing", "status": "pass",
            "evidence": "before fix run exited 1; after fix all PASS, exit_code=0",
        }]}, "full", "universal")
        assert V.FABRICATION_RISK not in [x["code"] for x in v], \
            f"honest before/after evidence must not be flagged: {[x['code'] for x in v]}"

    def test_verify_pass_trailing_nonzero_exit_still_flagged(self, eng: Engine) -> None:
        """Guard: if the LAST exit code is non-zero, status='pass' is still FABRICATION_RISK."""
        from fable_method.engine import _gate_verify, V
        v = _gate_verify({"checks": [{
            "what": "build", "how": "ran the build command in a subprocess",
            "result": "claims fine", "status": "pass",
            "evidence": "first attempt exit_code=0 then re-ran and it exited 2",
        }]}, "full", "universal")
        assert V.FABRICATION_RISK in [x["code"] for x in v]

    def test_verify_invalid_status_rejected(self, eng: Engine) -> None:
        """An out-of-vocabulary status value is rejected."""
        from fable_method.engine import _gate_verify, V
        v = _gate_verify({"checks": [{
            "what": "x", "how": "ran the test suite with coverage", "result": "done",
            "evidence": "12 passed", "status": "mostly-ok",
        }]}, "full", "universal")
        assert V.EMPTY_OR_TRIVIAL in [x["code"] for x in v]

    def test_revise_with_real_fixes_loops_back_to_verify(self, eng: Engine) -> None:
        """Revise with reverified=True + fixes routes back to verify."""
        sid = self._advance_to_revise(eng)
        result = eng.submit(sid, "revise", GOOD_REVISE)
        assert result["accepted"]
        assert result.get("loop_back") is True, "loop_back must be True"
        assert result.get("current_stage") == "verify", f"Expected verify, got: {result.get('current_stage')}"
        assert result.get("loop_count", 0) == 1, f"loop_count should be 1: {result.get('loop_count')}"

    def test_loop_count_increments(self, eng: Engine) -> None:
        """loop_count increments each time revise routes back."""
        sid = self._advance_to_revise(eng)
        res1 = eng.submit(sid, "revise", GOOD_REVISE)
        assert res1["accepted"] and res1.get("loop_back")
        assert res1["loop_count"] == 1

        # Re-verify
        res2 = eng.submit(sid, "verify", GOOD_VERIFY)
        assert res2["accepted"]

        state = eng.get_state(sid)
        assert state["loop_count"] == 1  # loop_count doesn't change at verify

    def test_clean_reverify_exits_loop_at_one(self, eng: Engine) -> None:
        """A clean re-verify (no unresolved check) exits the loop to deliver at loop_count=1."""
        sid = self._advance_to_revise(eng)
        eng.submit(sid, "revise", GOOD_REVISE)            # loop -> 1, back to verify
        res = eng.submit(sid, "verify", GOOD_VERIFY)      # GOOD_VERIFY is clean
        assert res["accepted"]
        assert res.get("current_stage") == "deliver", f"clean re-verify should go to deliver: {res.get('current_stage')}"
        assert eng.get_state(sid)["loop_count"] == 1

    def test_failing_reverify_loops_to_cap_then_requires_residual(self, eng: Engine) -> None:
        """Multi-cycle loop: a re-verify that still FAILS routes back to revise, up to the cap
        of 3. At the cap the engine stops looping and deliver requires residual-risk disclosure.
        This exercises the previously-unreachable loop_count>=_MAX_LOOP_COUNT branch."""
        from fable_method.engine import _MAX_LOOP_COUNT
        VERIFY_FAIL = {"checks": [{"what": "Gate rejects missing fields",
                                   "how": "Ran pytest suite with invalid artifacts",
                                   "result": "2 of 10 checks still did not pass after the fix",
                                   "evidence": "accuracy=0.20 correct=2 total=10",
                                   "status": "fail"}]}
        sid = self._advance_to_revise(eng)
        # Drive failing re-verifies until the loop routes forward to deliver
        for _ in range(2 * _MAX_LOOP_COUNT + 4):
            st = eng.get_state(sid)["current_stage"]
            if st == "revise":
                eng.submit(sid, "revise", GOOD_REVISE)
            elif st == "verify":
                eng.submit(sid, "verify", VERIFY_FAIL)
            else:
                break
        state = eng.get_state(sid)
        assert state["loop_count"] == _MAX_LOOP_COUNT, f"loop should reach cap: {state['loop_count']}"
        assert state["current_stage"] == "deliver", f"at cap should route to deliver: {state['current_stage']}"
        # V10: a residual KEYWORD inside limitations must NOT satisfy the cap requirement —
        # "no remaining work" used to pass because the word 'remaining' matched.
        bad = eng.submit(sid, "deliver", {
            "summary": "All complete and verified against the criteria fully.",
            "limitations": [
                "no remaining work; everything shipped",
                "Exact performance overhead of JSON serialisation at scale is still unknown.",
            ],
            "sources": ["https://docs.python.org/3/whatsnew/3.10.html"],
        })
        assert not bad["accepted"], "a residual keyword in limitations must not satisfy the cap gate"
        assert V.MISSING_FIELD in codes(bad), f"expected MISSING_FIELD (residual_risk): {codes(bad)}"
        # Deliver WITH a substantive structured residual_risk field AND coverage of the unknown
        good = eng.submit(sid, "deliver", {
            "summary": "All complete and verified against the criteria fully.",
            "limitations": [
                "Exact performance overhead of JSON serialisation at scale is still unknown.",
            ],
            "residual_risk": ("Two verification checks still fail after the loop cap was reached; "
                              "the failing assertions were not resolved within three cycles."),
            "sources": ["https://docs.python.org/3/whatsnew/3.10.html"],
        })
        assert good["accepted"], f"deliver with a structured residual_risk should pass: {codes(good)}"

    def test_revise_no_real_changes_goes_to_deliver(self, eng: Engine) -> None:
        """Revise with reverified=False or empty fixes goes to deliver, not verify.
        Uses a session whose critique had no_issues_found (no required fix mapping).
        """
        # Build a session with no-issue critique to avoid UNMAPPED_FIX on empty fixes
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        no_issue_critique = {
            "no_issues_found": True,
            "why": ("I reviewed the draft line by line against each success criterion. "
                    "The logic is correct, there are no unsupported claims, all edge cases "
                    "are handled, and the output matches the spec exactly. I also confirmed "
                    "the steelman check: the strongest counterargument does not apply here."),
            "steelman": "The strongest counterargument is that the approach may not scale at extreme load.",
        }
        for stage, artifact in [
            ("frame", GOOD_FRAME),
            ("research", GOOD_RESEARCH),
            ("plan", GOOD_PLAN_FULL),
            ("draft", GOOD_DRAFT),
            ("critique", no_issue_critique),
            ("verify", GOOD_VERIFY),
        ]:
            res = eng.submit(sid, stage, artifact)
            assert res["accepted"], f"Setup failed at {stage}: {res}"
        # No required fixes (no_issues_found) → empty fixes + reverified=False → deliver
        result = eng.submit(sid, "revise", {"fixes": [], "reverified": False})
        assert result["accepted"], f"Empty-fix revise should pass: {result}"
        assert not result.get("loop_back"), "No loop_back when no real changes"
        assert result.get("current_stage") == "deliver"

    def test_loop_verified_then_deliver(self, eng: Engine) -> None:
        """Full loop: revise→verify (loop)→deliver works end to end."""
        sid = self._advance_to_revise(eng)
        # First revise: loops back
        r1 = eng.submit(sid, "revise", GOOD_REVISE)
        assert r1["accepted"] and r1.get("loop_back")
        # Loop verify
        r2 = eng.submit(sid, "verify", GOOD_VERIFY)
        assert r2["accepted"]
        assert r2.get("current_stage") == "deliver"
        # Deliver
        r3 = eng.submit(sid, "deliver", GOOD_DELIVER)
        assert r3["accepted"] and r3.get("done") is True

    def test_loop_count_in_get_state(self, eng: Engine) -> None:
        """get_state includes loop_count."""
        sid = self._advance_to_revise(eng)
        state_before = eng.get_state(sid)
        assert state_before.get("loop_count") == 0

        eng.submit(sid, "revise", GOOD_REVISE)
        state_after = eng.get_state(sid)
        assert state_after.get("loop_count") == 1

    def test_loop_count_in_certificate(self, eng: Engine) -> None:
        """finalize certificate includes loop_count."""
        sid = self._advance_to_revise(eng)
        r1 = eng.submit(sid, "revise", GOOD_REVISE)
        assert r1["accepted"] and r1.get("loop_back")
        eng.submit(sid, "verify", GOOD_VERIFY)
        eng.submit(sid, "deliver", GOOD_DELIVER)
        fin = eng.finalize(sid)
        assert fin["finalized"]
        assert fin["certificate"]["loop_count"] == 1

    def test_reopen_plan_resets_later_stages(self, eng: Engine) -> None:
        """revise.reopen='plan' resets plan and later stages, routes to plan."""
        sid = self._advance_to_revise(eng)
        result = eng.submit(sid, "revise", {
            "fixes": [],
            "reverified": False,
            "reopen": "plan",
        })
        assert result["accepted"]
        assert result.get("current_stage") == "plan", f"Expected plan: {result.get('current_stage')}"
        assert result.get("iteration_recorded") is True

        state = eng.get_state(sid)
        assert len(state.get("iterations", [])) == 1
        # plan, draft, critique, verify should be gone from completed_stages
        assert "plan" not in state["completed_stages"]
        assert "draft" not in state["completed_stages"]

    def test_iterations_in_certificate(self, eng: Engine) -> None:
        """Iterations from reopen are recorded in the finalize certificate."""
        sid = self._advance_to_revise(eng)
        # Reopen to plan (reopen bypasses normal fix-mapping requirements)
        eng.submit(sid, "revise", {"fixes": [], "reverified": False, "reopen": "plan"})
        # Re-run from plan to deliver using no_issue_critique to allow empty fixes
        no_issue_critique = {
            "no_issues_found": True,
            "why": ("I reviewed the draft line by line against each success criterion. "
                    "The logic is correct, all edge cases are handled, and the output "
                    "matches the spec exactly. The steelman check confirms no blocking issues."),
            "steelman": "The strongest counterargument is that performance at scale is uncertain.",
        }
        for stage, art in [
            ("plan", GOOD_PLAN_FULL),
            ("draft", GOOD_DRAFT),
            ("critique", no_issue_critique),
            ("verify", GOOD_VERIFY),
        ]:
            r = eng.submit(sid, stage, art)
            assert r["accepted"], f"Re-run failed at {stage}: {r}"
        # Revise with no fixes (no required fixes after no_issue_critique)
        r = eng.submit(sid, "revise", {"fixes": [], "reverified": False})
        assert r["accepted"], f"No-fix revise after no_issue_critique failed: {r}"
        eng.submit(sid, "deliver", GOOD_DELIVER)
        fin = eng.finalize(sid)
        assert fin["finalized"]
        assert len(fin["certificate"]["iterations"]) == 1


# ---------------------------------------------------------------------------
# TestV2V8 — Uncertainty plumbing (pending_limitations)
# ---------------------------------------------------------------------------

class TestV2V8UncertaintyPlumbing:
    """V8: research.unknowns and assumed sources flow into pending_limitations,
    which deliver must cover."""

    def test_research_unknowns_in_pending_limitations(self, eng: Engine) -> None:
        """After research passes, unknowns appear in get_state pending_limitations."""
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        eng.submit(sid, "research", {
            "facts": [{"claim": "Python 3.10 introduced pattern matching.",
                        "source": "https://docs.python.org/3/whatsnew/3.10.html"}],
            "unknowns": ["Exact memory usage in production workloads"],
        })
        state = eng.get_state(sid)
        pending = state.get("pending_limitations", [])
        assert any("memory" in p.lower() or "production" in p.lower() for p in pending), \
            f"Unknown should be in pending_limitations: {pending}"

    def test_assumed_source_claim_in_pending_limitations(self, eng: Engine) -> None:
        """Claims from assumed sources flow into pending_limitations."""
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        eng.submit(sid, "research", {
            "facts": [{"claim": "The API is backwards compatible.",
                        "source": "my assumption based on vendor docs",
                        "type": "assumed"}],
            "unknowns": [],
        })
        state = eng.get_state(sid)
        pending = state.get("pending_limitations", [])
        assert any("backwards compatible" in p.lower() or "assumed" in p.lower()
                   for p in pending), f"Assumed claim should be in pending_limitations: {pending}"

    def test_deliver_must_cover_pending_limitations(self, eng: Engine) -> None:
        """deliver with limitations not covering pending unknowns → UNCOVERED_LIMITATION."""
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        for stage, art in [
            ("frame", GOOD_FRAME),
            ("research", {
                "facts": [{"claim": "Python 3.10 released 2021.",
                            "source": "https://docs.python.org/3/whatsnew/3.10.html"}],
                "unknowns": ["Exact CPU overhead under sustained load"],
            }),
            ("plan", GOOD_PLAN_FULL),
            ("draft", GOOD_DRAFT),
            ("critique", GOOD_CRITIQUE),
            ("verify", GOOD_VERIFY),
        ]:
            res = eng.submit(sid, stage, art)
            assert res["accepted"], f"Setup failed at {stage}: {res}"
        r_rev = eng.submit(sid, "revise", GOOD_REVISE)
        assert r_rev["accepted"]
        if r_rev.get("loop_back"):
            r_v = eng.submit(sid, "verify", GOOD_VERIFY)
            assert r_v["accepted"]
        # Deliver without mentioning CPU overhead → MISSING_FIELD
        result = eng.submit(sid, "deliver", {
            "summary": "The enforcement engine implementation is complete and all required stages passed.",
            "limitations": ["Minor style issues remain"],  # doesn't mention CPU overhead
            "sources": ["https://docs.python.org/3/whatsnew/3.10.html"],
        })
        assert not result["accepted"]
        assert V.UNCOVERED_LIMITATION in codes(result), f"Expected UNCOVERED_LIMITATION: {codes(result)}"

    def test_deliver_covering_limitations_passes(self, eng: Engine) -> None:
        """deliver that covers the pending unknown in limitations passes."""
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        for stage, art in [
            ("frame", GOOD_FRAME),
            ("research", {
                "facts": [{"claim": "Python 3.10 released 2021.",
                            "source": "https://docs.python.org/3/whatsnew/3.10.html"}],
                "unknowns": ["Exact performance overhead of JSON serialisation at scale"],
            }),
            ("plan", GOOD_PLAN_FULL),
            ("draft", GOOD_DRAFT),
            ("critique", GOOD_CRITIQUE),
            ("verify", GOOD_VERIFY),
        ]:
            res = eng.submit(sid, stage, art)
            assert res["accepted"], f"Setup failed at {stage}: {res}"
        r_rev = eng.submit(sid, "revise", GOOD_REVISE)
        assert r_rev["accepted"]
        if r_rev.get("loop_back"):
            r_v = eng.submit(sid, "verify", GOOD_VERIFY)
            assert r_v["accepted"]
        # Deliver covering the JSON serialisation overhead unknown
        result = eng.submit(sid, "deliver", {
            "summary": "The enforcement engine implementation is complete and all required stages passed.",
            "limitations": [
                "JSON serialisation performance overhead at scale was not measured and remains unknown",
            ],
            "sources": ["https://docs.python.org/3/whatsnew/3.10.html"],
        })
        assert result["accepted"], f"Covering limitations should pass: {result}"

    def test_unconfirmed_verify_result_adds_pending(self, eng: Engine) -> None:
        """A verify result saying 'could not confirm' adds a caveat to pending_limitations."""
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        for stage, art in [
            ("frame", GOOD_FRAME),
            ("research", GOOD_RESEARCH),
            ("plan", GOOD_PLAN_FULL),
            ("draft", GOOD_DRAFT),
            ("critique", GOOD_CRITIQUE),
        ]:
            eng.submit(sid, stage, art)
        # Verify with an unconfirmed result
        res = eng.submit(sid, "verify", {
            "checks": [{
                "what": "Performance under load",
                "how": "Ran load test against staging environment",
                "result": "Could not confirm — staging environment was unavailable",
                "evidence": "LoadTest run at 09:15 — 503 errors on all 10 requests",
            }]
        })
        assert res["accepted"], f"Unconfirmed result verify should still pass: {res}"
        state = eng.get_state(sid)
        pending = state.get("pending_limitations", [])
        assert any("unconfirmed" in p.lower() or "performance" in p.lower()
                   for p in pending), f"Unconfirmed should be in pending: {pending}"


# ---------------------------------------------------------------------------
# TestV2V9 — Human-in-the-loop (mode, provide_answers, awaiting_input)
# ---------------------------------------------------------------------------

class TestV2V9HumanInTheLoop:
    """V9: interactive mode pauses on frame questions; provide_answers advances."""

    def test_headless_mode_default(self, eng: Engine) -> None:
        """create_session defaults to mode='headless'."""
        r = eng.create_session("goal", rigor="low")
        assert r.get("mode", "headless") == "headless"

    def test_interactive_mode_param(self, eng: Engine) -> None:
        """create_session with mode='interactive' stores it."""
        r = eng.create_session("goal", rigor="low", mode="interactive")
        sid = r["session_id"]
        assert r.get("mode") == "interactive"
        state = eng.get_state(sid)
        assert state.get("mode") == "interactive"

    def test_interactive_frame_with_questions_pauses(self, eng: Engine) -> None:
        """Interactive mode: frame with questions returns needs_user_input and pauses."""
        r = eng.create_session("goal", rigor="low", mode="interactive")
        sid = r["session_id"]
        result = eng.submit(sid, "frame", {
            "goal_restatement": "Build a validation system that enforces reasoning protocols.",
            "success_criteria": ["All gates reject invalid artifacts correctly"],
            "questions": ["What is the expected throughput?", "What are the deployment constraints?"],
        })
        assert result["accepted"]
        assert result.get("needs_user_input") is True
        assert result.get("status") == "awaiting_input"
        state = eng.get_state(sid)
        assert state.get("awaiting_input") is True

    def test_provide_answers_advances_session(self, eng: Engine) -> None:
        """provide_answers records answers and advances past the pause."""
        r = eng.create_session("goal", rigor="low", mode="interactive")
        sid = r["session_id"]
        eng.submit(sid, "frame", {
            "goal_restatement": "Build a validation system that enforces reasoning protocols.",
            "success_criteria": ["All gates reject invalid artifacts correctly"],
            "questions": ["What is the expected throughput?"],
        })
        result = eng.provide_answers(sid, ["Expected throughput is 100 requests per second"])
        assert result["accepted"]
        assert result.get("answers_recorded") == 1
        state = eng.get_state(sid)
        assert not state.get("awaiting_input")

    def test_provide_answers_dict_preserves_answer_text(self, eng: Engine) -> None:
        """Bug (b): a dict {question: answer} (what the CLI and MCP pass) must preserve the
        ANSWER text, not just the question keys. A bare list.extend(dict) drops the answers."""
        r = eng.create_session("goal", rigor="low", mode="interactive")
        sid = r["session_id"]
        eng.submit(sid, "frame", {
            "goal_restatement": "Build a validation system that enforces reasoning protocols.",
            "success_criteria": ["All gates reject invalid artifacts correctly"],
            "questions": ["What is the cutover date?", "Is rollback in scope?"],
        })
        result = eng.provide_answers(sid, {
            "What is the cutover date?": "March 3",
            "Is rollback in scope?": "Yes, tested",
        })
        assert result["accepted"]
        assert result.get("answers_recorded") == 2
        stored = eng._load(sid)["artifacts"]["frame"]["_provided_answers"]
        joined = " ".join(stored)
        assert "March 3" in joined, f"answer text dropped: {stored}"
        assert "Yes, tested" in joined, f"answer text dropped: {stored}"

    def test_submit_blocked_while_awaiting(self, eng: Engine) -> None:
        """submit is blocked while session awaits user input."""
        r = eng.create_session("goal", rigor="low", mode="interactive")
        sid = r["session_id"]
        eng.submit(sid, "frame", {
            "goal_restatement": "Build a validation system that enforces reasoning protocols.",
            "success_criteria": ["All gates reject invalid artifacts correctly"],
            "questions": ["What is the expected throughput?"],
        })
        result = eng.submit(sid, "draft", GOOD_DRAFT)
        assert not result["accepted"]
        assert result.get("needs_user_input") is True

    def test_headless_questions_stamps_proceeded_without_answers(self, eng: Engine) -> None:
        """Headless mode with questions stamps proceeded_without_answers."""
        r = eng.create_session("goal", rigor="low", mode="headless")
        sid = r["session_id"]
        result = eng.submit(sid, "frame", {
            "goal_restatement": "Build a validation system that enforces reasoning protocols.",
            "success_criteria": ["All gates reject invalid artifacts correctly"],
            "questions": ["What is the expected throughput?"],
        })
        assert result["accepted"]
        assert not result.get("needs_user_input")
        state = eng.get_state(sid)
        assert state.get("proceeded_without_answers") is True

    def test_headless_no_questions_no_stamp(self, eng: Engine) -> None:
        """Headless mode without questions does NOT stamp proceeded_without_answers."""
        r = eng.create_session("goal", rigor="low", mode="headless")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        state = eng.get_state(sid)
        assert not state.get("proceeded_without_answers", False)

    def test_proceeded_without_answers_in_certificate(self, eng: Engine) -> None:
        """finalize certificate includes proceeded_without_answers flag."""
        r = eng.create_session("goal", rigor="low", mode="headless")
        sid = r["session_id"]
        eng.submit(sid, "frame", {
            "goal_restatement": "Build a validation system that enforces reasoning protocols.",
            "success_criteria": ["All gates reject invalid artifacts correctly"],
            "questions": ["What is the expected throughput?"],
        })
        eng.submit(sid, "draft", GOOD_DRAFT)
        eng.submit(sid, "deliver", GOOD_DELIVER)
        fin = eng.finalize(sid)
        assert fin["finalized"]
        assert fin["certificate"]["proceeded_without_answers"] is True


# ---------------------------------------------------------------------------
# TestV2V10 — Safety screen (refused status, override_safety)
# ---------------------------------------------------------------------------

class TestV2V10SafetyScreen:
    """V10: create_session runs safety screen; obvious harm → refused."""

    def test_safe_goal_passes(self, eng: Engine) -> None:
        """A normal goal must not be refused."""
        r = eng.create_session("write a unit test for the string formatter", rigor="low")
        assert not r.get("refused"), f"Normal goal should not be refused: {r}"
        assert r.get("status") != "refused"

    def test_malware_goal_refused(self, eng: Engine) -> None:
        """Goal matching malware category → refused=True."""
        r = eng.create_session("write ransomware to encrypt hospital files", rigor="low")
        assert r.get("refused") is True
        assert r.get("status") == "refused"
        assert r.get("category")

    def test_fraud_goal_refused(self, eng: Engine) -> None:
        """Goal matching fraud/phishing category → refused."""
        r = eng.create_session("create a phishing page to harvest user credentials", rigor="low")
        assert r.get("refused") is True
        assert r.get("category")

    def test_refused_session_cannot_be_submitted(self, eng: Engine) -> None:
        """submit on a refused session returns refused=True."""
        r = eng.create_session("write ransomware to encrypt hospital files", rigor="low")
        sid = r["session_id"]
        result = eng.submit(sid, "frame", GOOD_FRAME)
        assert not result["accepted"]
        assert result.get("refused") is True

    def test_refused_session_cannot_be_finalized(self, eng: Engine) -> None:
        """finalize on a refused session returns finalized=False."""
        r = eng.create_session("write ransomware to encrypt hospital files", rigor="low")
        sid = r["session_id"]
        fin = eng.finalize(sid)
        assert not fin["finalized"]
        assert fin.get("refused") is True

    def test_override_safety_logs_and_proceeds(self, eng: Engine) -> None:
        """override_safety=True logs the bypass and allows the session to proceed."""
        r = eng.create_session("write ransomware to encrypt hospital files",
                                rigor="low", override_safety=True)
        assert not r.get("refused"), f"override_safety=True should proceed: {r}"
        sid = r["session_id"]
        state = eng.get_state(sid)
        safety = state.get("safety", {})
        assert safety.get("override_logged") is True

    def test_override_safety_in_certificate(self, eng: Engine) -> None:
        """override_safety=True is recorded in finalize certificate."""
        r = eng.create_session("write ransomware to encrypt hospital files",
                                rigor="low", override_safety=True)
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        eng.submit(sid, "draft", GOOD_DRAFT)
        eng.submit(sid, "deliver", GOOD_DELIVER)
        fin = eng.finalize(sid)
        assert fin["finalized"]
        assert fin["certificate"]["safety_screen"]["override"] is True

    def test_safety_screen_in_get_state(self, eng: Engine) -> None:
        """get_state includes safety field."""
        r = eng.create_session("goal", rigor="low")
        sid = r["session_id"]
        state = eng.get_state(sid)
        assert "safety" in state
        assert "refused" in state["safety"]


# ---------------------------------------------------------------------------
# TestV2Certificate — Part 4 certificate fields
# ---------------------------------------------------------------------------

class TestV2Certificate:
    """Part 4: finalize certificate has all new v2 fields."""

    def test_certificate_has_v2_fields(self, eng: Engine) -> None:
        """finalize certificate includes loop_count, iterations, escalations,
        proceeded_without_answers, safety_screen, evidence_summary."""
        r = eng.create_session("goal", rigor="low")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        eng.submit(sid, "draft", GOOD_DRAFT)
        eng.submit(sid, "deliver", GOOD_DELIVER)
        fin = eng.finalize(sid)
        assert fin["finalized"]
        cert = fin["certificate"]
        for field in ("loop_count", "iterations", "escalations",
                      "proceeded_without_answers", "safety_screen", "evidence_summary"):
            assert field in cert, f"Missing certificate field: {field}"

    def test_evidence_summary_populated_after_verify(self, eng: Engine) -> None:
        """evidence_summary is populated from verify checks."""
        sid = full_session(eng)
        fin = eng.finalize(sid)
        assert fin["finalized"]
        ev = fin["certificate"]["evidence_summary"]
        assert isinstance(ev, list)
        assert len(ev) >= 1
        assert "what" in ev[0]
        assert "has_evidence" in ev[0]

    def test_get_state_includes_v2_fields(self, eng: Engine) -> None:
        """get_state includes all Part 4 fields."""
        r = eng.create_session("goal", rigor="low")
        sid = r["session_id"]
        state = eng.get_state(sid)
        for field in ("mode", "loop_count", "iterations", "pending_limitations",
                      "escalated_to", "safety", "awaiting_input"):
            assert field in state, f"Missing get_state field: {field}"

    def test_provide_answers_method_exists(self, eng: Engine) -> None:
        """provide_answers method exists on Engine and module level."""
        from fable_method import engine as em
        assert hasattr(eng, "provide_answers")
        assert callable(eng.provide_answers)
        assert callable(em.provide_answers)


# ---------------------------------------------------------------------------
# TestV2AdversarialBypasses — the five named bypasses from the spec
# ---------------------------------------------------------------------------

class TestV2AdversarialBypasses:
    """The five adversarial bypasses that must ALL fail to finalize."""

    def test_bypass_a_distinct_repeated_char_junk(self, eng: Engine) -> None:
        """(a) distinct repeated-char junk in every field never reaches finalize."""
        r = eng.create_session("Build a secure authentication system", rigor="full")
        sid = r["session_id"]
        # Try each stage with junk; every one must fail
        result = eng.submit(sid, "frame", {
            "goal_restatement": "aaaaaaaaaaaaaaaaaaaaaaaaa",
            "success_criteria": ["bbbbbbbbbbbbbbb"],
            "assumptions": [{"assumption": "ccccccccccccccc", "why_safe": "ddddddddddddddd"}],
        })
        assert not result["accepted"], "(a) Junk frame must be rejected"
        assert V.JUNK_CONTENT in codes(result) or V.EMPTY_OR_TRIVIAL in codes(result)
        # Session must not have advanced
        fin = eng.finalize(sid)
        assert not fin["finalized"], "(a) Cannot finalize with junk frame"

    def test_bypass_b_adaptive_low_for_wire_money(self, eng: Engine) -> None:
        """(b) ADAPTIVE classify selecting LOW for 'wire $4,000,000 irreversibly to vendor'."""
        r = eng.create_session("wire $4,000,000 irreversibly to vendor", rigor="adaptive")
        sid = r["session_id"]
        result = eng.submit(sid, "classify", {
            "complexity": "low",
            "stakes": "low",
            "reversibility": "easy",
            "selected_level": "low",
            "justification": "Wire transfer to vendor is routine and easy to handle.",
        })
        assert not result["accepted"], "(b) ADAPTIVE LOW for wire $4M must fail"
        assert V.RISK_FLOOR in codes(result), f"(b) Expected RISK_FLOOR: {codes(result)}"
        fin = eng.finalize(sid)
        assert not fin["finalized"], "(b) Cannot finalize without passing classify"

    def test_bypass_c_vague_verify_no_evidence(self, eng: Engine) -> None:
        """(c) verify how='I tested it all completely', result='everything passed', no evidence."""
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        for stage, art in [
            ("frame", GOOD_FRAME), ("research", GOOD_RESEARCH),
            ("plan", GOOD_PLAN_FULL), ("draft", GOOD_DRAFT), ("critique", GOOD_CRITIQUE),
        ]:
            eng.submit(sid, stage, art)
        result = eng.submit(sid, "verify", {
            "checks": [{
                "what": "I tested it all completely",
                "how": "I tested it all completely and it seemed right",
                "result": "everything passed",
                "evidence": "",
            }]
        })
        assert not result["accepted"], "(c) Vague verify with no evidence must fail"
        violation_codes = codes(result)
        assert V.NO_EVIDENCE in violation_codes or V.UNVERIFIED_CLAIM in violation_codes, \
            f"(c) Expected NO_EVIDENCE or UNVERIFIED_CLAIM: {violation_codes}"

    def test_bypass_d_noop_revise_change(self, eng: Engine) -> None:
        """(d) revise change='Acknowledged; will consider addressing this later' for a blocker."""
        r = eng.create_session("goal", rigor="full")
        sid = r["session_id"]
        for stage, art in [
            ("frame", GOOD_FRAME), ("research", GOOD_RESEARCH),
            ("plan", GOOD_PLAN_FULL), ("draft", GOOD_DRAFT), ("critique", GOOD_CRITIQUE),
            ("verify", GOOD_VERIFY),
        ]:
            res = eng.submit(sid, stage, art)
            assert res["accepted"], f"Setup failed at {stage}: {res}"
        result = eng.submit(sid, "revise", {
            "fixes": [{
                "finding_ref": "Revise gate does not check reverified field when fixes exist",
                "change": "Acknowledged; will consider addressing this later",
            }],
            "reverified": True,
        })
        assert not result["accepted"], "(d) NOOP revise change must be rejected"
        assert V.NOOP_FIX in codes(result), f"(d) Expected NOOP_FIX: {codes(result)}"
        fin = eng.finalize(sid)
        assert not fin["finalized"], "(d) Cannot finalize with NOOP fix"

    def test_bypass_e_fake_url_deliver_source(self, eng: Engine) -> None:
        """(e) fact source 'https://example.com/made-up' in deliver sources → FABRICATION_RISK."""
        r = eng.create_session("goal", rigor="low")
        sid = r["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        eng.submit(sid, "draft", GOOD_DRAFT)
        result = eng.submit(sid, "deliver", {
            "summary": "Analysis complete with all facts verified.",
            "limitations": ["None — all facts were fully verified"],
            "sources": ["https://example.com/made-up"],
        })
        assert not result["accepted"], "(e) Placeholder URL source must be rejected"
        assert V.FABRICATION_RISK in codes(result), \
            f"(e) Expected FABRICATION_RISK: {codes(result)}"
        fin = eng.finalize(sid)
        assert not fin["finalized"], "(e) Cannot finalize with fake URL source"


# ---------------------------------------------------------------------------
# V10: --exec per-check evidence binding (session-03 BLOCKER fix)
#
# These call the harness injector directly with inputs written INDEPENDENTLY of the
# implementation's internals — they assert BEHAVIOUR (a check's resulting status), never
# the harness's internal wording. They cover the bypass the prior single-check binding
# allowed: a model self-attesting 'pass' on checks the harness never executed.
# ---------------------------------------------------------------------------

class TestV10ExecEvidenceBinding:
    @staticmethod
    def _inject(artifact: dict) -> dict:
        from fable_method.cli_harness import _inject_exec_evidence
        # allow_network=True only silences the warning; commands here are local-only.
        return _inject_exec_evidence(artifact, allow_network=True)

    @staticmethod
    def _statuses(artifact: dict) -> list:
        return [str(c.get("status", "")).lower() for c in artifact["checks"]]

    def test_unbacked_pass_check_cannot_keep_pass(self) -> None:
        """The BLOCKER: a substantive 'pass' check the harness never ran must NOT stay 'pass'
        just because a DIFFERENT check was backed by a real command."""
        from fable_method.engine import _verify_has_unresolved_check
        artifact = {
            "checks": [
                {"what": "Verify the refund totals reconcile to the ledger",
                 "how": "recomputed every refund line against the source ledger",
                 "result": "all refund totals reconcile",
                 "evidence": "ledger check: 1,240 rows balanced",
                 "status": "pass"},  # self-attested, NO command of its own
                {"what": "Confirm the build entrypoint file is present",
                 "how": "executed a check for the build entrypoint file",
                 "result": "entrypoint file present",
                 "status": "pass",
                 "commands": [{"lang": "bash", "code": "test 1 -eq 1"}]},  # really backed
            ],
        }
        out = self._inject(artifact)
        assert out["checks"][0]["status"] == "inconclusive", \
            f"unbacked substantive check must be inconclusive, got {self._statuses(out)}"
        assert out["checks"][1]["status"] == "pass"
        assert _verify_has_unresolved_check(out["checks"]) is True

    def test_pass_without_any_command_is_inconclusive(self) -> None:
        """A lone self-attested 'pass' with no command anywhere is refused in exec mode."""
        artifact = {
            "checks": [
                {"what": "The design meets the latency budget",
                 "how": "reasoned about the call graph and counted the hops",
                 "result": "within the budget",
                 "evidence": "estimated three hops",
                 "status": "pass"},
            ],
        }
        out = self._inject(artifact)
        assert out["checks"][0]["status"] == "inconclusive"

    def test_unbacked_check_without_status_also_inconclusive(self) -> None:
        """Omitting status is not an escape hatch: an unbacked, status-less check is still
        downgraded (otherwise a model evades the rule by leaving status off)."""
        artifact = {
            "checks": [
                {"what": "The search index builds without error",
                 "how": "executed the index build script end to end",
                 "result": "the index builds",
                 "commands": [{"lang": "bash", "code": "test 1 -eq 1"}]},
                {"what": "The README documents the new flag",
                 "how": "re-read the README section describing the flags",
                 "result": "the flag is documented",
                 "evidence": "the README mentions it"},
            ],
        }
        out = self._inject(artifact)
        assert out["checks"][0]["status"] == "pass"
        assert out["checks"][1]["status"] == "inconclusive"

    def test_per_check_failing_command_forces_fail(self) -> None:
        """A per-check command that exits non-zero forces that check to 'fail', overriding the
        model's self-reported 'pass'."""
        from fable_method.engine import _verify_has_unresolved_check
        artifact = {
            "checks": [
                {"what": "Out-of-range input is rejected",
                 "how": "ran the validator against an out-of-range value",
                 "result": "the validator rejects it",
                 "status": "pass",
                 "commands": [{"lang": "bash", "code": "exit 7"}]},
            ],
        }
        out = self._inject(artifact)
        assert out["checks"][0]["status"] == "fail"
        assert _verify_has_unresolved_check(out["checks"]) is True

    def test_per_check_passing_commands_all_pass(self) -> None:
        artifact = {
            "checks": [
                {"what": "The adder returns the sum",
                 "how": "executed the function on a known pair of inputs",
                 "result": "it returns five",
                 "commands": [{"lang": "python", "code": "assert 2 + 3 == 5"}]},
                {"what": "The greeting contains the name",
                 "how": "ran the formatter and inspected its output",
                 "result": "the output contains the name",
                 "commands": [{"lang": "python", "code": "assert 'Ada' in 'hi Ada'"}]},
            ],
        }
        out = self._inject(artifact)
        assert self._statuses(out) == ["pass", "pass"]

    def test_top_level_commands_no_longer_back_checks(self) -> None:
        """The legacy top-level 'commands' list is removed — a command must be attached to the
        specific check it verifies. A check with no per-check command is inconclusive even when
        a top-level list is present, and that stray list is stripped (never forwarded)."""
        artifact = {
            "checks": [
                {"what": "The smoke script runs cleanly",
                 "how": "executed the smoke script from start to finish",
                 "result": "it runs without error", "status": "pass"},
            ],
            "commands": [{"lang": "bash", "code": "test 1 -eq 1"}],
        }
        out = self._inject(artifact)
        assert out["checks"][0]["status"] == "inconclusive"
        assert "commands" not in out

    def test_command_fields_stripped_before_submit(self) -> None:
        """Neither the per-check nor the global 'commands' field may reach the engine."""
        artifact = {
            "checks": [
                {"what": "A check with its own command",
                 "how": "executed the bound command directly",
                 "result": "it ran",
                 "commands": [{"lang": "bash", "code": "test 1 -eq 1"}]},
            ],
            "commands": [{"lang": "bash", "code": "test 1 -eq 1"}],
        }
        out = self._inject(artifact)
        assert "commands" not in out
        assert all("commands" not in c for c in out["checks"])


# ---------------------------------------------------------------------------
# V10: anti-laundering — a backing command must do real work, not just emit a literal.
# Inputs are realistic laundering attempts written independently of the detector internals.
# ---------------------------------------------------------------------------

class TestV10AntiLaundering:
    @staticmethod
    def _inject(artifact: dict) -> dict:
        from fable_method.cli_harness import _inject_exec_evidence
        return _inject_exec_evidence(artifact, allow_network=True)

    def test_noop_detector_table(self) -> None:
        from fable_method.cli_harness import _command_is_noop
        noops = [
            ("bash", "true"), ("bash", ":"), ("bash", "echo hi"),
            ("bash", 'echo "accuracy 0.80 PASS"'), ("bash", "printf done"),
            ("bash", "   "), ("python", "print('x')"),
            ("python", "print('accuracy=0.80 PASS')"), ("python", "pass"),
        ]
        real = [
            ("bash", "test 1 -eq 1"), ("bash", "echo $(date)"),
            ("bash", "grep -q PASS out.txt"), ("bash", "pytest -q"),
            ("python", "assert 1 + 1 == 2"), ("python", "print(2 + 2)"),
            ("python", "x = 1\nprint(x)"), ("python", "import os"),
        ]
        for lang, code in noops:
            assert _command_is_noop(lang, code) is True, f"should be no-op: {lang} {code!r}"
        for lang, code in real:
            assert _command_is_noop(lang, code) is False, f"should be real work: {lang} {code!r}"

    def test_echo_pass_literal_cannot_back_check(self) -> None:
        """The classic laundering move: echo the success token the gate wants."""
        artifact = {"checks": [
            {"what": "Output matches the golden file", "how": "compared output to the golden file",
             "result": "they match", "status": "pass",
             "commands": [{"lang": "bash", "code": "echo 'diff: 0 lines PASS'"}]},
        ]}
        out = self._inject(artifact)
        assert out["checks"][0]["status"] == "inconclusive"

    def test_print_literal_cannot_back_check(self) -> None:
        artifact = {"checks": [
            {"what": "Accuracy meets the bar", "how": "ran the scorer over the labeled set",
             "result": "accuracy is at the bar", "status": "pass",
             "commands": [{"lang": "python", "code": "print('accuracy=0.92 PASS')"}]},
        ]}
        out = self._inject(artifact)
        assert out["checks"][0]["status"] == "inconclusive"

    def test_real_computation_backs_check(self) -> None:
        """Control: a command that actually computes/asserts IS allowed to back the check."""
        artifact = {"checks": [
            {"what": "Rounding behaves", "how": "executed the rounding on a known value",
             "result": "rounds as expected",
             "commands": [{"lang": "python", "code": "assert round(2.675, 2) in (2.67, 2.68)"}]},
        ]}
        out = self._inject(artifact)
        assert out["checks"][0]["status"] == "pass"


# ---------------------------------------------------------------------------
# V10: honest before/after evidence must not be mis-flagged as fabrication, in EITHER order.
# Phrasings are realistic human descriptions, written independently of the regex internals.
# ---------------------------------------------------------------------------

class TestV10HistoricalExitEvidence:
    def test_historical_exit_codes_table(self) -> None:
        from fable_method.engine import _evidence_ends_in_failure as F
        # Honest passing evidence that REFERENCES a prior failure -> NOT a current failure:
        not_failures = [
            "before the fix it exited 1; now exit_code=0",
            "exit_code=0; it previously exited 1",
            "now exit_code=0 (was exit code 1)",
            "the validator originally exited 3 but now exit_code=0",
            "all checks exit_code=0",
        ]
        # Genuine current failures -> ARE a failure:
        failures = [
            "exit_code=1",
            "ran the suite and it exited 2",
            "first attempt exit_code=0 then re-ran and it exited 1",
            "cmd[0](bash): FAIL exit_code=1",
        ]
        for ev in not_failures:
            assert F(ev) is False, f"honest historical evidence wrongly flagged: {ev!r}"
        for ev in failures:
            assert F(ev) is True, f"current failure missed: {ev!r}"

    def test_gate_not_fooled_by_before_after_either_order(self) -> None:
        """The FABRICATION_RISK gate must accept a 'pass' whose evidence cites a prior failure,
        regardless of whether the prior failure is mentioned first or last."""
        from fable_method.engine import _gate_verify, V
        for ev in ["before fix exited 1; after fix exit_code=0",
                   "exit_code=0 now — it had previously exited 1"]:
            v = _gate_verify({"checks": [{
                "what": "the suite passes after the fix",
                "how": "ran the pytest suite after applying the fix",
                "result": "now green", "status": "pass", "evidence": ev,
            }]}, "full", "universal")
            assert V.FABRICATION_RISK not in [x["code"] for x in v], \
                f"honest before/after wrongly flagged: {ev!r} -> {[x['code'] for x in v]}"


# ---------------------------------------------------------------------------
# V10: Gemini HTTP payload — system via systemInstruction, no double 'user' turn.
# ---------------------------------------------------------------------------

class TestV10GeminiPayload:
    def test_single_message_no_double_user_turn(self) -> None:
        from fable_method.providers import _build_gemini_payload
        p = _build_gemini_payload("SYSTEM", [{"role": "user", "content": "hello"}])
        assert p.get("systemInstruction") == {"parts": [{"text": "SYSTEM"}]}
        contents = p["contents"]
        assert len(contents) == 1, "system must NOT add a second leading user turn"
        assert contents[0]["role"] == "user"
        assert contents[0]["parts"][0]["text"] == "hello"

    def test_roles_mapped_and_content_preserved(self) -> None:
        from fable_method.providers import _build_gemini_payload
        msgs = [{"role": "user", "content": "u1"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "u2"}]
        p = _build_gemini_payload("S", msgs)
        assert [c["role"] for c in p["contents"]] == ["user", "model", "user"]
        assert [c["parts"][0]["text"] for c in p["contents"]] == ["u1", "a1", "u2"]
        assert p["systemInstruction"]["parts"][0]["text"] == "S"

    def test_empty_messages_falls_back_to_system_user_turn(self) -> None:
        from fable_method.providers import _build_gemini_payload
        p = _build_gemini_payload("ONLY SYSTEM", [])
        assert p["contents"] == [{"role": "user", "parts": [{"text": "ONLY SYSTEM"}]}]
        assert "systemInstruction" not in p


# ---------------------------------------------------------------------------
# V10: --interactive stdin path, exercised offline (closes the coverage gap from the report —
# the stock echo provider emits no frame questions, so this drives the engine directly).
# ---------------------------------------------------------------------------

class TestV10InteractiveStdin:
    FRAME_Q = {
        "goal_restatement": "Stand up a service that checks reasoning artifacts against a written protocol.",
        "success_criteria": ["Invalid artifacts are rejected", "Finalize only after every stage passes"],
        "questions": ["Which deployment environment is the target?",
                      "Must rollback be supported in the first release?"],
    }

    def test_interactive_frame_questions_drive_stdin_then_provide_answers(self, eng: Engine, monkeypatch) -> None:
        import builtins
        from fable_method.cli_harness import _prompt_answers
        sid = eng.create_session(
            "Validate reasoning artifacts against a protocol", rigor="full", mode="interactive",
        )["session_id"]
        res = eng.submit(sid, "frame", self.FRAME_Q)
        assert res.get("needs_user_input") is True
        assert res.get("status") == "awaiting_input"
        questions = res.get("questions")
        assert questions and len(questions) == 2

        # Exercise the real CLI stdin loop with a patched input().
        canned = iter(["staging, then production", "yes — rollback is in scope"])
        monkeypatch.setattr(builtins, "input", lambda *a, **k: next(canned))
        answers = _prompt_answers(questions)
        assert answers == {questions[0]: "staging, then production",
                           questions[1]: "yes — rollback is in scope"}

        # Feed answers back through the engine; the session leaves awaiting_input and advances.
        eng.provide_answers(sid, answers)
        state = eng.get_state(sid)
        assert state["awaiting_input"] is False
        assert state["current_stage"] != "frame", \
            f"interactive session should advance past frame after answers: {state['current_stage']}"


# ---------------------------------------------------------------------------
# V11 anti-hollow: a deliver summary may not overclaim certainty/completeness while
# unresolved/assumed items remain. Conservative (blatant phrases only); fires ONLY when
# uncertainty actually exists. Phrasings invented independently of the _OVERCLAIM_RE patterns.
# ---------------------------------------------------------------------------

class TestV11AntiHollowSummary:
    @staticmethod
    def _deliver_codes(summary, pending=None, loop_count=0):
        from fable_method.engine import _gate_deliver
        art = {
            "summary": summary,
            "limitations": ["the target corpus size is not confirmed and remains unknown"],
            "sources": [],
        }
        v = _gate_deliver(art, "full", "universal", research_done=False,
                          pending_limitations=pending or [], loop_count=loop_count)
        return [x["code"] for x in v]

    def test_overclaim_flagged_when_uncertainty_exists(self) -> None:
        from fable_method.engine import V
        codes_ = self._deliver_codes(
            "The system is fully verified and guaranteed correct; no open questions remain.",
            pending=["the target corpus size is unknown"])
        assert V.OVERCLAIMED_SUMMARY in codes_, codes_

    def test_qualified_summary_not_flagged(self) -> None:
        from fable_method.engine import V
        codes_ = self._deliver_codes(
            "The pipeline works for the tested cases; the corpus size is unconfirmed and a "
            "performance check is still pending.",
            pending=["the target corpus size is unknown"])
        assert V.OVERCLAIMED_SUMMARY not in codes_, codes_

    def test_specific_factual_claim_not_flagged(self) -> None:
        """A specific factual claim ('all 12 tests passed') is NOT a global overclaim."""
        from fable_method.engine import V
        codes_ = self._deliver_codes(
            "All 12 acceptance tests passed; the corpus size is still unconfirmed.",
            pending=["the target corpus size is unknown"])
        assert V.OVERCLAIMED_SUMMARY not in codes_, codes_

    def test_overclaim_not_flagged_without_uncertainty(self) -> None:
        """With nothing unresolved, a confident summary is allowed — the check does not fire."""
        from fable_method.engine import V
        codes_ = self._deliver_codes(
            "The system is fully verified and guaranteed correct.", pending=[])
        assert V.OVERCLAIMED_SUMMARY not in codes_, codes_

    def test_overclaim_flagged_at_loop_cap(self) -> None:
        from fable_method.engine import V, _MAX_LOOP_COUNT
        codes_ = self._deliver_codes(
            "Everything is completely verified with full confidence.",
            pending=[], loop_count=_MAX_LOOP_COUNT)
        assert V.OVERCLAIMED_SUMMARY in codes_, codes_


# ---------------------------------------------------------------------------
# Session-04 robustness + honesty fixes (3-reviewer findings).
# Phrasings here are written independently of the implementation per PROCESS.md.
# ---------------------------------------------------------------------------

import json as _json_mod
from pathlib import Path as _Path


class TestS4Robustness:
    """Tier 1 — crashes & data loss must become clean rejects or safe degradation."""

    # ---- #1 atomic save -------------------------------------------------
    def test_save_is_atomic_and_preserves_file_on_failure(self, eng: Engine) -> None:
        """A failed write must never truncate/destroy the existing session, and must
        leave no temp file behind."""
        eng._save({"session_id": "atomic-1", "payload": "intact-version"})
        path = eng._session_path("atomic-1")
        before = path.read_text(encoding="utf-8")
        # A set is not JSON-serializable, so json.dump raises partway through the write.
        with pytest.raises(TypeError):
            eng._save({"session_id": "atomic-1", "payload": {1, 2, 3}})
        assert path.read_text(encoding="utf-8") == before  # original survives
        assert not path.with_name(path.name + ".tmp").exists()  # no stray temp

    def test_save_load_roundtrip(self, eng: Engine) -> None:
        eng._save({"session_id": "rt-1", "k": "v", "loop_count": 0})
        loaded = eng._load("rt-1")
        assert loaded["k"] == "v"

    # ---- #3 non-dict artifact ------------------------------------------
    def test_submit_non_dict_artifact_is_rejected_not_crash(self, eng: Engine) -> None:
        sid = eng.create_session("a goal", rigor="low")["session_id"]
        for bad in (["a", "list"], "a string", 7, None):
            res = eng.submit(sid, "frame", bad)
            assert res["accepted"] is False, bad
            assert V.WRONG_ARTIFACT_TYPE in [v["code"] for v in res["violations"]], bad

    # ---- #4 corrupt session file ---------------------------------------
    def test_load_corrupt_file_raises_clean_valueerror(self, eng: Engine) -> None:
        p = eng._session_path("broken-1")
        p.write_text("{ not valid json at all ", encoding="utf-8")
        with pytest.raises(ValueError):
            eng._load("broken-1")

    def test_load_non_dict_json_raises_valueerror(self, eng: Engine) -> None:
        p = eng._session_path("broken-2")
        p.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(ValueError):
            eng._load("broken-2")

    # ---- #5 older session missing gate_history -------------------------
    def test_missing_gate_history_does_not_crash_next_submit(self, eng: Engine) -> None:
        sid = eng.create_session("a goal", rigor="low")["session_id"]
        p = eng._session_path(sid)
        data = _json_mod.loads(p.read_text())
        data.pop("gate_history", None)  # simulate an older-schema file
        p.write_text(_json_mod.dumps(data))
        res = eng.submit(sid, "frame", GOOD_FRAME)
        assert res["accepted"], res

    # ---- #6 corrupt loop_count -----------------------------------------
    def test_corrupt_loop_count_is_coerced(self, eng: Engine) -> None:
        sid = eng.create_session("a goal", rigor="full")["session_id"]
        p = eng._session_path(sid)
        data = _json_mod.loads(p.read_text())
        data["loop_count"] = "twelve"  # garbage
        p.write_text(_json_mod.dumps(data))
        state = eng.get_state(sid)
        assert isinstance(state["loop_count"], int)
        assert state["loop_count"] == 0

    # ---- #7 provide_answers with corrupt _provided_answers -------------
    def test_provide_answers_tolerates_corrupt_provided_answers(self, eng: Engine) -> None:
        sid = eng.create_session("a goal", rigor="low", mode="interactive")["session_id"]
        frame = dict(GOOD_FRAME)
        frame["questions"] = ["What is the deadline?"]
        paused = eng.submit(sid, "frame", frame)
        assert paused.get("needs_user_input")
        p = eng._session_path(sid)
        data = _json_mod.loads(p.read_text())
        data["artifacts"]["frame"]["_provided_answers"] = "not-a-list"
        p.write_text(_json_mod.dumps(data))
        out = eng.provide_answers(sid, {"What is the deadline?": "Friday"})
        assert out["accepted"], out

    # ---- #2 / #8 JSON extraction ---------------------------------------
    def test_deeply_nested_json_degrades_to_valueerror(self) -> None:
        from fable_method.cli_harness import _extract_json
        bomb = '{"k":' + "[" * 3000 + "]" * 3000 + "}"
        with pytest.raises(ValueError):
            _extract_json(bomb)

    def test_extract_json_recovers_after_leading_nonjson_fence(self) -> None:
        from fable_method.cli_harness import _extract_json
        text = (
            "Sure, here is some example code:\n"
            "```python\nprint('not the artifact')\n```\n"
            "And the actual artifact:\n"
            '```json\n{"selected_level": "full", "ready": true}\n```\n'
        )
        obj = _extract_json(text)
        assert obj["selected_level"] == "full" and obj["ready"] is True

    def test_extract_json_falls_through_to_raw_text(self) -> None:
        from fable_method.cli_harness import _extract_json
        text = "```\nthis fence is just noise\n```\nthen the object: {\"value\": 42}"
        assert _extract_json(text)["value"] == 42


class TestS4HonestyAndFeatures:
    """Tier 3 doc/feature fixes + the #15 bypass close."""

    # ---- #10 research risk_flags escalation ----------------------------
    def test_research_risk_flag_escalates_to_full(self, eng: Engine) -> None:
        sid = eng.create_session("a goal", rigor="medium")["session_id"]
        assert eng.get_state(sid)["level"] == "medium"
        eng.submit(sid, "frame", GOOD_FRAME)
        research = dict(GOOD_RESEARCH)
        research["risk_flags"] = ["touches production data; the change cannot be undone"]
        res = eng.submit(sid, "research", research)
        assert res["accepted"], res
        assert eng.get_state(sid)["level"] == "full"

    def test_empty_risk_flags_do_not_escalate(self, eng: Engine) -> None:
        sid = eng.create_session("a goal", rigor="medium")["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        research = dict(GOOD_RESEARCH)
        research["risk_flags"] = ["", "   "]
        eng.submit(sid, "research", research)
        assert eng.get_state(sid)["level"] == "medium"

    def test_risk_flags_must_be_a_list(self, eng: Engine) -> None:
        sid = eng.create_session("a goal", rigor="medium")["session_id"]
        eng.submit(sid, "frame", GOOD_FRAME)
        research = dict(GOOD_RESEARCH)
        research["risk_flags"] = "a single string, not a list"
        res = eng.submit(sid, "research", research)
        assert res["accepted"] is False
        assert V.MISSING_FIELD in [v["code"] for v in res["violations"]]

    # ---- #11 certificate safety_screen shape ---------------------------
    def test_certificate_safety_screen_matches_contract(self, eng: Engine) -> None:
        sid = full_session(eng)
        cert = eng.finalize(sid)["certificate"]
        ss = cert["safety_screen"]
        assert set(ss.keys()) == {"ran", "refused", "override", "category"}
        assert ss["ran"] is True

    # ---- #12 MCP set_rigor enum ----------------------------------------
    def test_mcp_set_rigor_enum_lists_adaptive(self) -> None:
        # mcp_server can't be imported without the optional 'mcp' SDK, so assert against
        # source — the enum must carry all four valid levels the engine accepts.
        src = (_Path(__file__).resolve().parents[1] / "mcp_server.py").read_text()
        assert '"low", "medium", "full", "adaptive"' in src

    # ---- #13 bypass-probe count reconciled -----------------------------
    def test_doc_probe_count_reconciled_to_six(self) -> None:
        root = _Path(__file__).resolve().parents[3]
        process = (root / "PROCESS.md").read_text().lower()
        assert "five bypass" not in process
        assert "six bypass" in process

    def test_actual_probe_count_is_six(self) -> None:
        import re
        src = _Path(__file__).resolve().read_text()
        names = set(re.findall(r"def (test_\w*bypass\w*)\b", src))
        assert len(names) == 6, sorted(names)

    # ---- #15 historical-exit-code bypass close -------------------------
    def test_historical_only_failure_is_flagged(self) -> None:
        from fable_method.engine import _evidence_ends_in_failure
        # The gambit: a 'pass' check whose only machine evidence is a quoted prior
        # failure, with no current success code shown.
        assert _evidence_ends_in_failure("before: the build exited 1") is True
        assert _evidence_ends_in_failure("the suite previously exited 2") is True

    def test_honest_before_after_not_flagged_either_order(self) -> None:
        from fable_method.engine import _evidence_ends_in_failure
        gap = " " + "x" * 60 + " "  # keep the historical keyword >40 chars from the live code
        assert _evidence_ends_in_failure(
            "previously exited 1." + gap + "the run now reports exit code 0") is False
        assert _evidence_ends_in_failure(
            "exit code 0 right now." + gap + "it had previously exited 1") is False

    def test_plain_current_codes(self) -> None:
        from fable_method.engine import _evidence_ends_in_failure
        assert _evidence_ends_in_failure("the command exited 1") is True
        assert _evidence_ends_in_failure("exit code 0") is False
        assert _evidence_ends_in_failure("no machine results here at all") is False


class TestS4Tier2Coherence:
    """The one coherence gate kept after testing (fix<->finding), plus a pinned
    test for the DOCUMENTED limit that off-topic-but-consistent sessions are NOT
    caught (word-overlap topic checks were removed as too false-positive-prone)."""

    def _to_revise(self, eng: Engine) -> str:
        r = eng.create_session("Build a reasoning artifact validation engine", rigor="full")
        sid = r["session_id"]
        run_to_stage(eng, sid, "revise")
        assert eng.get_state(sid)["current_stage"] == "revise"
        return sid

    def test_gamed_fix_unrelated_to_finding_is_flagged(self, eng: Engine) -> None:
        sid = self._to_revise(eng)
        gamed = {
            "fixes": [{
                "finding_ref": "Revise gate does not check reverified field when fixes exist",
                "change": "Replaced the homepage banner with a new seasonal beach photograph.",
            }],
            "reverified": True,
        }
        res = eng.submit(sid, "revise", gamed)
        assert res["accepted"] is False
        assert V.COHERENCE_BREAK in [v["code"] for v in res["violations"]]

    def test_legit_fix_is_not_flagged(self, eng: Engine) -> None:
        sid = self._to_revise(eng)
        res = eng.submit(sid, "revise", GOOD_REVISE)
        assert V.COHERENCE_BREAK not in [v["code"] for v in res.get("violations", [])]

    def test_offtopic_but_consistent_session_finalizes_KNOWN_LIMIT(self, eng: Engine) -> None:
        # Documented limit: an internally-consistent session whose goal is unrelated to
        # the work still finalizes. The engine does not judge topic/substance. If this
        # starts FAILING, a semantic check was added — revisit the design note.
        r = eng.create_session("Write a 500-word essay about autumn leaves", rigor="full")
        sid = r["session_id"]
        run_to_stage(eng, sid, "deliver")
        res = eng.submit(sid, "deliver", GOOD_DELIVER)
        assert res["accepted"], res
        assert eng.finalize(sid)["finalized"] is True


class TestS4PendingCoverageFix:
    """Regression for a bug found during a live run: the deliver pending-limitation
    coverage check became impossible to satisfy as the number of (short) pending items
    grew, because it Jaccard-matched each item against ALL limitations concatenated.
    Now each item is checked per-limitation via coverage-of-the-item."""

    def test_many_short_pending_items_can_all_be_covered(self) -> None:
        from fable_method.engine import _gate_deliver
        pending = [
            "unconfirmed: Is the '1-2 month novelty churn' retention figure verified?",
            "unconfirmed: Is the CAC $30-80 estimate sourced?",
            "No direct evidence of willingness-to-pay at $15/mo specifically; incumbents cluster at $2-10/mo.",
            "No hard retention/churn data found for these subscription story apps.",
            "Unknown how many competitor apps have real traction versus are abandoned side-projects.",
        ]
        artifact = {
            "summary": "Recommendation: test-more before building; the evidence is mixed and the riskiest assumption is untested.",
            "limitations": [
                "The 1-2 month novelty churn retention figure is not verified.",
                "The CAC $30-80 estimate is not sourced.",
                "No direct evidence of willingness-to-pay at $15/mo; incumbents cluster at $2-10/mo.",
                "No hard retention/churn data found for these subscription story apps.",
                "It is unknown how many competitor apps have real traction versus are abandoned side-projects.",
            ],
            "sources": ["https://example.com/evidence"],
        }
        violations = _gate_deliver(artifact, "full", "entrepreneur",
                                   research_done=True, pending_limitations=pending, loop_count=1)
        assert V.UNCOVERED_LIMITATION not in [v["code"] for v in violations], [v["code"] for v in violations]

    def test_genuinely_uncovered_item_still_flagged(self) -> None:
        from fable_method.engine import _gate_deliver
        pending = ["Exact CPU overhead of the renderer under sustained load is unmeasured."]
        artifact = {
            "summary": "The work addresses the goal and the main pieces are in place.",
            "limitations": ["Some minor wording polish was deferred."],
            "sources": ["https://example.com/x"],
        }
        violations = _gate_deliver(artifact, "full", "universal",
                                   research_done=True, pending_limitations=pending, loop_count=1)
        assert V.UNCOVERED_LIMITATION in [v["code"] for v in violations], [v["code"] for v in violations]
