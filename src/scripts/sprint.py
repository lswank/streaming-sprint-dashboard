#!/usr/bin/env python3
"""One CLI for a streaming sprint: scaffold it, write to it, render it, serve it.

Agents never write HTML and never hand-edit JSON. They call this script, which
owns the schema, the ids, the atomic writes and the presentation. Run it; there
is nothing in here to reimplement in a prompt.

    sprint.py init RUNDIR --title T --question Q --agent name:remit
    sprint.py serve RUNDIR [--port 8787]
    sprint.py log RUNDIR AGENT "what just happened"
    sprint.py set RUNDIR AGENT --status running --summary "..."
    sprint.py add RUNDIR AGENT default --decision D --rationale R --cost-if-wrong C
    sprint.py add RUNDIR AGENT ask --question Q --option A --option B
    sprint.py answer RUNDIR q1 --verdict "Settled, not open" --answer "..." --source URL
    sprint.py replies RUNDIR
    sprint.py check RUNDIR
    sprint.py findings RUNDIR

Python 3.10+, standard library only.
"""
from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import render  # noqa: E402
import schema  # noqa: E402
from schema import (  # noqa: E402
    AgentState, Answer, ConfirmStep, Default, Evidence, HumanInput, Manifest,
    Question, RosterEntry, Unknown, ValidationError, now, qid,
)

DEFAULT_PORT = 8787
# WHY local time in the log and UTC in updated_at: the log is read by a human
# glancing at a card, the timestamp is read by the tooling.
LOG_STAMP = "%H:%M:%S"


# --- run directory ---------------------------------------------------------------

