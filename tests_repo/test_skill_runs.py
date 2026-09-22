"""Runs every shell block in each SKILL.md, verbatim, and checks what it produced.

WHY this exists: a blind reviewer killed an earlier build because the launch
block did not run as written. Placeholders like `MODEL=<a cheaper model>` and
`RUNDIR` read fine and are not shell. The only way to keep that from coming back
is to execute the documentation.

The launcher (`grok`, `codex`) is stubbed, so no agent is spawned and nothing is
billed; what is checked is the command line the skill produces.

    python3 -m unittest discover -s tests_repo -t .
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SPRINT = REPO / "src/scripts/sprint.py"
SKILLS = REPO / "src/hosts"

# The host whose launcher each shell-driven variant calls. The Claude variant
# launches subagents through its own tool, so it has no shell block to run.
SHELL_HOSTS = {"grok": "grok", "codex": "codex"}
EXPECTED_AGENTS = {"lease-terms", "exit-cost", "signoff"}


def bash_blocks(host: str) -> list[str]:
    text = (SKILLS / f"SKILL.{host}.md").read_text()
    return re.findall(r"```bash\n(.*?)```", text, re.S)


class TestSkillBlocksRun(unittest.TestCase):
    def run_skill(self, host: str, launcher: str):
        """Execute every block from that skill in a sandbox and return what happened."""
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        work = Path(tmp)
        bindir = work / "bin"
        bindir.mkdir()
        stub = bindir / launcher
        (work / "calls").mkdir()
        stub.write_text('#!/bin/sh\n'
                        'f=$(mktemp "$LAUNCH_DIR/call.XXXXXX")\n'
                        'printf "%s" "$*" > "$f"\n')
        stub.chmod(0o755)

        script = [f'export PATH={bindir}:$PATH', f'export LAUNCH_DIR={work}/calls']
        for block in bash_blocks(host):
            # Three substitutions, and only these three: the run directory (so the
            # test writes in its sandbox), the tool path (so it tests this working
            # tree rather than an installed copy), and the port (so a developer's
            # own sprint on 8787 does not make this fail).
            block = block.replace('RUN="$HOME/work/lease-2026/sprint"', f'RUN={work}/run')
            block = re.sub(r"SPRINT=\$\(ls -d.*?head -1\)", f"SPRINT={SPRINT}", block, flags=re.S)
            block = block.replace('--port "$PORT"', "--port 0").replace(" --open", "")
            script.append(block)
        path = work / "as-written.sh"
        path.write_text("\n".join(script))
        proc = subprocess.run(["bash", str(path)], capture_output=True, text=True, timeout=120)
        self.addCleanup(self.stop_servers, work)
        return proc, work

    @staticmethod
    def launch_calls(work: Path) -> list[str]:
        """One file per launcher call, newlines collapsed so a call is one string."""
        return [" ".join(p.read_text().split())
                for p in sorted((work / "calls").iterdir())]

    @staticmethod
    def stop_servers(work: Path):
        subprocess.run(["pkill", "-f", f"sprint.py serve {work}/run"], capture_output=True)

    def test_every_block_runs_with_no_error(self):
        for host, launcher in SHELL_HOSTS.items():
            with self.subTest(host=host):
                proc, _ = self.run_skill(host, launcher)
                self.assertEqual(proc.returncode, 0,
                                 f"{host}: a documented command failed:\n{proc.stderr[-800:]}")
                for noise in ("command not found", "syntax error", "No such file or directory",
                              "unexpected token", "Traceback"):
                    self.assertNotIn(noise, proc.stderr, f"{host}: {noise} in stderr")

    def test_the_scaffold_prints_the_question_ids(self):
        for host, launcher in SHELL_HOSTS.items():
            with self.subTest(host=host):
                proc, _ = self.run_skill(host, launcher)
                self.assertIn("q1  Do we renew", proc.stdout)
                self.assertIn("q3  Who has to sign off", proc.stdout)

    def test_the_serve_block_reports_a_url(self):
        for host, launcher in SHELL_HOSTS.items():
            with self.subTest(host=host):
                proc, _ = self.run_skill(host, launcher)
                self.assertRegex(proc.stdout, r"http://127\.0\.0\.1:\d+/",
                                 "the URL was not printed; an empty serve.log was read")

    def test_one_prompt_file_per_agent_and_none_for_the_coordinator(self):
        for host, launcher in SHELL_HOSTS.items():
            with self.subTest(host=host):
                _, work = self.run_skill(host, launcher)
                written = {p.stem for p in (work / "run/prompts").iterdir()}
                self.assertEqual(written, EXPECTED_AGENTS)

    def test_a_written_prompt_has_no_unresolved_variable(self):
        for host, launcher in SHELL_HOSTS.items():
            with self.subTest(host=host):
                _, work = self.run_skill(host, launcher)
                text = (work / "run/prompts/exit-cost.md").read_text()
                for leftover in ("$RUN", "$SPRINT", "$name", "$question", "RUNDIR"):
                    self.assertNotIn(leftover, text, f"{host}: {leftover} reached the agent")
                self.assertIn(str(SPRINT), text, "the agent was never given the tool's path")
                self.assertIn("--agent exit-cost", text, "the agent's check was not scoped")

    def test_each_agent_is_launched_once_with_its_own_prompt(self):
        for host, launcher in SHELL_HOSTS.items():
            with self.subTest(host=host):
                _, work = self.run_skill(host, launcher)
                calls = self.launch_calls(work)
                self.assertEqual(len(calls), len(EXPECTED_AGENTS))
                for agent in EXPECTED_AGENTS:
                    self.assertTrue(any(agent in c for c in calls),
                                    f"{agent} was never launched: {calls}")
                self.assertFalse(any("coordinator" in c for c in calls),
                                 "the coordinator is this session, not a process to launch")

    def test_the_grok_launch_carries_the_flag_that_stops_it_stalling(self):
        _, work = self.run_skill("grok", "grok")
        for line in self.launch_calls(work):
            self.assertIn("--always-approve", line)
            self.assertIn("--prompt-file", line)

    def test_the_codex_launch_carries_its_sandbox_and_working_root(self):
        _, work = self.run_skill("codex", "codex")
        for line in self.launch_calls(work):
            self.assertIn("exec --skip-git-repo-check", line)
            self.assertIn("--sandbox workspace-write", line)
            self.assertIn(f"-C {work}/run", line)

    def test_check_refuses_the_run_until_the_request_is_pasted_in(self):
        """The scaffold leaves a placeholder in CONTEXT.md on purpose."""
        for host, launcher in SHELL_HOSTS.items():
            with self.subTest(host=host):
                proc, _ = self.run_skill(host, launcher)
                self.assertIn("CONTEXT.md still holds the placeholder", proc.stdout)


class TestClaudeVariantHasNoShellFanOut(unittest.TestCase):
    def test_the_claude_variant_launches_through_its_own_tool(self):
        text = (SKILLS / "SKILL.claude.md").read_text()
        launch = text.split("## 4. Launch", 1)[1].split("## 5.")[0]
        self.assertNotIn("```bash", launch, "the Claude variant should not shell out to fan out")
        self.assertIn("`Agent` call per roster entry", launch)
        self.assertIn("never spawn a subagent for it", launch)

    def test_its_prompt_template_spells_out_both_paths(self):
        """A subagent inherits no shell variables, so the template must not use them."""
        text = (SKILLS / "SKILL.claude.md").read_text()
        launch = text.split("## 4. Launch", 1)[1].split("## 5.")[0]
        template = launch.split("```")[1]
        self.assertIn("<SPRINT>", template)
        self.assertIn("<RUN>", template)
        self.assertNotIn("$SPRINT", template, "a subagent cannot expand a variable")
        self.assertNotIn("$RUN", template)
        self.assertIn("--agent exit-cost", template)
        self.assertIn("written out in full", launch)


if __name__ == "__main__":
    unittest.main()
