#!/bin/sh
# One-time repository setup with the GitHub CLI (gh auth login first). Safe to run again.
#   sh tools/github_setup.sh vivek-vpc/erpnext-ai-agents
set -eu
REPO="${1:?usage: sh tools/github_setup.sh OWNER/REPO}"

gh repo edit "$REPO" --enable-discussions --enable-issues \
  --description "Agents prepare, a person approves: stock monitoring and purchase drafts for ERPNext, with an MCP server" \
  --add-topic erpnext --add-topic frappe --add-topic mcp --add-topic model-context-protocol \
  --add-topic ai-agents --add-topic agentic-ai --add-topic ollama --add-topic inventory \
  --add-topic procurement --add-topic fastapi --add-topic human-in-the-loop

python3 - "$REPO" <<'PY'
import json, subprocess, sys
repo = sys.argv[1]
for d in json.load(open(".github/labels.json")):
    subprocess.run(["gh", "label", "create", d["name"], "--repo", repo, "--color", d["color"],
                    "--description", d["description"], "--force"], check=True)
PY
echo "Done. Next: create 3 to 5 issues labelled 'good first issue', and pin one Discussion asking what an agent should always ask before acting."