class Run:
    """The state directory. Every read tolerates a file an agent is mid-write on."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.state = root / "state"

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    def require(self) -> None:
        if not self.manifest_path.exists():
            raise SystemExit(f"{self.root} is not a sprint run directory (no manifest.json). "
                             f"Run: sprint.py init {self.root} --title ... --agent ...")

    def manifest(self) -> Manifest:
        return schema.read_json(self.manifest_path, Manifest)

    def agent_path(self, name: str) -> Path:
        return self.state / f"{name}.json"

    def log_path(self, name: str) -> Path:
        return self.state / f"{name}.log"

    def agent_names(self) -> list[str]:
        """Roster order first, then any extra state file, so a stray agent still shows."""
        roster = [r.name for r in self.manifest().roster]
        extra = sorted(p.stem for p in self.state.glob("*.json")
                       if not p.name.startswith("_") and p.stem not in roster)
        return roster + extra

    def read_agent(self, name: str) -> AgentState:
        path = self.agent_path(name)
        if not path.exists():
            remit = next((r.remit for r in self.manifest().roster if r.name == name), "")
            return AgentState(name=name, remit=remit)
        return schema.read_json(path, AgentState)

    def read_agent_lenient(self, name: str) -> tuple[AgentState, str]:
        """For rendering: a bad file becomes a blocked card, never a traceback on screen."""
        try:
            return self.read_agent(name), ""
        except ValidationError as exc:
            return AgentState(name=name, status="blocked",
                              summary=f"state file rejected: {exc}"), str(exc)

    def write_agent(self, st: AgentState) -> None:
        st.updated_at = now()
        self.state.mkdir(parents=True, exist_ok=True)
        schema.write_json(self.agent_path(st.name), st)

    # WHY a byte cap: the page only ever shows the last few lines, and an agent
    # that logs a stack trace or a whole file would otherwise be re-read from
    # disk in full on every poll.
    LOG_TAIL_BYTES = 64_000

    def read_logs(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for p in sorted(self.state.glob("*.log")):
            with p.open("rb") as fh:
                size = p.stat().st_size
                if size > self.LOG_TAIL_BYTES:
                    fh.seek(size - self.LOG_TAIL_BYTES)
                    fh.readline()          # drop the half line the seek landed in
                text = fh.read().decode(errors="replace")
            out[p.stem] = text.splitlines()
        return out

    def answers(self) -> dict[str, Answer]:
        path = self.state / "_answers.json"
        if not path.exists():
            return {}
        raw = json.loads(path.read_text())
        return {k: schema.build(Answer, v, f"_answers.json[{k}]") for k, v in raw.items()}

    def write_answer(self, a: Answer) -> None:
        current = {k: schema.to_dict(v) for k, v in self.answers().items()}
        current[a.question_id] = schema.to_dict(a)
        path = self.state / "_answers.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(current, indent=2) + "\n")
        tmp.replace(path)

    def feedback(self) -> dict[str, dict]:
        """Last reply per question id wins: the user is allowed to change their mind."""
        path = self.state / "_feedback.jsonl"
        out: dict[str, dict] = {}
        if not path.exists():
            return out
        for line in path.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("id"):
                out[row["id"]] = row
        return out

    def append_feedback(self, row: dict) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        with (self.state / "_feedback.jsonl").open("a") as fh:
            fh.write(json.dumps(row) + "\n")

    def snapshot(self) -> tuple[Manifest, list[AgentState], dict[str, Answer], dict[str, dict], dict[str, list[str]]]:
        m = self.manifest()
        states = [self.read_agent_lenient(n)[0] for n in self.agent_names()]
        return m, states, self.answers(), self.feedback(), self.read_logs()


# --- commands --------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> int:
    run = Run(Path(args.dir).resolve())
    if run.manifest_path.exists() and not args.force:
        raise SystemExit(f"{run.manifest_path} already exists. Pass --force to overwrite the manifest.")
    if not args.agent:
        raise SystemExit("--agent is required: a sprint with no roster renders an empty page")
    if not args.question:
        raise SystemExit("--question is required: the answers section is the deliverable")
    roster = []
    for spec in args.agent:
        name, _, remit = spec.partition(":")
        name = name.strip()
        if not name:
            raise SystemExit(f"--agent {spec!r}: name is empty; use name or name:remit")
        roster.append(RosterEntry(name=name, remit=remit.strip(), model=args.model))
    questions = [Question(id=f"q{i}", text=t) for i, t in enumerate(args.question, start=1)]
    m = Manifest(title=args.title, questions=questions, roster=roster, created=now())
    run.state.mkdir(parents=True, exist_ok=True)
    schema.write_json(run.manifest_path, m)
    for entry in roster:
        if not run.agent_path(entry.name).exists():
            run.write_agent(AgentState(name=entry.name, remit=entry.remit))
    _copy_template("SCHEMA.md", run.root / "SCHEMA.md")
    _write_context(run, m)
    print(f"initialised {run.root}")
    print(f"  {len(roster)} agents, {len(questions)} questions")
    print(f"  contract: {run.root / 'SCHEMA.md'}  shared facts: {run.root / 'CONTEXT.md'}")
    print(f"  next: python3 {Path(__file__).name} serve {run.root}")
    return 0


def _copy_template(name: str, dest: Path) -> None:
    src = HERE / "templates" / name
    if dest.exists():
        return
    dest.write_text(src.read_text())


def _write_context(run: Run, m: Manifest) -> None:
    dest = run.root / "CONTEXT.md"
    if dest.exists():
        return
    qs = "\n".join(f"- **{q.id}** {q.text}" for q in m.questions) or "- (none recorded)"
    agents = "\n".join(f"- `{r.name}` {r.remit}".rstrip() for r in m.roster)
    dest.write_text(f"""# Shared context: {m.title}

Every agent reads this file first. Keep it factual. If two agents would answer
differently because of something in here, fix it here rather than in a prompt.

## The request, verbatim

> (paste the exact words that triggered this sprint)

## Questions being answered

{qs}

## Roster

{agents}

## People, entities, systems

| Name | What it is | Why it matters here |
|---|---|---|

## Already settled

