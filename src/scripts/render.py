#!/usr/bin/env python3
"""Renders the sprint state directory into one self-contained HTML page.

Every pixel the user sees is decided here. Agents write JSON; this file owns the
presentation, so an agent can never break the layout or leak markup into it.
The page is served, not opened from disk, so it can also capture answers back.

Read this file when you want to change how the dashboard looks. Do not hand-edit
the HTML it produces: the next build overwrites it.
"""
from __future__ import annotations

import hashlib
import html
import json
from typing import Iterable

from schema import AgentState, Answer, Manifest, qid

LOG_TAIL_LINES = 12          # WHY: a card stays under one screenful at phone width
MAX_LOG_LINE_CHARS = 200     # WHY: one runaway line must not stretch the grid

STATUS_LABEL = {"queued": "queued", "running": "running", "blocked": "blocked",
                "done": "done", "rejected": "file rejected"}


def e(text: object) -> str:
    """Escape for text and attribute positions alike (quote=True covers both)."""
    return html.escape(str(text), quote=True)


def plural(n: int, word: str) -> str:
    """"1 unknown" and "2 unknowns". A count that disagrees with its label reads
    as a bug in the tool, which costs trust in everything else on the page."""
    return word if n == 1 else word + "s"


def _hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:12]


# --- regions -------------------------------------------------------------------

def answers_region(manifest: Manifest, answers: dict[str, Answer]) -> str:
    """Section 1: the deliverable. Curated answers to what was actually asked."""
    if not manifest.questions:
        return ""
    out = []
    for q in manifest.questions:
        a = answers.get(q.id)
        if a is None:
            out.append(
                f'<article class="ans pending"><div class="qtext">{e(q.text)}</div>'
                f'<div class="chiprow"><span class="chip waiting">not answered yet</span></div>'
                f'<p class="pendingnote">The coordinator writes this once the agents '
                f'that own it have reported.</p></article>'
            )
            continue
        sources = "".join(
            f'<li>{_source_link(s)}</li>' for s in a.sources
        )
        out.append(
            f'<article class="ans"><div class="qtext">{e(q.text)}</div>'
            f'<div class="chiprow"><span class="chip verdict">{e(a.verdict)}</span>'
            f'<span class="chip conf conf-{e(a.confidence)}">{e(a.confidence)} confidence</span></div>'
            f'<div class="body">{_paras(a.answer)}</div>'
            + (f'<ul class="sources">{sources}</ul>' if sources else
               '<p class="nosource">No source attached. Treat as a hypothesis.</p>')
            + '</article>'
        )
    return "".join(out)


def _source_link(s: str) -> str:
    if s.startswith(("http://", "https://")):
        return f'<a href="{e(s)}" target="_blank" rel="noreferrer">{e(s)}</a>'
    return e(s)


def _paras(text: str) -> str:
    return "".join(f"<p>{e(p.strip())}</p>" for p in text.split("\n\n") if p.strip())


def questions_region(states: Iterable[AgentState], feedback: dict[str, dict]) -> str:
    """Section 2: the interactive panel. Loudest thing on the page while it has rows."""
    open_cards, answered = [], []
    for st in states:
        for h in st.human_input:
            i = qid(st.name, h.question)
            card = _question_card(st.name, i, h, feedback.get(i))
            (answered if i in feedback else open_cards).append(card)
    if not open_cards and not answered:
        return '<p class="empty">Nothing needs you yet.</p>'
    parts = []
    if open_cards:
        parts.append(f'<div class="qgrid">{"".join(open_cards)}</div>')
    if answered:
        parts.append(
            f'<details class="answered"><summary>{len(answered)} answered</summary>'
            f'<div class="qgrid">{"".join(answered)}</div></details>'
        )
    return "".join(parts)


