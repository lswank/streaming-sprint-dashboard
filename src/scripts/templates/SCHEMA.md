# Agent contract

Read this before you do anything. Read `CONTEXT.md` next: it holds the shared
facts, so you never re-derive what another agent already established.

For the options of any one kind, the run directory and your name come first:
`sprint.py add <RUN> <AGENT> default --help`.

You own one question. You do not write HTML, you do not edit JSON by hand, and
you do not touch another agent's files. One process per agent: two processes
writing the same agent's file will lose rows, because each write rewrites the
whole file. Everything you report goes through
`sprint.py`, which owns the schema and the ids.

Your name is `<AGENT>` below. `<RUN>` is the run directory you were given.

## Report as you go, not at the end

```
sprint.py log <RUN> <AGENT> "opened the Q3 filing, looking for the escalator"
```

One line per step, as it happens. That log is what the user watches. An agent
that logs only when it finishes leaves a dead card on the screen for twenty
minutes, which is worse than no dashboard at all.

Set your summary when it changes, and your status when it changes:

```
sprint.py set <RUN> <AGENT> --status running --summary "one or two sentences, current"
sprint.py set <RUN> <AGENT> --status done      # requires a non-empty summary
sprint.py set <RUN> <AGENT> --status blocked   # say what unblocks you in the summary
```

## The four things you report

**Evidence.** Every claim carries a source and a date. No exceptions.

```
sprint.py add <RUN> <AGENT> evidence \
  --claim "The escalator is fixed at 3.5%" \
  --source "executed-lease.pdf p.4 sec 12.1" --date 2026-09-20 \
  --quote "increase by three and one half percent (3.5%) per annum"
```

If you could not open a source, that is an unknown, not evidence. A 403 is a
reason, and it belongs in `--tried`.

**Unknowns.** Absence of evidence is reported as absence. Never fill it in.

```
sprint.py add <RUN> <AGENT> unknown \
  --question "What does the restoration clause require?" \
  --why "It is the largest single term in the exit number" \
  --tried "Read the lease and both amendments; the clause points at an annex nobody has"
```

**Defaults.** A default is a decision you would defend, not a guess, and it
states the cost of being wrong in concrete terms.

```
sprint.py add <RUN> <AGENT> default \
  --decision "Assume the 3.5% escalator applies to year one of the renewal" \
  --rationale "Schedule B applies it to every extension term" \
  --cost-if-wrong "Understates a five-year renewal by about 40 thousand dollars"
```

If you cannot state the cost, you have a guess. Report it as an unknown.

**Confirmation steps.** One line per thing a named human has to check. Each one
becomes a tracked task at the end, so `--owner` is a role or a person, never
"someone".

```
sprint.py add <RUN> <AGENT> confirm \
  --step "Have counsel confirm the escalator applies to extension terms" \
  --owner counsel --source "executed-lease.pdf sec 12.1"
```

## Asking the human

Only for a question that no amount of your work can settle: a preference, an
authority, a fact only they hold. Everything else you go and find out.

```
sprint.py add <RUN> <AGENT> ask \
  --question "Is a six month gap in occupancy acceptable?" \
  --option "No gap" --option "Up to 3 months" --option "Up to 6 months" \
  --why "It decides whether the two cheapest comparables are reachable at all" \
  --if-unanswered "Assumes no gap, which removes both cheap options"
```

Two to four options. Keep each option short: it is rendered as a button. The
command prints the question's id and is safe to run twice with the same text.

Then keep working. You never block on an answer; `--if-unanswered` is what
happens if none arrives, and you proceed on it.

Check once more before you finish, because the answer often lands while you work:

```
sprint.py replies <RUN>
```

If your question was answered, use the answer and say so in your summary.

## Before you finish

Re-read the "Already settled" table in `CONTEXT.md`: the coordinator adds
verified facts there while you work, and one of them may settle something you
reported as unknown.

```
sprint.py check <RUN> --agent <AGENT>       # your file only; must print ok
sprint.py replies <RUN>                     # did your question get answered?
sprint.py set <RUN> <AGENT> --status done \
  --summary "Two sentences answering your question, with the number and its unit."
```

`--status done` is refused without a summary, so write the summary first. If you
cannot answer your question, finish as `blocked` and say in the summary what
would unblock it.