Facts verified by the coordinator. Do not re-derive these.

| Fact | Source | Date |
|---|---|---|
""")


def cmd_build(args: argparse.Namespace) -> int:
    run = Run(Path(args.dir).resolve())
    run.require()
    m, states, answers, feedback, logs = run.snapshot()
    out = Path(args.out) if args.out else run.root / "dashboard.html"
    out.write_text(render.page(m, states, answers, feedback, logs, live=False))
    print(out)
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    run = Run(Path(args.dir).resolve())
    run.require()
    run.state.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime(LOG_STAMP)
    with run.log_path(args.agent).open("a") as fh:
        fh.write(f"{stamp} {args.line}\n")
    # WHY: the first log line means the agent has started. Flipping the status here
    # keeps the card honest without asking the agent to remember two calls.
    st = run.read_agent(args.agent)
    if st.status == "queued":
        st.status = "running"
        run.write_agent(st)
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    run = Run(Path(args.dir).resolve())
    run.require()
    st = run.read_agent(args.agent)
    if args.status:
        if args.status not in schema.STATUSES:
            raise SystemExit(f"--status {args.status!r}; use one of {', '.join(schema.STATUSES)}")
        st.status = args.status
    if args.summary is not None:
        st.summary = args.summary
    if args.remit is not None:
        st.remit = args.remit
    run.write_agent(st)
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    run = Run(Path(args.dir).resolve())
    run.require()
    st = run.read_agent(args.agent)
    if args.kind == "unknown":
        st.unknowns.append(Unknown(question=args.question, why_it_matters=args.why, tried=args.tried or ""))
    elif args.kind == "default":
        st.defaults.append(Default(decision=args.decision, rationale=args.rationale,
                                   cost_if_wrong=args.cost_if_wrong))
    elif args.kind == "confirm":
        st.confirm.append(ConfirmStep(step=args.step, owner=args.owner, source=args.source or ""))
    elif args.kind == "evidence":
        st.evidence.append(Evidence(claim=args.claim, source=args.source, date=args.date,
                                    quote=args.quote or "", verified_by=args.verified_by or ""))
    elif args.kind == "ask":
        # WHY idempotent: agents retry, and a duplicated question would render twice
        # and collect two answers under one id.
        existing = {qid(st.name, h.question) for h in st.human_input}
        if qid(st.name, args.question) in existing:
            print(f"already asked: {qid(st.name, args.question)}")
            return 0
        st.human_input.append(HumanInput(question=args.question, options=args.option or [],
                                         why=args.why or "", if_unanswered=args.if_unanswered or ""))
        print(qid(st.name, args.question))
    run.write_agent(st)
    return 0


def cmd_answer(args: argparse.Namespace) -> int:
    run = Run(Path(args.dir).resolve())
    run.require()
    ids = {q.id for q in run.manifest().questions}
    if args.question_id not in ids:
        raise SystemExit(f"{args.question_id!r} is not a question in this run (have: {', '.join(sorted(ids))})")
    run.write_answer(Answer(question_id=args.question_id, verdict=args.verdict, answer=args.answer,
                            confidence=args.confidence, sources=args.source or []))
    return 0


def cmd_replies(args: argparse.Namespace) -> int:
    run = Run(Path(args.dir).resolve())
    run.require()
    rows = run.feedback()
    if args.json:
        print(json.dumps(list(rows.values()), indent=2))
        return 0
    if not rows:
        print("no replies yet")
        return 0
    for row in rows.values():
        print(f"[{row.get('id')}] {row.get('agent','?')}: {row.get('question','')}")
        print(f"    -> {row.get('answer','')}   ({row.get('at','')})")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Validate every file. This is what makes the contract real."""
    run = Run(Path(args.dir).resolve())
    run.require()
    problems: list[str] = []
    try:
        m = run.manifest()
    except ValidationError as exc:
        print("\n".join(f"error  {p}" for p in exc.problems))
        return 1
    rostered = {r.name for r in m.roster}
    for name in run.agent_names():
        try:
            st = run.read_agent(name)
        except ValidationError as exc:
            problems += [f"{name}.json: {p}" for p in exc.problems]
            continue
        if st.name != name:
            problems.append(f"{name}.json: name is {st.name!r}; it must match the filename")
        if name not in rostered:
            print(f"warning  {name} wrote state but is not on the roster")
        if st.status == "done" and not st.summary.strip():
            problems.append(f"{name}.json: status is done with an empty summary")
        for d in st.defaults:
            if len(d.cost_if_wrong.split()) < 3:
                problems.append(f"{name}.json: default {d.decision!r} has no real cost_if_wrong")
    try:
        answers = run.answers()
    except ValidationError as exc:
        problems += [f"_answers.json: {p}" for p in exc.problems]
        answers = {}
    known = {q.id for q in m.questions}
    for k, a in answers.items():
        if k not in known:
            problems.append(f"_answers.json: {k!r} is not a question in the manifest")
        if k != a.question_id:
            problems.append(f"_answers.json: key {k!r} does not match question_id {a.question_id!r}")
    for p in problems:
        print(f"error  {p}")
    if not problems:
        print(f"ok  {len(run.agent_names())} agents, {len(answers)}/{len(known)} questions answered")
    return 1 if problems else 0