def _question_card(agent: str, i: str, h, given: dict | None) -> str:
    # WHY the label is never interpolated into the handler: apostrophes in a
    # label ("owner's cut") break an inline JS string. The handler receives the
    # element and reads its own textContent instead.
    buttons = "".join(
        f'<button type="button" class="opt" onclick="answerWith(this)">{e(o)}</button>'
        for o in h.options
    )
    meta = f'<div class="why">{e(h.why)}</div>' if h.why else ""
    fallback = (f'<div class="fallback">If unanswered: {e(h.if_unanswered)}</div>'
                if h.if_unanswered else "")
    if given:
        return (
            f'<article class="q done" data-qid="{e(i)}" data-agent="{e(agent)}">'
            f'<div class="qhead"><span class="agent">{e(agent)}</span></div>'
            f'<div class="qtext">{e(h.question)}</div>'
            f'<div class="given">{e(given.get("answer", ""))}</div></article>'
        )
    return (
        f'<article class="q" data-qid="{e(i)}" data-agent="{e(agent)}" data-question="{e(h.question)}">'
        f'<div class="qhead"><span class="agent">{e(agent)}</span></div>'
        f'<div class="qtext">{e(h.question)}</div>'
        f'{meta}'
        f'<div class="opts">{buttons}</div>'
        f'<div class="freerow"><input type="text" placeholder="or type an answer"'
        f' onkeydown="if(event.key===\'Enter\')answerFree(this)"><button type="button"'
        f' class="send" onclick="answerFree(this.previousElementSibling)">Send</button></div>'
        f'{fallback}</article>'
    )


def agents_region(states: Iterable[AgentState], logs: dict[str, list[str]]) -> str:
    """Section 3: one card per rostered agent, with the tail of its own log."""
    cards = []
    for st in states:
        tail = logs.get(st.name, [])[-LOG_TAIL_LINES:]
        if tail:
            lines = "".join(f"<div>{e(l[:MAX_LOG_LINE_CHARS])}</div>" for l in tail)
            log = f'<div class="log">{lines}</div>'
        elif st.status == "queued":
            log = '<div class="log skel"><span></span><span></span></div>'
        else:
            log = '<div class="log quiet">no log lines yet</div>'
        tallies = [
            (len(st.evidence), "sourced claim", "a claim with a source and a date"),
            (len(st.defaults), "default", "decisions taken with the cost stated"),
            (len(st.unknowns), "unknown", "questions left open"),
            (len(st.confirm), "check to confirm", "checks a human has to make"),
        ]
        counts = "".join(f'<span title="{e(t)}">{n} {e(plural(n, label))}</span>'
                         for n, label, t in tallies if n)
        cards.append(
            f'<article class="card s-{e(st.status)}">'
            f'<header><h3>{e(st.name)}</h3>'
            f'<span class="dot"></span><span class="status">{e(STATUS_LABEL.get(st.status, st.status))}</span></header>'
            # WHY the write time is an attribute and not text: the stamp is UTC
            # and the reader is not, so the page words it in local terms.
            + (f'<span class="ago" data-at="{e(st.updated_at)}"></span>' if st.updated_at else "")
            + (f'<p class="remit">{e(st.remit)}</p>' if st.remit else "")
            + (f'<p class="summary">{e(st.summary)}</p>' if st.summary else
               '<p class="summary quiet">no summary yet</p>')
            + (f'<div class="counts">{counts}</div>' if counts else "")
            + f'{log}</article>'
        )
    return f'<div class="cards">{"".join(cards)}</div>'


