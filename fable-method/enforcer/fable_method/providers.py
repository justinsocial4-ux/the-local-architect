"""
providers.py — LLM provider adapters for the Fable Method CLI harness.

All network and SDK calls are isolated here.  The engine itself never imports
this module.

Usage
-----
    from fable_method.providers import get_provider

    provider = get_provider("openai")          # or anthropic / google / echo
    text = provider.complete(system, messages, model="gpt-4o-mini")

Keys are read from environment variables:
    OPENAI_API_KEY
    ANTHROPIC_API_KEY
    GOOGLE_API_KEY  (or GEMINI_API_KEY)

Missing optional SDKs are caught at import time and do NOT crash the module.
A helpful error is raised only when the adapter is actually instantiated.

v2 changes (EchoProvider)
--------------------------
The echo provider's canned artifacts have been updated to satisfy all v2 gates
and to DEMONSTRATE the backtracking loop:

- research: includes one real-looking URL source + one `assumed` source whose
  claim is disclosed in deliver.limitations.
- critique: includes a `blocker` finding, which triggers auto-escalation to FULL
  rigor and causes revise→verify backtracking.
- verify (first pass): includes concrete evidence (digit + PASS/FAIL token +
  quoted snippet). Also includes a `commands` list for --exec mode (harness
  strips this and runs it, injecting real output).
- revise: fixes map to the blocker finding with ≥0.4 token overlap + concrete
  edit verbs. reverified=True triggers exactly one verify re-loop.
- verify (second pass / post-revise): same concrete evidence, different result
  text — passes with no new blocker so the engine routes to deliver.
- deliver: limitations cover the assumed-source claim and research unknowns.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Any

# ---------------------------------------------------------------------------
# Optional SDK guards
# ---------------------------------------------------------------------------

try:
    import openai as _openai_sdk  # type: ignore

    _OPENAI_SDK_AVAILABLE = True
except ImportError:
    _openai_sdk = None  # type: ignore
    _OPENAI_SDK_AVAILABLE = False

try:
    import anthropic as _anthropic_sdk  # type: ignore

    _ANTHROPIC_SDK_AVAILABLE = True
except ImportError:
    _anthropic_sdk = None  # type: ignore
    _ANTHROPIC_SDK_AVAILABLE = False

try:
    import google.generativeai as _google_sdk  # type: ignore

    _GOOGLE_SDK_AVAILABLE = True
except ImportError:
    _google_sdk = None  # type: ignore
    _GOOGLE_SDK_AVAILABLE = False


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class Provider(ABC):
    """Abstract base class for all provider adapters."""

    @abstractmethod
    def complete(self, system: str, messages: list[dict], **opts: Any) -> str:
        """
        Call the LLM and return the assistant's text response.

        Parameters
        ----------
        system:
            System prompt (plain text).
        messages:
            List of {"role": "user"|"assistant", "content": str} dicts.
        **opts:
            Provider-specific kwargs (e.g. ``model``, ``temperature``,
            ``max_tokens``).

        Returns
        -------
        str
            The assistant's reply as a plain string.
        """


# ---------------------------------------------------------------------------
# Echo (offline mock) — v2 updated canned artifacts
# ---------------------------------------------------------------------------

# Stage call counter — tracks how many times each stage has been requested
# so that re-requested stages (after backtracking) return the correct artifact.
_ECHO_STAGE_CALL_COUNT: dict[str, int] = {}

# ---------------------------------------------------------------------------
# v2 canned artifacts — designed to:
#   1. Pass all v2 gates on every stage
#   2. Demonstrate the backtracking loop (revise → verify → deliver)
#   3. Include real-looking evidence, assumed sources, and limitations coverage
# ---------------------------------------------------------------------------

_CANNED_ARTIFACTS: dict[str, dict] = {
    "classify": {
        "complexity": "high",
        "stakes": "medium",
        "reversibility": "easy",
        "selected_level": "full",
        "justification": (
            "The chatbot task involves multiple components: ingestion pipeline, "
            "retrieval logic, and a conversational interface over docs. "
            "Complexity is high because integrating these pieces correctly requires "
            "careful sequencing. Full rigor ensures we verify each layer before shipping."
        ),
    },
    "frame": {
        "goal_restatement": (
            "Create a small FAQ chatbot that can answer questions by searching "
            "through a documentation corpus, returning accurate and scoped responses."
        ),
        "success_criteria": [
            "Chatbot retrieves relevant FAQ answers from the docs with at least 80% accuracy.",
            "Latency per query stays under 2 seconds on typical hardware.",
            "The system handles unknown questions gracefully with a fallback message.",
        ],
        "assumptions": [
            {
                "assumption": (
                    "The documentation corpus is in plain-text or Markdown format "
                    "and does not require OCR or proprietary parsing."
                ),
                "why_safe": (
                    "Most internal docs teams use Markdown; operator can override "
                    "if the corpus format differs."
                ),
            }
        ],
    },
    "research": {
        "facts": [
            {
                "claim": (
                    "Retrieval-Augmented Generation (RAG) achieves state-of-the-art "
                    "results on open-domain QA by combining a dense retriever with "
                    "a generative model, as measured on Natural Questions and TriviaQA."
                ),
                "source": "https://arxiv.org/abs/2005.11401",
                "type": "url",
            },
            {
                "claim": (
                    "A BM25 keyword index over a small (<10 k doc) corpus typically "
                    "retrieves the correct document in the top-3 results roughly 70% "
                    "of the time for narrow FAQ domains."
                ),
                "source": "internal-estimation/training-data",
                "type": "assumed",
            },
        ],
        "unknowns": [
            "Exact size and structure of the target documentation corpus is not confirmed.",
            "Whether the deployment environment has GPU access for embedding models.",
        ],
    },
    "plan": {
        "steps": [
            "Step 1: Ingest and chunk documentation into passages of 200-400 tokens each.",
            "Step 2: Build a BM25 index over the chunked passages for keyword retrieval.",
            "Step 3: Implement a query handler that retrieves the top-3 passages and "
            "formats the answer with source attribution.",
        ],
        "risks": [
            "Chunking strategy may split relevant context across boundaries, reducing accuracy.",
            "BM25 alone may miss semantic matches; a hybrid retriever may be needed.",
        ],
        "verification_strategy": [
            "Run a smoke test with 10 known FAQ pairs and check that the top-1 retrieved "
            "passage contains the expected answer for at least 8 of them.",
        ],
    },
    "draft": {
        "content": (
            "FAQ Chatbot Design — v1 Draft\n\n"
            "Architecture: three-layer pipeline.\n"
            "Layer 1 (Ingest): parse Markdown docs, split on heading boundaries and "
            "paragraph breaks into 300-token chunks, store as plain-text passages.\n"
            "Layer 2 (Index): build a BM25 index using rank_bm25 (Python). "
            "For semantic fallback, embed passages with a small sentence-transformer model "
            "(all-MiniLM-L6-v2, 22 MB) and store vectors in a numpy array.\n"
            "Layer 3 (Query): accept a user question, score passages with BM25, "
            "re-rank the top-10 by cosine similarity, return top-3 with source labels.\n"
            "Fallback: if max score < 0.35, return a canned 'I don't know, please contact "
            "support' message rather than hallucinating an answer.\n"
            "The design meets all three success criteria: accuracy via hybrid retrieval, "
            "latency via in-memory BM25 (sub-50 ms), and graceful fallback via threshold gating."
        ),
    },
    "critique": {
        "findings": [
            {
                "severity": "blocker",
                "issue": (
                    "No evaluation harness is specified: the draft lacks a concrete plan "
                    "for measuring retrieval accuracy against the 80% success criterion. "
                    "Without a labeled test set and a scoring script, the criterion is "
                    "unverifiable and the pipeline cannot be shipped with confidence."
                ),
                "location": "draft.content — verification layer",
            },
            {
                "severity": "major",
                "issue": (
                    "The assumed BM25 70% accuracy figure is unverified for this specific "
                    "corpus. Using it without corpus-specific benchmarking risks "
                    "over-promising on retrieval quality."
                ),
                "location": "research.facts[1] and draft.content Layer 2",
            },
        ],
        "steelman": (
            "The strongest counterargument is that the hybrid BM25 + embedding approach "
            "is a well-established pattern that has proven effective across many FAQ "
            "domains, and the threshold-gating fallback prevents the worst failure mode. "
            "For a small internal tool, shipping quickly with known limitations may be "
            "more valuable than perfect evaluation infrastructure upfront."
        ),
    },
    # verify (first pass — before backtracking) and (second pass — after revise)
    # The EchoProvider returns different artifacts for subsequent calls; see below.
    "verify": {
        "checks": [
            {
                "what": "Evaluation harness produces a measurable accuracy score",
                "how": (
                    "Ran the smoke-test script against 10 labeled FAQ pairs and "
                    "confirmed the scorer reports a numeric accuracy."
                ),
                "result": (
                    "PASS — smoke test completed: 8/10 correct retrievals = 80.0% accuracy, "
                    "meeting the ≥80% criterion."
                ),
                "evidence": (
                    "smoke_test.py:47 output: `accuracy=0.80, correct=8, total=10` PASS"
                ),
                # V10: per-check command — real computation (not a literal echo) so it backs
                # the check under the anti-laundering rule; the harness sets this check's status.
                "commands": [
                    {"lang": "python", "code": "assert round(8/10, 2) == 0.80; print('accuracy', round(8/10, 2), 'over 10 cases PASS')"}
                ],
            },
            {
                "what": "Fallback threshold fires on an out-of-scope query",
                "how": (
                    "Tested with a query known to be outside the corpus and confirmed "
                    "the fallback message is returned instead of a hallucinated answer."
                ),
                "result": (
                    "PASS — query 'What is the capital of France?' returned the canned "
                    "fallback message (score=0.12 < threshold 0.35)."
                ),
                "evidence": (
                    "fallback_test.py:31: score=0.12, threshold=0.35, "
                    'response=`"I don\'t know, please contact support"` PASS'
                ),
                # V10: per-check command — real comparison (not a literal echo); backs this
                # check independently of check[0].
                "commands": [
                    {"lang": "python", "code": "assert 0.12 < 0.35; print('fallback score 0.12 below threshold 0.35 PASS')"}
                ],
            },
        ],
    },
    "revise": {
        "fixes": [
            {
                "finding_ref": (
                    "No evaluation harness is specified: the draft lacks a concrete plan "
                    "for measuring retrieval accuracy against the 80% success criterion."
                ),
                "change": (
                    "Added a labeled test set of 10 FAQ pairs and a smoke_test.py scorer "
                    "that reports accuracy, correct count, and total. Updated the draft's "
                    "verification layer to reference this harness explicitly."
                ),
            },
            {
                "finding_ref": (
                    "The assumed BM25 70% accuracy figure is unverified for this specific corpus."
                ),
                "change": (
                    "Removed the unverified 70% accuracy claim from the draft. "
                    "Replaced it with a note that retrieval quality must be measured "
                    "empirically on the target corpus before making accuracy guarantees."
                ),
            },
        ],
        "reverified": True,
    },
    "deliver": {
        "summary": (
            "FAQ chatbot design complete. The three-layer pipeline (ingest → BM25+embedding "
            "index → query handler with fallback) meets all three success criteria: "
            "80% retrieval accuracy verified via smoke test (8/10 correct), sub-50 ms "
            "latency via in-memory BM25, and graceful fallback for out-of-scope queries. "
            "An evaluation harness (smoke_test.py) was added following the critique review."
        ),
        "limitations": [
            # Mirrors pending item 1 verbatim (covers unknowns[0])
            (
                "Exact size and structure of the target documentation corpus is not confirmed."
            ),
            # Mirrors pending item 2 (covers unknowns[1])
            (
                "Whether the deployment environment has GPU access for embedding models "
                "is not known; CPU fallback may increase latency."
            ),
            # Mirrors pending item 3 (covers assumed BM25 claim)
            (
                "Assumed: A BM25 keyword index over a small corpus may not retrieve "
                "correctly 70% of the time for this specific FAQ domain — verify empirically."
            ),
        ],
        "sources": [
            "https://arxiv.org/abs/2005.11401",
            "smoke_test.py (internal evaluation harness)",
        ],
    },
}

# Second-pass verify artifact: returned after the backtracking loop.
# Slightly different result text but same concrete evidence — no new blocker.
_VERIFY_SECOND_PASS: dict = {
    "checks": [
        {
            "what": "Evaluation harness produces a measurable accuracy score",
            "how": (
                "Re-ran the smoke-test script after the revise fixes were applied "
                "and confirmed the scorer still reports the correct numeric accuracy."
            ),
            "result": (
                "PASS — post-revise smoke test: 8/10 correct retrievals = 80.0%, "
                "evaluation harness confirmed working with updated draft."
            ),
            "evidence": (
                "smoke_test.py:47 re-run output: `accuracy=0.80, correct=8, total=10` PASS"
            ),
            "commands": [
                {"lang": "python", "code": "assert 8/10 >= 0.8; print('post-revise accuracy', 8/10, 'PASS')"}
            ],
        },
        {
            "what": "Assumed accuracy claim removed from draft",
            "how": (
                "Compared the revised draft content against the original to confirm "
                "the unverified 70% BM25 claim was removed and replaced with an "
                "empirical-measurement note."
            ),
            "result": (
                "PASS — the phrase '70%' no longer appears in the draft; "
                "the note about empirical measurement is present."
            ),
            "evidence": (
                "diff draft_v1 draft_v2: -1 line containing '70%', "
                "+1 line 'must be measured empirically' PASS"
            ),
            "commands": [
                {"lang": "python", "code": "removed = '70%' not in 'retrieval quality must be measured empirically'; assert removed; print('70% claim removed PASS')"}
            ],
        },
    ],
}


import re as _re

_STAGE_HEADER_RE = _re.compile(
    r"=== FABLE METHOD .* STAGE:\s*(\w+)", _re.IGNORECASE
)
_STAGE_REJECTED_RE = _re.compile(
    r'for stage ["\'](\w+)["\']', _re.IGNORECASE
)
_KNOWN_STAGES = [
    "classify", "frame", "research", "plan", "draft",
    "critique", "verify", "revise", "deliver",
]


def _detect_stage(messages: list[dict]) -> str:
    """
    Detect which stage is being requested from the conversation messages.

    Priority:
    1. Explicit STAGE header in ANY message: '=== FABLE METHOD — STAGE: <stage> ==='
    2. Explicit REJECTED message: 'for stage "<stage>" was REJECTED'
    3. Fallback: first stage keyword found in the first user message only.
    """
    if not messages:
        return "frame"

    # Scan all messages from last to first for authoritative headers
    for msg in reversed(messages):
        content = msg.get("content", "")
        # Priority 1: stage header
        m = _STAGE_HEADER_RE.search(content)
        if m:
            found = m.group(1).lower()
            if found in _KNOWN_STAGES:
                return found
        # Priority 2: rejected message
        m = _STAGE_REJECTED_RE.search(content)
        if m:
            found = m.group(1).lower()
            if found in _KNOWN_STAGES:
                return found

    # Fallback: scan the FIRST user message only for a stage keyword
    # (avoids false matches on violation text that contains stage names)
    first_user = next(
        (msg.get("content", "") for msg in messages if msg.get("role") == "user"),
        "",
    )
    first_lower = first_user.lower()
    for stage in _KNOWN_STAGES:
        if stage in first_lower:
            return stage

    return "frame"


class EchoProvider(Provider):
    """
    Offline mock provider.  Returns deterministic, gate-passing artifact JSON.
    No API key required.

    v2: Call-count aware — the second call to 'verify' (after the backtracking
    loop triggered by revise) returns a different artifact (_VERIFY_SECOND_PASS)
    that still passes all gates, demonstrating the loop completes cleanly.
    """

    def __init__(self) -> None:
        self._call_counts: dict[str, int] = {}

    def complete(self, system: str, messages: list[dict], **opts: Any) -> str:
        stage = _detect_stage(messages)

        # Track how many times this stage has been called
        count = self._call_counts.get(stage, 0)
        self._call_counts[stage] = count + 1

        if stage == "verify" and count >= 1:
            # Second (or later) verify call — post-revise re-verification
            artifact = _VERIFY_SECOND_PASS
        else:
            artifact = _CANNED_ARTIFACTS.get(stage, _CANNED_ARTIFACTS["frame"])

        return json.dumps(artifact, indent=2)


# ---------------------------------------------------------------------------
# OpenAI adapter
# ---------------------------------------------------------------------------


def _openai_http_complete(
    api_key: str,
    model: str,
    system: str,
    messages: list[dict],
    max_tokens: int = 4096,
) -> str:
    """Fallback raw-HTTP path for when the openai SDK is not installed."""
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}] + messages,
        "max_tokens": max_tokens,
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read())
        return body["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"OpenAI HTTP error {exc.code}: {exc.read().decode()}"
        ) from exc


class OpenAIProvider(Provider):
    """
    OpenAI adapter.  Uses the ``openai`` SDK if installed, otherwise falls back
    to raw urllib HTTP calls.
    """

    def __init__(self) -> None:
        self.api_key = os.environ.get("OPENAI_API_KEY", "")
        if not self.api_key:
            raise RuntimeError(
                "OPENAI_API_KEY environment variable is not set. "
                "Export it before running: export OPENAI_API_KEY=sk-..."
            )

    def complete(self, system: str, messages: list[dict], **opts: Any) -> str:
        model = opts.get("model", "gpt-4o-mini")
        max_tokens = int(opts.get("max_tokens", 4096))

        if _OPENAI_SDK_AVAILABLE:
            client = _openai_sdk.OpenAI(api_key=self.api_key)
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system}] + messages,
                max_tokens=max_tokens,
            )
            return resp.choices[0].message.content or ""
        else:
            return _openai_http_complete(
                self.api_key, model, system, messages, max_tokens
            )


# ---------------------------------------------------------------------------
# Anthropic adapter
# ---------------------------------------------------------------------------


def _anthropic_http_complete(
    api_key: str,
    model: str,
    system: str,
    messages: list[dict],
    max_tokens: int = 4096,
) -> str:
    """Fallback raw-HTTP path for when the anthropic SDK is not installed."""
    payload = {
        "model": model,
        "system": system,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=data,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read())
        return body["content"][0]["text"]
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"Anthropic HTTP error {exc.code}: {exc.read().decode()}"
        ) from exc


class AnthropicProvider(Provider):
    """
    Anthropic adapter.  Uses the ``anthropic`` SDK if installed, otherwise
    falls back to raw urllib HTTP calls.
    """

    def __init__(self) -> None:
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not self.api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY environment variable is not set. "
                "Export it before running: export ANTHROPIC_API_KEY=sk-ant-..."
            )

    def complete(self, system: str, messages: list[dict], **opts: Any) -> str:
        model = opts.get("model", "claude-3-5-haiku-latest")
        max_tokens = int(opts.get("max_tokens", 4096))

        if _ANTHROPIC_SDK_AVAILABLE:
            client = _anthropic_sdk.Anthropic(api_key=self.api_key)
            resp = client.messages.create(
                model=model,
                system=system,
                messages=messages,
                max_tokens=max_tokens,
            )
            return resp.content[0].text
        else:
            return _anthropic_http_complete(
                self.api_key, model, system, messages, max_tokens
            )


# ---------------------------------------------------------------------------
# Google / Gemini adapter
# ---------------------------------------------------------------------------


def _build_gemini_payload(system: str, messages: list, max_tokens: int = 4096) -> dict:
    """Build the Gemini generateContent REST body.

    System is delivered via ``systemInstruction`` (matching the SDK path's
    ``system_instruction=``), NOT as a synthetic leading 'user' turn — the old form produced
    two adjacent 'user' roles in ``contents`` (the injected system turn + the first real user
    turn), which strict Gemini REST can reject with a 400. All message content is preserved.
    """
    role_map = {"user": "user", "assistant": "model"}
    contents = [
        {"role": role_map.get(m.get("role", "user"), "user"),
         "parts": [{"text": m.get("content", "")}]}
        for m in messages
    ]
    payload: dict = {"generationConfig": {"maxOutputTokens": max_tokens}}
    if contents:
        payload["contents"] = contents
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
    else:
        # No turns supplied — send the system prompt as the sole user turn.
        payload["contents"] = [{"role": "user", "parts": [{"text": system}]}]
    return payload


def _google_http_complete(
    api_key: str,
    model: str,
    system: str,
    messages: list[dict],
    max_tokens: int = 4096,
) -> str:
    """
    Fallback raw-HTTP path for when the google-generativeai SDK is not installed.
    Uses the Gemini generateContent REST endpoint.
    """
    payload = _build_gemini_payload(system, messages, max_tokens)
    safe_model = model.replace("/", "-")
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{safe_model}:generateContent?key={api_key}"
    )
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read())
        return body["candidates"][0]["content"]["parts"][0]["text"]
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"Google HTTP error {exc.code}: {exc.read().decode()}"
        ) from exc


class GoogleProvider(Provider):
    """
    Google Gemini adapter.  Uses the ``google-generativeai`` SDK if installed,
    otherwise falls back to raw urllib HTTP calls.
    """

    def __init__(self) -> None:
        self.api_key = (
            os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY") or ""
        )
        if not self.api_key:
            raise RuntimeError(
                "Neither GOOGLE_API_KEY nor GEMINI_API_KEY environment variable is set. "
                "Export one before running: export GOOGLE_API_KEY=AIza..."
            )

    def complete(self, system: str, messages: list[dict], **opts: Any) -> str:
        model = opts.get("model", "gemini-1.5-flash")
        max_tokens = int(opts.get("max_tokens", 4096))

        if _GOOGLE_SDK_AVAILABLE:
            _google_sdk.configure(api_key=self.api_key)
            gmodel = _google_sdk.GenerativeModel(
                model_name=model,
                system_instruction=system,
            )
            history = []
            role_map = {"user": "user", "assistant": "model"}
            for msg in messages[:-1]:
                history.append(
                    {
                        "role": role_map.get(msg["role"], "user"),
                        "parts": [msg["content"]],
                    }
                )
            chat = gmodel.start_chat(history=history)
            last_user = messages[-1]["content"] if messages else system
            resp = chat.send_message(
                last_user,
                generation_config={"max_output_tokens": max_tokens},
            )
            return resp.text
        else:
            return _google_http_complete(
                self.api_key, model, system, messages, max_tokens
            )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_PROVIDER_MAP: dict[str, type[Provider]] = {
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "google": GoogleProvider,
    "echo": EchoProvider,
}


def get_provider(name: str) -> Provider:
    """
    Return a Provider instance for ``name``.

    Parameters
    ----------
    name:
        One of ``"openai"``, ``"anthropic"``, ``"google"``, ``"echo"``.

    Raises
    ------
    ValueError
        If ``name`` is not recognised.
    RuntimeError
        If the required API key is missing (raised by the adapter constructor).
    """
    name = name.lower().strip()
    cls = _PROVIDER_MAP.get(name)
    if cls is None:
        available = ", ".join(sorted(_PROVIDER_MAP))
        raise ValueError(
            f"Unknown provider {name!r}. Available providers: {available}"
        )
    return cls()
