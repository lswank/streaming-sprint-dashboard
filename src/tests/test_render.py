"""Rendering tests, including the contrast ratios of the palette.

WHY contrast is a test and not an eyeball: the page is read on a laptop in a
bright room as often as in the dark, and "looks fine" is not a number.
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import render
import schema
from schema import AgentState, Answer, HumanInput, Manifest, Question, qid

WCAG_TEXT = 4.5      # body text, WCAG AA
WCAG_LARGE = 3.0     # large text and UI boundaries, WCAG AA


def manifest():
    return Manifest(title="Renewal sprint", questions=[Question(id="q1", text="Renew or leave?")])


class TestEscaping(unittest.TestCase):
    def test_apostrophe_in_an_option_survives_as_text(self):
        st = AgentState(name="market", status="running", human_input=[
            HumanInput(question="Whose call is it?", options=["The owner's", "The board's"])])
        html = render.questions_region([st], {})
        self.assertIn("The owner&#x27;s", html)
        self.assertNotIn("The owner's", html)

    def test_markup_in_agent_text_cannot_reach_the_page(self):
        st = AgentState(name="x", status="done", summary="<script>alert(1)</script>")
        html = render.agents_region([st], {})
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_markup_in_the_title_cannot_reach_the_page(self):
        m = Manifest(title='</title><script>alert(1)</script>')
        html = render.page(m, [], {}, {}, {}, live=True)
        self.assertNotIn("<script>alert(1)", html)

    def test_a_pipe_in_a_table_cell_does_not_split_the_row(self):
        st = AgentState(name="x", unknowns=[schema.Unknown(question="a|b", why_it_matters="c")])
        self.assertIn("a|b", render.aggregate_region([st]))


class TestQuestions(unittest.TestCase):
    def test_card_carries_the_stable_id(self):
        st = AgentState(name="market", human_input=[HumanInput(question="Gap ok?", options=["Yes"])])
        html = render.questions_region([st], {})
        self.assertIn(f'data-qid="{qid("market", "Gap ok?")}"', html)

    def test_handler_never_interpolates_the_label(self):
        st = AgentState(name="market", human_input=[HumanInput(question="q", options=["it's fine"])])
        html = render.questions_region([st], {})
        self.assertIn('onclick="answerWith(this)"', html)
        self.assertNotIn("answerWith('", html)

    def test_answered_question_moves_out_of_the_open_grid(self):
        st = AgentState(name="market", human_input=[HumanInput(question="Gap ok?", options=["Yes"])])
        i = qid("market", "Gap ok?")
        html = render.questions_region([st], {i: {"answer": "Yes"}})
        self.assertIn("1 answered", html)
        self.assertIn('class="given"', html)
        self.assertNotIn('class="opts"', html)

    def test_empty_panel_says_so(self):
        self.assertIn("Nothing needs you yet", render.questions_region([], {}))

    def test_free_text_row_is_offered_alongside_the_options(self):
        st = AgentState(name="market", human_input=[HumanInput(question="q", options=["a"])])
        self.assertIn("or type an answer", render.questions_region([st], {}))


class TestAgents(unittest.TestCase):
    def test_queued_agent_renders_a_skeleton_not_an_absence(self):
        html = render.agents_region([AgentState(name="market")], {})
        self.assertIn("market", html)
        self.assertIn('class="log skel"', html)

    def test_log_tail_is_capped(self):
        lines = [f"line {i}" for i in range(60)]
        html = render.agents_region([AgentState(name="m", status="running")], {"m": lines})
        self.assertIn("line 59", html)
        self.assertNotIn("line 47", html)
        self.assertEqual(html.count("<div>line"), render.LOG_TAIL_LINES)

    def test_one_runaway_log_line_is_truncated(self):
        html = render.agents_region([AgentState(name="m", status="running")], {"m": ["x" * 5000]})
        self.assertNotIn("x" * (render.MAX_LOG_LINE_CHARS + 1), html)

    def test_zero_tallies_are_not_shown(self):
        html = render.agents_region([AgentState(name="m", status="running")], {})
        self.assertNotIn("0 ", html)

    def test_tallies_appear_once_there_is_something_to_count(self):
        st = AgentState(name="m", status="running",
                        evidence=[schema.Evidence(claim="c", source="s", date="2026-01-01")])
        self.assertIn("1 sourced claim", render.agents_region([st], {}))

    def test_a_count_agrees_with_its_label(self):
        one = AgentState(name="m", status="running",
                         unknowns=[schema.Unknown(question="q", why_it_matters="w")])
        two = AgentState(name="m", status="running",
                         unknowns=[schema.Unknown(question="q", why_it_matters="w"),
                                   schema.Unknown(question="r", why_it_matters="w")])
        self.assertIn("1 unknown<", render.agents_region([one], {}))
        self.assertIn("2 unknowns<", render.agents_region([two], {}))


class TestAnswers(unittest.TestCase):
    def test_unanswered_question_shows_a_placeholder(self):
        html = render.answers_region(manifest(), {})
        self.assertIn("not answered yet", html)
        self.assertIn("Renew or leave?", html)

    def test_answer_without_a_source_is_labelled_a_hypothesis(self):
        a = Answer(question_id="q1", verdict="Leave", answer="body", confidence="low")
        self.assertIn("hypothesis", render.answers_region(manifest(), {"q1": a}))

    def test_http_source_becomes_a_link_and_a_path_does_not(self):
        a = Answer(question_id="q1", verdict="v", answer="b",
                   sources=["https://example.com/x", "lease.pdf p.4"])
        html = render.answers_region(manifest(), {"q1": a})
        self.assertIn('href="https://example.com/x"', html)
        self.assertNotIn('href="lease.pdf', html)

    def test_verdict_and_confidence_are_both_chips(self):
        a = Answer(question_id="q1", verdict="Settled, not open", answer="b", confidence="high")
        html = render.answers_region(manifest(), {"q1": a})
        self.assertIn("Settled, not open", html)
        self.assertIn("high confidence", html)


class TestPayload(unittest.TestCase):
    def test_regions_and_hashes_line_up(self):
        p = render.state_payload(manifest(), [], {}, {}, {})
        self.assertEqual(set(p["regions"]), set(p["hashes"]))

    def test_hash_changes_when_state_changes(self):
        a = render.state_payload(manifest(), [AgentState(name="m", status="running")], {}, {}, {})
        b = render.state_payload(manifest(), [AgentState(name="m", status="done", summary="s")], {}, {}, {})
        self.assertNotEqual(a["hashes"]["agents"], b["hashes"]["agents"])

    def test_counts_exclude_answered_questions_from_waiting(self):
        st = AgentState(name="m", human_input=[HumanInput(question="q", options=["a"])])
        i = qid("m", "q")
        self.assertEqual(render.counts([st], {})["waiting"], 1)
        self.assertEqual(render.counts([st], {i: {"answer": "a"}})["waiting"], 0)


class TestPage(unittest.TestCase):
    def test_static_build_says_answers_cannot_be_captured(self):
        html = render.page(manifest(), [], {}, {}, {}, live=False)
        self.assertIn("const LIVE = false", html)
        self.assertIn("Static build", html)

    def test_live_page_polls(self):
        html = render.page(manifest(), [], {}, {}, {}, live=True)
        self.assertIn("const LIVE = true", html)
        self.assertIn("setInterval(tick", html)

    def test_typing_guard_exists(self):
        html = render.page(manifest(), [], {}, {}, {}, live=True)
        self.assertIn("if (name === 'questions' && typing) continue;", html)

    def test_page_loads_nothing_from_the_network(self):
        html = render.page(manifest(), [], {}, {}, {}, live=True)
        for tag in ("<link", "<img", "@import", "src=\"http"):
            self.assertNotIn(tag, html)

    def test_page_is_responsive_and_declares_both_schemes(self):
        html = render.page(manifest(), [], {}, {}, {}, live=True)
        self.assertIn('name="viewport"', html)
        self.assertIn("prefers-color-scheme: dark", html)
        self.assertIn("color-scheme: light dark", html)

    def test_reduced_motion_is_honoured(self):
        self.assertIn("prefers-reduced-motion", render.CSS)


# --- palette, measured ----------------------------------------------------------

def tokens(block: str) -> dict[str, str]:
    found = re.findall(r"--([a-z]+):\s*#([0-9a-fA-F]{3,6})\b", block)
    return {k: "#" + ("".join(c * 2 for c in v) if len(v) == 3 else v) for k, v in found}


def palettes() -> dict[str, dict[str, str]]:
    light = tokens(render.CSS.split("@media", 1)[0])
    dark = dict(light)
    block = re.search(r"prefers-color-scheme: dark\).*?^\}", render.CSS, re.S | re.M)
    assert block, "the dark-scheme media block moved; the contrast test cannot read the palette"
    dark.update(tokens(block.group(0)))
    return {"light": light, "dark": dark}


def luminance(hex_colour: str) -> float:
    c = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def ratio(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


class TestContrast(unittest.TestCase):
    """Fails with the measured number, so a palette change reports what it cost."""

    TEXT_PAIRS = [("ink", "bg"), ("ink", "panel"), ("dim", "bg"), ("dim", "panel"),
                  ("accent", "panel"), ("need", "panel"), ("warn", "panel"), ("bg", "accent")]
    UI_PAIRS = [("line", "panel"), ("line", "bg")]

    def test_text_pairs_meet_aa(self):
        for scheme, p in palettes().items():
            for fg, bg in self.TEXT_PAIRS:
                with self.subTest(scheme=scheme, pair=f"{fg}/{bg}"):
                    r = ratio(p[fg], p[bg])
                    self.assertGreaterEqual(round(r, 2), WCAG_TEXT,
                                            f"{scheme} {fg} on {bg} is {r:.2f}:1, needs {WCAG_TEXT}:1")

    def test_hairlines_are_visible(self):
        for scheme, p in palettes().items():
            for fg, bg in self.UI_PAIRS:
                with self.subTest(scheme=scheme, pair=f"{fg}/{bg}"):
                    r = ratio(p[fg], p[bg])
                    self.assertGreaterEqual(round(r, 2), 1.1,
                                            f"{scheme} {fg} on {bg} is {r:.2f}:1 and disappears")

    def test_both_schemes_define_every_token(self):
        light, dark = palettes()["light"], palettes()["dark"]
        for key in ("bg", "panel", "ink", "dim", "line", "accent", "need", "warn", "ok"):
            self.assertIn(key, light)
            self.assertIn(key, dark)


if __name__ == "__main__":
    unittest.main()
