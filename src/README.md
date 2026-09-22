# Streaming sprint dashboard

A skill for running a bounded fan-out of research agents and watching their work
arrive on a live local page you can answer from. One version each for Claude
Code, Codex CLI and grok CLI.

The point of it: when six agents are going to run for twenty minutes and two of
them will hit a question only you can settle, you should not be staring at a
spinner and you should not be interrupted six times. The page shows what each
agent is doing, what it has found, what it assumed and what it needs, and it
captures your answers while the work continues.

The agents never write HTML. They write state through one command line tool
(`sprint.py`) that owns the schema, the identifiers, the atomic writes and every
pixel of the page. That is what keeps a long run legible instead of a pile of
half-formatted output.

## When not to use it

- The fan-out finishes in about two minutes. A dashboard that shows up after the
  work is done is a report.
- One question, one agent.
- Nothing mid-flight needs a human decision.

The skill says this too, so the agent declines it rather than building
scaffolding you did not need.

## Install

Unzip, then pick a host:

```bash
./install.sh claude     # ${CLAUDE_CONFIG_DIR:-~/.claude}/skills/
./install.sh codex      # ${CODEX_HOME:-~/.codex}/skills/
./install.sh grok       # ${GROK_HOME:-~/.grok}/skills/
```

Add `--dest DIR` to install somewhere else, or `--force` to replace an existing
copy (the old one is moved aside, never deleted). To install by hand, copy
`<host>/streaming-sprint-dashboard/` into that host's skills directory.

Requirements: Python 3.10 or newer. Standard library only, no install step, no
network access.

## See it before you trust it

```bash
python3 claude/streaming-sprint-dashboard/scripts/sprint.py demo ./sprint-demo
python3 claude/streaming-sprint-dashboard/scripts/sprint.py serve ./sprint-demo
```

That fills a run with realistic state (five agents, two answered questions, one
blocked agent, two questions waiting on a human) and serves it at
`http://127.0.0.1:8787/`. Click an option and the answer lands in
`sprint-demo/state/_feedback.jsonl`.

Run the tests:

```bash
cd claude/streaming-sprint-dashboard && python3 -m unittest discover -s tests
```

91 tests. They cover the schema, the escaping, the answer round trip, and the
contrast ratios of both colour palettes, which are asserted as numbers rather
than judged by eye.

## What is in the package

```
claude/streaming-sprint-dashboard/   drop into the Claude Code skills directory
codex/streaming-sprint-dashboard/    drop into the Codex CLI skills directory
grok/streaming-sprint-dashboard/     drop into the grok CLI skills directory
install.sh                           copies one of them into place
```

Each host directory is self-contained and identical apart from `SKILL.md`, which
differs only in the fan-out mechanics of that host: the Claude version launches
subagents in one message, the Codex and grok versions launch background
processes with the exact flags each command line tool needs.

Inside a host directory:

```
SKILL.md                 the judgment: when to use it, the build order, the
                         coordinator's job. Short on purpose.
scripts/sprint.py        the whole tool: init, serve, log, set, add, answer,
                         replies, check, findings, demo
scripts/schema.py        the state contract in typed objects, strictly validated
scripts/render.py        the only file that produces HTML
scripts/templates/       the agent contract copied into each run directory
tests/                   the specification, runnable
evals/                   five scenarios for checking the skill behaves
```

## The three rules that carry the quality

Everything else is plumbing. These are enforced by the tool, not just described:

1. **A default is a decision you would defend, with the cost of being wrong
   stated.** `sprint.py add ... default` refuses a default with no
   `--cost-if-wrong`, and `check` rejects a one-word one. If the cost cannot be
   stated, it is a guess, and a guess is reported as an unknown.
2. **Every claim carries a source and a date.** Evidence rows require both. An
   answer with no source renders as a hypothesis, in those words.
3. **Agents log as they go.** The log line is what streams. An agent that writes
   only when it finishes leaves a dead card on screen, which is worse than no
   dashboard.

## What the run directory holds

```
manifest.json            the questions asked and the roster answering them
CONTEXT.md               the shared facts every agent reads first
SCHEMA.md                the agent contract
state/<agent>.json       one file per agent, written only through sprint.py
state/<agent>.log        one line per step, appended as it happens
state/_answers.json      the coordinator's answers to the asked questions
state/_feedback.jsonl    what the human clicked or typed, append only
FINDINGS.md              generated at the end from the same state
dashboard.html           only written by `build`; `serve` renders on each poll
```

Nothing in the run directory is hand-edited, including by you. `sprint.py check`
is what says whether it is valid.

## License

MIT. See LICENSE.
