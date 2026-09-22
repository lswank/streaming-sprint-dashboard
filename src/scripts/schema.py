#!/usr/bin/env python3
"""Typed state objects for a streaming sprint, plus strict validation.

WHY dataclasses and not Pydantic: this file is copied into three different agent
hosts and has to import on a bare `python3` with no install step. The strictness
Pydantic would give us is written out below instead: unknown keys are rejected,
every field's type is checked, and fields that carry the quality of the output
(a default's cost_if_wrong, a piece of evidence's source and date) are required
to be non-empty.

Read this file to learn the agent contract; run `sprint.py check` to enforce it.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from contextlib import contextmanager
from dataclasses import MISSING, dataclass, field, fields, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, get_args, get_origin

STATUSES = ("queued", "running", "blocked", "done")
CONFIDENCES = ("high", "medium", "low")

# WHY 10 hex chars: short enough to read in a URL or a log line, and 40 bits of
# md5 over (agent, question) is far past collision range for a handful of agents
# asking a handful of questions. The id must not depend on array position,
# because positions shift every time an agent appends to its own list.
ID_LEN = 10


def qid(agent: str, question: str) -> str:
    """Stable id for one human question. Same agent and text always give the same id."""
    digest = hashlib.md5(f"{agent.strip()}\n{question.strip()}".encode()).hexdigest()
    return digest[:ID_LEN]


def now() -> str:
    """UTC timestamp, seconds precision. Sorts lexically, so the log tail needs no parsing."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ValidationError(ValueError):
    """Carries every problem found, not just the first one."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


# WHY a name pattern: the agent name becomes a filename. Without this, an agent
# called "../../x" writes outside the run directory, and one called "/tmp/x"
# writes anywhere at all.
AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def check_agent_name(name: str) -> str:
    """Return the name, or raise. Called on every path that turns a name into a file."""
    if not AGENT_NAME_RE.match(name or ""):
        raise ValidationError([
            f"agent name {name!r} is not usable as a filename: use 1 to 64 characters of "
            f"letters, digits, dot, underscore or hyphen, starting with a letter or digit"])
    return name


# --- the pieces an agent reports ------------------------------------------------

@dataclass(slots=True)
class Evidence:
    """One sourced claim. No source or no date means it is not evidence yet."""
    claim: str
    source: str
    date: str
    quote: str = ""
    verified_by: str = ""


@dataclass(slots=True)
class Unknown:
    """Something the agent could not settle. Reported as absent, never filled in."""
    question: str
    why_it_matters: str
    tried: str = ""


@dataclass(slots=True)
class Default:
    """A decision the agent would defend, with the cost of being wrong stated."""
    decision: str
    rationale: str
    cost_if_wrong: str


@dataclass(slots=True)
class ConfirmStep:
    """One check that turns a default into a fact. Becomes a tracked task at the end."""
    step: str
    owner: str
    source: str = ""


@dataclass(slots=True)
class HumanInput:
    """A question only the human can answer. Rendered as a clickable card."""
    question: str
    options: list[str] = field(default_factory=list)
    why: str = ""
    if_unanswered: str = ""


@dataclass(slots=True)
class AgentState:
    """One agent's whole state. One file per agent: state/<name>.json."""
    name: str
    status: str = "queued"
    summary: str = ""
    remit: str = ""
    unknowns: list[Unknown] = field(default_factory=list)
    defaults: list[Default] = field(default_factory=list)
    confirm: list[ConfirmStep] = field(default_factory=list)
    human_input: list[HumanInput] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    updated_at: str = ""


# --- what the coordinator and the run itself carry ------------------------------

@dataclass(slots=True)
class Answer:
    """The coordinator's curated answer to one asked question. This is the deliverable.

    verdict is a short chip that is allowed to contradict the question
    ("Settled, not open"). confidence keeps a reader from reading a range as a
    number.
    """
    question_id: str
    verdict: str
    answer: str
    confidence: str = "medium"
    sources: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Question:
    id: str
    text: str


@dataclass(slots=True)
class RosterEntry:
    name: str
    remit: str = ""
    model: str = ""


@dataclass(slots=True)
class Manifest:
    """The run's fixed facts: what was asked and who is answering it."""
    title: str
    questions: list[Question] = field(default_factory=list)
    roster: list[RosterEntry] = field(default_factory=list)
    created: str = ""


# --- validation -----------------------------------------------------------------

# WHY: these are the fields whose emptiness silently degrades the output, so
# emptiness is an error rather than a warning.
REQUIRED_NONEMPTY: dict[type, tuple[str, ...]] = {
    Evidence: ("claim", "source", "date"),
    Unknown: ("question", "why_it_matters"),
    Default: ("decision", "rationale", "cost_if_wrong"),
    ConfirmStep: ("step", "owner"),
    HumanInput: ("question",),
    AgentState: ("name",),
    Answer: ("question_id", "verdict", "answer"),
    Question: ("id", "text"),
    RosterEntry: ("name",),
    Manifest: ("title",),
}


def _type_name(tp: Any) -> str:
    return getattr(tp, "__name__", str(tp))


def build(cls: type, data: Any, where: str = "") -> Any:
    """Build a dataclass from plain JSON data, collecting every problem."""
    problems: list[str] = []
    value = _build(cls, data, where or cls.__name__, problems)
    if problems:
        raise ValidationError(problems)
    return value