def aggregate_region(states: Iterable[AgentState]) -> str:
    """Section 4: everything unresolved, pooled across agents."""
    states = list(states)
    unknowns = [(st.name, u) for st in states for u in st.unknowns]
    defaults = [(st.name, d) for st in states for d in st.defaults]
    confirm = [(st.name, c) for st in states for c in st.confirm]

    evidence = [(st.name, ev) for st in states for ev in st.evidence]

    e_rows = "".join(
        f'<tr><td class="agent">{e(n)}</td><td>{e(ev.claim)}'
        + (f'<div class="quote">{e(ev.quote)}</div>' if ev.quote else "")
        + f'</td><td class="dim">{_source_link(ev.source)}</td>'
        f'<td class="dim nowrap">{e(ev.date)}</td>'
        f'<td class="dim">{e(ev.verified_by or "unverified")}</td></tr>'
        for n, ev in evidence
    )
    u_rows = "".join(
        f'<tr><td class="agent">{e(n)}</td><td>{e(u.question)}</td>'
        f'<td class="dim">{e(u.why_it_matters)}</td></tr>' for n, u in unknowns
    )
    d_rows = "".join(
        f'<tr><td class="agent">{e(n)}</td><td>{e(d.decision)}'
        f'<div class="quote">{e(d.rationale)}</div></td>'
        f'<td class="dim">{e(d.cost_if_wrong)}</td></tr>' for n, d in defaults
    )
    c_rows = "".join(
        f'<tr><td class="agent">{e(n)}</td><td>{e(c.step)}</td>'
        f'<td class="dim">{e(c.owner)}</td></tr>' for n, c in confirm
    )
    return "".join([
        _table("Evidence", ("agent", "claim", "source", "date", "verified by"), e_rows,
               "No sourced claim yet."),
        _table("Open unknowns", ("agent", "question", "why it matters"), u_rows,
               "Nothing open."),
        _table("Defaults taken", ("agent", "decision", "cost if wrong"), d_rows,
               "No defaults taken."),
        _table("Confirmation plan", ("agent", "step", "owner"), c_rows,
               "Nothing to confirm."),
    ])


def _table(title: str, cols: tuple[str, ...], rows: str, empty: str) -> str:
    head = "".join(f"<th>{e(c)}</th>" for c in cols)
    body = rows or f'<tr><td colspan="{len(cols)}" class="quiet">{e(empty)}</td></tr>'
    return (f'<section class="tbl"><h3>{e(title)}</h3><table><thead><tr>{head}</tr></thead>'
            f'<tbody>{body}</tbody></table></section>')


def counts(states: Iterable[AgentState], feedback: dict[str, dict]) -> dict[str, int]:
    states = list(states)
    waiting = sum(
        1 for st in states for h in st.human_input if qid(st.name, h.question) not in feedback
    )
    return {
        "agents": len(states),
        "done": sum(1 for s in states if s.status == "done"),
        "running": sum(1 for s in states if s.status == "running"),
        "blocked": sum(1 for s in states if s.status == "blocked"),
        "rejected": sum(1 for s in states if s.status == "rejected"),
        "waiting": waiting,
    }


def pills_region(states: Iterable[AgentState], feedback: dict[str, dict]) -> str:
    """The roster size always shows; a count of zero is noise, so it does not."""
    c = counts(states, feedback)
    out = [f'<span class="pill">{c["agents"]} {plural(c["agents"], "agent")}</span>']
    for key, label, cls in (("running", "running", "run"), ("done", "done", "ok"),
                            ("blocked", "blocked", "warn"),
                            ("rejected", "unreadable", "warn"),
                            ("waiting", "need you", "need")):
        if c[key]:
            out.append(f'<span class="pill {cls}">{c[key]} {label}</span>')
    return "".join(out)


def regions(manifest: Manifest, states: list[AgentState], answers: dict[str, Answer],
            feedback: dict[str, dict], logs: dict[str, list[str]]) -> dict[str, str]:
    return {
        "pills": pills_region(states, feedback),
        "answers": answers_region(manifest, answers),
        "questions": questions_region(states, feedback),
        "agents": agents_region(states, logs),
        "aggregate": aggregate_region(states),
    }


def state_payload(manifest: Manifest, states: list[AgentState], answers: dict[str, Answer],
                  feedback: dict[str, dict], logs: dict[str, list[str]]) -> dict:
    """What the page polls. Regions arrive as HTML so presentation stays in one place."""
    r = regions(manifest, states, answers, feedback, logs)
    return {"regions": r, "hashes": {k: _hash(v) for k, v in r.items()},
            "counts": counts(states, feedback)}


# --- the page ------------------------------------------------------------------

def page(manifest: Manifest, states: list[AgentState], answers: dict[str, Answer],
         feedback: dict[str, dict], logs: dict[str, list[str]], *, live: bool) -> str:
    r = regions(manifest, states, answers, feedback, logs)
    hashes = json.dumps({k: _hash(v) for k, v in r.items()})
    live_note = ("" if live else
                 '<div class="banner">Static build. Answers can only be captured by '
                 '<code>sprint.py serve</code>.</div>')
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(manifest.title)}</title>
<style>{CSS}</style></head>
<body>
<header class="top">
  <div class="tl"><h1>{e(manifest.title)}</h1>
  <div class="pills" id="region-pills">{r["pills"]}</div></div>
  <div class="pulse" id="pulse" title="live"><span></span>live</div>
