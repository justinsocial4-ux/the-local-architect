# Fable Method Enforcer

## What it is

The Enforcer is the "kitchen line" of the Fable Method system. While the Fable
Method is a reasoning discipline a model can follow on its own, the Enforcer
adds mechanical gate-checking: it tracks a task through a fixed pipeline of
stages (frame, research, plan, draft, critique, verify, revise, deliver), checks
each stage's output against hard rules, and refuses to let the task finish until
every required gate has passed.

A mechanical gate can verify the *shape* of rigor — that each stage was done,
with the right structure — not the *substance* of whether the thinking was good.
What the Enforcer does is make the lazy path more expensive than the honest path
for a cooperating model, raise the floor of effort, and — in the CLI-harness
mode — make skipping stages literally impossible because the harness, not the
model, controls the loop.

**How strong the enforcement is depends on which part you use:**

| Mode | What it means |
|------|---------------|
| **Skill only (no enforcer)** | Voluntary self-enforcement. Best-effort. |
| **MCP server** | The gates are real, but the model must choose to call the tools. A model can decline and answer directly. Use a system-prompt instruction requiring tool use to close this gap. |
| **CLI harness** | The only non-bypassable mode. The harness owns the loop and drives the model stage by stage. The model has no path around the gates. |

The Enforcer has three parts:

- **Engine** — the rule-checker. Pure Python, no internet, no external packages.
  Stores sessions as JSON files so nothing is lost if the process restarts.
- **MCP server** — exposes the engine as six tools an AI assistant can call
  inside any MCP-compatible host (Claude Desktop, Cursor, etc.).
- **CLI harness** — runs a complete session automatically by calling an external
  AI model in a loop. The harness controls the loop entirely; the model answers
  one stage at a time and cannot proceed until its answer passes the gate.

---

## Installation

You need Python 3.10 or newer. No other package is required for the engine itself.

```bash
# From the enforcer/ directory:
pip install -e .

# Or, without installing, set the Python path:
export PYTHONPATH=/path/to/fable-method/enforcer
```

To use the MCP server, also install the `mcp` package:

```bash
pip install mcp
```

To use a live AI provider in the CLI harness, install that provider's SDK
(all are optional — the harness has a built-in fallback that uses standard
Python networking instead):

```bash
pip install openai           # for OpenAI / GPT models
pip install anthropic        # for Anthropic / Claude models
pip install google-generativeai  # for Google / Gemini models
```

---

## Registering the MCP server

Add this block to your MCP client's configuration file (for Claude Desktop,
that file is usually `claude_desktop_config.json` in your application support
folder):

```json
{
  "mcpServers": {
    "fable-method": {
      "command": "python",
      "args": ["-m", "fable_method.mcp_server"],
      "env": {
        "PYTHONPATH": "/path/to/fable-method/enforcer"
      }
    }
  }
}
```

Replace `/path/to/fable-method/enforcer` with the actual path on your machine.
Once registered, your AI assistant will see six tools: `begin_task`,
`get_state`, `submit_stage`, `finalize`, `set_rigor`, and `answer_questions`.
The tool descriptions tell the model that the pipeline is mandatory, that
finalize will be refused until all gates pass, that obviously harmful goals may
be refused before any stage runs, and that interactive sessions may pause for
human input.

`answer_questions` is used in interactive mode: when a frame artifact contains
open questions, the engine pauses and returns `needs_user_input: true`. The
model (or a human via the client UI) calls `answer_questions` with the answers
as a list of strings to resume the session. In headless mode this tool is
never required — questions are noted and the pipeline continues, with the
certificate stamped `proceeded_without_answers: true`.

---

## Running the CLI harness

The CLI harness drives an external AI model through a complete session and
prints the audit certificate at the end.

**Offline test (no API key needed):**

```bash
python -m fable_method.cli_harness \
    --provider echo \
    --goal "Design a REST API for a task tracker"
```

**With a real model:**

