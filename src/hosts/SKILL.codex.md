---
name: streaming-sprint-dashboard
description: >-
  Fans out a bounded set of research agents against a fixed question set and
  streams their progress, findings, defaults and open questions to a live local
  dashboard that captures answers back. Use when parallel investigation will run
  longer than a few minutes and a human has to make decisions mid-flight, or on
  a request that says "fan out", "run this in parallel", "sprint on this",
  "dashboard", "show me progress" or "what needs me". Not for a fan-out that
  finishes in two minutes.
---

# Streaming sprint dashboard

A bounded fan-out whose findings render live to a page a human can click. Agents
write JSON through `sprint.py`; the scripts own the schema, the ids, the atomic
writes and every pixel of the HTML. Nothing here improvises markup.

`$SPRINT` below means `scripts/sprint.py` inside this skill directory. Set it
once: `SPRINT=<this skill dir>/scripts/sprint.py`.

## Do not use this when

- The fan-out finishes in about two minutes. A dashboard that appears after the
  work is done is a report. Write `FINDINGS.md` and skip the rest.
- There is one question and one agent. The cost of the scaffolding is the point
  of the scaffolding: many agents, long runs.
- Nothing mid-flight needs a human. With no decision to capture, the page is
  decoration; a summary at the end is better.
- The work is a code change rather than an investigation. Progress there is the
  diff and the test run.

## Build in this order

Scaffolding first, agents second. Getting the page on screen before anything is
launched is the whole difference between a dashboard and a report.

```
Sprint progress:
- [ ] Question set written down, one line each, in the order asked
- [ ] init run: roster declared, CONTEXT.md filled in with the verbatim request
- [ ] serve started, page open, empty cards visible
- [ ] Agents launched, one per question
- [ ] Coordinator verifying claims, not researching alongside them
- [ ] Answers written for every asked question
- [ ] check passes, FINDINGS.md written, confirmation steps filed as tasks
```

## 1. Scaffold

```bash
python3 "$SPRINT" init RUNDIR --title "the question in one line" \
  --question "first thing asked" --question "second thing asked" \
  --agent "lease-terms:reads the lease itself" \
  --agent "market:comparable space and rates" \
  --agent "coordinator:what the coordinator verified first hand"
python3 "$SPRINT" serve RUNDIR --open   # prints the URL, opens it, keeps running
```

Put `RUNDIR` where the work lives, not in a temp directory: the user will come
back to `FINDINGS.md`. Fill in `CONTEXT.md` before launching anyone.

## 2. CONTEXT.md carries everything shared

Paste the triggering request verbatim. Name every person, entity and system an
agent will meet. List what is already settled, with the source. Agent prompts
stay short because this file is long, and every agent works from identical
facts. When two agents would answer differently because of something in here,
fix it here rather than in a prompt.

## 3. Roster

Six agents is a good default. Split by question, not by source, so each agent
owns an answer rather than a search. Add `coordinator` as a roster entry so what
was verified first hand sits beside agent output instead of being confused with
it.

Give the legwork to cheaper models and keep the verification for this session.

## 4. Launch

Write one prompt file per agent, then launch them all in the background. Each
prompt file is short because `CONTEXT.md` carries the shared facts.

```bash
mkdir -p RUNDIR/prompts
cat > RUNDIR/prompts/market.md <<'PROMPT'
You are the `market` agent on sprint RUNDIR.

Read RUNDIR/SCHEMA.md first, then RUNDIR/CONTEXT.md. SCHEMA.md is the contract:
every report goes through sprint.py, never by editing JSON.

Your question: what is comparable space asking, per usable square foot?
Your remit: listings and broker sheets only. Not the lease, not the exit cost.

Log every step as it happens. Do not batch the log at the end.
Finish with: sprint.py check RUNDIR, then --status done and a two-sentence
summary that answers the question with the number and its unit.
PROMPT

for a in market lease-terms exit-cost; do
  nohup codex exec --skip-git-repo-check --sandbox workspace-write \
    -C RUNDIR --output-last-message "RUNDIR/state/$a.final" \
    "$(cat "RUNDIR/prompts/$a.md")" > "RUNDIR/state/$a.stdout" 2>&1 &
done
```

