"""The CI's secret and personal-data scan is only worth something if it can match and if what it excludes still exists.

Two things went wrong with it without anyone noticing: its personal-data pattern reached `git grep` with the wrong backslashes (so `git grep` failed and the step read the failure as "nothing found"), and its
exclusions named test files that had moved. These tests read `.github/workflows/ci.yml` itself.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CI = ROOT / ".github" / "workflows" / "ci.yml"


def scan_step() -> str:
    lines = CI.read_text(encoding="utf-8").splitlines()
    start = next(n for n, line in enumerate(lines) if line.strip() == "- name: Scan")
    body = []
    for line in lines[start + 2:]:
        if line.strip() and not line.startswith("          "):
            break
        body.append(line.strip())
    return "\n".join(body)


def patterns() -> list[str]:
    return re.findall(r"\bscan '([^']+)'", scan_step())


def test_the_scan_step_has_both_patterns_in_single_quotes():
    found = patterns()
    assert len(found) == 2, found                                   # a pattern in double quotes loses its backslashes in bash and git grep fails


def test_the_patterns_compile_and_catch_what_they_are_for():
    secret, personal = patterns()
    pem_header = "-----" + "BEGIN RSA PRIV" + "ATE KEY" + "-----"                  # built here so this file does not trip the very scan it tests
    for sample in ("AKIA" + "A" * 16, pem_header, "123456789:" + "a" * 35, "EAA" + "b" * 41):
        assert re.search(secret, sample), sample
    for sample in ("someone@" + "gmail" + ".com", "host " + "192.168" + ".1.20", "C:" + "\\Users\\" + "someone"):          # split so this file is not itself a hit
        assert re.search(personal, sample), sample
    for sample in ("172.16.0.5", "plain text", "gmailXcom", "C:/Users/x"):
        assert not re.search(personal, sample), sample


def test_a_scan_that_fails_to_run_fails_the_build():
    step = scan_step()
    assert "-gt 1" in step and "exit 2" in step                     # git grep exits 1 for "nothing found" and 2 or more for an error: the second must never read as clean


def test_every_excluded_file_still_exists():
    excluded = re.findall(r"':!([^']+)'", scan_step())
    assert excluded
    missing = [path for path in excluded if "*" not in path and not (ROOT / path).exists()]
    assert not missing, f"the CI scan excludes files that moved or were removed: {missing}"
