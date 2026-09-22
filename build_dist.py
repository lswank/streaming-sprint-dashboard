#!/usr/bin/env python3
"""Assemble the distributable package from src/ and zip it.

    python3 build_dist.py [--out DIR]

src/ is the single source of truth: one copy of the scripts, one copy of the
tests, one set of evals, and one SKILL.md per host. The package ships three
self-contained skill directories so each one can be dropped into its host with
no shared parent, which is why the scripts are copied rather than linked.

Exits non-zero if the tests fail or if a personal path leaked into the package.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent
SRC = REPO / "src"
SKILL = "streaming-sprint-dashboard"
HOSTS = ("claude", "codex", "grok")

# WHY: the package is for other people's machines. A path out of this one, or the
# author's username, is a bug in the artifact.
LEAKS = re.compile(r"/Users/[a-z]|/home/[a-z]|lswank|lorenzo", re.I)
# The launch expectation in the shared eval differs per host.
LAUNCH = {
    "claude": "Launches one subagent per roster entry in a single message rather than one at a time",
    "codex": "Writes one prompt file per agent and launches each as a background codex exec process",
    "grok": "Writes one prompt file per agent and launches each as a background grok process with --always-approve",
}


def run_tests() -> dict[str, int]:
    """Both suites: the shipped skill's own tests, and this repository's."""
    counts: dict[str, int] = {}
    for label, cwd, start in (("skill", SRC, "tests"), ("package", REPO, "tests_repo")):
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", start, "-t", ".", "-q"],
            cwd=cwd, capture_output=True, text=True)
        out = (proc.stderr or proc.stdout).strip()
        if proc.returncode != 0:
            print(out)
            raise SystemExit(f"{label} tests failed; the package was not built")
        found = re.search(r"Ran (\d+) test", out)
        counts[label] = int(found.group(1)) if found else 0
        print(f"{label} tests: {counts[label]} passed")
    return counts


def build_host(host: str, dest: Path) -> None:
    root = dest / host / SKILL
    (root / "scripts").mkdir(parents=True)
    shutil.copytree(SRC / "scripts", root / "scripts", dirs_exist_ok=True)
    shutil.copytree(SRC / "tests", root / "tests")
    (root / "evals").mkdir()
    for eval_file in sorted((SRC / "evals").glob("*.json")):
        data = json.loads(eval_file.read_text())
        data["expected_behavior"] = [
            LAUNCH[host] if step == "__LAUNCH__" else step
            for step in data["expected_behavior"]
        ]
        (root / "evals" / eval_file.name).write_text(json.dumps(data, indent=2) + "\n")
    shutil.copy(SRC / "hosts" / f"SKILL.{host}.md", root / "SKILL.md")
    for junk in list(root.rglob("__pycache__")) + list(root.rglob(".DS_Store")):
        shutil.rmtree(junk) if junk.is_dir() else junk.unlink()


def check_leaks(dest: Path) -> None:
    hits = []
    for path in sorted(dest.rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text(errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            if LEAKS.search(line) and "Lorenzo Swank" not in line:
                hits.append(f"{path.relative_to(dest)}:{n}: {line.strip()[:90]}")
    if hits:
        print("\n".join(hits))
        raise SystemExit("a personal path leaked into the package")
    print("no personal paths in the package")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path.home() / "Desktop"),
                    help="where the zip lands (default: the Desktop)")
    args = ap.parse_args()

    counts = run_tests()
    staging = REPO / "dist"
    if staging.exists():
        shutil.rmtree(staging)
    dest = staging / SKILL
    dest.mkdir(parents=True)

    for host in HOSTS:
        build_host(host, dest)
    readme = (SRC / "README.md").read_text().replace(
        "{{SKILL_TEST_COUNT}}", str(counts["skill"]))
    assert "{{" not in readme, "an unresolved placeholder is left in the README"
    (dest / "README.md").write_text(readme)
    shutil.copy(REPO / "LICENSE", dest / "LICENSE")
    shutil.copy(SRC / "install.sh", dest / "install.sh")
    (dest / "install.sh").chmod(0o755)
    check_leaks(dest)

    out = Path(args.out).expanduser() / f"{SKILL}-{date.today().isoformat()}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(dest.rglob("*")):
            arc = path.relative_to(staging)
            if path.is_file():
                info = zipfile.ZipInfo.from_file(path, arc)
                # WHY the mode is set explicitly: zipfile drops the execute bit on
                # extraction otherwise, and install.sh has to stay runnable.
                info.external_attr = (0o755 if path.suffix == ".sh" else 0o644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(info, path.read_bytes())
    size_kb = out.stat().st_size / 1024
    print(f"{out} ({size_kb:.0f} KB, {len(zipfile.ZipFile(out).namelist())} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