def cmd_findings(args: argparse.Namespace) -> int:
    run = Run(Path(args.dir).resolve())
    run.require()
    m, states, answers, feedback, _ = run.snapshot()
    out = Path(args.out) if args.out else run.root / "FINDINGS.md"
    lines = [f"# {m.title}", "", f"Generated {now()} from {len(states)} agents.", ""]
    lines += ["## Answers", ""]
    for q in m.questions:
        a = answers.get(q.id)
        lines.append(f"### {q.text}")
        if not a:
            lines += ["", "Not answered.", ""]
            continue
        lines += ["", f"**{a.verdict}** ({a.confidence} confidence)", "", a.answer, ""]
        if a.sources:
            lines += ["Sources:", ""] + [f"- {s}" for s in a.sources] + [""]
        else:
            lines += ["No source attached. Treat as a hypothesis.", ""]
    if feedback:
        lines += ["## Answered by the human", ""]
        for row in feedback.values():
            lines.append(f"- {row.get('question','')} -> **{row.get('answer','')}** ({row.get('at','')})")
        lines.append("")
    rows = [(s.name, d) for s in states for d in s.defaults]
    if rows:
        lines += ["## Defaults taken", "", "| Agent | Decision | Cost if wrong |", "|---|---|---|"]
        lines += [f"| {n} | {_cell(d.decision)} | {_cell(d.cost_if_wrong)} |" for n, d in rows] + [""]
    rows = [(s.name, u) for s in states for u in s.unknowns]
    if rows:
        lines += ["## Still unknown", "", "| Agent | Question | Why it matters |", "|---|---|---|"]
        lines += [f"| {n} | {_cell(u.question)} | {_cell(u.why_it_matters)} |" for n, u in rows] + [""]
    rows = [(s.name, c) for s in states for c in s.confirm]
    if rows:
        lines += ["## Confirmation plan", "",
                  "Each row becomes a tracked task; none of them is done here.", "",
                  "| Owner | Step | Source | Found by |", "|---|---|---|---|"]
        lines += [f"| {c.owner} | {_cell(c.step)} | {_cell(c.source)} | {n} |" for n, c in rows] + [""]
    rows = [(s.name, ev) for s in states for ev in s.evidence]
    if rows:
        lines += ["## Evidence", "", "| Claim | Source | Date | Verified by |", "|---|---|---|---|"]
        lines += [f"| {_cell(ev.claim)} | {_cell(ev.source)} | {ev.date} | {ev.verified_by or 'unverified'} |"
                  for _, ev in rows] + [""]
    out.write_text("\n".join(lines))
    print(out)
    return 0


