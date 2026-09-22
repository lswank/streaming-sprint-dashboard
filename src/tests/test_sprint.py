"""CLI and server tests, including the answer round trip the dashboard depends on."""
import contextlib
import io
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import schema
import sprint
from schema import AgentState, qid


LAST_OUT = ""


def run(*argv) -> int:
    """Call the CLI in-process, keeping its stdout out of the test report."""
    global LAST_OUT
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            return sprint.main([str(a) for a in argv])
    finally:
        LAST_OUT = buf.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name) / "run"
        self.addCleanup(self._tmp.cleanup)
        run("init", self.dir, "--title", "Lease sprint",
            "--question", "Renew or leave?", "--question", "What does leaving cost?",
            "--agent", "market:rates and comparables", "--agent", "exit-cost")
        self.run_obj = sprint.Run(self.dir)


class TestInit(Base):
    def test_scaffolds_the_whole_directory(self):
        for rel in ("manifest.json", "SCHEMA.md", "CONTEXT.md",
                    "state/market.json", "state/exit-cost.json"):
            self.assertTrue((self.dir / rel).exists(), rel)

    def test_roster_order_is_the_order_given(self):
        self.assertEqual(self.run_obj.agent_names(), ["market", "exit-cost"])

    def test_remit_survives_into_the_agent_file(self):
        self.assertEqual(self.run_obj.read_agent("market").remit, "rates and comparables")

    def test_questions_are_numbered_in_the_order_asked(self):
        qs = self.run_obj.manifest().questions
        self.assertEqual([q.id for q in qs], ["q1", "q2"])
        self.assertEqual(qs[0].text, "Renew or leave?")

    def test_context_file_quotes_the_questions(self):
        self.assertIn("Renew or leave?", (self.dir / "CONTEXT.md").read_text())

    def test_agents_start_queued(self):
        self.assertEqual(self.run_obj.read_agent("market").status, "queued")

    def test_second_init_refuses_without_force(self):
        with self.assertRaises(SystemExit):
            run("init", self.dir, "--title", "again", "--agent", "market")

    def test_force_reinit_keeps_existing_agent_state(self):
        run("set", self.dir, "market", "--summary", "kept")
        run("init", self.dir, "--title", "again", "--question", "q?", "--agent", "market", "--force")
        self.assertEqual(self.run_obj.read_agent("market").summary, "kept")

    def test_init_without_a_roster_is_refused(self):
        with self.assertRaises(SystemExit) as cm:
            run("init", self.dir.parent / "empty", "--title", "t", "--question", "q?")
        self.assertIn("--agent is required", str(cm.exception))

    def test_init_without_a_question_is_refused(self):
        with self.assertRaises(SystemExit) as cm:
            run("init", self.dir.parent / "empty", "--title", "t", "--agent", "market")
        self.assertIn("--question is required", str(cm.exception))

    def test_a_command_on_a_non_run_directory_says_what_to_do(self):
        with self.assertRaises(SystemExit) as cm:
            run("build", self.dir.parent / "nope")
        self.assertIn("init", str(cm.exception))


class TestWrites(Base):
    def test_log_appends_and_flips_queued_to_running(self):
        run("log", self.dir, "market", "pulled six listings")
        self.assertIn("pulled six listings", (self.dir / "state/market.log").read_text())
        self.assertEqual(self.run_obj.read_agent("market").status, "running")

    def test_log_does_not_override_a_terminal_status(self):
        run("set", self.dir, "market", "--status", "done", "--summary", "s")
        run("log", self.dir, "market", "late line")
        self.assertEqual(self.run_obj.read_agent("market").status, "done")

    def test_set_rejects_an_invented_status(self):
        with self.assertRaises(SystemExit):
            run("set", self.dir, "market", "--status", "thinking")

    def test_add_default_requires_a_cost(self):
        with self.assertRaises(SystemExit):
            run("add", self.dir, "market", "default", "--decision", "d", "--rationale", "r")

    def test_add_evidence_lands_in_state(self):
        run("add", self.dir, "market", "evidence", "--claim", "rate is 14", "--source",
            "https://example.com/l", "--date", "2026-09-20")
        ev = self.run_obj.read_agent("market").evidence
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0].source, "https://example.com/l")

    def test_ask_is_idempotent(self):
        run("add", self.dir, "market", "ask", "--question", "Gap ok?", "--option", "Yes")
        run("add", self.dir, "market", "ask", "--question", "Gap ok?", "--option", "Yes")
        self.assertEqual(len(self.run_obj.read_agent("market").human_input), 1)

    def test_updated_at_is_stamped_on_every_write(self):
        run("set", self.dir, "market", "--summary", "s")
        self.assertTrue(self.run_obj.read_agent("market").updated_at)

    def test_writes_from_two_agents_do_not_collide(self):
        run("add", self.dir, "market", "unknown", "--question", "a", "--why", "b")
        run("add", self.dir, "exit-cost", "unknown", "--question", "c", "--why", "d")
        self.assertEqual(len(self.run_obj.read_agent("market").unknowns), 1)
        self.assertEqual(len(self.run_obj.read_agent("exit-cost").unknowns), 1)


