# Fable Method

**Make any AI actually think — instead of guessing.**

The Fable Method forces an AI through eight ordered, gated stages — Frame → Research → Plan → Draft → Critique → Verify → Revise → Deliver — and it can't move to the next step until the current one passes. Effort scales to the stakes with a rigor dial. It's model-agnostic (works with any model) and the engine has **zero dependencies** beyond the Python standard library.

![How the Fable Method works](docs/how-it-works.png)

---

## Two ways to use it

### 1. The easy way — the Skill (no terminal, 1 minute)

The **Skill** is a "recipe card" an AI reads and follows on its own. This is all most people need.

- **In an app that supports skills (e.g. Claude):** install `packaged/fable-method.skill`, then start a request with *"Use the Fable Method."*
- **In any AI chat:** paste the contents of `skills/fable-method/SKILL.md` and say *"Follow this method for my task."*

That's it. The AI will walk through the stages on its own. (Honest note: in this mode the AI is *following* the method on good faith — a cooperative model does, which is ~95% of the value.)

### 2. The strict way — the MCP server (real enforcement)

This connects the engine to your AI app as a tool, so the AI **mechanically cannot skip a step** — each stage is validated and rejected if it fails a gate.

```bash
git clone <this-repo> fable-method
cd fable-method
./setup.sh
```

`setup.sh` creates an isolated Python environment, installs everything, runs a self-test, and **prints the exact config line to paste into your AI app** (with the path already filled in). Then restart your app. No hand-editing paths, no dependency headaches.

> Requires Python 3.10+. macOS/Linux. (On Windows, see `setup.sh` for the two commands to run manually.)

---

## What's in here

```
fable-method/
├── README.md                     ← you are here
├── setup.sh                      ← one-command setup for the MCP server
├── LICENSE                       ← MIT
├── docs/how-it-works.png         ← the diagram above
├── skills/fable-method/          ← the Skill (recipe card)
│   ├── SKILL.md                  ← the map: rigor dial, flavor selection, pipeline
│   └── references/               ← stage templates, mindset, the 3 flavors
├── packaged/fable-method.skill   ← the installable Skill bundle
├── enforcer/                     ← the engine (pure-stdlib) + MCP server + CLI
│   ├── pyproject.toml            ← `pip install -e ".[mcp]"`
│   └── fable_method/             ← engine.py, mcp_server.py, cli_harness.py, ...
├── PROTOCOL.md                   ← the full reasoning protocol
├── CONTRACT.md                   ← the technical interface
└── PROCESS.md                    ← how the project is developed
```

The Skill picks a **flavor** by task: **general**, **AI-builder** (building prompts/agents/software), or **entrepreneur** (validating a business idea). Each flavor just adds a few extra checks to specific stages.

---

## How it works (the short version)

Left alone, an AI wants to plate the dish and send it out. This makes it work like a careful professional instead:

1. **Frame** — understand the real problem; set success criteria.
2. **Research** — gather real facts, with sources.
3. **Plan** — decide the approach and how you'll verify it.
4. **Draft** — do the actual work.
5. **Critique** — attack your own work for flaws.
6. **Verify** — prove it with concrete evidence, not assertions.
7. **Revise** — fix every problem; re-verify (loops until it holds).
8. **Deliver** — lead with the answer; disclose limits and sources.

A green checkpoint between each stage means it can't continue until that stage passes.

---

## Honest limits

- The engine verifies the **shape** of rigor (each stage was done, with evidence), **not** whether the content is factually correct or on-topic. It raises the floor of effort; it doesn't replace judgment.
- Real "you literally cannot skip a step" enforcement only happens in the **MCP server** or CLI modes — where the program drives the loop, not the model.
- The CLI harness can drive a real model end-to-end; it reads your API key from an environment variable (`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY`). No keys are stored in this repo.

---

## Run the tests

```bash
cd fable-method/enforcer
./run_tests.sh            # full suite
./run_tests.sh -k bypass  # the anti-cheating probes
```

## License

MIT — see `LICENSE`. Use it, fork it, build on it.