def _cell(text: str) -> str:
    """Keep a pipe inside a cell from splitting the table."""
    return text.replace("|", "\\|").replace("\n", " ").strip()


def cmd_demo(args: argparse.Namespace) -> int:
    """Populate a run with believable state, so the page can be checked in seconds."""
    root = Path(args.dir).resolve()
    run = Run(root)
    init = argparse.Namespace(
        dir=str(root), force=True, model="demo",
        title="Demo sprint: should we renew the warehouse lease?",
        question=["Do we renew, renegotiate or leave?",
                  "What does leaving actually cost?",
                  "Who has to sign off, and by when?"],
        agent=["lease-terms:reads the lease itself", "market:comparable space and rates",
               "exit-cost:moving, downtime and restoration", "signoff:who approves what",
               "coordinator:what the coordinator verified first hand"])
    cmd_init(init)
    steps = {
        "lease-terms": ["opened the executed lease", "found the renewal window clause",
                        "cross-checked the escalator against schedule B"],
        "market": ["pulled 6 comparable listings", "normalised to usable square feet",
                   "two listings are stale, dropped them"],
        "exit-cost": ["quoted two movers", "restoration clause needs a walkthrough"],
        "signoff": ["board calendar read", "treasurer is on leave until the 14th"],
        "coordinator": ["verified the renewal date against the recorded lease myself"],
    }
    for agent, lines in steps.items():
        for line in lines:
            cmd_log(argparse.Namespace(dir=str(root), agent=agent, line=line))
    run.write_agent(_demo_state(run, "lease-terms", "done",
        "Renewal window opens 90 days out and closes 30 days out. Escalator is 3.5% fixed, not CPI."))
    st = run.read_agent("lease-terms")
    st.evidence.append(Evidence(claim="Renewal window is 90 to 30 days before expiry",
                                source="executed-lease.pdf p.4 sec 12.1", date="2026-09-20",
                                quote="Tenant shall give notice not more than ninety (90) nor less than thirty (30) days",
                                verified_by="coordinator"))
    st.defaults.append(Default(decision="Assume the 3.5% escalator applies to year one of the renewal",
                               rationale="Schedule B applies it to every extension term",
                               cost_if_wrong="Understates a five-year renewal by about 40 thousand dollars"))
    st.confirm.append(ConfirmStep(step="Have counsel confirm the escalator applies to extension terms",
                                  owner="counsel", source="executed-lease.pdf sec 12.1"))
    run.write_agent(st)
    run.write_agent(_demo_state(run, "market", "running",
        "Comparable space is running 11 to 14 percent under our current rate."))
    st = run.read_agent("market")
    st.human_input.append(HumanInput(question="Is a 6 month gap in occupancy acceptable?",
                                     options=["No gap", "Up to 3 months", "Up to 6 months"],
                                     why="It decides whether the two cheapest comparables are reachable at all",
                                     if_unanswered="Assumes no gap, which removes both cheap options"))
    run.write_agent(st)
    run.write_agent(_demo_state(run, "exit-cost", "blocked",
        "Blocked on the restoration clause: cannot price it without a walkthrough."))
    st = run.read_agent("exit-cost")
    st.unknowns.append(Unknown(question="What does the restoration clause actually require?",
                               why_it_matters="It is the single largest unknown in the exit number",
                               tried="Read the lease and two amendments; the clause points at an annex nobody has"))
    st.human_input.append(HumanInput(question="Can we get a landlord walkthrough this month?",
                                     options=["Yes", "No", "Ask the broker"],
                                     why="Without it the exit number stays a range, not a number"))
    run.write_agent(st)
    run.write_agent(_demo_state(run, "signoff", "done",
        "Board approves anything over five years; the treasurer signs below that."))
    st = run.read_agent("signoff")
    st.confirm.append(ConfirmStep(step="Get the renewal on the October board agenda", owner="chair"))
    run.write_agent(st)
    run.write_agent(_demo_state(run, "coordinator", "running",
        "Verified the renewal dates first hand. The market rates are still second hand."))
    cmd_answer(argparse.Namespace(
        dir=str(root), question_id="q1", verdict="Renegotiate, do not renew as written",
        answer="The renewal window is open and the escalator is fixed at 3.5%, which is above "
               "what comparable space is asking. Renegotiating inside the window keeps the exit "
               "option alive; renewing as written gives it up.",
        confidence="medium", source=["executed-lease.pdf p.4 sec 12.1"]))
    cmd_answer(argparse.Namespace(
        dir=str(root), question_id="q3", verdict="Board, by 30 September",
        answer="Anything over a five year term needs the board. The renewal window closes 30 days "
               "before expiry, which lands before the November meeting.",
        confidence="high", source=["board-charter.pdf sec 4"]))
    print(f"demo run ready: {root}")
    return 0


