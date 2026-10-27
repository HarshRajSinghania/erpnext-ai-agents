#!/bin/sh
# Create the starter issues in .github/good_first_issues.json (gh auth login first, and run tools/github_setup.sh before this so the labels exist).
# Safe to run again: an issue whose title already exists is skipped.
#   sh tools/github_issues.sh vivek-vpc/erpnext-ai-agents
set -eu
REPO="${1:?usage: sh tools/github_issues.sh OWNER/REPO}"

python3 - "$REPO" <<'PY'
import json, subprocess, sys
repo = sys.argv[1]
listed = subprocess.run(["gh", "issue", "list", "--repo", repo, "--state", "all", "--limit", "200", "--json", "title"],
                        capture_output=True, text=True, check=True).stdout
existing = {i["title"] for i in json.loads(listed)}
with open(".github/good_first_issues.json", encoding="utf-8") as f:
    wanted = json.load(f)
for d in wanted:
    if d["title"] in existing:
        print("exists:  ", d["title"])
        continue
    cmd = ["gh", "issue", "create", "--repo", repo, "--title", d["title"], "--body", d["body"]]
    for label in d["labels"]:
        cmd += ["--label", label]
    print("created: ", subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip())
PY