```bash
# OpenAI
export OPENAI_API_KEY=sk-...
python -m fable_method.cli_harness \
    --provider openai \
    --model gpt-4o \
    --profile ai_builder \
    --rigor full \
    --goal "Design a REST API for a task tracker"

# Anthropic
export ANTHROPIC_API_KEY=sk-ant-...
python -m fable_method.cli_harness \
    --provider anthropic \
    --model claude-3-5-sonnet-latest \
    --goal "Write a go-to-market plan for a B2B SaaS product"

# Google
export GOOGLE_API_KEY=AIza...
python -m fable_method.cli_harness \
    --provider google \
    --model gemini-1.5-pro \
    --rigor medium \
    --goal "Evaluate the market opportunity for an AI writing tool"
```

Additional flags:

| Flag | Default | Purpose |
|------|---------|---------|
| `--profile` | `universal` | Reasoning profile: `universal`, `ai_builder`, `entrepreneur` |
| `--rigor` | `adaptive` | Rigor level: `low`, `medium`, `full`, `adaptive` |
| `--involves-facts` | off | Tell the engine the task involves present-day facts (activates the research gate) |
| `--max-retries` | `3` | How many times to retry a failed stage before asking for human help |
| `--store-dir` | `~/.fable_method` | Where session files are saved |
| `--exec` | off | Evidence mode: harness runs the commands attached to each check in a subprocess and sets THAT check's evidence and pass/fail status from the real exit code. A check backed by a command cannot have its evidence or verdict fabricated; a check with no command cannot be marked `pass` — the harness records it `inconclusive` (not machine-verified). The harness checks only the exit code, not whether the command truly exercises the claim. **Runs real commands on your machine — review commands before enabling.** |
| `--interactive` | off | Enables interactive mode: if the frame stage contains open questions, the harness prompts for answers on stdin and calls `provide_answers` before continuing. |
| `--allow-network` | off | With `--exec`: an **intent flag only — it does NOT block network.** When off, the harness prints a warning but still runs the command (stdlib subprocess cannot truly block network). Assume every `--exec` command can reach the network and filesystem. |
| `--override-safety` | off | Bypasses the coarse safety screen in `create_session`. The bypass is logged in the certificate. Use only if the screen is producing a false positive on a legitimate task. |

**Safety note on `--exec`:** in exec mode the harness runs shell commands (or `python -c` snippets)
that the model proposes at the VERIFY stage. These run in a subprocess on your machine with a
timeout and captured output. The harness does not sandbox or containerize the subprocess: there
is no filesystem confinement (commands can read and write outside the temp working dir), and
`--allow-network` is an intent flag that warns but does not actually block network access. The
only real limit is the timeout. Review the commands before running a session with `--exec` on
sensitive systems, and assume a command can do anything your shell user can.

**Safety note on the safety screen:** the `create_session` safety screen is a coarse keyword
and category filter. It will refuse goals that clearly match categories such as weapons,
malware, fraud/phishing, CSAM, or self-harm facilitation. It is not nuanced safety judgment —
it will produce false positives on legitimate research tasks that use flagged vocabulary (e.g.,
a security research session that mentions "malware analysis"), and it will miss sophisticated
harmful goals that avoid trigger words. `--override-safety` is the escape valve for legitimate
false positives; the bypass is always logged.

---

## How the enforcement works (plain English)

The engine is a gatekeeper. When the CLI harness sends a stage artifact to the
engine, the engine checks it against a set of mechanical rules — things like:
does the plan have at least two steps? does the critique contain at least one
finding of blocker or major severity? does the revision address every blocker?
does each verification check show a concrete method and a result that differs
from the claim? If the artifact fails, the engine sends back a list of exactly
what is wrong and why. The harness hands those violations back to the model,
which must fix them and try again.

In the CLI harness, the harness owns the loop, not the model. The model cannot
move on to the next stage until the current stage passes. It cannot jump to the
end. It cannot declare itself done. If a stage fails too many times, the harness
stops and asks the human to intervene rather than silently producing bad work.
This is what makes the CLI harness the only non-bypassable mode.

In v2, the loop is not strictly one-way: after a REVISE stage that records real
fixes, the harness routes the session back to VERIFY to re-verify those fixes.
This can happen up to 3 times; the loop count and each iteration are recorded in
the audit certificate as positive rigor signals. A REVISE with no concrete
changes (vague intent text) is rejected before the loop even runs.

The MCP server provides the same gate logic, but in that mode the model must
choose to route work through the tools. A system-prompt instruction to require
tool use is recommended when using the MCP server.