def _demo_state(run: Run, name: str, status: str, summary: str) -> AgentState:
    st = run.read_agent(name)
    st.status = status
    st.summary = summary
    return st


# --- server ----------------------------------------------------------------------

def make_handler(run: Run):
    class Handler(BaseHTTPRequestHandler):
        server_version = "sprint-dashboard"

        def log_message(self, fmt, *a):  # WHY: a poll every 3s would bury real errors
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            try:
                if path == "/":
                    m, states, answers, feedback, logs = run.snapshot()
                    body = render.page(m, states, answers, feedback, logs, live=True)
                    self._send(200, body.encode(), "text/html; charset=utf-8")
                elif path == "/state.json":
                    m, states, answers, feedback, logs = run.snapshot()
                    payload = render.state_payload(m, states, answers, feedback, logs)
                    self._send(200, json.dumps(payload).encode(), "application/json")
                else:
                    # WHY no file serving at all: nothing on this page needs it, and a
                    # static handler over a state directory is a traversal hole.
                    self._send(404, b"not found", "text/plain")
            except Exception as exc:  # a broken state file must not kill the server
                self._send(500, f"{type(exc).__name__}: {exc}".encode(), "text/plain")

        def do_POST(self) -> None:
            if self.path.split("?", 1)[0] != "/answer":
                self._send(404, b"not found", "text/plain")
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > 64_000:  # WHY: a typed answer is never this long
                self._send(413, b"too large", "text/plain")
                return
            try:
                row = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(row, dict) or not row.get("answer"):
                    raise ValueError("answer is required")
                run.append_feedback({
                    "id": str(row.get("id") or "")[:32],
                    "agent": str(row.get("agent") or "")[:64],
                    "question": str(row.get("question") or "")[:2000],
                    "answer": str(row["answer"])[:4000],
                    "at": now(),
                })
            except (ValueError, json.JSONDecodeError) as exc:
                self._send(400, f"bad request: {exc}".encode(), "text/plain")
                return
            self._send(200, b'{"ok":true}', "application/json")

    return Handler


def cmd_serve(args: argparse.Namespace) -> int:
    run = Run(Path(args.dir).resolve())
    run.require()
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(run))
    except OSError as exc:
        raise SystemExit(f"cannot serve on port {args.port}: {exc}. "
                         f"Another sprint is probably already serving; pass --port with a free one.")
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(url, flush=True)
    if args.open:
        webbrowser.open(url)
    if args.once:  # used by the tests: serve exactly one request and exit
        httpd.handle_request()
        return 0
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


# --- argument parsing -------------------------------------------------------------