</header>
{live_note}
<main>
  <section class="block need" id="needblock">
    <h2>Needs a human answer</h2>
    <div id="region-questions" class="region">{r["questions"]}</div>
  </section>
  <section class="block">
    <h2>Answers</h2>
    <div id="region-answers" class="region">{r["answers"]}</div>
  </section>
  <section class="block">
    <h2>Agents</h2>
    <div id="region-agents" class="region">{r["agents"]}</div>
  </section>
  <section class="block">
    <h2>Still open</h2>
    <div id="region-aggregate" class="region">{r["aggregate"]}</div>
  </section>
</main>
<script>
const LIVE = {str(live).lower()};
const hashes = {hashes};
let typing = false;
document.addEventListener('focusin', ev => {{ if (ev.target.matches('input,textarea')) typing = true; }});
document.addEventListener('focusout', ev => {{ if (ev.target.matches('input,textarea')) typing = false; }});

function regionOf(el) {{ return el.closest('.region'); }}

async function post(card, answer) {{
  const body = {{
    id: card.dataset.qid,
    agent: card.dataset.agent,
    question: card.dataset.question,
    answer: answer,
  }};
  card.classList.add('sending');
  try {{
    const res = await fetch('/answer', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify(body),
    }});
    if (!res.ok) throw new Error(res.status);
    card.classList.remove('sending');
    // WHY optimistic in-place swap: the next poll is up to 3s away and the row
    // must acknowledge the click immediately without moving anything else.
    card.classList.add('done');
    card.querySelector('.opts')?.remove();
    card.querySelector('.freerow')?.remove();
    card.querySelector('.fallback')?.remove();
    const given = document.createElement('div');
    given.className = 'given';
    given.textContent = answer;
    card.appendChild(given);
    tick();
  }} catch (err) {{
    card.classList.remove('sending');
    card.classList.add('failed');
  }}
}}

function answerWith(btn) {{ post(btn.closest('.q'), btn.textContent.trim()); }}

function answerFree(input) {{
  const v = input.value.trim();
  if (!v) return;
  input.value = '';
  post(input.closest('.q'), v);
}}

function ago(iso) {{
  const then = Date.parse(iso);
  if (!then) return '';
  const secs = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (secs < 45) return 'wrote just now';
  const mins = Math.round(secs / 60);
  if (mins < 60) return `wrote ${{mins}} min ago`;
  const hours = Math.floor(mins / 60);
  return `wrote ${{hours}}h ${{mins % 60}}m ago`;
}}

function paintTimes() {{
  document.querySelectorAll('.ago[data-at]').forEach(el => {{
    el.textContent = ago(el.dataset.at);
  }});
}}

async function tick() {{
  if (!LIVE) return;
  try {{
    const res = await fetch('/state.json', {{cache: 'no-store'}});
    const s = await res.json();
    for (const [name, h] of Object.entries(s.hashes)) {{
      // WHY skip while typing: swapping the questions region would eat a
      // half-typed answer. Everything else keeps updating.
      if (name === 'questions' && typing) continue;
      if (hashes[name] === h) continue;
      const el = document.getElementById('region-' + name);
      if (!el) continue;
      hashes[name] = h;
      el.innerHTML = s.regions[name];
      paintTimes();
    }}
    document.getElementById('pulse').classList.remove('stale');
  }} catch (err) {{
    document.getElementById('pulse').classList.add('stale');
  }}
}}
paintTimes();
setInterval(paintTimes, 30000);   // the wording ages even when nothing else changes
if (LIVE) setInterval(tick, 3000);
</script>
</body></html>
"""


CSS = """
:root {
  color-scheme: light dark;
  --bg: #f6f6f4; --panel: #fff; --ink: #14161a; --dim: #5d6470; --line: #e2e2dd;
  --accent: #2f6f5e; --need: #a8601b; --warn: #a33a2a; --ok: #2f6f5e;
  --mono: ui-monospace, SFMono-Regular, Menlo, monospace;
  --sans: ui-sans-serif, -apple-system, "Segoe UI", system-ui, sans-serif;
  --r: 10px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #101214; --panel: #171a1d; --ink: #e9eaec; --dim: #9aa2ad; --line: #262a2f;
    --accent: #5fbfa3; --need: #e0a355; --warn: #e27a68; --ok: #5fbfa3;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font-family: var(--sans);
       font-size: 15px; line-height: 1.45; }