`-C RUNDIR` with `--sandbox workspace-write` is what lets an agent write its own
state file. If the scripts live outside `RUNDIR`, add `--add-dir <skill dir>`.
Every launched process gets its own stdout file: when a card stays queued, that
file says why, and `sprint.py check RUNDIR` says whether what it wrote is valid.

Watch the page rather than the processes. `wait` blocks until the whole fan-out
is done, which is exactly what the dashboard exists to avoid.

Do not hand an agent a second question because it finished early. Its file is
its answer; a new question gets a new roster entry and its own process.

## 5. While they run, verify

The coordinator is not a sixth researcher.

- Independently check any high-stakes claim before relaying it: money,
  counterparties, contracts, anything that will be quoted to someone else. Open
  the source personally. Mark what could not be verified as unverified and say
  why; a 403 is a reason.
- Record what was verified first hand on the `coordinator` agent, with evidence
  rows that name the source and the date.
- Feed verified facts back into `CONTEXT.md` under "Already settled" so running
  agents stop re-deriving them.
- Distrust agent-supplied ids and cross-references. They drift. Match on
  content.
- Watch `python3 "$SPRINT" replies RUNDIR` and pass new answers to the agent
  that asked.

## 6. Answers are the deliverable

Agents produce material. The answers to what was actually asked are written
here, one per question, and they are allowed to contradict the question.

```bash
python3 "$SPRINT" answer RUNDIR q1 \
  --verdict "Renegotiate, do not renew as written" \
  --answer "Two sentences in plain words, with the number and its unit." \
  --confidence medium --source "executed-lease.pdf p.4 sec 12.1"
```

Every answer carries a confidence. An answer with no source renders as a
hypothesis, which is the honest label for it.

## 7. Finish

```bash
python3 "$SPRINT" check RUNDIR       # must print ok
python3 "$SPRINT" findings RUNDIR    # durable FINDINGS.md from the same state
```

File every confirmation step as a task in whatever tracker the user runs, one
per row, with its owner. Leave the server running so the user can keep
answering, and tell them the URL and how to stop it.

## Scripts

Run these. Do not reimplement any of them in a prompt.

| Command | Job |
|---|---|
| `sprint.py init` | Scaffolds the run: manifest, state directory, `SCHEMA.md`, `CONTEXT.md`, one queued card per agent. |
| `sprint.py serve` | Serves the live page on localhost, rebuilds on every poll, captures answers to `state/_feedback.jsonl`. |
| `sprint.py build` | Renders a static `dashboard.html`. No answer capture. |
| `sprint.py log` | Appends one line to an agent's log and flips it from queued to running. |
| `sprint.py set` | Status and summary. `done` is refused without a summary. |
| `sprint.py add` | Appends an `unknown`, `default`, `confirm`, `evidence` or `ask` row. `ask` prints a stable question id and is safe to repeat. |
| `sprint.py answer` | Records the coordinator's answer to one asked question. |
| `sprint.py replies` | What the human has answered so far. |
| `sprint.py check` | Validates every file against the contract. Exit 1 with the reasons. |
| `sprint.py findings` | Writes `FINDINGS.md` from the same state. |
| `sprint.py demo` | Fills a run with example state, to see the page in about five seconds. |

`scripts/schema.py` is the contract in code and `scripts/render.py` owns the
presentation. Read either one only to change it. The tests are the specification:
`python3 -m unittest discover -s tests`.

## Old patterns

Earlier versions of this pattern ran a `nohup` loop that re-rendered the page
every few seconds and opened it over `file://`. The server renders on each poll
instead, so there is no loop to leak and no static file to go stale.