class TestBigLog(Base):
    def test_only_the_tail_of_a_huge_log_is_read(self):
        big = self.dir / "state/market.log"
        with big.open("w") as fh:
            for i in range(200_000):
                fh.write(f"12:00:00 line {i}\n")
        self.assertGreater(big.stat().st_size, 2_000_000)
        lines = self.run_obj.read_logs()["market"]
        self.assertLessEqual(len("\n".join(lines).encode()), sprint.Run.LOG_TAIL_BYTES)
        self.assertIn("12:00:00 line 199999", lines[-1])

    def test_a_partial_first_line_is_dropped_not_shown(self):
        big = self.dir / "state/market.log"
        with big.open("w") as fh:
            fh.write("x" * (sprint.Run.LOG_TAIL_BYTES + 500) + "\n12:00:00 real line\n")
        lines = self.run_obj.read_logs()["market"]
        self.assertEqual(lines, ["12:00:00 real line"])


class TestAnswersAndChecks(Base):
    def test_answer_rejects_an_unknown_question_id(self):
        with self.assertRaises(SystemExit) as cm:
            run("answer", self.dir, "q9", "--verdict", "v", "--answer", "a")
        self.assertIn("q1", str(cm.exception))

    def test_answer_is_stored_and_overwritten_in_place(self):
        run("answer", self.dir, "q1", "--verdict", "Leave", "--answer", "first")
        run("answer", self.dir, "q1", "--verdict", "Renegotiate", "--answer", "second",
            "--confidence", "high", "--source", "lease.pdf")
        a = self.run_obj.answers()["q1"]
        self.assertEqual((a.verdict, a.answer, a.confidence), ("Renegotiate", "second", "high"))
        self.assertEqual(len(self.run_obj.answers()), 1)

    def test_check_passes_on_a_fresh_run(self):
        self.assertEqual(run("check", self.dir), 0)

    def test_check_catches_a_done_agent_with_no_summary(self):
        st = self.run_obj.read_agent("market")
        st.status = "done"
        self.run_obj.write_agent(st)
        self.assertEqual(run("check", self.dir), 1)

    def test_check_catches_a_hand_edited_state_file(self):
        (self.dir / "state/market.json").write_text('{"name": "market", "status": "winning"}')
        self.assertEqual(run("check", self.dir), 1)

    def test_check_catches_a_one_word_cost_if_wrong(self):
        st = self.run_obj.read_agent("market")
        st.defaults.append(schema.Default(decision="d", rationale="r", cost_if_wrong="bad"))
        self.run_obj.write_agent(st)
        self.assertEqual(run("check", self.dir), 1)

    def test_check_catches_a_filename_that_does_not_match_the_name_inside(self):
        (self.dir / "state/market.json").write_text('{"name": "markets"}')
        self.assertEqual(run("check", self.dir), 1)


class TestOutputs(Base):
    def test_build_writes_a_static_page(self):
        run("build", self.dir)
        html = (self.dir / "dashboard.html").read_text()
        self.assertIn("Lease sprint", html)
        self.assertIn("Static build", html)

    def test_a_broken_state_file_renders_as_a_blocked_card_not_a_traceback(self):
        (self.dir / "state/market.json").write_text("{oops")
        run("build", self.dir)
        html = (self.dir / "dashboard.html").read_text()
        self.assertIn("state file rejected", html)
        self.assertIn("exit-cost", html)

    def test_findings_carries_answers_defaults_unknowns_and_the_plan(self):
        run("answer", self.dir, "q1", "--verdict", "Leave", "--answer", "because", "--source", "l.pdf")
        run("add", self.dir, "market", "default", "--decision", "d", "--rationale", "r",
            "--cost-if-wrong", "costs about 40 thousand dollars")
        run("add", self.dir, "market", "unknown", "--question", "u", "--why", "matters")
        run("add", self.dir, "market", "confirm", "--step", "call counsel", "--owner", "counsel")
        run("findings", self.dir)
        md = (self.dir / "FINDINGS.md").read_text()
        for expected in ("Leave", "because", "l.pdf", "Defaults taken", "Still unknown",
                         "Confirmation plan", "call counsel", "counsel"):
            self.assertIn(expected, md)

    def test_findings_marks_an_unsourced_answer_as_a_hypothesis(self):
        run("answer", self.dir, "q1", "--verdict", "Leave", "--answer", "because")
        run("findings", self.dir)
        self.assertIn("hypothesis", (self.dir / "FINDINGS.md").read_text())

    def test_findings_escapes_a_pipe_so_the_table_survives(self):
        run("add", self.dir, "market", "unknown", "--question", "a|b", "--why", "matters")
        run("findings", self.dir)
        self.assertIn("a\\|b", (self.dir / "FINDINGS.md").read_text())