h1 { font-size: 19px; margin: 0; letter-spacing: -0.01em; }
h2 { font-size: 12px; text-transform: uppercase; letter-spacing: 0.09em; color: var(--dim);
     margin: 0 0 10px; font-weight: 600; }
h3 { font-size: 14px; margin: 0; }
.top { display: flex; align-items: center; justify-content: space-between; gap: 16px;
       padding: 18px 16px 12px; border-bottom: 1px solid var(--line); position: sticky; top: 0;
       background: color-mix(in srgb, var(--bg) 88%, transparent); backdrop-filter: blur(8px); z-index: 5; }
.pills { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.pill { font-size: 11px; padding: 2px 8px; border: 1px solid var(--line); border-radius: 999px;
        color: var(--dim); background: var(--panel); }
.pill.run { color: var(--accent); } .pill.ok { color: var(--ok); }
.pill.warn { color: var(--warn); } .pill.need { color: var(--need); border-color: var(--need); }
.pulse { display: flex; align-items: center; gap: 6px; font-size: 11px; color: var(--dim); }
.pulse span { width: 7px; height: 7px; border-radius: 50%; background: var(--accent);
              animation: breathe 2.4s ease-in-out infinite; }
.pulse.stale span { background: var(--warn); animation: none; }
@keyframes breathe { 0%,100% { opacity: .35 } 50% { opacity: 1 } }
.banner { margin: 12px 16px 0; padding: 8px 12px; border: 1px solid var(--need);
          border-radius: var(--r); color: var(--need); font-size: 13px; }
main { padding: 16px; display: grid; gap: 22px; max-width: 1180px; margin: 0 auto; }
.region { display: grid; gap: 10px; }

.ans { background: var(--panel); border: 1px solid var(--line); border-radius: var(--r);
       padding: 14px 16px; }
.ans .qtext { font-weight: 600; margin-bottom: 8px; }
.chiprow { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }
.chip { font-size: 11px; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--line); }
.chip.verdict { background: var(--accent); border-color: var(--accent); color: var(--bg); font-weight: 600; }
.chip.conf-high { color: var(--ok); border-color: var(--ok); }
.chip.conf-low { color: var(--warn); border-color: var(--warn); }
.chip.conf-medium { color: var(--need); border-color: var(--need); }
.chip.waiting { color: var(--dim); }
.ans .body p { margin: 0 0 8px; }
.sources { margin: 6px 0 0; padding-left: 18px; font-size: 12px; color: var(--dim); }
.sources a { color: inherit; }
.nosource { font-size: 12px; color: var(--warn); margin: 6px 0 0; }

.block.need h2 { color: var(--need); }
.qgrid { display: grid; gap: 10px; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); }
.q { background: var(--panel); border: 1px solid var(--need); border-radius: var(--r);
     padding: 12px 14px; transition: opacity .18s ease; }
.q.done { border-color: var(--line); opacity: .72; }
.q.sending { opacity: .5; }
.q.failed { border-color: var(--warn); }
.qhead { font-size: 11px; color: var(--dim); text-transform: uppercase; letter-spacing: .06em; }
.q .qtext { font-weight: 600; margin: 4px 0 6px; }
.why { font-size: 12px; color: var(--dim); margin-bottom: 8px; }
.opts { display: flex; flex-wrap: wrap; gap: 6px; }
button { font: inherit; font-size: 13px; cursor: pointer; border-radius: 8px;
         border: 1px solid var(--line); background: var(--bg); color: var(--ink); padding: 5px 11px; }
