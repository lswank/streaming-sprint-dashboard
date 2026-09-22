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
user-invocable: true
---

# Streaming sprint dashboard

A bounded fan-out whose findings render live to a page a human can click. Agents
write JSON through `sprint.py`; the scripts own the schema, the ids, the atomic
writes and every pixel of the HTML. Nothing here improvises markup.

Two shell variables carry the whole skill. Set them before anything else.

`SPRINT` is `scripts/sprint.py` inside this skill's own directory, the directory
this file sits in. The host that loaded this skill knows that path; if it does
not, search for it and use the first hit:

```bash
SPRINT=$(ls -d "$HOME"/.claude/skills/streaming-sprint-dashboard/scripts/sprint.py \
               "$HOME"/.codex/skills/streaming-sprint-dashboard/scripts/sprint.py \
               "$HOME"/.grok/skills/streaming-sprint-dashboard/scripts/sprint.py \
               2>/dev/null | head -1)
python3 "$SPRINT" --help >/dev/null || echo "set SPRINT by hand: this file's directory + /scripts/sprint.py"
```

Those are the three places a host installs a skill. If it was unpacked somewhere
else, use the path the host reported for this file; do not run a `find` over the
whole home directory, which takes minutes.

`RUN` is the run directory, which does not exist yet. Put it where the work
lives, not in a temp directory: the user comes back to `FINDINGS.md`.

```bash
RUN="$HOME/work/lease-2026/sprint"   # change this to where the work lives
```

Every command below uses `"$SPRINT"` and `"$RUN"`, so it runs as written once
those two are set.

## Do not use this when

- The work is a handful of lookups. If the honest estimate is a couple of
  minutes, a dashboard would appear after the work is done, which is a report.
  Say so, do the lookups, and write the findings straight into the reply.
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
python3 "$SPRINT" init "$RUN" --title "Renew, renegotiate or leave the warehouse?" \
  --question "Do we renew, renegotiate or leave?" \
  --question "What does leaving actually cost?" \
  --question "Who has to sign off, and by when?" \
  --agent "lease-terms:the lease itself; owns question 1" \
  --agent "exit-cost:moving, downtime and restoration; owns question 2" \
  --agent "signoff:who approves what; owns question 3" \
  --agent "coordinator:what the coordinator verified first hand"
```

`init` prints the question ids (`q1`, `q2`, `q3`). Those are what `answer` takes.

`serve` runs until it is stopped, so start it in the background, never in the
foreground of a turn, and wait for the URL rather than reading an empty file:

```bash
PORT=8787       # a second sprint on this machine needs a different one
nohup python3 "$SPRINT" serve "$RUN" --port "$PORT" --open > "$RUN/serve.log" 2>&1 &
for _ in 1 2 3 4 5 6 7 8 9 10; do grep -q http "$RUN/serve.log" && break; sleep 0.3; done
cat "$RUN/serve.log"     # the URL, or the reason it could not serve
```

Give the user that URL. Then fill in `CONTEXT.md`: `check` refuses to pass while
the request placeholder is still in it, which is the point.

## 2. CONTEXT.md carries everything shared

`init` writes `CONTEXT.md` with a placeholder line for the request. Replace it
with the triggering request, word for word. Name every person, entity and system
an agent will meet. List what is already settled, with the source. Agent prompts
stay short because this file is long, and every agent works from identical
facts. When two agents would answer differently because of something in here,
fix it here rather than in a prompt.

## 3. Roster

One agent per question, plus `coordinator`. Up to six is a good ceiling: more
than that and nothing on the page can be verified in time.

The roster and the question set must line up. With three questions the roster is
three agents and a coordinator, and each agent's prompt names the question it
owns, in the same words as `--question`. Splitting by source instead ("one for
the web, one for the files") leaves every answer half-owned.

Add `coordinator` as a roster entry so what was verified first hand sits beside
agent output instead of being confused with it.

One shell trap: in zsh, `--agent "$name:$remit"` applies the `:r` history
modifier to `$name` and silently mangles it. Write `"${name}:${remit}"`, or
literal strings as above.

Give the legwork to cheaper models and keep the verification for this session.

## 4. Launch

Write one prompt file per agent, then launch them all in the background.

```bash
mkdir -p "$RUN/prompts"