class TestServer(Base):
    def setUp(self):
        super().setUp()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), sprint.make_handler(self.run_obj))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.shutdown)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as r:
            return r.status, r.read().decode()

    def post(self, path, payload, raw=None):
        data = raw if raw is not None else json.dumps(payload).encode()
        req = urllib.request.Request(self.base + path, data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()

    def test_root_serves_the_live_page(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("const LIVE = true", body)

    def test_state_json_carries_regions_hashes_and_counts(self):
        _, body = self.get("/state.json")
        payload = json.loads(body)
        self.assertEqual(set(payload), {"regions", "hashes", "counts"})
        self.assertEqual(payload["counts"]["agents"], 2)

    def test_answer_round_trip_reaches_the_replies_file(self):
        run("add", self.dir, "market", "ask", "--question", "Gap ok?", "--option", "Yes")
        i = qid("market", "Gap ok?")
        status, body = self.post("/answer", {"id": i, "agent": "market",
                                             "question": "Gap ok?", "answer": "Up to 3 months"})
        self.assertEqual((status, json.loads(body)["ok"]), (200, True))
        self.assertEqual(self.run_obj.feedback()[i]["answer"], "Up to 3 months")

    def test_a_second_answer_replaces_the_first(self):
        i = "abc1234567"
        self.post("/answer", {"id": i, "answer": "first"})
        self.post("/answer", {"id": i, "answer": "second"})
        self.assertEqual(self.run_obj.feedback()[i]["answer"], "second")
        self.assertEqual(len((self.dir / "state/_feedback.jsonl").read_text().strip().splitlines()), 2)

    def test_answered_question_leaves_the_waiting_count(self):
        run("add", self.dir, "market", "ask", "--question", "Gap ok?", "--option", "Yes")
        self.assertEqual(json.loads(self.get("/state.json")[1])["counts"]["waiting"], 1)
        self.post("/answer", {"id": qid("market", "Gap ok?"), "answer": "Yes"})
        self.assertEqual(json.loads(self.get("/state.json")[1])["counts"]["waiting"], 0)

    def test_an_empty_answer_is_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post("/answer", {"id": "x", "answer": ""})
        self.assertEqual(cm.exception.code, 400)

    def test_malformed_json_is_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post("/answer", None, raw=b"{not json")
        self.assertEqual(cm.exception.code, 400)

    def test_an_oversized_body_is_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post("/answer", {"id": "x", "answer": "y" * 70000})
        self.assertEqual(cm.exception.code, 413)

    def test_nothing_else_is_served(self):
        for path in ("/manifest.json", "/state/market.json", "/../manifest.json", "/index.html"):
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as cm:
                self.get(path)
            self.assertEqual(cm.exception.code, 404)

    def test_posting_anywhere_else_is_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post("/state.json", {"answer": "x"})
        self.assertEqual(cm.exception.code, 404)

    def test_a_broken_state_file_keeps_the_server_up(self):
        (self.dir / "state/market.json").write_text("{oops")
        self.assertEqual(self.get("/")[0], 200)
        self.assertEqual(self.get("/state.json")[0], 200)

    def test_long_answers_are_truncated_not_rejected(self):
        self.post("/answer", {"id": "trunc", "answer": "z" * 4100})
        self.assertEqual(len(self.run_obj.feedback()["trunc"]["answer"]), 4000)


class TestPortInUse(Base):
    def test_a_busy_port_gets_a_sentence_not_a_traceback(self):
        import socket
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        self.addCleanup(sock.close)
        port = sock.getsockname()[1]
        with self.assertRaises(SystemExit) as cm:
            run("serve", self.dir, "--port", port)
        self.assertIn("--port", str(cm.exception))


class TestDemo(unittest.TestCase):
    def test_demo_run_passes_its_own_check(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "demo"
            run("demo", root)
            self.assertEqual(run("check", root), 0)
            run("build", root)
            html = (root / "dashboard.html").read_text()
            self.assertIn("Needs a human answer", html)
            self.assertIn("lease-terms", html)


if __name__ == "__main__":
    unittest.main()
