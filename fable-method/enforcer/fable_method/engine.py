"""
engine.py — The fable_method enforcement engine (v2).

Implements:
  - Engine class (state-machine session manager with JSON persistence)
  - Module-level convenience wrappers (create_session, get_state, submit, finalize, set_rigor,
    provide_answers)
  - All gate validators for every stage
  - Anti-laziness tripwire detectors (PROTOCOL §4)
  - Adaptive rigor flow (classify artifact → select level → compute required stages)

v2 additions (FIXES_V2.md):
  V1  Junk detector (JUNK_CONTENT) — unique-char<5, top-char>60%, <3 distinct tokens
  V2  _scan_risk + RISK_FLOOR + justification-must-reference-goal + auto-escalation to FULL
  V3  NOOP_FIX detection + token-overlap ≥0.4 finding→fix mapping
  V4  Source typing (url/file/tool_output/assumed); FABRICATION_RISK only for fake URLs
  V5  verify evidence field gate (NO_EVIDENCE)
  V7  Backtracking loop: revise→verify→deliver; revise.reopen; loop_count; iterations
  V8  Uncertainty plumbing: pending_limitations, unknowns, assumed sources, unconfirmed results
  V9  Human-in-the-loop: mode=headless|interactive; provide_answers; awaiting_input status
  V10 Safety screen: _safety_screen; refused status; override_safety

CONTRACT: engine.py (v2) — standard library only, Python 3.10+.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

# Profiles module — imported with graceful degradation if missing.
try:
    from . import profiles as _profiles
except ImportError:
    _profiles = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Canonical stage order (reflect is optional and never gates)
STAGE_ORDER: list[str] = [
    "frame", "research", "plan", "draft", "critique", "verify", "revise", "deliver",
]

# Required stages by level
REQUIRED_STAGES: dict[str, list[str]] = {
    "low":    ["frame", "draft", "deliver"],
    "medium": ["frame", "research", "plan", "draft", "critique", "deliver"],
    "full":   ["frame", "research", "plan", "draft", "critique", "verify", "revise", "deliver"],
}

# Rigor ordering for "may only raise" enforcement
_RIGOR_RANK: dict[str, int] = {"low": 0, "medium": 1, "full": 2, "adaptive": 3}

# Max backtracking loops before requiring residual disclosure
_MAX_LOOP_COUNT = 3


# ---------------------------------------------------------------------------
# Violation codes (stable — harness branches on these)
# ---------------------------------------------------------------------------

class V:
    OUT_OF_ORDER       = "OUT_OF_ORDER"
    WRONG_ARTIFACT_TYPE = "WRONG_ARTIFACT_TYPE"  # artifact is not a JSON object (dict)
    MISSING_FIELD      = "MISSING_FIELD"
    TOO_FEW_ITEMS      = "TOO_FEW_ITEMS"
    EMPTY_OR_TRIVIAL   = "EMPTY_OR_TRIVIAL"
    UNVERIFIED_CLAIM   = "UNVERIFIED_CLAIM"
    HOLLOW_CRITIQUE    = "HOLLOW_CRITIQUE"
    UNMAPPED_FIX       = "UNMAPPED_FIX"
    HANDWAVING         = "HANDWAVING"
    FABRICATION_RISK   = "FABRICATION_RISK"
    LEVEL_INCONSISTENT = "LEVEL_INCONSISTENT"
    NOT_ENOUGH_RIGOR   = "NOT_ENOUGH_RIGOR"
    # v2 new codes
    JUNK_CONTENT       = "JUNK_CONTENT"
    RISK_FLOOR         = "RISK_FLOOR"
    NOOP_FIX           = "NOOP_FIX"
    NO_EVIDENCE        = "NO_EVIDENCE"
    NEEDS_USER_INPUT   = "NEEDS_USER_INPUT"
    REFUSED            = "REFUSED"
    UNVERIFIED_CLAIM_ASSUMED = "UNVERIFIED_CLAIM"  # alias — assumed sources need limitations
    # bug (c): specific codes the docs reference (were emitting MISSING_FIELD / EMPTY_OR_TRIVIAL)
    UNCOVERED_LIMITATION = "UNCOVERED_LIMITATION"  # pending unknown/assumed claim not in deliver.limitations
    MISSING_GOAL_TOKEN   = "MISSING_GOAL_TOKEN"    # classify.justification shares no token with the goal
    # V11 anti-hollow: summary overclaims certainty/completeness while unresolved items remain
    OVERCLAIMED_SUMMARY  = "OVERCLAIMED_SUMMARY"
    # Tier-2 cross-stage coherence: a stage went off-topic / disconnected from the chain
    COHERENCE_BREAK      = "COHERENCE_BREAK"
    # Tier-2: a revise fix names a finding but the change is unrelated to it
    FIX_UNRELATED        = "FIX_UNRELATED"


# Minimum content lengths (characters)
_DRAFT_MIN_CHARS       = 50
_JUSTIFY_MIN_CHARS     = 30
_RESTATEMENT_MIN_CHARS = 20
_NO_ISSUES_WHY_MIN     = 80  # "no_issues_found" justification minimum

# ---------------------------------------------------------------------------
# Handwaving / tripwire patterns (PROTOCOL §4, case-insensitive)
# ---------------------------------------------------------------------------

_HANDWAVING_PATTERNS: list[re.Pattern] = [
    re.compile(r"\byou could\b", re.IGNORECASE),
    re.compile(r"\bas an ai\b", re.IGNORECASE),
    re.compile(r"\bvarious approaches\b", re.IGNORECASE),
    re.compile(r"\bvarious methods\b", re.IGNORECASE),
    re.compile(r"\betc[\.\s]*etc\b", re.IGNORECASE),
    re.compile(r"\betcetera\b", re.IGNORECASE),
    re.compile(r"\bTODO:?\b"),                           # case-sensitive uppercase TODO
    re.compile(r"\bto be done\b", re.IGNORECASE),
    re.compile(r"\bfill in (?:here|your|the blank)\b", re.IGNORECASE),
    re.compile(r"\binsert here\b", re.IGNORECASE),
    re.compile(r"\[placeholder\b", re.IGNORECASE),
    re.compile(r"\bone approach would be\b", re.IGNORECASE),
    re.compile(r"\bmany options exist\b", re.IGNORECASE),
    re.compile(r"\bseveral approaches\b", re.IGNORECASE),
]

# Concrete verification keywords for verify.how
_VERIFY_CONCRETE_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bran\b", re.IGNORECASE),
    re.compile(r"\btest(ed|s)?\b", re.IGNORECASE),
    re.compile(r"\brecomputed?\b", re.IGNORECASE),
    re.compile(r"\bre-?read\b", re.IGNORECASE),
    re.compile(r"\bmeasured\b", re.IGNORECASE),
    re.compile(r"\bexecuted\b", re.IGNORECASE),
    re.compile(r"\bchecked against\b", re.IGNORECASE),
    re.compile(r"\bcompared\b", re.IGNORECASE),
    re.compile(r"\bverified against\b", re.IGNORECASE),
    re.compile(r"\bconfirmed\b", re.IGNORECASE),
    re.compile(r"\brunning\b", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# V1: Junk detector helpers
# ---------------------------------------------------------------------------

# Free-text fields that must pass the 3-distinct-token check
_FREE_TEXT_FIELDS = {"steps", "risks", "criteria", "issue", "change", "claim", "summary",
                     "goal_restatement", "justification", "steelman", "content", "residual"}


def _is_junk(text: str, field_name: str = "") -> tuple[bool, str]:
    """
    Return (is_junk, reason). Fires JUNK_CONTENT when ANY:
    - unique character count (excluding spaces) < 5
    - most-frequent non-space character > 60% of non-space chars
    - fewer than 3 distinct word-tokens (for designated free-text fields)
    """
    stripped = text.strip()
    if not stripped:
        return False, ""  # length checks handle empty separately

    non_space = re.sub(r"\s", "", stripped)
    if not non_space:
        return False, ""

    # unique-char count
    unique_chars = set(non_space.lower())
    if len(unique_chars) < 5:
        return True, (
            f"only {len(unique_chars)} unique character(s) — looks like junk/filler"
        )

    # top-char frequency
    char_counts: dict[str, int] = {}
    for c in non_space.lower():
        char_counts[c] = char_counts.get(c, 0) + 1
    top_char_count = max(char_counts.values())
    top_ratio = top_char_count / len(non_space)
    if top_ratio > 0.60:
        top_char = max(char_counts, key=lambda c: char_counts[c])
        return True, (
            f"'{top_char}' makes up {top_ratio:.0%} of non-space chars — "
            "looks like repeated-character filler"
        )

    # distinct-token check for designated free-text fields
    is_free_text = any(k in field_name.lower() for k in _FREE_TEXT_FIELDS)
    if is_free_text:
        tokens = re.findall(r"\b\w+\b", stripped.lower())
        distinct = set(tokens)
        if len(distinct) < 3:
            return True, (
                f"only {len(distinct)} distinct word token(s) — "
                "needs ≥3 distinct words to be meaningful"
            )

    return False, ""


def _junk_violation(text: str, field: str, stage: str) -> dict | None:
    """Return a JUNK_CONTENT violation dict if the text fails junk detection, else None."""
    is_j, reason = _is_junk(text, field)
    if is_j:
        return _violation(
            V.JUNK_CONTENT,
            f"{field} appears to be junk/filler content: {reason}.",
            "Replace with substantive, meaningful content.",
            stage, field,
        )
    return None


# ---------------------------------------------------------------------------
# V2: Risk scanner
# ---------------------------------------------------------------------------

_RISK_SIGNAL_PATTERNS: list[tuple[str, re.Pattern]] = [
    # money / financial
    ("money:dollar-amount",   re.compile(r"\$\s?\d", re.IGNORECASE)),
    ("money:wire",            re.compile(r"\bwire\b", re.IGNORECASE)),
    ("money:transfer",        re.compile(r"\btransfer\b", re.IGNORECASE)),
    ("money:payment",         re.compile(r"\bpayment\b", re.IGNORECASE)),
    ("money:invoice",         re.compile(r"\binvoice\b", re.IGNORECASE)),
    ("money:money",           re.compile(r"\bmoney\b", re.IGNORECASE)),
    ("money:funds",           re.compile(r"\bfunds?\b", re.IGNORECASE)),
    ("money:cash",            re.compile(r"\bcash\b", re.IGNORECASE)),
    ("money:withdraw",        re.compile(r"\bwithdraw\w*\b", re.IGNORECASE)),
    # irreversibility
    ("irreversible:delete",   re.compile(r"\bdelete\b", re.IGNORECASE)),
    ("irreversible:drop",     re.compile(r"\bdrop\b", re.IGNORECASE)),
    ("irreversible:migrat",   re.compile(r"\bmigrat\w*\b", re.IGNORECASE)),
    ("irreversible:deploy",   re.compile(r"\bdeploy\b", re.IGNORECASE)),
    ("irreversible:prod",     re.compile(r"\bproduction\b|\bprod\b", re.IGNORECASE)),
    ("irreversible:launch",   re.compile(r"\blaunch\b", re.IGNORECASE)),
    ("irreversible:irreversible", re.compile(r"\birreversibl\w*", re.IGNORECASE)),
    # sensitive domains
    ("domain:legal",          re.compile(r"\blegal\b", re.IGNORECASE)),
    ("domain:contract",       re.compile(r"\bcontract\b", re.IGNORECASE)),
    ("domain:medical",        re.compile(r"\bmedical\b", re.IGNORECASE)),
    ("domain:health",         re.compile(r"\bhealth\b", re.IGNORECASE)),
    ("domain:security",       re.compile(r"\bsecurity\b", re.IGNORECASE)),
    ("domain:auth",           re.compile(r"\bauth\b", re.IGNORECASE)),
    ("domain:credential",     re.compile(r"\bcredential\b", re.IGNORECASE)),
    ("domain:tax",            re.compile(r"\btax\b", re.IGNORECASE)),
    ("domain:compliance",     re.compile(r"\bcompliance\b", re.IGNORECASE)),
]


def _scan_risk(goal: str) -> list[str]:
    """Return matched risk signals from the goal string. Case-insensitive."""
    matched: list[str] = []
    for label, pattern in _RISK_SIGNAL_PATTERNS:
        if pattern.search(goal):
            matched.append(label)
    return matched


# ---------------------------------------------------------------------------
# V10: Safety screen
# ---------------------------------------------------------------------------

_SAFETY_CATEGORIES: list[tuple[str, list[re.Pattern]]] = [
    ("weapons_explosives", [
        re.compile(r"\b(bomb|explosive|weapon|firearm|ammo|ammunition|grenade|IED|C4|detonat)\b", re.IGNORECASE),
        re.compile(r"\b(how to make|synthesize|manufacture).{0,30}(weapon|explosive|bomb)\b", re.IGNORECASE),
    ]),
    ("bio_chem", [
        re.compile(r"\b(bioweapon|nerve agent|VX |sarin|ricin|anthrax|botulinum)\b", re.IGNORECASE),
        re.compile(r"\b(synthesize|produce|weaponize).{0,30}(pathogen|toxin|virus|bacteria)\b", re.IGNORECASE),
    ]),
    ("malware_intrusion", [
        re.compile(r"\b(malware|ransomware|keylogger|rootkit|exploit kit|zero.?day)\b", re.IGNORECASE),
        re.compile(r"\b(hack|intrude|breach|compromise).{0,20}(system|network|server|account)\b", re.IGNORECASE),
        re.compile(r"\bwrite.{0,20}(virus|worm|trojan|malware)\b", re.IGNORECASE),
    ]),
    ("fraud_phishing", [
        re.compile(r"\b(phishing|spear.?phish|credential.?harvest|spoofing)\b", re.IGNORECASE),
        re.compile(r"\b(scam|defraud|impersonat).{0,20}(victim|user|customer|bank)\b", re.IGNORECASE),
    ]),
    ("csam", [
        re.compile(r"\b(CSAM|child.?porn|minor.?sexual|child.?exploit)\b", re.IGNORECASE),
    ]),
    ("self_harm", [
        re.compile(r"\b(how to|steps to|method for).{0,30}(suicide|kill (myself|yourself)|self.?harm)\b", re.IGNORECASE),
    ]),
    ("surveillance_doxxing", [
        re.compile(r"\b(mass.?surveillance|doxx(ing)?|stalk.{0,10}(app|software|tool))\b", re.IGNORECASE),
    ]),
]


def _safety_screen(goal: str) -> tuple[bool, str, str]:
    """
    Check goal against coarse safety categories.
    Returns (refused: bool, category: str, reason: str).
    This is a COARSE keyword/category circuit-breaker, not nuanced safety judgment.
    """
    for category, patterns in _SAFETY_CATEGORIES:
        for pattern in patterns:
            if pattern.search(goal):
                return (
                    True,
                    category,
                    f"Goal matches safety category '{category}'. "
                    "This coarse screen blocks obviously harmful work. "
                    "Use override_safety=True to proceed with operator accountability logged.",
                )
    return False, "", ""


# ---------------------------------------------------------------------------
# V4: Source typing helpers
# ---------------------------------------------------------------------------

def _infer_source_type(source: str) -> str:
    """Infer source type if not provided: url, file, tool_output, or assumed."""
    s = source.strip().lower()
    if s.startswith("http://") or s.startswith("https://"):
        return "url"
    if re.match(r"^[./~]", s) or re.search(r"\.\w{1,4}(:\d+)?$", s):
        return "file"
    if re.match(r"(tool|function|api|output|result)[:_]", s, re.IGNORECASE):
        return "tool_output"
    return "assumed"


_PLACEHOLDER_URL_PATTERNS: list[re.Pattern] = [
    re.compile(r"^https?://example\.com", re.IGNORECASE),
    re.compile(r"\btodo\b", re.IGNORECASE),
    re.compile(r"\bxxx\b", re.IGNORECASE),
    re.compile(r"^\s*\.\.\.\s*$"),
    re.compile(r"^https?://[^/]+$"),  # bare domain with no path — suspicious
]

# Patterns that indicate a URL is a real reference (has meaningful path)
_REAL_URL_PATTERN = re.compile(
    r"^https?://(?!example\.com)[\w\-\.]+\.[a-z]{2,}/[\w\-\./%?=&+#]{3,}", re.IGNORECASE
)


def _is_fabrication_source_v2(source: str, source_type: str) -> bool:
    """
    V4: FABRICATION_RISK only for sources claiming verification they lack:
    url/tool_output that are empty or obviously placeholder.
    Honest 'assumed' sources are NOT fabrication risk (they're handled by V8 plumbing).
    """
    s = source.strip()
    if not s:
        return True  # empty source always fabrication

    if source_type in ("url", "tool_output"):
        # Check for obviously fake / placeholder
        for pattern in _PLACEHOLDER_URL_PATTERNS:
            if pattern.search(s):
                return True
        # A url/tool_output claiming verification that has no real path
        if source_type == "url" and not _REAL_URL_PATTERN.match(s):
            return True

    return False


# ---------------------------------------------------------------------------
# V3: NOOP_FIX patterns
# ---------------------------------------------------------------------------

_NOOP_FIX_PATTERNS: list[re.Pattern] = [
    re.compile(r"^ack\b", re.IGNORECASE),
    re.compile(r"^acknowledg", re.IGNORECASE),
    re.compile(r"^noted\b", re.IGNORECASE),
    re.compile(r"^will\s+(consider|address|fix|do)\b", re.IGNORECASE),
    re.compile(r"^to-?do\b", re.IGNORECASE),
    re.compile(r"^tbd\b", re.IGNORECASE),
    re.compile(r"^later\b", re.IGNORECASE),
    re.compile(r"^in\s+(a\s+)?future\b", re.IGNORECASE),
    re.compile(r"^recommend(ed)?\s+(adding|that)\b", re.IGNORECASE),
]

_CONCRETE_EDIT_VERBS: list[re.Pattern] = [
    re.compile(r"\bchanged\b", re.IGNORECASE),
    re.compile(r"\badded\b", re.IGNORECASE),
    re.compile(r"\bremoved\b", re.IGNORECASE),
    re.compile(r"\breplaced\b", re.IGNORECASE),
    re.compile(r"\brewrote\b", re.IGNORECASE),
    re.compile(r"\brecomputed\b", re.IGNORECASE),
    re.compile(r"\bcorrected\b", re.IGNORECASE),
    re.compile(r"\brefactored\b", re.IGNORECASE),
    re.compile(r"\brenamed\b", re.IGNORECASE),
    re.compile(r"\bset\b", re.IGNORECASE),
    re.compile(r"\bupdated\b", re.IGNORECASE),
]


def _is_noop_fix(change_text: str) -> bool:
    """Return True if the change text is a no-op intent statement with no concrete edit."""
    stripped = change_text.strip()

    # Check explicit no-op prefix patterns
    for pattern in _NOOP_FIX_PATTERNS:
        if pattern.search(stripped):
            return True

    # Check: must have at least one concrete-edit verb
    has_concrete = any(p.search(stripped) for p in _CONCRETE_EDIT_VERBS)
    if not has_concrete:
        return True

    return False


# ---------------------------------------------------------------------------
# V5: Evidence gate helpers
# ---------------------------------------------------------------------------

_EVIDENCE_CONCRETE_PATTERNS: list[re.Pattern] = [
    re.compile(r"\d"),                           # any digit
    re.compile(r"\bPASS\b|\bFAIL\b"),            # PASS/FAIL tokens
    re.compile(r"\w+\.py:\d+|\w+\.\w+:\d+"),     # file:line pattern
    re.compile(r'"[^"]{3,}"'),                    # quoted snippet ≥3 chars
    re.compile(r"`[^`]{2,}`"),                    # backtick snippet ≥2 chars
]

# Patterns suggesting a check result says it could NOT confirm
_UNCONFIRMED_RESULT_PATTERNS: list[re.Pattern] = [
    re.compile(r"could not", re.IGNORECASE),
    re.compile(r"unable", re.IGNORECASE),
    re.compile(r"unverified", re.IGNORECASE),
    re.compile(r"inconclusive", re.IGNORECASE),
]


def _has_concrete_evidence(evidence: str) -> bool:
    """Return True if evidence contains at least one concrete artifact token."""
    return any(p.search(evidence) for p in _EVIDENCE_CONCRETE_PATTERNS)


def _result_is_unconfirmed(result: str) -> bool:
    """Return True if the result says it could not confirm the check."""
    return any(p.search(result) for p in _UNCONFIRMED_RESULT_PATTERNS)


# Failure signals beyond "unconfirmed" — an explicit failing/regressed check result.
VERIFY_STATUS_VALUES = ("pass", "fail", "inconclusive")

# Process exit codes in injected/quoted evidence — unambiguous machine output.
# Captures the numeric value so we can inspect the LAST exit code in the evidence rather
# than any occurrence: honest before/after evidence ("before exited 1; after exit_code=0")
# ends on a zero exit and must NOT be flagged. Matches "exit_code=1", "exit code: 2",
# "exited 3", "exit_code=0", etc.
_EXIT_CODE_PATTERN = re.compile(
    r"exit(?:[_ ]?code)?\s*[=:]?\s*(\d+)\b|exited\s+(\d+)\b", re.IGNORECASE
)


_HISTORICAL_HINT_RE = re.compile(
    r"\b(?:was|were|before|previously|prior|originally|earlier|initially|formerly|used\s+to)\b",
    re.IGNORECASE,
)


def _evidence_ends_in_failure(evidence: str) -> bool:
    """True if the CURRENT machine result in the evidence is a failure.

    Scans for exit codes. A code in a clearly HISTORICAL context — a retrospective keyword
    (was/before/previously/prior/originally/earlier/initially/formerly/used to) within the
    ~40 characters preceding it — is discounted as a quoted prior run ONLY WHEN the evidence
    also shows a current (non-historical) exit code. So honest before/after evidence is not
    flagged in either order ("before exited 1; now exit_code=0" OR "exit_code=0; it previously
    exited 1") — both carry a real current code (0) that decides.

    Bypass closed (#15): if EVERY exit code in the evidence is historical (e.g. "before: the
    build exited 1", with no current code shown), the last historical code decides instead of
    being silently treated as clean. A check marked 'pass' whose only machine evidence is a
    discounted failure — current success merely asserted in prose — is flagged for revision.

    Honest limits — this is a non-exec BACKSTOP only (in --exec mode the harness sets each
    check's status from the real exit codes, which is authoritative). It matches only explicit
    "exit code N" / "exited N" phrasing, so a model can phrase a failure another way to avoid
    it; and a real failure followed textually by a benign exit (e.g. a teardown 'exit 0') is
    not caught. It checks the SHAPE of the evidence, not the substance of the result."""
    last_current = None      # last non-historical (current) code — authoritative if present
    last_historical = None   # last historical code — fallback when no current code exists
    for m in _EXIT_CODE_PATTERN.finditer(evidence):
        code = m.group(1) if m.group(1) is not None else m.group(2)
        window = evidence[max(0, m.start() - 40):m.start()]
        if _HISTORICAL_HINT_RE.search(window):
            last_historical = code
        else:
            last_current = code
    deciding = last_current if last_current is not None else last_historical
    return deciding is not None and int(deciding) != 0


def _verify_has_unresolved_check(checks) -> bool:
    """V7 backtracking driver (structured): True if any verify check carries an explicit
    status of 'fail' or 'inconclusive'. Looping is driven by a STRUCTURED signal the model
    states per check (and which the harness derives from real exit codes in --exec mode),
    NOT by keyword-scanning free-text prose — prose scanning fired on benign phrasings like
    'no errors found' and missed real failures like 'output does not match'. If no check
    declares a status, the multi-cycle loop does not engage (the one guaranteed re-verify
    after fixes still happens via the revise→verify routing)."""
    if not isinstance(checks, list):
        return False
    for check in checks:
        if not isinstance(check, dict):
            continue
        status = str(check.get("status", "")).strip().lower()
        if status in ("fail", "inconclusive"):
            return True
    return False


# ---------------------------------------------------------------------------
# V8: Uncertainty plumbing helpers
# ---------------------------------------------------------------------------

def _overlap_any(item: str, candidates: list[str], threshold: float = 0.25) -> bool:
    """Return True if item has token overlap ≥ threshold with any candidate."""
    item_tokens = set(re.findall(r"\b\w+\b", item.lower()))
    if not item_tokens:
        return False
    for candidate in candidates:
        cand_tokens = set(re.findall(r"\b\w+\b", candidate.lower()))
        if not cand_tokens:
            continue
        intersection = item_tokens & cand_tokens
        union = item_tokens | cand_tokens
        if union and len(intersection) / len(union) >= threshold:
            return True
    return False


# ---------------------------------------------------------------------------
# Helpers (shared utilities)
# ---------------------------------------------------------------------------

def _violation(code: str, message: str, fix_hint: str,
               stage: str = "", field: str | None = None) -> dict:
    v: dict[str, Any] = {"code": code, "message": message, "fix_hint": fix_hint, "stage": stage}
    if field is not None:
        v["field"] = field
    return v


def _is_handwaving(text: str) -> bool:
    return any(p.search(text) for p in _HANDWAVING_PATTERNS)


def _has_concrete_verify(text: str) -> bool:
    return any(p.search(text) for p in _VERIFY_CONCRETE_PATTERNS)


# ---------------------------------------------------------------------------
# A1: Anti-filler helpers — duplicate/trivial list-item detection
# ---------------------------------------------------------------------------

_ITEM_MIN_CHARS = 12  # minimum non-space chars for a free-text list item


def _normalise(text: str) -> str:
    """Lowercase + collapse whitespace for duplicate comparison."""
    return re.sub(r"\s+", " ", text.strip().lower())


def _salient_text(item: object, kind: str) -> str:
    """Extract the salient text field from a list item."""
    if isinstance(item, str):
        return item
    if not isinstance(item, dict):
        return str(item)
    if kind == "facts":
        return item.get("claim", "")
    if kind == "findings":
        return item.get("issue", "")
    if kind == "checks":
        return (item.get("what", "") + " " + item.get("how", "")).strip()
    if kind == "fixes":
        return item.get("change", "")
    if kind == "assumptions":
        return item.get("assumption", "")
    return str(item)


def _check_list_items(
    items: list,
    kind: str,
    stage: str,
    field: str,
) -> list[dict]:
    """
    Check a gated list for:
      1. Per-item minimum length (12 non-space chars)
      2. Duplicate items (after normalisation)
      3. V1 Junk detection on the salient text

    Returns a list of violations.
    """
    violations: list[dict] = []
    seen: dict[str, int] = {}

    for i, item in enumerate(items):
        text = _salient_text(item, kind)
        non_space = re.sub(r"\s", "", text)
        norm = _normalise(text)

        # Length check
        if len(non_space) < _ITEM_MIN_CHARS:
            violations.append(_violation(
                V.EMPTY_OR_TRIVIAL,
                f"{field}[{i}] is too short or trivial "
                f"({len(non_space)} non-space chars; minimum {_ITEM_MIN_CHARS}): '{text[:60]}'.",
                f"Replace with a substantive {kind} item (at least {_ITEM_MIN_CHARS} real chars).",
                stage, field,
            ))
        else:
            # V1: junk detection on sufficiently long items
            junk_v = _junk_violation(text, field, stage)
            if junk_v:
                violations.append(junk_v)

        # Duplicate check
        if norm in seen:
            violations.append(_violation(
                V.EMPTY_OR_TRIVIAL,
                f"{field}[{i}] is a duplicate of {field}[{seen[norm]}]: '{text[:60]}'.",
                f"Replace with a distinct {kind} item; duplicates count as placeholder filler.",
                stage, field,
            ))
        else:
            seen[norm] = i

    return violations


def _get_instructions(profile: str, stage: str, level: str) -> str:
    if _profiles is not None:
        try:
            return _profiles.get_instructions(profile, stage, level)
        except Exception:
            pass
    return f"Complete the '{stage}' stage as described in the fable_method protocol."


def _get_overlay_checks(profile: str, stage: str) -> list[str]:
    if _profiles is not None:
        try:
            return _profiles.get_overlay_checks(profile, stage)
        except Exception:
            pass
    return []


def _required_artifact_schema(stage: str) -> dict:
    """Return a human-readable schema hint for the required artifact."""
    schemas = {
        "classify": {
            "complexity": "low|medium|high",
            "stakes": "low|medium|high",
            "reversibility": "easy|hard",
            "selected_level": "low|medium|full",
            "justification": "str (non-trivial explanation)",
        },
        "frame": {
            "goal_restatement": "str",
            "success_criteria": ["str", "..."],
            "questions": ["str (load-bearing ambiguities) — OR use assumptions"],
            "assumptions": [{"assumption": "str", "why_safe": "str"}],
        },
        "research": {
            "facts": [{"claim": "str", "source": "str (specific)", "type": "url|file|tool_output|assumed (optional)"}],
            "unknowns": ["str"],
            "risk_flags": ["str (optional — name any safety, financial, legal, or "
                           "irreversibility risk the research surfaced; ANY non-empty "
                           "entry auto-escalates the session to FULL rigor)"],
            "__alt__": {"no_research_needed": True, "why": "str"},
        },
        "plan": {
            "steps": ["str (ordered, ≥2; FULL: ≥3)"],
            "risks": ["str (≥1)"],
            "verification_strategy": ["str (≥1)"],
        },
        "draft": {
            "content": "str (substantive work; no TODOs / hand-waving)",
        },
        "critique": {
            "findings": [{"severity": "blocker|major|minor", "issue": "str", "location": "str"}],
            "steelman": "str",
            "__alt__": {"no_issues_found": True, "why": f"str (≥{_NO_ISSUES_WHY_MIN} chars)"},
        },
        "verify": {
            "checks": [{"what": "str", "how": "str (must show concrete method)",
                        "result": "str", "evidence": "str (optional but strongly recommended)",
                        "status": "pass|fail|inconclusive (optional; 'fail'/'inconclusive' "
                                   "routes the loop back to revise)"}],
            "_note": "At least one check must have evidence with a concrete artifact token (digit, PASS/FAIL, file:line, quoted snippet)",
        },
        "revise": {
            "fixes": [{"finding_ref": "str (must match a blocker/major finding with ≥0.4 token overlap)",
                        "change": "str (must contain a concrete-edit verb)"}],
            "reverified": "bool (true if anything changed)",
            "reopen": "plan|draft (optional — major replan, resets later stages)",
        },
        "deliver": {
            "summary": "str",
            "limitations": ["str (or explicit 'none, because …')"],
            "sources": ["str"],
        },
    }
    return schemas.get(stage, {"_note": f"No schema defined for stage '{stage}'"})


# ---------------------------------------------------------------------------
# Gate validators
# ---------------------------------------------------------------------------

def _gate_classify(artifact: dict, level: str, profile: str, goal: str = "") -> list[dict]:
    """Validate the classify artifact (adaptive rigor only). V2: RISK_FLOOR + goal-ref check."""
    violations: list[dict] = []
    stage = "classify"

    for field in ("complexity", "stakes", "reversibility", "selected_level", "justification"):
        if field not in artifact:
            violations.append(_violation(
                V.MISSING_FIELD, f"'{field}' is required in classify artifact.",
                f"Add a '{field}' key.", stage, field,
            ))

    if violations:
        return violations

    valid_csl = {"low", "medium", "high"}
    if artifact.get("complexity") not in valid_csl:
        violations.append(_violation(
            V.MISSING_FIELD, "complexity must be 'low', 'medium', or 'high'.",
            "Set complexity to one of: low, medium, high.", stage, "complexity",
        ))
    if artifact.get("stakes") not in valid_csl:
        violations.append(_violation(
            V.MISSING_FIELD, "stakes must be 'low', 'medium', or 'high'.",
            "Set stakes to one of: low, medium, high.", stage, "stakes",
        ))
    if artifact.get("reversibility") not in {"easy", "hard"}:
        violations.append(_violation(
            V.MISSING_FIELD, "reversibility must be 'easy' or 'hard'.",
            "Set reversibility to 'easy' or 'hard'.", stage, "reversibility",
        ))
    if artifact.get("selected_level") not in {"low", "medium", "full"}:
        violations.append(_violation(
            V.MISSING_FIELD, "selected_level must be 'low', 'medium', or 'full'.",
            "Set selected_level to one of: low, medium, full.", stage, "selected_level",
        ))

    if violations:
        return violations

    justification = str(artifact.get("justification", "")).strip()
    if len(justification) < _JUSTIFY_MIN_CHARS:
        violations.append(_violation(
            V.EMPTY_OR_TRIVIAL,
            f"justification is too short ({len(justification)} chars; minimum {_JUSTIFY_MIN_CHARS}).",
            "Explain your reasoning for the selected level in at least a sentence.",
            stage, "justification",
        ))
    else:
        # V1: junk check on justification
        junk_v = _junk_violation(justification, "justification", stage)
        if junk_v:
            violations.append(junk_v)

        # V2: justification must share ≥1 content token with the goal.
        # Only fires when the goal itself has ≥2 substantive tokens (4+ chars, non-stopword)
        # so that short/generic test goals like "goal" don't false-positive.
        if goal:
            stopwords = {"this", "that", "with", "from", "have", "will", "been", "they",
                         "their", "which", "would", "could", "should", "more", "than",
                         "very", "just", "also", "some", "your", "about", "level",
                         "task", "here", "when", "what", "does", "make", "work"}
            just_tokens = set(re.findall(r"\b\w{4,}\b", justification.lower())) - stopwords
            goal_tokens = set(re.findall(r"\b\w{4,}\b", goal.lower())) - stopwords
            # Only apply when the goal has ≥3 substantive tokens (enough signal to compare).
            # Goals with 1-2 tokens (e.g. 'goal', 'Test goal') are too short to mandate matching.
            if len(goal_tokens) >= 3 and just_tokens and not (just_tokens & goal_tokens):
                violations.append(_violation(
                    V.MISSING_GOAL_TOKEN,
                    "classify.justification must reference the goal — it shares no content "
                    "tokens with the goal text. Don't justify generically.",
                    "Explain WHY this SPECIFIC goal warrants the chosen rigor level.",
                    stage, "justification",
                ))

    selected = artifact.get("selected_level")
    stakes = artifact.get("stakes")
    reversibility = artifact.get("reversibility")
    complexity = artifact.get("complexity")

    # V2: RISK_FLOOR — if _scan_risk(goal) is non-empty, selected_level may NOT be "low"
    if selected == "low" and goal:
        risk_signals = _scan_risk(goal)
        if risk_signals:
            violations.append(_violation(
                V.RISK_FLOOR,
                f"selected_level='low' is not allowed when the goal contains risk signals: "
                f"{risk_signals}. Minimum rigor is 'medium' for goals with financial, "
                "irreversibility, or sensitive-domain signals.",
                "Raise selected_level to 'medium' or 'full'.",
                stage, "selected_level",
            ))

    # Existing semantic consistency check
    if selected == "low" and (
        stakes == "high" or reversibility == "hard" or complexity == "high"
    ):
        violations.append(_violation(
            V.LEVEL_INCONSISTENT,
            f"selected_level='low' is inconsistent with complexity='{complexity}', "
            f"stakes='{stakes}', reversibility='{reversibility}'. "
            "High complexity, high stakes, or hard reversibility require medium or full rigor.",
            "Raise selected_level to 'medium' or 'full'.",
            stage, "selected_level",
        ))

    return violations


def _token_overlap(a: str, b: str) -> float:
    """Return the Jaccard token overlap between two strings (0.0–1.0)."""
    tokens_a = set(re.findall(r"\b\w+\b", a.lower()))
    tokens_b = set(re.findall(r"\b\w+\b", b.lower()))
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


# ---------------------------------------------------------------------------
# Tier-2: cross-stage coherence.
#
# DESIGN NOTE (data-driven, 2026-06-13). We tried to detect "off-topic" work by
# checking whether stages share WORDS with the goal / each other. Two evidence
# sources killed that approach as a blocking gate:
#   1. A threshold sweep over 114 labeled cases (Haiku-generated + a deterministic
#      battery) showed that requiring shared goal-words flags ~70-100% of honest but
#      PARAPHRASED work (goal "autumn leaves" vs honest "fall foliage / chlorophyll").
#   2. Re-running the engine's own test suite showed the stage-to-stage "drift" check
#      and the critique-vs-draft check also false-fire whenever legitimate stages use
#      different vocabulary. Word overlap cannot tell "related but worded differently"
#      from "unrelated".
# So all the word-overlap TOPIC checks were removed (goal anchor, chain drift,
# critique-vs-draft). The ONLY coherence check retained as a hard gate is
# fix<->finding: a "fix" must relate to the finding it cites. That one compares two
# tightly-coupled siblings written by the same author about the same defect, so it
# does NOT suffer the paraphrase problem, and it closes a real gaming hole.
#
# KNOWN LIMIT (documented, not hidden): the engine does NOT detect a session that is
# internally consistent but collectively off-goal, nor does it judge factual accuracy.
# Reliably catching off-topic/off-goal work needs semantic matching (embeddings or a
# model judging it) — a substance check this engine deliberately does not attempt.
# `_salient_for_stage` / `_artifact_tokens` are retained below for that future work.
# ---------------------------------------------------------------------------

_CONTENT_STOPWORDS = {
    "this", "that", "with", "from", "have", "will", "been", "they", "their",
    "which", "would", "could", "should", "more", "than", "very", "just", "also",
    "some", "your", "about", "level", "task", "here", "when", "what", "does",
    "make", "work", "into", "then", "them", "there", "these", "those", "over",
    "under", "each", "such", "only", "must", "need", "want", "like", "well",
}


def _content_tokens(text: str) -> set:
    """Substantive tokens for coherence comparison: ≥4 chars, lowercased, minus
    common stopwords. Short/filler words are dropped so overlap reflects subject
    matter, not grammar."""
    return set(re.findall(r"\b\w{4,}\b", str(text).lower())) - _CONTENT_STOPWORDS


def _join_list(seq) -> str:
    return " ".join(str(x) for x in seq) if isinstance(seq, list) else ""


def _salient_for_stage(stage: str, artifact: object) -> str:
    """The topic-bearing text of an artifact, by stage. Used to compare what each
    stage is ABOUT against the goal and the rest of the session."""
    if not isinstance(artifact, dict):
        return ""
    if stage == "frame":
        return str(artifact.get("goal_restatement", "")) + " " + _join_list(artifact.get("success_criteria", []))
    if stage == "research":
        facts = artifact.get("facts", [])
        claims = " ".join(str(f.get("claim", "")) for f in facts if isinstance(f, dict)) if isinstance(facts, list) else ""
        return claims + " " + _join_list(artifact.get("unknowns", [])) + " " + _join_list(artifact.get("risk_flags", []))
    if stage == "plan":
        return (_join_list(artifact.get("steps", [])) + " " + _join_list(artifact.get("risks", []))
                + " " + _join_list(artifact.get("verification_strategy", [])))
    if stage == "draft":
        return str(artifact.get("content", ""))
    if stage == "critique":
        findings = artifact.get("findings", [])
        issues = " ".join(str(f.get("issue", "")) for f in findings if isinstance(f, dict)) if isinstance(findings, list) else ""
        return issues + " " + str(artifact.get("steelman", ""))
    if stage == "verify":
        checks = artifact.get("checks", [])
        if isinstance(checks, list):
            return " ".join(
                str(c.get("what", "")) + " " + str(c.get("how", "")) + " " + str(c.get("result", ""))
                for c in checks if isinstance(c, dict)
            )
        return ""
    if stage == "revise":
        fixes = artifact.get("fixes", [])
        return " ".join(str(f.get("change", "")) for f in fixes if isinstance(f, dict)) if isinstance(fixes, list) else ""
    if stage == "deliver":
        return (str(artifact.get("summary", "")) + " " + _join_list(artifact.get("limitations", []))
                + " " + _join_list(artifact.get("sources", [])))
    return ""


def _artifact_tokens(session: dict) -> set:
    """Union of substantive tokens across all artifacts recorded so far (excludes
    the goal). 'Has the goal's subject shown up anywhere in the actual work?'"""
    toks: set = set()
    artifacts = session.get("artifacts", {})
    if isinstance(artifacts, dict):
        for st, art in artifacts.items():
            toks |= _content_tokens(_salient_for_stage(st, art))
    return toks


def _fix_finding_coherence(artifact: dict, session: dict) -> list:
    """Revise check: each concrete fix should relate to the finding it claims to
    address. This is a WITHIN-session comparison (the fix's change text vs the
    referenced finding's issue text), so it does not suffer the paraphrase problem
    that sank the goal-word anchor — the change and the finding are written by the
    same author about the same defect and share vocabulary naturally. Conservative:
    fires only on ZERO overlap, and only when both sides carry enough content."""
    violations: list = []
    fixes = artifact.get("fixes", [])
    if not isinstance(fixes, list):
        return violations
    crit = session.get("artifacts", {}).get("critique", {})
    findings = crit.get("findings", []) if isinstance(crit, dict) else []
    findings = [f for f in findings if isinstance(f, dict)]
    if not findings:
        return violations
    for i, fix in enumerate(fixes):
        if not isinstance(fix, dict):
            continue
        change_tokens = _content_tokens(str(fix.get("change", "")))
        if len(change_tokens) < 4:
            continue  # too little to judge; NOOP_FIX/other gates handle thin changes
        # Map the fix to the finding it best references (mirrors _gate_revise's map).
        ref = str(fix.get("finding_ref", ""))
        best_issue, best = "", 0.0
        for f in findings:
            issue = str(f.get("issue", ""))
            ov = _token_overlap(ref, issue)
            if ov > best:
                best, best_issue = ov, issue
        issue_tokens = _content_tokens(best_issue)
        if len(issue_tokens) >= 3 and not (change_tokens & issue_tokens):
            violations.append(_violation(
                V.COHERENCE_BREAK,
                f"fixes[{i}].change does not relate to the finding it references — its "
                "description shares no content with the problem it claims to address.",
                "Describe a change that actually addresses the referenced finding.",
                "revise", f"fixes[{i}].change",
            ))
    return violations


def _coherence_violations(stage: str, artifact: dict, session: dict, goal: str) -> list:
    """The only retained coherence gate: a revise "fix" must relate to the finding it
    cites (closes a gaming hole). The word-overlap TOPIC checks (goal anchor, drift,
    critique-vs-draft) were removed — see the DESIGN NOTE above — because they flag
    honest work that merely uses different words. The `goal` arg is kept for signature
    stability and a possible future semantic check."""
    if stage == "revise":
        return _fix_finding_coherence(artifact, session)
    return []


def _gate_frame(artifact: dict, level: str, profile: str,
                goal: str = "") -> list[dict]:
    """Validate the frame artifact."""
    violations: list[dict] = []
    stage = "frame"

    if "goal_restatement" not in artifact:
        violations.append(_violation(
            V.MISSING_FIELD, "goal_restatement is required.",
            "Add a goal_restatement string.", stage, "goal_restatement",
        ))
    else:
        restatement = str(artifact["goal_restatement"]).strip()
        if len(restatement) < _RESTATEMENT_MIN_CHARS:
            violations.append(_violation(
                V.EMPTY_OR_TRIVIAL,
                f"goal_restatement is too short ({len(restatement)} chars).",
                "Restate the goal in your own words; do not echo verbatim.",
                stage, "goal_restatement",
            ))
        else:
            # V1: junk check
            junk_v = _junk_violation(restatement, "goal_restatement", stage)
            if junk_v:
                violations.append(junk_v)
            elif goal:
                # A5: echo tripwire
                goal_stripped = goal.strip()
                if _normalise(restatement) == _normalise(goal_stripped):
                    violations.append(_violation(
                        V.EMPTY_OR_TRIVIAL,
                        "goal_restatement echoes the goal verbatim. Restate in your own words.",
                        "Paraphrase the goal — explain what it means to you, not a copy.",
                        stage, "goal_restatement",
                    ))
                elif _token_overlap(restatement, goal_stripped) >= 0.9:
                    violations.append(_violation(
                        V.EMPTY_OR_TRIVIAL,
                        "goal_restatement is too similar to the goal (≥90% token overlap). "
                        "Restate in your own words.",
                        "Substantially reword the goal — don't just rearrange the same words.",
                        stage, "goal_restatement",
                    ))

    if "success_criteria" not in artifact:
        violations.append(_violation(
            V.MISSING_FIELD, "success_criteria list is required.",
            "Add a success_criteria list with at least one item.", stage, "success_criteria",
        ))
    else:
        sc = artifact["success_criteria"]
        if not isinstance(sc, list) or len(sc) < 1:
            violations.append(_violation(
                V.TOO_FEW_ITEMS, "success_criteria must have ≥1 item.",
                "Add at least one concrete, checkable success criterion.",
                stage, "success_criteria",
            ))
        elif isinstance(sc, list):
            violations.extend(_check_list_items(sc, "success_criteria", stage, "success_criteria"))

    has_questions = bool(artifact.get("questions"))
    has_assumptions = bool(artifact.get("assumptions"))
    if not has_questions and not has_assumptions:
        violations.append(_violation(
            V.MISSING_FIELD,
            "Either 'questions' (list of clarifying questions) or 'assumptions' "
            "(list of {assumption, why_safe} dicts) must be non-empty.",
            "List load-bearing questions OR explicit safe assumptions.",
            stage,
        ))
    else:
        assumptions = artifact.get("assumptions")
        if isinstance(assumptions, list) and assumptions:
            violations.extend(_check_list_items(
                assumptions, "assumptions", stage, "assumptions"
            ))

    return violations


def _gate_research(artifact: dict, level: str, profile: str,
                   session: dict | None = None) -> list[dict]:
    """Validate the research artifact. V4: source typing. V8: assumed-source plumbing."""
    violations: list[dict] = []
    stage = "research"

    # Explicit opt-out
    if artifact.get("no_research_needed"):
        why = str(artifact.get("why", "")).strip()
        if len(why) < 10:
            violations.append(_violation(
                V.EMPTY_OR_TRIVIAL,
                "no_research_needed=true requires a 'why' explanation.",
                "Explain why no external research is needed.",
                stage, "why",
            ))
        return violations

    if "facts" not in artifact:
        violations.append(_violation(
            V.MISSING_FIELD, "facts list is required (or no_research_needed:true with why).",
            "Add at least one fact with a real source.", stage, "facts",
        ))
    else:
        facts = artifact["facts"]
        if not isinstance(facts, list) or len(facts) < 1:
            violations.append(_violation(
                V.TOO_FEW_ITEMS, "facts must contain ≥1 entry.",
                "Add at least one {claim, source} entry.", stage, "facts",
            ))
        else:
            violations.extend(_check_list_items(facts, "facts", stage, "facts"))
            for i, fact in enumerate(facts):
                if not isinstance(fact, dict):
                    continue
                source = str(fact.get("source", "")).strip()
                # V4: infer or use declared type
                declared_type = fact.get("type", "").strip().lower()
                if declared_type not in ("url", "file", "tool_output", "assumed", ""):
                    declared_type = ""
                source_type = declared_type if declared_type else _infer_source_type(source)

                # V4: FABRICATION_RISK only for url/tool_output that are empty/placeholder
                if _is_fabrication_source_v2(source, source_type):
                    violations.append(_violation(
                        V.FABRICATION_RISK,
                        f"facts[{i}].source '{source}' (type={source_type}) "
                        "claims verification it cannot provide — it is empty or a placeholder.",
                        "Replace with a real, specific source. "
                        "If uncertain, use type='assumed' honestly.",
                        stage, f"facts[{i}].source",
                    ))
                # V4/V8: assumed sources: the claim must later appear in limitations
                # (we record these for V8 plumbing in the session; no gate failure here)

    if "unknowns" not in artifact:
        violations.append(_violation(
            V.MISSING_FIELD, "unknowns list is required (may be empty list).",
            "Add an 'unknowns' list. Empty is fine if there are no known unknowns.",
            stage, "unknowns",
        ))

    # risk_flags is optional (#10), but if present it must be a list so the
    # auto-escalation reader can trust it.
    if "risk_flags" in artifact and not isinstance(artifact["risk_flags"], list):
        violations.append(_violation(
            V.MISSING_FIELD, "risk_flags must be a list of strings when present.",
            "Provide risk_flags as a list, e.g. [\"irreversible data deletion\"]. "
            "Omit the field if there are no risks.",
            stage, "risk_flags",
        ))

    return violations


def _gate_plan(artifact: dict, level: str, profile: str) -> list[dict]:
    violations: list[dict] = []
    stage = "plan"

    min_steps = 3 if level == "full" else 2

    for field in ("steps", "risks", "verification_strategy"):
        if field not in artifact:
            violations.append(_violation(
                V.MISSING_FIELD, f"'{field}' list is required in plan.",
                f"Add a '{field}' list.", stage, field,
            ))

    steps = artifact.get("steps", [])
    if isinstance(steps, list) and len(steps) < min_steps:
        violations.append(_violation(
            V.TOO_FEW_ITEMS,
            f"plan.steps has {len(steps)} item(s); minimum is {min_steps} for level '{level}'.",
            f"Add at least {min_steps} ordered steps.",
            stage, "steps",
        ))
    elif isinstance(steps, list):
        violations.extend(_check_list_items(steps, "steps", stage, "steps"))

    risks = artifact.get("risks", [])
    if isinstance(risks, list) and len(risks) < 1:
        violations.append(_violation(
            V.TOO_FEW_ITEMS, "plan.risks must have ≥1 item.",
            "Name at least one risk — where is this most likely to go wrong?",
            stage, "risks",
        ))
    elif isinstance(risks, list):
        violations.extend(_check_list_items(risks, "risks", stage, "risks"))

    vs = artifact.get("verification_strategy", [])
    if isinstance(vs, list) and len(vs) < 1:
        violations.append(_violation(
            V.TOO_FEW_ITEMS, "plan.verification_strategy must have ≥1 item.",
            "Define how you will prove correctness (tests, recompute, re-read sources).",
            stage, "verification_strategy",
        ))
    elif isinstance(vs, list):
        violations.extend(_check_list_items(vs, "verification_strategy", stage, "verification_strategy"))

    return violations


def _gate_draft(artifact: dict, level: str, profile: str) -> list[dict]:
    violations: list[dict] = []
    stage = "draft"

    if "content" not in artifact:
        violations.append(_violation(
            V.MISSING_FIELD, "draft.content is required.",
            "Add a 'content' key with the substantive draft.", stage, "content",
        ))
        return violations

    content = str(artifact["content"]).strip()
    if len(content) < _DRAFT_MIN_CHARS:
        violations.append(_violation(
            V.EMPTY_OR_TRIVIAL,
            f"draft.content is too short ({len(content)} chars; minimum {_DRAFT_MIN_CHARS}).",
            "Provide substantive draft content, not a placeholder.",
            stage, "content",
        ))

    # V1: junk check on content (only if long enough to pass length check)
    if len(content) >= _DRAFT_MIN_CHARS:
        junk_v = _junk_violation(content, "content", stage)
        if junk_v:
            violations.append(junk_v)

    if _is_handwaving(content):
        matching = [p.pattern for p in _HANDWAVING_PATTERNS if p.search(content)]
        violations.append(_violation(
            V.HANDWAVING,
            f"draft.content contains hand-waving pattern(s): {matching[:3]}.",
            "Replace hand-waving ('you could', 'TODO', 'as an AI', etc.) with actual work.",
            stage, "content",
        ))

    return violations


def _gate_critique(artifact: dict, level: str, profile: str) -> list[dict]:
    violations: list[dict] = []
    stage = "critique"

    # Explicit "no issues" opt-out
    if artifact.get("no_issues_found"):
        why = str(artifact.get("why", "")).strip()
        if len(why) < _NO_ISSUES_WHY_MIN:
            violations.append(_violation(
                V.HOLLOW_CRITIQUE,
                f"no_issues_found=true requires a 'why' of ≥{_NO_ISSUES_WHY_MIN} chars "
                f"(got {len(why)}).",
                "Justify why there are genuinely no issues; the bar is high.",
                stage, "why",
            ))
        steelman = str(artifact.get("steelman", "")).strip()
        if not steelman:
            violations.append(_violation(
                V.MISSING_FIELD, "steelman is required even when no_issues_found=true.",
                "Provide the strongest counterargument to your conclusion.",
                stage, "steelman",
            ))
        return violations

    if "findings" not in artifact:
        violations.append(_violation(
            V.MISSING_FIELD, "critique.findings list is required.",
            "Add a findings list with at least one entry (severity, issue, location).",
            stage, "findings",
        ))
    else:
        findings = artifact["findings"]
        min_findings = 2 if level == "full" else 1
        if not isinstance(findings, list) or len(findings) < min_findings:
            violations.append(_violation(
                V.HOLLOW_CRITIQUE,
                f"critique.findings has {len(findings) if isinstance(findings, list) else 0} "
                f"item(s); minimum is {min_findings} for level '{level}'.",
                f"Add at least {min_findings} finding(s). A complex task with zero findings "
                "means you didn't look hard enough.",
                stage, "findings",
            ))
        if isinstance(findings, list) and len(findings) >= 1:
            violations.extend(_check_list_items(findings, "findings", stage, "findings"))
            for i, f in enumerate(findings):
                if not isinstance(f, dict):
                    continue
                if f.get("severity") not in ("blocker", "major", "minor"):
                    violations.append(_violation(
                        V.MISSING_FIELD,
                        f"findings[{i}].severity must be 'blocker', 'major', or 'minor'.",
                        "Set severity appropriately.",
                        stage, f"findings[{i}].severity",
                    ))
            if level == "full" and isinstance(findings, list):
                has_critical = any(
                    isinstance(f, dict) and f.get("severity") in ("blocker", "major")
                    for f in findings
                )
                if not has_critical:
                    violations.append(_violation(
                        V.HOLLOW_CRITIQUE,
                        "At FULL rigor, critique must contain ≥1 finding of severity "
                        "'blocker' or 'major'. Two minor findings alone is insufficient — "
                        "either find a real blocker/major or use no_issues_found with ≥80-char why.",
                        "Escalate the most serious finding to 'major' or 'blocker', or use "
                        "no_issues_found=true with a thorough justification.",
                        stage, "findings",
                    ))

    if "steelman" not in artifact or not str(artifact.get("steelman", "")).strip():
        violations.append(_violation(
            V.MISSING_FIELD, "critique.steelman is required.",
            "Provide the strongest counterargument to your conclusion.",
            stage, "steelman",
        ))

    return violations


def _gate_verify(artifact: dict, level: str, profile: str) -> list[dict]:
    """Validate the verify artifact. V5: evidence field gate."""
    violations: list[dict] = []
    stage = "verify"

    if "checks" not in artifact:
        violations.append(_violation(
            V.MISSING_FIELD, "verify.checks list is required.",
            "Add a checks list with {what, how, result, evidence} for each verification.",
            stage, "checks",
        ))
        return violations

    checks = artifact["checks"]
    if not isinstance(checks, list) or len(checks) < 1:
        violations.append(_violation(
            V.TOO_FEW_ITEMS, "verify.checks must have ≥1 entry.",
            "Add at least one {what, how, result} check.",
            stage, "checks",
        ))
        return violations

    violations.extend(_check_list_items(checks, "checks", stage, "checks"))

    any_has_evidence = False

    for i, check in enumerate(checks):
        if not isinstance(check, dict):
            continue
        for sub in ("what", "how", "result"):
            if sub not in check or not str(check.get(sub, "")).strip():
                violations.append(_violation(
                    V.MISSING_FIELD,
                    f"checks[{i}].{sub} is missing or empty.",
                    f"Provide a non-empty '{sub}' for each check.",
                    stage, f"checks[{i}].{sub}",
                ))

        how = str(check.get("how", "")).strip()
        what = str(check.get("what", "")).strip()
        result = str(check.get("result", "")).strip()
        evidence = str(check.get("evidence", "")).strip()

        # A2: how must be ≥15 chars AND contain a concrete method keyword
        if how:
            if len(how) < 15:
                violations.append(_violation(
                    V.UNVERIFIED_CLAIM,
                    f"checks[{i}].how is too short ({len(how)} chars; minimum 15): '{how}'.",
                    "Describe the concrete verification method in at least 15 characters.",
                    stage, f"checks[{i}].how",
                ))
            elif not _has_concrete_verify(how):
                violations.append(_violation(
                    V.UNVERIFIED_CLAIM,
                    f"checks[{i}].how='{how[:80]}' does not show a concrete verification method.",
                    "Use concrete language: ran, tested, recomputed, re-read, measured, "
                    "executed, checked against, compared.",
                    stage, f"checks[{i}].how",
                ))

        # A2: result must differ from what
        if result and what and _normalise(result) == _normalise(what):
            violations.append(_violation(
                V.UNVERIFIED_CLAIM,
                f"checks[{i}].result echoes checks[{i}].what verbatim. "
                "The result must state what you actually found, not repeat the question.",
                "Write what the verification actually produced, not a restatement of what was checked.",
                stage, f"checks[{i}].result",
            ))

        # V5: check for concrete evidence
        if evidence and _has_concrete_evidence(evidence):
            any_has_evidence = True

        # V7 (structured loop): optional per-check status drives the backtracking loop.
        status = str(check.get("status", "")).strip().lower()
        if status and status not in VERIFY_STATUS_VALUES:
            violations.append(_violation(
                V.EMPTY_OR_TRIVIAL,
                f"checks[{i}].status='{status}' is not a valid status.",
                f"Use one of: {', '.join(VERIFY_STATUS_VALUES)} (or omit status).",
                stage, f"checks[{i}].status",
            ))
        # #3 fix: a check cannot claim PASS when the LAST exit code in its evidence is non-zero.
        # In --exec mode the harness derives status from real exit codes, so this also stops a
        # model overriding harness-injected failure evidence with a fabricated PASS verdict.
        # (Looking at the final exit code, not any occurrence, avoids punishing honest
        # before/after evidence that quotes a prior failing run.)
        if status == "pass" and evidence and _evidence_ends_in_failure(evidence):
            violations.append(_violation(
                V.FABRICATION_RISK,
                f"checks[{i}] claims status='pass' but its evidence shows a non-zero exit "
                f"code (failure): '{evidence[:80]}'.",
                "A check whose evidence shows failure cannot be marked 'pass'. "
                "Set status='fail' and address the failure in revise.",
                stage, f"checks[{i}].status",
            ))

    # V5: at least one check must have concrete evidence
    if not any_has_evidence:
        violations.append(_violation(
            V.NO_EVIDENCE,
            "No check has a concrete evidence artifact. At least one check must include "
            "an 'evidence' field containing a digit, PASS/FAIL token, file:line reference, "
            "or quoted/backtick-wrapped output snippet.",
            "Add an 'evidence' field to at least one check with a concrete artifact "
            "(e.g., evidence='All 47 tests PASS', evidence='engine.py:123 confirmed', "
            "evidence='output: `{\"status\": \"ok\"}`').",
            stage, "checks",
        ))

    return violations


def _gate_revise(artifact: dict, level: str, profile: str,
                 critique_findings: list[dict] | None = None,
                 loop_count: int = 0) -> list[dict]:
    """Validate the revise artifact. V3: NOOP_FIX + token-overlap ≥0.4 mapping."""
    violations: list[dict] = []
    stage = "revise"

    if "fixes" not in artifact:
        violations.append(_violation(
            V.MISSING_FIELD, "revise.fixes list is required.",
            "Add a fixes list mapping finding_ref → change for every blocker/major finding.",
            stage, "fixes",
        ))
        return violations

    if "reverified" not in artifact:
        violations.append(_violation(
            V.MISSING_FIELD, "revise.reverified (bool) is required.",
            "Set reverified=true if anything changed, false otherwise.",
            stage, "reverified",
        ))

    fixes = artifact.get("fixes", [])
    if not isinstance(fixes, list):
        violations.append(_violation(
            V.MISSING_FIELD, "revise.fixes must be a list.",
            "Make fixes a list of {finding_ref, change} dicts.",
            stage, "fixes",
        ))
        return violations

    if fixes:
        violations.extend(_check_list_items(fixes, "fixes", stage, "fixes"))

    # V3: NOOP_FIX detection
    for i, fix in enumerate(fixes):
        if not isinstance(fix, dict):
            continue
        change = str(fix.get("change", "")).strip()
        if change and _is_noop_fix(change):
            violations.append(_violation(
                V.NOOP_FIX,
                f"fixes[{i}].change is a no-op intent statement with no concrete edit: "
                f"'{change[:120]}'. The fix must describe what was actually done.",
                "Use a concrete edit verb (changed/added/removed/replaced/rewrote/"
                "recomputed/corrected/refactored/renamed/set/updated) + what was changed.",
                stage, f"fixes[{i}].change",
            ))

    if critique_findings:
        critical = [
            f for f in critique_findings
            if isinstance(f, dict) and f.get("severity") in ("blocker", "major")
        ]
        if critical and not fixes:
            violations.append(_violation(
                V.UNMAPPED_FIX,
                f"Critique recorded {len(critical)} blocker/major finding(s) but "
                "revise.fixes is empty. Every blocker/major must be addressed.",
                "Add a fix entry for each blocker/major finding.",
                stage, "fixes",
            ))
        if critical and artifact.get("reverified") is not True:
            violations.append(_violation(
                V.NOT_ENOUGH_RIGOR,
                "Critique had blocker/major finding(s); reverified must be true.",
                "Set reverified=true to confirm fixes were actually applied and re-checked.",
                stage, "reverified",
            ))

    # V3: tightened finding→fix mapping — token-overlap ≥0.4 (was 20-char prefix)
    if critique_findings:
        critical = [
            f for f in critique_findings
            if isinstance(f, dict) and f.get("severity") in ("blocker", "major")
        ]
        for finding in critical:
            issue_text = str(finding.get("issue", "")).strip()
            mapped = False
            for fix in fixes:
                if not isinstance(fix, dict):
                    continue
                ref = str(fix.get("finding_ref", "")).strip()
                if not ref:
                    continue
                # V3: Jaccard token overlap ≥ 0.4
                overlap = _token_overlap(ref, issue_text)
                if overlap >= 0.4:
                    mapped = True
                    break
                # Fallback: substring containment (for very short refs)
                ref_norm = _normalise(ref)
                issue_norm = _normalise(issue_text)
                if ref_norm in issue_norm or issue_norm in ref_norm:
                    mapped = True
                    break
            if not mapped and fixes:
                violations.append(_violation(
                    V.UNMAPPED_FIX,
                    f"No fix found for blocker/major finding: '{issue_text}'. "
                    "finding_ref must share ≥40% token overlap (Jaccard) with the finding's issue.",
                    "Add a fix entry whose finding_ref closely matches this finding's issue description.",
                    stage, "fixes",
                ))

        # Tier-2 coherence: a fix that NAMES a finding must also make a change that
        # RELATES to it. Echoing the finding into finding_ref (to pass the mapping
        # check) while making an unrelated change is the gaming hole this closes.
        # Conservative: flag only when the change shares ZERO content with the finding.
        for i, fix in enumerate(fixes):
            if not isinstance(fix, dict):
                continue
            ref = str(fix.get("finding_ref", "")).strip()
            change = str(fix.get("change", "")).strip()
            if not ref or not change:
                continue
            best_issue, best_overlap = "", 0.0
            for finding in critique_findings:
                if not isinstance(finding, dict):
                    continue
                issue_text = str(finding.get("issue", "")).strip()
                ov = _token_overlap(ref, issue_text)
                if ov > best_overlap:
                    best_overlap, best_issue = ov, issue_text
            if best_issue and best_overlap >= 0.4:
                change_tokens = _content_tokens(change)
                issue_tokens = _content_tokens(best_issue)
                if change_tokens and issue_tokens and not (change_tokens & issue_tokens):
                    violations.append(_violation(
                        V.FIX_UNRELATED,
                        f"fixes[{i}].change describes work unrelated to the finding it "
                        f"references ('{best_issue[:80]}') — it shares no content with the "
                        "problem it claims to fix.",
                        "Describe the edit you actually made to address THIS finding.",
                        stage, f"fixes[{i}].change",
                    ))

    if fixes and artifact.get("reverified") is False:
        violations.append(_violation(
            V.NOT_ENOUGH_RIGOR,
            "reverified=false but fixes are present. You must re-verify anything you changed.",
            "Set reverified=true and confirm the fixes actually resolved the issues.",
            stage, "reverified",
        ))

    # V7: if loop_count >= _MAX_LOOP_COUNT, revise can still pass but we note it
    # (the caller handles requiring residual disclosure in deliver)

    return violations


# V11 anti-hollow: blatant, unqualified certainty/completeness claims. Deliberately a SMALL,
# specific PHRASE list (not common single words like "all" or "complete"), so a specific
# factual claim ("all 12 tests passed") is NOT flagged — only a global overclaim is. Honest
# limit: this catches the blatant cases; a model can dodge it by rephrasing (shape, not
# substance). It only fires when unresolved/assumed items actually remain (see _gate_deliver).
_OVERCLAIM_RE = re.compile(
    r"\b(?:fully|thoroughly|completely|100%)\s+(?:verified|tested|validated|confirmed|certain|proven)\b"
    r"|\bguaranteed\s+(?:correct|accurate|right|to\s+work)\b"
    r"|\bno\s+(?:limitations|caveats|open\s+questions|unknowns|uncertaint\w+|remaining\s+\w+)\b"
    r"|\b(?:with\s+)?(?:full|complete|total|absolute)\s+(?:confidence|certainty)\b"
    r"|\bnothing\s+(?:left|remains)\s+(?:to\s+verify|unverified|unchecked|unresolved)\b",
    re.IGNORECASE,
)


def _gate_deliver(artifact: dict, level: str, profile: str,
                  research_done: bool = False,
                  pending_limitations: list[str] | None = None,
                  loop_count: int = 0) -> list[dict]:
    """Validate the deliver artifact. V8: pending_limitations must be covered."""
    violations: list[dict] = []
    stage = "deliver"

    if "summary" not in artifact or not str(artifact.get("summary", "")).strip():
        violations.append(_violation(
            V.MISSING_FIELD, "deliver.summary is required and must be non-empty.",
            "Lead with the answer/result in summary.",
            stage, "summary",
        ))
    else:
        # V1: junk check on summary
        summary = str(artifact.get("summary", "")).strip()
        junk_v = _junk_violation(summary, "summary", stage)
        if junk_v:
            violations.append(junk_v)
        # V11 anti-hollow: if unresolved/assumed items remain, the summary must not make a
        # blatant unqualified certainty/completeness claim — surface the uncertainty up front
        # rather than letting the headline read clean while caveats hide in sub-fields.
        has_uncertainty = bool(pending_limitations) or loop_count >= _MAX_LOOP_COUNT
        if has_uncertainty and _OVERCLAIM_RE.search(summary):
            violations.append(_violation(
                V.OVERCLAIMED_SUMMARY,
                "deliver.summary makes an unqualified certainty/completeness claim while "
                "unresolved or assumed items remain (research unknowns, assumed-source claims, "
                "or residual risk at the loop cap). Surface that uncertainty in the summary.",
                "Qualify the summary: name what remains unverified/assumed/uncertain up front, "
                "not only in limitations.",
                stage, "summary",
            ))

    if "limitations" not in artifact:
        violations.append(_violation(
            V.MISSING_FIELD, "deliver.limitations is required.",
            "List limitations/caveats, or write 'none, because…' explicitly.",
            stage, "limitations",
        ))
    else:
        lims = artifact["limitations"]
        if not isinstance(lims, list):
            violations.append(_violation(
                V.MISSING_FIELD, "deliver.limitations must be a list.",
                "Use a list, e.g. ['none, because the task was fully verifiable'].",
                stage, "limitations",
            ))
        elif len(lims) == 0:
            violations.append(_violation(
                V.EMPTY_OR_TRIVIAL,
                "deliver.limitations is an empty list. If there are no limitations, "
                "say so explicitly.",
                "Add at least one entry, e.g. 'none, because…'.",
                stage, "limitations",
            ))
        else:
            # V8: check pending_limitations are covered.
            # FIX (2026-06-13, found live): the old check joined ALL limitations into
            # one string and measured Jaccard overlap of each pending item against that
            # blob. Because Jaccard divides by the UNION, the score shrank as more
            # disclosures were added — so once several pending items existed, SHORT
            # items (e.g. "Is the CAC $30-80 estimate sourced?") could never reach the
            # threshold even when disclosed verbatim. Disclosing more made it HARDER to
            # pass. Now each pending item is checked against EACH limitation
            # individually, using coverage-of-the-pending-item (shared substantive
            # tokens / the pending item's own tokens) — length-independent, so the
            # number of disclosures no longer affects whether any one item is covered.
            if pending_limitations:
                lim_token_sets = [_content_tokens(str(l)) for l in lims]
                uncovered: list[str] = []
                for pending in pending_limitations:
                    p_tokens = _content_tokens(str(pending))
                    if not p_tokens:
                        continue  # nothing substantive to match on
                    covered = any(
                        lt and len(p_tokens & lt) / len(p_tokens) >= 0.5
                        for lt in lim_token_sets
                    )
                    if not covered:
                        uncovered.append(pending)
                if uncovered:
                    violations.append(_violation(
                        V.UNCOVERED_LIMITATION,
                        f"deliver.limitations does not cover {len(uncovered)} unresolved "
                        f"uncertainty item(s) from research/verify: {uncovered[:3]}. "
                        "Unresolved unknowns and assumed-source claims must be disclosed at delivery.",
                        "Add entries to limitations that address each pending uncertainty.",
                        stage, "limitations",
                    ))
            # V7/V10: at the loop cap, require an explicit, substantive 'residual_risk' field —
            # not a keyword anywhere in limitations. The old substring scan was satisfied by a
            # benign "no remaining work" (the word 'remaining' alone passed); a dedicated field
            # must be consciously filled and is surfaced in the certificate for human review.
            # (Honest limit: the engine ensures the disclosure EXISTS and is substantive; it
            # cannot judge whether the disclosure is truthful or complete.)
            if loop_count >= _MAX_LOOP_COUNT:
                residual = artifact.get("residual_risk", "")
                if isinstance(residual, list):
                    residual_text = " ".join(str(r) for r in residual).strip()
                else:
                    residual_text = str(residual or "").strip()
                if not residual_text:
                    violations.append(_violation(
                        V.MISSING_FIELD,
                        f"Backtracking loop count ({loop_count}) reached the cap ({_MAX_LOOP_COUNT}). "
                        "deliver must include a non-empty 'residual_risk' field documenting the "
                        "issues that remain unresolved/unverified after the loop cap.",
                        "Add a 'residual_risk' field describing what could not be closed within the "
                        "loop and why it still matters.",
                        stage, "residual_risk",
                    ))
                else:
                    junk_v = _junk_violation(residual_text, "residual_risk", stage)
                    if junk_v:
                        violations.append(junk_v)

    if "sources" not in artifact:
        violations.append(_violation(
            V.MISSING_FIELD, "deliver.sources is required (may be empty list if no facts asserted).",
            "Add a 'sources' list.",
            stage, "sources",
        ))
    else:
        sources = artifact["sources"]
        if not isinstance(sources, list):
            violations.append(_violation(
                V.MISSING_FIELD, "deliver.sources must be a list.",
                "Use a list of source strings.",
                stage, "sources",
            ))
        elif research_done and len(sources) == 0:
            violations.append(_violation(
                V.MISSING_FIELD,
                "Facts were asserted in the research stage but deliver.sources is empty. "
                "Cite at least one source.",
                "Add at least one source entry matching the facts asserted in research.",
                stage, "sources",
            ))
        else:
            # V4: FABRICATION_RISK for placeholder sources in deliver.
            # Sources may be bare strings OR dicts {text, type?} per CONTRACT line 167.
            # Mirror the research.facts handling: extract the text and honor a declared type
            # so a dict-shaped source cannot smuggle a placeholder URL past the check.
            for i, src in enumerate(sources):
                if isinstance(src, dict):
                    src_str = str(src.get("text", "")).strip()
                    declared_type = str(src.get("type", "")).strip().lower()
                    if declared_type not in ("url", "file", "tool_output", "assumed"):
                        declared_type = ""
                else:
                    src_str = str(src).strip()
                    declared_type = ""
                src_type = declared_type if declared_type else _infer_source_type(src_str)
                if _is_fabrication_source_v2(src_str, src_type):
                    violations.append(_violation(
                        V.FABRICATION_RISK,
                        f"deliver.sources[{i}] '{src_str}' is a placeholder/fabricated source "
                        "(empty or obvious example URL).",
                        "Replace with a real source URL or remove if unverified.",
                        stage, f"sources[{i}]",
                    ))

    return violations


# Map stage names to gate functions (basic routing; Engine.submit handles special cases)
_GATES: dict[str, Any] = {
    "classify":  _gate_classify,
    "frame":     _gate_frame,
    "research":  _gate_research,
    "plan":      _gate_plan,
    "draft":     _gate_draft,
    "critique":  _gate_critique,
    "verify":    _gate_verify,
    "revise":    _gate_revise,
    "deliver":   _gate_deliver,
}


# ---------------------------------------------------------------------------
# V8: Pending limitations extraction from research artifact
# ---------------------------------------------------------------------------

def _extract_pending_limitations(research_artifact: dict | None,
                                 verify_checks: list[dict] | None = None) -> list[str]:
    """
    Extract items that must appear in deliver.limitations:
    - research.unknowns
    - claims from assumed-type sources
    - verify checks whose result is unconfirmed
    """
    pending: list[str] = []

    if research_artifact and isinstance(research_artifact, dict):
        # unknowns
        unknowns = research_artifact.get("unknowns", [])
        if isinstance(unknowns, list):
            for u in unknowns:
                u_str = str(u).strip()
                if u_str:
                    pending.append(u_str)

        # assumed-source claims
        facts = research_artifact.get("facts", [])
        if isinstance(facts, list):
            for fact in facts:
                if not isinstance(fact, dict):
                    continue
                source = str(fact.get("source", "")).strip()
                declared_type = fact.get("type", "").strip().lower()
                source_type = declared_type if declared_type in ("url", "file", "tool_output", "assumed") \
                              else _infer_source_type(source)
                if source_type == "assumed":
                    claim = str(fact.get("claim", "")).strip()
                    if claim:
                        pending.append(f"assumed: {claim}")

    # verify checks that could not confirm
    if verify_checks:
        for check in verify_checks:
            if not isinstance(check, dict):
                continue
            result = str(check.get("result", "")).strip()
            what = str(check.get("what", "")).strip()
            if result and _result_is_unconfirmed(result) and what:
                pending.append(f"unconfirmed: {what}")

    return pending


# ---------------------------------------------------------------------------
# Engine class
# ---------------------------------------------------------------------------

class Engine:
    """
    State-machine enforcement engine for the fable_method protocol (v2).

    Sessions are persisted as JSON files in `store_dir` so that stateless
    callers (e.g. an MCP server) can reload them between calls.
    """

    def __init__(self, store_dir: str = "~/.fable_method") -> None:
        self.store_dir = Path(store_dir).expanduser()
        self.store_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _session_path(self, session_id: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_\-]", "_", session_id)
        return self.store_dir / f"{safe}.json"

    # Fields every reader/writer assumes exist, with their default factories.
    _LIST_FIELDS = ("completed_stages", "gate_history", "iterations",
                    "pending_limitations", "escalated_to")

    def _migrate(self, session: dict) -> dict:
        """Backfill and repair a loaded session so older-schema or partially-corrupt
        files don't crash downstream readers/writers.

        Why this exists (robustness findings #4/#5/#6): the write path appends to
        gate_history directly and the revise path does int math on loop_count, so a
        session missing gate_history or carrying a non-int loop_count used to crash on
        the next submit. Centralizing the repair on load means every entry point
        (submit, get_state, provide_answers, finalize, set_rigor) is protected once.
        """
        if not isinstance(session, dict):
            raise ValueError("Session data is not a JSON object.")
        session.setdefault("mode", "headless")
        session.setdefault("awaiting_input", False)
        if not isinstance(session.get("artifacts"), dict):
            session["artifacts"] = {}
        for field in self._LIST_FIELDS:
            if not isinstance(session.get(field), list):
                session[field] = []
        if not isinstance(session.get("safety"), dict):
            session["safety"] = {"refused": False, "category": ""}
        # loop_count must be a non-negative int — the revise path does int comparison
        # and addition on it, so a corrupt non-int value used to crash that path (#6).
        loop_count = session.get("loop_count", 0)
        if isinstance(loop_count, bool) or not isinstance(loop_count, int):
            try:
                loop_count = int(loop_count)
            except (TypeError, ValueError):
                loop_count = 0
        if loop_count < 0:
            loop_count = 0
        session["loop_count"] = loop_count
        return session

    def _load(self, session_id: str) -> dict:
        path = self._session_path(session_id)
        if not path.exists():
            raise KeyError(f"Session '{session_id}' not found.")
        # Degrade gracefully (#4): a truncated/corrupt/non-dict file should raise a
        # clear ValueError, not leak an opaque JSONDecodeError mid-parse to callers.
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            raise ValueError(
                f"Session '{session_id}' could not be read (corrupt or truncated): {exc}"
            ) from exc
        return self._migrate(data)

    def _save(self, session: dict) -> None:
        """Atomic write (#1): stream to a temp file in the same directory, fsync, then
        os.replace() into place. os.replace is atomic on a single filesystem, so an
        interrupted/failed write can never truncate or destroy the existing session —
        a reader sees either the old complete file or the new complete file."""
        path = self._session_path(session["session_id"])
        tmp = path.with_name(path.name + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(session, fh, indent=2, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        finally:
            # Never leave a stray temp file behind on failure.
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _required_stages(self, session: dict) -> list[str]:
        level = session.get("level")
        involves_facts = session.get("involves_facts", True)
        if level is None:
            return []
        stages = list(REQUIRED_STAGES.get(level, REQUIRED_STAGES["full"]))
        if level == "medium" and not involves_facts and "research" in stages:
            stages.remove("research")
        return stages

    def _next_stage(self, session: dict) -> str | None:
        completed = set(session.get("completed_stages", []))
        for stage in self._required_stages(session):
            if stage not in completed:
                return stage
        return None

    def _get_critique_findings(self, session: dict) -> list[dict]:
        artifacts = session.get("artifacts", {})
        critique = artifacts.get("critique", {})
        return critique.get("findings", []) if isinstance(critique, dict) else []

    def _research_had_real_facts(self, session: dict) -> bool:
        artifacts = session.get("artifacts", {})
        research = artifacts.get("research")
        if not isinstance(research, dict):
            return False
        if research.get("no_research_needed"):
            return False
        facts = research.get("facts", [])
        return isinstance(facts, list) and len(facts) > 0

    def _is_done(self, session: dict) -> bool:
        required = self._required_stages(session)
        if not required:
            return False
        completed = set(session.get("completed_stages", []))
        return all(s in completed for s in required)

    def _get_pending_limitations(self, session: dict) -> list[str]:
        """V8: Collect all pending_limitations from research unknowns, assumed sources, unconfirmed verify."""
        artifacts = session.get("artifacts", {})
        research = artifacts.get("research")
        verify = artifacts.get("verify")
        verify_checks = verify.get("checks", []) if isinstance(verify, dict) else None
        # Also use session-stored pending_limitations (accumulated over time)
        base = list(session.get("pending_limitations", []))
        derived = _extract_pending_limitations(research, verify_checks)
        # Merge, dedup
        seen = set(base)
        for item in derived:
            if item not in seen:
                base.append(item)
                seen.add(item)
        return base

    def _check_refused(self, session: dict) -> dict | None:
        """Return a refused response if the session was refused."""
        safety = session.get("safety", {})
        if safety.get("refused"):
            return {
                "accepted": False,
                "refused": True,
                "status": "refused",
                "category": safety.get("category", ""),
                "reason": safety.get("reason", "Session was refused by safety screen."),
                "violations": [_violation(
                    V.REFUSED,
                    "This session was refused by the safety screen.",
                    "The goal matched a prohibited category. "
                    "Use override_safety=True on create_session to proceed with accountability logging.",
                )],
            }
        return None

    # ------------------------------------------------------------------
    # V2: Auto-escalation helper
    # ------------------------------------------------------------------

    def _check_auto_escalation(self, session: dict, stage: str, artifact: dict) -> None:
        """
        V2 (hardened): If research or critique artifact has any finding of severity
        'major' or 'blocker', auto-escalate session level to FULL and record in
        gate_history + escalated_to. The threshold was raised from 'blocker'-only to
        'major'-or-worse because severity is self-assigned: pinning escalation to the
        single highest label let a model dodge it by writing 'major' instead of 'blocker'.
        Escalation is raise-only, so this can never reduce rigor.
        """
        should_escalate = False
        reason = ""
        _ESCALATING_SEVERITIES = {"major", "blocker"}

        if stage == "critique":
            findings = artifact.get("findings", [])
            if isinstance(findings, list):
                serious = [f for f in findings
                           if isinstance(f, dict) and f.get("severity") in _ESCALATING_SEVERITIES]
                if serious:
                    should_escalate = True
                    reason = (f"Auto-escalated to FULL: critique contains "
                              f"{len(serious)} major/blocker finding(s).")

        elif stage == "research":
            risk_flags = artifact.get("risk_flags", [])
            if isinstance(risk_flags, list):
                real_flags = [str(f).strip() for f in risk_flags if str(f).strip()]
                if real_flags:
                    should_escalate = True
                    reason = (f"Auto-escalated to FULL: research surfaced "
                              f"risk_flags: {real_flags}.")

        if should_escalate:
            current_level = session.get("level", "low")
            if current_level != "full":
                session["level"] = "full"
                escalation_record = {
                    "stage": stage,
                    "timestamp": time.time(),
                    "reason": reason,
                    "from_level": current_level,
                    "to_level": "full",
                }
                if "escalated_to" not in session:
                    session["escalated_to"] = []
                session["escalated_to"].append(escalation_record)
                session["gate_history"].append({
                    "stage": stage,
                    "event": "AUTO_ESCALATION",
                    "reason": reason,
                    "timestamp": time.time(),
                })

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_session(
        self,
        goal: str,
        profile: str = "universal",
        rigor: str = "adaptive",
        involves_facts: bool | None = None,
        mode: str = "headless",
        override_safety: bool = False,
    ) -> dict:
        """
        Create a new session and return the initial state dict.

        V9: mode param ("headless"|"interactive").
        V10: safety screen; may return {refused:true, ...}; override_safety logs bypass.

        Returns: {session_id, status, current_stage, rigor, profile,
                  instructions, required_artifact, next_action}
        May return: {refused:true, category, reason, session_id, status:"refused"}
        """
        valid_profiles = {"universal", "ai_builder", "entrepreneur"}
        valid_rigor = {"low", "medium", "full", "adaptive"}
        valid_modes = {"headless", "interactive"}

        if profile not in valid_profiles:
            profile = "universal"
        if rigor not in valid_rigor:
            rigor = "adaptive"
        if mode not in valid_modes:
            mode = "headless"

        session_id = str(uuid.uuid4())
        now = time.time()

        # V10: safety screen
        refused, category, reason = _safety_screen(goal)
        # When override_safety=True, the session proceeds (refused=False in record)
        # but the override is logged separately for audit accountability.
        safety_record: dict = {
            "refused": refused and not override_safety,
            "category": category if refused else "",
        }

        if refused and not override_safety:
            # Persist the refused session for audit trail
            session: dict = {
                "session_id": session_id,
                "goal": goal,
                "profile": profile,
                "rigor": rigor,
                "level": None,
                "mode": mode,
                "involves_facts": involves_facts if involves_facts is not None else True,
                "current_stage": None,
                "status": "refused",
                "completed_stages": [],
                "artifacts": {},
                "gate_history": [],
                "created_at": now,
                "updated_at": now,
                "safety": safety_record,
                "loop_count": 0,
                "iterations": [],
                "pending_limitations": [],
                "escalated_to": [],
                "awaiting_input": False,
            }
            self._save(session)
            return {
                "refused": True,
                "category": category,
                "reason": reason,
                "session_id": session_id,
                "status": "refused",
            }

        if rigor == "adaptive":
            current_stage = "classify"
            level = None
        else:
            current_stage = REQUIRED_STAGES[rigor][0]
            level = rigor

        session = {
            "session_id": session_id,
            "goal": goal,
            "profile": profile,
            "rigor": rigor,
            "level": level,
            "mode": mode,
            "involves_facts": involves_facts if involves_facts is not None else True,
            "current_stage": current_stage,
            "status": "in_progress",
            "completed_stages": [],
            "artifacts": {},
            "gate_history": [],
            "created_at": now,
            "updated_at": now,
            # v2 fields
            "safety": safety_record,
            "loop_count": 0,
            "iterations": [],
            "pending_limitations": [],
            "escalated_to": [],
            "awaiting_input": False,
        }

        if refused and override_safety:
            session["safety"]["override_logged"] = True
            session["safety"]["override_reason"] = (
                "Operator explicitly set override_safety=True. Bypass logged for accountability."
            )

        self._save(session)

        instructions = _get_instructions(profile, current_stage, rigor)
        required_artifact = _required_artifact_schema(current_stage)

        return {
            "session_id": session_id,
            "status": "in_progress",
            "current_stage": current_stage,
            "rigor": rigor,
            "profile": profile,
            "mode": mode,
            "instructions": instructions,
            "required_artifact": required_artifact,
            "next_action": f"Call submit(session_id, '{current_stage}', artifact) with the required artifact.",
        }

    def provide_answers(self, session_id: str, answers) -> dict:
        """
        V9: Advance an awaiting_input session by recording answers to frame questions.

        Accepts either a list of strings OR a dict mapping question -> answer (both the
        CLI harness and the MCP server pass a dict). A dict is normalized to
        ["<question>: <answer>", ...] so the human's answer text is preserved — a bare
        list.extend(dict) would keep only the question keys and silently drop the answers.

        Records answers into the frame artifact and advances the session.
        """
        # Normalize input shape (bug (b) fix): dict -> "q: a" strings; list -> as-is.
        if isinstance(answers, dict):
            answers = [f"{q}: {a}" for q, a in answers.items()]
        elif isinstance(answers, (list, tuple)):
            answers = [str(a) for a in answers]
        elif answers is None:
            answers = []
        else:
            answers = [str(answers)]

        session = self._load(session_id)

        # Check refused
        refused_resp = self._check_refused(session)
        if refused_resp:
            return refused_resp

        if not session.get("awaiting_input"):
            return {
                "accepted": False,
                "error": "Session is not awaiting input. No pending questions.",
            }

        # Record answers into the frame artifact
        frame_artifact = session.get("artifacts", {}).get("frame", {})
        if not isinstance(frame_artifact, dict):
            frame_artifact = {}

        # Guard (#7): a corrupt frame artifact could carry a non-list _provided_answers,
        # and .extend() on a non-list would wedge the interactive session.
        existing_answers = frame_artifact.get("_provided_answers", [])
        if not isinstance(existing_answers, list):
            existing_answers = []
        existing_answers.extend(answers)
        frame_artifact["_provided_answers"] = existing_answers

        # Mark questions as answered
        frame_artifact["_questions_answered"] = True
        session["artifacts"]["frame"] = frame_artifact

        # Clear awaiting_input flag and advance
        session["awaiting_input"] = False
        session["status"] = "in_progress"

        # Find the next stage after frame (which was paused)
        completed = set(session.get("completed_stages", []))
        next_st = None
        for s in self._required_stages(session):
            if s not in completed:
                next_st = s
                break
        session["current_stage"] = next_st

        session["gate_history"].append({
            "stage": "frame",
            "event": "ANSWERS_PROVIDED",
            "answer_count": len(answers),
            "timestamp": time.time(),
        })

        session["updated_at"] = time.time()
        self._save(session)

        resolved_level = session.get("level") or session.get("rigor") or "full"
        profile = session.get("profile", "universal")

        if next_st:
            instructions = _get_instructions(profile, next_st, resolved_level)
            required_artifact = _required_artifact_schema(next_st)
            next_action = f"Call submit(session_id, '{next_st}', artifact)."
        else:
            instructions = "All required stages are complete. Call finalize() to close the session."
            required_artifact = {}
            next_action = "Call finalize(session_id)."

        return {
            "accepted": True,
            "answers_recorded": len(answers),
            "current_stage": next_st,
            "instructions": instructions,
            "required_artifact": required_artifact,
            "next_action": next_action,
        }

    def get_state(self, session_id: str) -> dict:
        """Return a full session snapshot. V2: includes mode, loop_count, etc."""
        session = self._load(session_id)
        state = dict(session)
        state["done"] = self._is_done(session)
        # Ensure v2 fields present for older sessions
        state.setdefault("mode", "headless")
        state.setdefault("loop_count", 0)
        state.setdefault("iterations", [])
        state.setdefault("pending_limitations", [])
        state.setdefault("escalated_to", [])
        state.setdefault("safety", {"refused": False, "category": ""})
        state.setdefault("awaiting_input", False)
        return state

    def submit(self, session_id: str, stage: str, artifact: dict) -> dict:
        """
        Validate `artifact` for `stage` via that stage's gate.

        V7: Passing revise routes BACK to verify (loop_count++).
        V7: revise.reopen resets later stages and records an iteration.
        V9: Interactive frame with questions pauses and returns needs_user_input.
        """
        session = self._load(session_id)

        # V10: Refuse if session was refused
        refused_resp = self._check_refused(session)
        if refused_resp:
            return refused_resp

        # V9: If awaiting_input, only provide_answers can advance
        if session.get("awaiting_input"):
            return {
                "accepted": False,
                "stage": stage,
                "violations": [_violation(
                    V.NEEDS_USER_INPUT,
                    "Session is awaiting user input (questions from the frame stage). "
                    "Call provide_answers() before submitting the next stage.",
                    "Use provide_answers(session_id, answers=[...]) to advance.",
                    stage,
                )],
                "retry": False,
                "needs_user_input": True,
                "status": "awaiting_input",
            }

        # Type guard (#3): every gate indexes the artifact as a dict. A top-level
        # non-dict artifact (list/str/int/None) — reachable via the MCP server — used
        # to crash the gate; reject it cleanly instead.
        if not isinstance(artifact, dict):
            return {
                "accepted": False,
                "stage": stage,
                "violations": [_violation(
                    V.WRONG_ARTIFACT_TYPE,
                    f"Artifact must be a JSON object, got {type(artifact).__name__}.",
                    "Submit the stage artifact as a JSON object matching the required schema.",
                    stage,
                )],
                "retry": True,
            }

        current_stage = session.get("current_stage")
        level = session.get("level")
        rigor = session.get("rigor")
        profile = session.get("profile", "universal")
        goal = session.get("goal", "")
        mode = session.get("mode", "headless")
        loop_count = session.get("loop_count", 0)

        # ----- Order check -----
        if stage != current_stage:
            v = _violation(
                V.OUT_OF_ORDER,
                f"Expected stage '{current_stage}' but received '{stage}'.",
                f"Submit the '{current_stage}' stage artifact first.",
                stage,
            )
            return {"accepted": False, "stage": stage, "violations": [v], "retry": True}

        # ----- V7 reopen short-circuit (before gate validation) -----
        # revise.reopen is a major replanning signal that bypasses normal fix-mapping.
        # It resets later stages and routes back to the reopen stage immediately.
        if stage == "revise":
            reopen = artifact.get("reopen", "")
            if reopen in ("plan", "draft"):
                # Skip gate validation; apply reopen logic directly
                reopen_idx = STAGE_ORDER.index(reopen)
                stages_to_reset = [s for s in STAGE_ORDER[reopen_idx:]
                                   if s in session["completed_stages"]]
                for s in stages_to_reset:
                    session["completed_stages"].remove(s)
                    if s in session.get("artifacts", {}):
                        del session["artifacts"][s]
                # Record iteration
                iteration_record = {
                    "timestamp": time.time(),
                    "reopen_stage": reopen,
                    "loop_count_at_reopen": loop_count,
                    "stages_reset": stages_to_reset,
                }
                session.setdefault("iterations", []).append(iteration_record)
                # Route back to reopen stage
                session["current_stage"] = reopen
                session["status"] = "in_progress"
                gate_record = {
                    "stage": stage,
                    "timestamp": time.time(),
                    "passed": True,
                    "event": "REOPEN",
                    "reopen": reopen,
                }
                session["gate_history"].append(gate_record)
                session["updated_at"] = time.time()
                self._save(session)
                resolved_level = session.get("level") or rigor or "full"
                instructions = _get_instructions(profile, reopen, resolved_level)
                required_artifact = _required_artifact_schema(reopen)
                return {
                    "accepted": True,
                    "done": False,
                    "current_stage": reopen,
                    "instructions": instructions,
                    "required_artifact": required_artifact,
                    "next_action": f"Call submit(session_id, '{reopen}', artifact) — major replan.",
                    "loop_count": loop_count,
                    "iterations": session.get("iterations", []),
                    "iteration_recorded": True,
                }

        # ----- Run gate -----
        effective_level = level or rigor or "full"

        if stage == "classify":
            violations = _gate_classify(artifact, effective_level, profile, goal=goal)
        elif stage == "frame":
            violations = _gate_frame(artifact, effective_level, profile, goal=goal)
        elif stage == "research":
            violations = _gate_research(artifact, effective_level, profile, session=session)
        elif stage == "revise":
            critique_findings = self._get_critique_findings(session)
            violations = _gate_revise(artifact, effective_level, profile,
                                      critique_findings, loop_count=loop_count)
        elif stage == "deliver":
            research_done = self._research_had_real_facts(session)
            pending_lims = self._get_pending_limitations(session)
            violations = _gate_deliver(artifact, effective_level, profile,
                                       research_done=research_done,
                                       pending_limitations=pending_lims,
                                       loop_count=loop_count)
        else:
            gate_fn = _GATES.get(stage)
            if gate_fn is None:
                violations = []
            else:
                violations = gate_fn(artifact, effective_level, profile)

        # Tier-2 cross-stage coherence: the artifact must connect to the goal and the
        # work done so far. Conservative — fires only on a clear break (zero overlap).
        violations = list(violations) + _coherence_violations(stage, artifact, session, goal)

        # Overlay checks
        overlay_checks = _get_overlay_checks(profile, stage)

        # ----- Record gate result -----
        gate_record = {
            "stage": stage,
            "timestamp": time.time(),
            "passed": len(violations) == 0,
            "violations": violations,
        }
        session["gate_history"].append(gate_record)

        if violations:
            self._save(session)
            result = {
                "accepted": False,
                "stage": stage,
                "violations": violations,
                "retry": True,
                "loop_count": loop_count,
            }
            if overlay_checks:
                result["advisory_checks"] = overlay_checks
            return result

        # ----- PASS: update session state -----

        # V2: Auto-escalation check (before recording artifact)
        self._check_auto_escalation(session, stage, artifact)
        # Re-read effective_level after possible escalation
        effective_level = session.get("level") or rigor or "full"

        session["artifacts"][stage] = artifact

        if stage not in session["completed_stages"]:
            session["completed_stages"].append(stage)

        # ---- Handle classify: resolve level ----
        if stage == "classify":
            selected_level = artifact["selected_level"]
            session["level"] = selected_level
            effective_level = selected_level
            completed = set(session["completed_stages"])
            next_st = None
            for s in self._required_stages(session):
                if s not in completed:
                    next_st = s
                    break
            session["current_stage"] = next_st

        # ---- Handle frame: V9 interactive mode ----
        elif stage == "frame":
            questions = artifact.get("questions", [])
            if questions and mode == "interactive":
                # Pause and ask user
                session["awaiting_input"] = True
                session["status"] = "awaiting_input"
                session["updated_at"] = time.time()
                self._save(session)
                return {
                    "accepted": True,
                    "needs_user_input": True,
                    "questions": questions,
                    "next_action": "answer_questions",
                    "status": "awaiting_input",
                    "loop_count": loop_count,
                }
            elif questions and mode == "headless":
                # Headless: stamp proceeded_without_answers
                session["proceeded_without_answers"] = True
                session.setdefault("unanswered_questions", []).extend(questions)
            # Advance normally
            completed = set(session["completed_stages"])
            next_st = None
            for s in self._required_stages(session):
                if s not in completed:
                    next_st = s
                    break
            session["current_stage"] = next_st

        # ---- Handle revise: V7 backtracking loop ----
        elif stage == "revise":
            # Note: reopen is handled BEFORE gate validation (short-circuit above).
            # If we reach here, reopen was absent or not a valid stage.

            # V7: if revise passed with real changes (reverified=true + ≥1 concrete fix),
            # route BACK to verify
            reverified = artifact.get("reverified", False)
            fixes = artifact.get("fixes", [])
            has_real_changes = (
                reverified is True
                and isinstance(fixes, list)
                and len(fixes) > 0
                and all(
                    isinstance(f, dict) and str(f.get("change", "")).strip()
                    for f in fixes
                )
            )

            if has_real_changes and loop_count < _MAX_LOOP_COUNT:
                # Route BACK to verify
                new_loop_count = loop_count + 1
                session["loop_count"] = new_loop_count
                # Reset verify from completed stages and artifacts so it must be re-submitted
                if "verify" in session["completed_stages"]:
                    session["completed_stages"].remove("verify")
                if "verify" in session.get("artifacts", {}):
                    del session["artifacts"]["verify"]
                session["current_stage"] = "verify"
                session["status"] = "in_progress"
                session["updated_at"] = time.time()
                self._save(session)
                resolved_level = session.get("level") or rigor or "full"
                instructions = _get_instructions(profile, "verify", resolved_level)
                required_artifact = _required_artifact_schema("verify")
                return {
                    "accepted": True,
                    "done": False,
                    "current_stage": "verify",
                    "instructions": instructions,
                    "required_artifact": required_artifact,
                    "next_action": "Call submit(session_id, 'verify', artifact) — re-verify the fixes.",
                    "loop_count": new_loop_count,
                    "loop_back": True,
                    "message": f"Fixes recorded. Re-verifying (loop {new_loop_count}/{_MAX_LOOP_COUNT}).",
                }
            else:
                # No real changes OR loop cap reached — advance to deliver
                completed = set(session["completed_stages"])
                next_st = None
                for s in self._required_stages(session):
                    if s not in completed:
                        next_st = s
                        break
                session["current_stage"] = next_st

        # ---- V8: After research passes, update pending_limitations ----
        elif stage == "research":
            new_pending = _extract_pending_limitations(artifact)
            existing = set(session.get("pending_limitations", []))
            for item in new_pending:
                if item not in existing:
                    session.setdefault("pending_limitations", []).append(item)
                    existing.add(item)
            # Advance normally
            completed = set(session["completed_stages"])
            next_st = None
            for s in self._required_stages(session):
                if s not in completed:
                    next_st = s
                    break
            session["current_stage"] = next_st

        # ---- V8: After verify passes, update pending_limitations with unconfirmed results ----
        elif stage == "verify":
            checks = artifact.get("checks", [])
            new_pending = _extract_pending_limitations(None, checks)
            existing = set(session.get("pending_limitations", []))
            for item in new_pending:
                if item not in existing:
                    session.setdefault("pending_limitations", []).append(item)
                    existing.add(item)

            # V7 backtracking loop (multi-cycle): if this is a RE-verify (we have already
            # looped at least once) and it STILL reports an unresolved check, route back to
            # revise so the fixes get another pass — up to _MAX_LOOP_COUNT cycles. The loop is
            # driven by evidence (a problem remains), not a fixed number. A clean re-verify
            # exits to deliver. At the cap, we stop looping and deliver requires residual-risk
            # disclosure (see _gate_deliver). The first verify (loop_count 0) advances normally
            # to revise via the standard ordering.
            if (loop_count >= 1
                    and loop_count < _MAX_LOOP_COUNT
                    and "revise" in session.get("completed_stages", [])
                    and _verify_has_unresolved_check(checks)):
                session["completed_stages"].remove("revise")
                if "revise" in session.get("artifacts", {}):
                    del session["artifacts"]["revise"]
                session["current_stage"] = "revise"
                session["status"] = "in_progress"
                session["gate_history"].append({
                    "stage": "verify",
                    "event": "REVERIFY_FOUND_ISSUE",
                    "reason": "Re-verify still reports an unresolved/failing check; routing back to revise.",
                    "loop_count": loop_count,
                    "timestamp": time.time(),
                })
                session["updated_at"] = time.time()
                self._save(session)
                resolved_level = session.get("level") or rigor or "full"
                instructions = _get_instructions(profile, "revise", resolved_level)
                required_artifact = _required_artifact_schema("revise")
                return {
                    "accepted": True,
                    "done": False,
                    "current_stage": "revise",
                    "instructions": instructions,
                    "required_artifact": required_artifact,
                    "next_action": "Call submit(session_id, 'revise', artifact) — re-verify still found an issue.",
                    "loop_count": loop_count,
                    "loop_back": True,
                    "message": f"Re-verify still shows an unresolved check; looping back to revise "
                               f"(loop {loop_count}/{_MAX_LOOP_COUNT}).",
                }

            # Advance normally
            completed = set(session["completed_stages"])
            next_st = None
            for s in self._required_stages(session):
                if s not in completed:
                    next_st = s
                    break
            session["current_stage"] = next_st

        else:
            # Default: advance to next required stage
            completed = set(session["completed_stages"])
            next_st = None
            for s in self._required_stages(session):
                if s not in completed:
                    next_st = s
                    break
            session["current_stage"] = next_st

        if session["current_stage"] is None:
            session["status"] = "ready_to_finalize"

        session["updated_at"] = time.time()
        self._save(session)

        resolved_level = session.get("level") or session.get("rigor") or "full"
        next_stage = session.get("current_stage")

        if next_stage:
            instructions = _get_instructions(profile, next_stage, resolved_level)
            required_artifact = _required_artifact_schema(next_stage)
            next_action = f"Call submit(session_id, '{next_stage}', artifact)."
        else:
            instructions = "All required stages are complete. Call finalize() to close the session."
            required_artifact = {}
            next_action = "Call finalize(session_id)."

        done = self._is_done(session)

        result = {
            "accepted": True,
            "done": done,
            "current_stage": next_stage,
            "instructions": instructions,
            "required_artifact": required_artifact,
            "next_action": next_action,
            "loop_count": session.get("loop_count", 0),
        }
        if session.get("escalated_to"):
            result["escalated_to"] = session["escalated_to"]
        if overlay_checks:
            result["advisory_checks"] = overlay_checks
        return result

    def finalize(self, session_id: str) -> dict:
        """
        Finalize the session. Allowed only if every required stage has passed.
        V10: refused sessions cannot be finalized.
        V9: proceeded_without_answers stamped in certificate.
        V7: loop_count and iterations in certificate.
        """
        session = self._load(session_id)

        # V10: refused sessions cannot be finalized
        safety = session.get("safety", {})
        if safety.get("refused") and not safety.get("override_logged"):
            return {
                "finalized": False,
                "refused": True,
                "message": "Cannot finalize a refused session.",
            }

        required = self._required_stages(session)
        completed = set(session.get("completed_stages", []))
        missing = [s for s in required if s not in completed]

        if session.get("level") is None and session.get("rigor") == "adaptive":
            return {
                "finalized": False,
                "missing_stages": ["classify"] + required,
                "message": "Adaptive session: you must submit the 'classify' artifact first.",
            }

        if missing:
            return {
                "finalized": False,
                "missing_stages": missing,
                "message": (
                    f"Cannot finalize: {len(missing)} required stage(s) not yet passed: "
                    f"{missing}."
                ),
            }

        # Build evidence summary from verify checks
        evidence_summary: list[dict] = []
        verify_artifact = session.get("artifacts", {}).get("verify")
        if isinstance(verify_artifact, dict):
            for check in verify_artifact.get("checks", []):
                if isinstance(check, dict):
                    evidence_summary.append({
                        "what": check.get("what", ""),
                        "has_evidence": bool(check.get("evidence", "").strip()),
                        "evidence_snippet": str(check.get("evidence", ""))[:100],
                    })

        # Build certificate
        certificate: dict[str, Any] = {
            "session_id": session_id,
            "goal": session.get("goal"),
            "profile": session.get("profile"),
            "rigor": session.get("rigor"),
            "level": session.get("level"),
            "mode": session.get("mode", "headless"),
            "finalized_at": time.time(),
            "stages_completed": list(session.get("completed_stages", [])),
            "gate_summary": [
                {"stage": g["stage"], "passed": g.get("passed", True),
                 "timestamp": g.get("timestamp", 0)}
                for g in session.get("gate_history", [])
                if "passed" in g
            ],
            # v2 additions
            "loop_count": session.get("loop_count", 0),
            "iterations": session.get("iterations", []),
            "escalations": session.get("escalated_to", []),
            "proceeded_without_answers": session.get("proceeded_without_answers", False),
            # Shape matches CONTRACT.md (#11): {ran, refused, override, category}.
            "safety_screen": {
                "ran": True,
                "refused": safety.get("refused", False),
                "override": safety.get("override_logged", False),
                "category": (safety.get("category") or None),
            },
            "evidence_summary": evidence_summary,
        }

        session["status"] = "finalized"
        session["certificate"] = certificate
        session["updated_at"] = time.time()
        self._save(session)

        return {"finalized": True, "certificate": certificate}

    def set_rigor(self, session_id: str, rigor: str) -> dict:
        """Operator override for rigor level. May ONLY raise rigor, never lower it."""
        valid = {"low", "medium", "full", "adaptive"}
        if rigor not in valid:
            return {
                "accepted": False,
                "error": f"Invalid rigor level '{rigor}'. Must be one of: {sorted(valid)}.",
            }

        session = self._load(session_id)
        current_rigor = session.get("rigor", "adaptive")
        current_level = session.get("level")

        effective_current = current_level or current_rigor
        effective_new = rigor if rigor != "adaptive" else "full"

        current_rank = _RIGOR_RANK.get(effective_current, 3)
        new_rank = _RIGOR_RANK.get(effective_new, 3)

        if new_rank < current_rank:
            return {
                "accepted": False,
                "error": (
                    f"Cannot lower rigor from '{effective_current}' to '{rigor}'. "
                    "The protocol only allows raising rigor."
                ),
            }

        session["rigor"] = rigor
        if rigor != "adaptive":
            session["level"] = rigor
            completed = set(session.get("completed_stages", []))
            next_st = None
            for s in self._required_stages(session):
                if s not in completed:
                    next_st = s
                    break
            session["current_stage"] = next_st or session.get("current_stage")
            if next_st:
                session["status"] = "in_progress"

        session["updated_at"] = time.time()
        self._save(session)

        return {
            "accepted": True,
            "rigor": rigor,
            "level": session.get("level"),
            "current_stage": session.get("current_stage"),
        }


# ---------------------------------------------------------------------------
# Module-level convenience wrappers
# ---------------------------------------------------------------------------

_default_engine: Engine | None = None


def _engine() -> Engine:
    global _default_engine
    if _default_engine is None:
        _default_engine = Engine()
    return _default_engine


def create_session(
    goal: str,
    profile: str = "universal",
    rigor: str = "adaptive",
    involves_facts: bool | None = None,
    mode: str = "headless",
    override_safety: bool = False,
) -> dict:
    """Module-level convenience: create a session using the default engine."""
    return _engine().create_session(goal, profile, rigor, involves_facts, mode, override_safety)


def provide_answers(session_id: str, answers: list[str]) -> dict:
    """Module-level convenience: provide answers to an awaiting_input session."""
    return _engine().provide_answers(session_id, answers)


def get_state(session_id: str) -> dict:
    """Module-level convenience: get session state from the default engine."""
    return _engine().get_state(session_id)


def submit(session_id: str, stage: str, artifact: dict) -> dict:
    """Module-level convenience: submit a stage artifact to the default engine."""
    return _engine().submit(session_id, stage, artifact)


def finalize(session_id: str) -> dict:
    """Module-level convenience: finalize a session via the default engine."""
    return _engine().finalize(session_id)


def set_rigor(session_id: str, rigor: str) -> dict:
    """Module-level convenience: set rigor level via the default engine."""
    return _engine().set_rigor(session_id, rigor)
