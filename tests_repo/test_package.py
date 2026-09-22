"""Tests for the package itself, not for the shipped skill.

These run in this repository only: they check that the three host variants stay
in sync, that each SKILL.md is mechanically valid, and that the built package is
installable. The shipped skill's own tests live in src/tests and go out with it.

    python3 -m unittest discover -s tests_repo -t .
"""
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
HOSTS = ("claude", "codex", "grok")
SKILL_NAME = "streaming-sprint-dashboard"
MAX_DESCRIPTION = 1024   # Anthropic's stated ceiling
MAX_BODY_LINES = 500     # Anthropic's stated ceiling for a SKILL.md body

LAUNCHER = {
    "claude": "`Agent` call per roster entry",
    "codex": "codex exec",
    "grok": "grok --prompt-file",
}


def skill_text(host: str) -> str:
    return (SRC / "hosts" / f"SKILL.{host}.md").read_text()


def split_skill(text: str) -> tuple[str, str, str, str]:
    """frontmatter, body before launch, the launch section, body after it."""
    assert text.startswith("---\n")
    end = text.index("\n---", 4)
    front, body = text[4:end], text[end + 4:]
    before, rest = body.split("## 4. Launch", 1)
    launch, after = rest.split("## 5. While they run, verify", 1)
    return front, before, launch, after


class TestHostsStayInSync(unittest.TestCase):
    def test_everything_but_the_launch_section_is_identical(self):
        parts = {h: split_skill(skill_text(h)) for h in HOSTS}
        for host in HOSTS[1:]:
            self.assertEqual(parts["claude"][1], parts[host][1],
                             f"the body before the launch section drifted in {host}")
            self.assertEqual(parts["claude"][3], parts[host][3],
                             f"the body after the launch section drifted in {host}")

    def test_each_launch_section_names_its_own_launcher(self):
        for host in HOSTS:
            launch = split_skill(skill_text(host))[2]
            with self.subTest(host=host):
                self.assertIn(LAUNCHER[host], launch)
                for other, needle in LAUNCHER.items():
                    if other != host:
                        self.assertNotIn(needle, launch)

    def test_only_grok_declares_user_invocable(self):
        self.assertIn("user-invocable: true", skill_text("grok"))
        self.assertNotIn("user-invocable", skill_text("claude"))
        self.assertNotIn("user-invocable", skill_text("codex"))


class TestSkillIsMechanicallyValid(unittest.TestCase):
    """The rules a skill host enforces, checked here so a bad one never ships."""

    def test_name_matches_the_installed_directory(self):
        for host in HOSTS:
            front = split_skill(skill_text(host))[0]
            self.assertIn(f"name: {SKILL_NAME}", front)

    def test_description_is_third_person_and_within_the_limit(self):
        for host in HOSTS:
            front = split_skill(skill_text(host))[0]
            desc = front.split("description:", 1)[1].split("\n---")[0]
            desc = re.sub(r"\s+", " ", desc.replace(">-", "")).strip()
            with self.subTest(host=host):
                self.assertLessEqual(len(desc), MAX_DESCRIPTION)
                self.assertTrue(re.search(r"\bUse when\b", desc), "no 'Use when' clause")
                unquoted = re.sub(r'"[^"]*"', "", desc)
                banned = re.search(r"\b(I|we|you|your)\b", unquoted)
                self.assertIsNone(banned, f"first or second person: {banned}")

    def test_body_is_under_the_line_ceiling(self):
        for host in HOSTS:
            body = skill_text(host).split("\n---", 1)[1]
            self.assertLess(body.count("\n"), MAX_BODY_LINES, host)

    def test_no_windows_paths_and_no_raw_mcp_ids(self):
        for host in HOSTS:
            text = skill_text(host)
            self.assertIsNone(re.search(r"mcp__[a-zA-Z0-9_]+", text), host)
            self.assertIsNone(re.search(r"[A-Za-z0-9]\\[A-Za-z0-9]", text), host)

    def test_every_command_in_the_skill_exists_in_the_cli(self):
        help_text = subprocess.run([sys.executable, str(SRC / "scripts/sprint.py"), "--help"],
                                   capture_output=True, text=True).stdout
        available = set(re.findall(r"^\s{4}(\w+)\s", help_text, re.M))
        self.assertTrue(available, "could not read the subcommand list from --help")
        for host in HOSTS:
            used = set(re.findall(r'sprint\.py" (\w+)|sprint\.py (\w+)', skill_text(host)))
            for a, b in used:
                cmd = a or b
                if cmd in ("check", "init", "serve", "log", "set", "add", "answer",
                           "replies", "findings", "build", "demo"):
                    self.assertIn(cmd, available, f"{host} uses sprint.py {cmd}")


class TestEvals(unittest.TestCase):
    def test_at_least_three_scenarios_with_the_required_keys(self):
        files = sorted((SRC / "evals").glob("*.json"))
        self.assertGreaterEqual(len(files), 3)
        for f in files:
            data = json.loads(f.read_text())
            with self.subTest(eval=f.name):
                for key in ("skills", "query", "expected_behavior"):
                    self.assertIn(key, data)
                self.assertIn(SKILL_NAME, data["skills"])
                self.assertTrue(data["expected_behavior"])

    def test_one_scenario_covers_declining_the_dashboard(self):
        texts = [f.read_text() for f in (SRC / "evals").glob("*.json")]
        self.assertTrue(any("not worth it" in t or "without scaffolding" in t for t in texts),
                        "no scenario checks that the skill declines a short fan-out")


class TestPackaging(unittest.TestCase):
    def test_installer_covers_every_host(self):
        script = (SRC / "install.sh").read_text()
        for host in HOSTS:
            self.assertIn(f"{host})", script)

    def test_installer_never_deletes(self):
        # WHY: an installer that removes a directory it did not create is how
        # someone loses a customised skill.
        self.assertNotIn("rm -", (SRC / "install.sh").read_text())

    def test_readme_test_command_is_the_one_that_works(self):
        readme = (SRC / "README.md").read_text()
        self.assertIn("python3 -m unittest discover -s tests", readme)
        self.assertTrue((SRC / "tests/__init__.py").exists(),
                        "discover -s tests needs tests to be a package")

    def test_readme_states_when_not_to_use_it(self):
        self.assertIn("When not to use it", (SRC / "README.md").read_text())


if __name__ == "__main__":
    unittest.main()