# One line per agent: name | the question it owns | its remit. The coordinator is
# not here: that role is this session, not a process to launch.
AGENTS=(
  "lease-terms|Do we renew, renegotiate or leave?|the lease and its amendments; not the market, not the exit cost"
  "exit-cost|What does leaving actually cost?|movers, downtime and the restoration clause; not the lease terms"
  "signoff|Who has to sign off, and by when?|the board charter and the calendar; not the money"
)

for entry in "${AGENTS[@]}"; do
  IFS='|' read -r name question remit <<< "$entry"
  cat > "$RUN/prompts/$name.md" <<PROMPT
You are the \`$name\` agent on sprint $RUN.

Read $RUN/SCHEMA.md first, then $RUN/CONTEXT.md. SCHEMA.md is the contract:
every report goes through $SPRINT, never by editing JSON.

Your question, word for word from the manifest: $question
Your remit: $remit.

Log every step as it happens. Do not batch the log at the end.

Finish with these three, in this order:
  python3 $SPRINT check $RUN --agent $name
  python3 $SPRINT replies $RUN
  python3 $SPRINT set $RUN $name --status done --summary "<two sentences>"
PROMPT
done
```

The heredoc is unquoted on purpose, so `$RUN`, `$SPRINT`, `$name` and `$question`
are substituted as each file is written. Check one before launching: every path
in it should be absolute, with no `$` left.

```bash
MODEL=""        # optional: a cheaper model id for the legwork
for entry in "${AGENTS[@]}"; do
  name="${entry%%|*}"
  nohup grok --prompt-file "$RUN/prompts/$name.md" --always-approve \
    ${MODEL:+--model "$MODEL"} --cwd "$RUN" > "$RUN/state/$name.stdout" 2>&1 &
done
```

`--always-approve` is required: without it each agent stops at its first tool
approval prompt with nobody watching, and its card sits queued forever.

Every launched process gets its own stdout file: when a card stays queued, that
file says why, and `python3 "$SPRINT" check "$RUN" --agent <name>` says whether
what it wrote is valid.

Watch the page rather than the processes. `wait` blocks until the whole fan-out
is done, which is exactly what the dashboard exists to avoid.

Do not hand an agent a second question because it finished early. Its file is
its answer; a new question gets a new roster entry and its own process.

## 5. While they run, verify

The `coordinator` roster entry is this session, not an agent to launch. It is not
another researcher either: its job is to check what the others report.

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
- Watch `python3 "$SPRINT" replies "$RUN"` and pass new answers to the agent
  that asked.
- A card stuck on running with no new log lines means that agent died. Do not
  wait it out: read whatever it left, then set it blocked with the reason, so
  the page says what happened instead of implying work is still going on.

```bash
python3 "$SPRINT" set "$RUN" exit-cost --status blocked \
  --summary "process exited after the mover quote; restoration never priced"
```

## 6. Answers are the deliverable

Agents produce material. The answers to what was actually asked are written
here, one per question, and they are allowed to contradict the question.

```bash
python3 "$SPRINT" answer "$RUN" q1 \
  --verdict "Renegotiate, do not renew as written" \
  --answer "Two sentences in plain words, with the number and its unit." \
  --confidence medium --source "executed-lease.pdf p.4 sec 12.1"
```

Every answer carries a confidence. An answer with no source renders as a
hypothesis, which is the honest label for it.

## 7. Finish

```bash
python3 "$SPRINT" check "$RUN"       # must print ok
python3 "$SPRINT" findings "$RUN"    # durable FINDINGS.md from the same state
```

File every confirmation step where the user tracks work, one task per row, with
its owner. If there is no tracker, they stay in the confirmation table in
`FINDINGS.md` and the hand-back says so.

Then hand back in three lines: the URL, the path to `FINDINGS.md`, and the
manual steps left. Leave the server running so more questions can be answered,
and say plainly how to stop it: Ctrl-C in the terminal it is running in, or
`pkill -f "sprint.py serve"` if it was launched in the background.

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
| `sprint.py check` | Validates every file against the contract. Exit 1 on any error, with the reasons. An agent that wrote state without being on the roster is a warning, not an error. |
| `sprint.py findings` | Writes `FINDINGS.md` from the same state. |
| `sprint.py demo` | Fills a run with example state, to see the page in about five seconds. |

`scripts/schema.py` is the contract in code and `scripts/render.py` owns the
presentation. Read either one only to change it. The tests are the specification:
`python3 -m unittest discover -s tests`.

## Old patterns

Earlier versions of this pattern ran a `nohup` loop that re-rendered the page
every few seconds and opened it over `file://`. The server renders on each poll
instead, so there is no loop to leak and no static file to go stale.