button:hover { border-color: var(--accent); color: var(--accent); }
.freerow { display: flex; gap: 6px; margin-top: 8px; }
.freerow input { flex: 1; min-width: 0; font: inherit; font-size: 13px; padding: 5px 9px;
                 border: 1px solid var(--line); border-radius: 8px; background: var(--bg); color: var(--ink); }
.fallback { font-size: 11px; color: var(--dim); margin-top: 8px; }
.given { margin-top: 8px; font-size: 13px; border-left: 2px solid var(--accent); padding-left: 8px; }
.answered summary { font-size: 12px; color: var(--dim); cursor: pointer; margin-bottom: 8px; }

.cards { display: grid; gap: 10px; grid-template-columns: repeat(auto-fit, minmax(310px, 1fr)); }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: var(--r); padding: 12px 14px; }
.card header { display: flex; align-items: center; gap: 7px; }
.card .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--dim); margin-left: auto; }
.card.s-running .dot { background: var(--accent); animation: breathe 2.4s ease-in-out infinite; }
.card.s-done .dot { background: var(--ok); }
.card.s-blocked .dot { background: var(--warn); }
.card .status { font-size: 11px; color: var(--dim); }
.ago { font-size: 11px; color: var(--dim); display: block; margin-top: 5px; }
.ago:empty { display: none; }
.pendingnote { font-size: 13px; color: var(--dim); margin: 0; }
.remit { font-size: 12px; color: var(--dim); margin: 6px 0 0; }
.summary { font-size: 13px; margin: 8px 0 0; }
.counts { display: flex; gap: 10px; font-size: 11px; color: var(--dim); margin: 9px 0 8px; }
.log { font-family: var(--mono); font-size: 11px; color: var(--dim); background: var(--bg);
       border: 1px solid var(--line); border-radius: 8px; padding: 8px; display: grid; gap: 2px; }
.log div { white-space: pre-wrap; overflow-wrap: anywhere; }
.log.quiet { color: var(--dim); font-family: var(--sans); }

/* WHY overflow-x on the wrapper: measured at a 390px viewport nothing overflows
   today, but one long owner or step must scroll inside its own table rather than
   make the whole page scroll sideways. */
.tbl { margin-bottom: 14px; overflow-x: auto; }
.quote { font-size: 12px; color: var(--dim); margin-top: 3px; border-left: 2px solid var(--line);
         padding-left: 7px; }
td.nowrap { white-space: nowrap; }
.card.s-rejected .dot { background: var(--warn); }
.tbl h3 { font-size: 12px; text-transform: uppercase; letter-spacing: .07em; color: var(--dim);
          margin-bottom: 6px; }
table { width: 100%; border-collapse: collapse; background: var(--panel);
        border: 1px solid var(--line); border-radius: var(--r); overflow: hidden; font-size: 13px; }
th { text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: .06em;
     color: var(--dim); font-weight: 600; padding: 7px 10px; border-bottom: 1px solid var(--line); }
td { padding: 7px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
tr:last-child td { border-bottom: none; }
td.agent { color: var(--dim); white-space: nowrap; }
td.dim, .dim { color: var(--dim); }
.quiet { color: var(--dim); }
.empty { color: var(--dim); font-size: 13px; margin: 0; }

/* Skeletons: a queued agent shows shape, not emptiness, and nothing pops in. */
.skel { display: grid; gap: 5px; }
.skel span { height: 8px; border-radius: 4px;
             background: linear-gradient(90deg, var(--line), color-mix(in srgb, var(--line) 40%, transparent), var(--line));
             background-size: 200% 100%; animation: shimmer 1.6s linear infinite; }
.skel span:nth-child(2) { width: 78% } .skel span:nth-child(3) { width: 54% }
@keyframes shimmer { from { background-position: 200% 0 } to { background-position: -200% 0 } }
@media (prefers-reduced-motion: reduce) { * { animation: none !important; transition: none !important; } }
@media (max-width: 560px) {
  .top { flex-direction: column; align-items: flex-start; gap: 8px; }
  main { padding: 16px; }
}
"""
