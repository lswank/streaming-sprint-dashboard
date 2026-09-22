"""Contract tests for the state objects. Standard library only: python3 -m unittest discover."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import schema
from schema import AgentState, Answer, Default, Manifest, ValidationError


class TestQid(unittest.TestCase):
    def test_stable_for_same_text(self):
        self.assertEqual(schema.qid("market", "Is a gap ok?"), schema.qid("market", "Is a gap ok?"))

    def test_ignores_surrounding_whitespace(self):
        self.assertEqual(schema.qid("market", "Is a gap ok?"), schema.qid(" market ", "Is a gap ok? "))

    def test_differs_by_agent_and_by_question(self):
        a = schema.qid("market", "Is a gap ok?")
        self.assertNotEqual(a, schema.qid("exit-cost", "Is a gap ok?"))
        self.assertNotEqual(a, schema.qid("market", "Is a gap okay?"))

    def test_is_ten_hex_chars(self):
        i = schema.qid("market", "Is a gap ok?")
        self.assertEqual(len(i), 10)
        int(i, 16)


class TestValidation(unittest.TestCase):
    def test_minimal_agent_state(self):
        st = schema.build(AgentState, {"name": "market"})
        self.assertEqual(st.status, "queued")
        self.assertEqual(st.unknowns, [])

    def test_unknown_field_is_rejected(self):
        with self.assertRaises(ValidationError) as cm:
            schema.build(AgentState, {"name": "market", "findings": []})
        self.assertIn("unknown field 'findings'", str(cm.exception))

    def test_missing_required_field_is_named(self):
        with self.assertRaises(ValidationError) as cm:
            schema.build(AgentState, {"status": "done"})
        self.assertIn("missing required field 'name'", str(cm.exception))

    def test_default_without_cost_is_rejected(self):
        with self.assertRaises(ValidationError) as cm:
            schema.build(Default, {"decision": "d", "rationale": "r"})
        self.assertIn("cost_if_wrong", str(cm.exception))

    def test_empty_required_field_is_rejected(self):
        with self.assertRaises(ValidationError) as cm:
            schema.build(Default, {"decision": "d", "rationale": "r", "cost_if_wrong": "   "})
        self.assertIn("must not be empty", str(cm.exception))

    def test_bad_status_is_rejected(self):
        with self.assertRaises(ValidationError) as cm:
            schema.build(AgentState, {"name": "m", "status": "thinking"})
        self.assertIn("queued, running, blocked, done", str(cm.exception))

    def test_bad_confidence_is_rejected(self):
        with self.assertRaises(ValidationError) as cm:
            schema.build(Answer, {"question_id": "q1", "verdict": "v", "answer": "a",
                                  "confidence": "certain"})
        self.assertIn("confidence", str(cm.exception))

    def test_wrong_type_is_rejected(self):
        with self.assertRaises(ValidationError) as cm:
            schema.build(AgentState, {"name": "m", "unknowns": "none"})
        self.assertIn("expected a list", str(cm.exception))

    def test_every_problem_is_reported_not_just_the_first(self):
        with self.assertRaises(ValidationError) as cm:
            schema.build(AgentState, {"name": "m", "status": "x", "bogus": 1,
                                      "evidence": [{"claim": "c", "source": "", "date": "2026-01-01"}]})
        self.assertEqual(len(cm.exception.problems), 3, cm.exception.problems)

    def test_nested_problem_names_its_index(self):
        with self.assertRaises(ValidationError) as cm:
            schema.build(AgentState, {"name": "m", "defaults": [
                {"decision": "a", "rationale": "r", "cost_if_wrong": "c"},
                {"decision": "b", "rationale": "r"}]})
        self.assertIn("defaults[1]", str(cm.exception))

    def test_string_list_rejects_a_non_string_item(self):
        with self.assertRaises(ValidationError) as cm:
            schema.build(Answer, {"question_id": "q1", "verdict": "v", "answer": "a",
                                  "sources": ["ok", 7]})
        self.assertIn("sources[1]", str(cm.exception))


class TestFiles(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "market.json"
            st = AgentState(name="market", status="running", summary="s")
            schema.write_json(p, st)
            back = schema.read_json(p, AgentState)
            self.assertEqual(back, st)

    def test_write_leaves_no_temp_file_behind(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "market.json"
            schema.write_json(p, AgentState(name="market"))
            self.assertEqual([f.name for f in Path(d).iterdir()], ["market.json"])

    def test_invalid_json_names_the_file_and_line(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "market.json"
            p.write_text('{"name": "market",\n  oops}')
            with self.assertRaises(ValidationError) as cm:
                schema.read_json(p, AgentState)
            self.assertIn("market.json", str(cm.exception))
            self.assertIn("line 2", str(cm.exception))

    def test_manifest_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "manifest.json"
            m = Manifest(title="t", questions=[schema.Question(id="q1", text="why?")],
                         roster=[schema.RosterEntry(name="market", remit="rates")])
            schema.write_json(p, m)
            self.assertEqual(schema.read_json(p, Manifest), m)
            self.assertIn("questions", json.loads(p.read_text()))


if __name__ == "__main__":
    unittest.main()