def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sprint.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init", help="scaffold a run directory")
    i.add_argument("dir")
    i.add_argument("--title", required=True)
    i.add_argument("--question", action="append", default=[],
                   help="one asked question; repeat in the order they were asked")
    i.add_argument("--agent", action="append", default=[], metavar="NAME[:REMIT]")
    i.add_argument("--model", default="", help="model the agents run on, for the record")
    i.add_argument("--force", action="store_true")
    i.set_defaults(fn=cmd_init)

    b = sub.add_parser("build", help="render dashboard.html once (no answer capture)")
    b.add_argument("dir")
    b.add_argument("--out")
    b.set_defaults(fn=cmd_build)

    s = sub.add_parser("serve", help="serve the live dashboard on localhost")
    s.add_argument("dir")
    s.add_argument("--port", type=int, default=DEFAULT_PORT)
    s.add_argument("--open", action="store_true", help="open the page in the default browser")
    s.add_argument("--once", action="store_true", help=argparse.SUPPRESS)
    s.set_defaults(fn=cmd_serve)

    l = sub.add_parser("log", help="append one line to an agent's log")
    l.add_argument("dir")
    l.add_argument("agent")
    l.add_argument("line")
    l.set_defaults(fn=cmd_log)

    st = sub.add_parser("set", help="set an agent's status, summary or remit")
    st.add_argument("dir")
    st.add_argument("agent")
    st.add_argument("--status", choices=list(schema.STATUSES))
    st.add_argument("--summary")
    st.add_argument("--remit")
    st.set_defaults(fn=cmd_set)

    a = sub.add_parser("add", help="append one finding to an agent's state")
    a.add_argument("dir")
    a.add_argument("agent")
    kinds = a.add_subparsers(dest="kind", required=True)

    k = kinds.add_parser("unknown")
    k.add_argument("--question", required=True)
    k.add_argument("--why", required=True, help="why it matters to the answer")
    k.add_argument("--tried")

    k = kinds.add_parser("default")
    k.add_argument("--decision", required=True)
    k.add_argument("--rationale", required=True)
    k.add_argument("--cost-if-wrong", required=True, dest="cost_if_wrong")

    k = kinds.add_parser("confirm")
    k.add_argument("--step", required=True)
    k.add_argument("--owner", required=True)
    k.add_argument("--source")

    k = kinds.add_parser("evidence")
    k.add_argument("--claim", required=True)
    k.add_argument("--source", required=True)
    k.add_argument("--date", required=True, help="the date the source carries, or the date fetched")
    k.add_argument("--quote")
    k.add_argument("--verified-by", dest="verified_by")

    k = kinds.add_parser("ask", help="ask the human one question; prints its stable id")
    k.add_argument("--question", required=True)
    k.add_argument("--option", action="append", default=[])
    k.add_argument("--why")
    k.add_argument("--if-unanswered", dest="if_unanswered")
    a.set_defaults(fn=cmd_add)

    an = sub.add_parser("answer", help="record the coordinator's answer to an asked question")
    an.add_argument("dir")
    an.add_argument("question_id")
    an.add_argument("--verdict", required=True, help="short chip; allowed to contradict the question")
    an.add_argument("--answer", required=True)
    an.add_argument("--confidence", choices=list(schema.CONFIDENCES), default="medium")
    an.add_argument("--source", action="append", default=[])
    an.set_defaults(fn=cmd_answer)

    r = sub.add_parser("replies", help="show what the human answered")
    r.add_argument("dir")
    r.add_argument("--json", action="store_true")
    r.set_defaults(fn=cmd_replies)

    c = sub.add_parser("check", help="validate every file against the contract")
    c.add_argument("dir")
    c.set_defaults(fn=cmd_check)

    f = sub.add_parser("findings", help="write FINDINGS.md from the same state")
    f.add_argument("dir")
    f.add_argument("--out")
    f.set_defaults(fn=cmd_findings)

    d = sub.add_parser("demo", help="fill a run with example state to check the page")
    d.add_argument("dir")
    d.set_defaults(fn=cmd_demo)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.fn(args)
    except ValidationError as exc:
        print("\n".join(f"error  {p}" for p in exc.problems), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