def _build(cls: type, data: Any, where: str, problems: list[str]) -> Any:
    if not isinstance(data, dict):
        problems.append(f"{where}: expected an object, got {type(data).__name__}")
        return None
    known = {f.name: f for f in fields(cls)}
    for key in data:
        if key not in known:
            problems.append(f"{where}: unknown field {key!r} (allowed: {', '.join(known)})")
    kwargs: dict[str, Any] = {}
    missing: list[str] = []
    for name, f in known.items():
        if name not in data:
            # WHY: a field with no default is part of the contract; name it plainly
            # rather than letting __init__ raise a TypeError the agent has to decode.
            if f.default is MISSING and f.default_factory is MISSING:
                missing.append(name)
                problems.append(f"{where}: missing required field {name!r}")
            continue
        kwargs[name] = _coerce(f.type, data[name], f"{where}.{name}", problems)
    if missing:
        return None
    obj = cls(**{k: v for k, v in kwargs.items() if v is not None})
    for name in REQUIRED_NONEMPTY.get(cls, ()):
        if not str(getattr(obj, name, "") or "").strip():
            problems.append(f"{where}.{name} is required and must not be empty")
    if cls in (AgentState, RosterEntry) and not AGENT_NAME_RE.match(getattr(obj, "name", "") or ""):
        problems.append(f"{where}.name {obj.name!r} is not usable as a filename")
    if cls is AgentState and obj.status not in STATUSES:
        problems.append(f"{where}.status is {obj.status!r}; use one of {', '.join(STATUSES)}")
    if cls is Answer and obj.confidence not in CONFIDENCES:
        problems.append(f"{where}.confidence is {obj.confidence!r}; use one of {', '.join(CONFIDENCES)}")
    return obj


CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
BIDI_OVERRIDES = re.compile(r"[\u202a-\u202e\u2066-\u2069]")


def clean_text(value: str) -> str:
    """Strip what has no business in a report: control characters and the bidi
    overrides that let text render in an order it was not written in."""
    return BIDI_OVERRIDES.sub("", CONTROL_CHARS.sub("", value))


def _coerce(tp: Any, value: Any, where: str, problems: list[str]) -> Any:
    """Resolve the string annotations dataclasses leaves us under `from __future__`."""
    if isinstance(tp, str):
        tp = _RESOLVE.get(tp, tp)
    if tp is str or tp == "str":
        if not isinstance(value, str):
            problems.append(f"{where}: expected a string, got {type(value).__name__}")
            return None
        return clean_text(value)
    origin = get_origin(tp)
    if origin is list:
        (item_tp,) = get_args(tp)
        if not isinstance(value, list):
            problems.append(f"{where}: expected a list, got {type(value).__name__}")
            return None
        out = []
        for i, item in enumerate(value):
            if item_tp is str:
                if not isinstance(item, str):
                    problems.append(f"{where}[{i}]: expected a string, got {type(item).__name__}")
                    continue
                out.append(clean_text(item))
            else:
                built = _build(item_tp, item, f"{where}[{i}]", problems)
                if built is not None:
                    out.append(built)
        return out
    if is_dataclass(tp):
        return _build(tp, value, where, problems)
    problems.append(f"{where}: unsupported field type {_type_name(tp)}")
    return None


_RESOLVE: dict[str, Any] = {
    "str": str,
    "list[str]": list[str],
    "list[Unknown]": list[Unknown],
    "list[Default]": list[Default],
    "list[ConfirmStep]": list[ConfirmStep],
    "list[HumanInput]": list[HumanInput],
    "list[Evidence]": list[Evidence],
    "list[Question]": list[Question],
    "list[RosterEntry]": list[RosterEntry],
}


def to_dict(obj: Any) -> Any:
    """dataclasses.asdict, minus the recursion cost of dict copies we do not need."""
    if is_dataclass(obj):
        return {f.name: to_dict(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, list):
        return [to_dict(i) for i in obj]
    return obj


def read_json(path: Path, cls: type) -> Any:
    """Read one state file. Every failure becomes a ValidationError naming the file,
    so one unreadable file never reaches the caller as a traceback."""
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValidationError([f"{path.name}: invalid JSON at line {exc.lineno} ({exc.msg})"]) from exc
    except OSError as exc:
        raise ValidationError([f"{path.name}: cannot be read ({exc.strerror or exc})"]) from exc
    except UnicodeDecodeError as exc:
        raise ValidationError([f"{path.name}: is not text ({exc.reason})"]) from exc
    return build(cls, data, path.name)


def write_json(path: Path, obj: Any) -> None:
    """Atomic write: agents and the renderer run at the same time, so a half-written
    state file would render as a parse error in the user's face.

    The temp name carries the process id. Two writers sharing one temp path used
    to race and one of them died with FileNotFoundError on replace.
    """
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    try:
        tmp.write_text(json.dumps(to_dict(obj), indent=2) + "\n")
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink()


@contextmanager
def locked(path: Path):
    """Hold an exclusive advisory lock for a read-modify-write on one file.

    WHY: an agent usually has several tool calls in flight. Without this, two
    `add` commands both read the file, both append one row, and the second write
    drops the first one's row. The lock file sits beside the target so the lock
    survives the atomic replace of the file itself.
    """
    # WHY a hidden subdirectory: the lock outlives the atomic replace of the file
    # it guards, and state/ is a directory a person reads. Locks do not belong in it.
    locks = path.parent / ".locks"
    locks.mkdir(parents=True, exist_ok=True)
    lock = locks / (path.name + ".lock")
    with lock.open("a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
