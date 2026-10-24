# GitHub open-source readiness review (21 Oct 2026)

Reviewed: the `erp-agents` project folder (about 9,300 lines of source, 6,750 lines of tests). Companion to `PUBLISHING_PLAN.md`.

## Verdict
- **Code: ready for a public GitHub beta** once the blockers below are closed (about half a day).
- **Frappe Marketplace: not ready.** It is a standalone Python service, not a Frappe app (see "Marketplace gap"). That is a port, as the publishing plan says.

## Closed since the publishing plan (18 Oct)
- B1 currency conversion: fixed, with tests.
- B3 draft PO against live ERPNext: passed live (PO-05, TRACE-01: draft PO and Material Request, approver recorded).
- B5 default admin token: the demo key is only accepted from the local machine; start-up warns; live config has no fallback.
- Penetration test (19 Oct): all 29 routes refuse strangers, no injection found, webhooks signature-checked, 44 hardening tests.
- Live end-to-end run (20 Oct): 80 pass, 1 fail, 8 blocked, 7 not run.

## Blockers before the first public push
1. **No `LICENSE` file** (pyproject says GPL-3.0-or-later). Add the GPL-3.0 text. Also change `license = { text = ... }` to an SPDX string.
2. **No git repo yet; `.gitignore` is incomplete.** Not ignored: `tools/testdata/manifest.jsonl` (700 KB), `tools/testdata/*.log`, `tools/testdata/days_state.json` (real document names), `*.db`.
3. **Internal docs contain personal or machine details.** A real mailbox address used as the service account appears in `E2E_FINDINGS.md`, `E2E_TEST_RESULTS.md` and `PENTEST_REPORT.md`; `PENTEST_REPORT.md` also has a LAN address, local paths and your open security findings. Do not publish these as they are. Move them out of the repo, or publish redacted copies (use `agent@yourco.com`).
4. **README is stale and contradicts the test results.** It says 103 tests (there are 586), says adapters are "not tested against live accounts" and "Not tested against a live ERPNext order" although live runs now pass. It must also say plainly that WhatsApp has only been tested against a fake transport, not Meta's sandbox.
5. **Real bug on Python 3.10 and 3.11:** `erp-agents policy set --scope item --key X min_qty=5` fails ("unrecognized arguments"). 2 tests fail on Python 3.10 for this reason. pyproject claims `>=3.10`. Fix with `parse_intermixed_args`, or set `requires-python >= 3.12`.
6. **Default `WHATSAPP_VERIFY_TOKEN=stockbot-verify-2026` in `.env.example`.** Leave it blank and tell users to generate one.

## Should do (fine for a beta if stated in the README)
- Add `SECURITY.md`, `CONTRIBUTING.md`, CI workflow (Linux and Windows, Python 3.10 to 3.13), issue templates, authors and URLs in `pyproject.toml`.
- Decide about `ui-design/` (design mock-ups): keep out of the repo or state their origin and licence.
- Document in the README that multi-replica deployment is unsupported (SQLite plus in-process scheduler) and that no schema versioning exists yet (OPS-06).
- Service account: finish AUTH-02 (give it only Purchase User, Stock User, Accounts User, Accounts Manager) so the live test goes green and the README can say so.
- Bind the demo ERPNext to 127.0.0.1 if the machine stays on a shared network.

## Marketplace gap (what a Frappe app needs that this repo lacks)
No `hooks.py`, no modules, no DocTypes, no patches, no roles, no `scheduler_events`. It uses SQLite, APScheduler, FastAPI, a REST client with API keys and a Telegram poller, none of which run on Frappe Cloud. Plan: keep the analysis, scoring, taxes, messages and approval logic as a shared core; replace the edges (ERP client, storage, scheduler, webhooks, settings, console) as in section 3 of `PUBLISHING_PLAN.md`. A privacy policy URL, support URL, square logo and a demo video are also required.

## Strengths worth saying in the README
Explanations are filled from stored numbers, not an LLM; nothing is auto-approved; documents are created as Draft with the approver recorded; permissions follow ERPNext's own table; dependencies are all GPL-compatible (MIT, BSD, Apache-2.0) and a dependency audit found no known vulnerabilities in them.

## Not verified in this review
Real WhatsApp (Meta sandbox) delivery and button presses; backup and restore; network interruption; the local language model against hostile prompts.

## Status after follow-up work (21 Oct 2026, later the same day)
Checked against the repository, then fixed where it needed no decision from the owner.

| Item | Status |
|---|---|
| Blocker 1: `LICENSE` | Done: GPL-3.0 text added; `pyproject.toml` uses the SPDX string `GPL-3.0-or-later` and `license-files` (build backend `setuptools>=77`). |
| Blocker 2: git repo, `.gitignore` | `.gitignore` completed (databases, logs, `manifest.jsonl`, `days_state.json`, the last live-run log, caches, editor folders, `ui-design/`). **`git init` and the first commit are left to the owner**: read `git status` before the first `git add`. |
| Blocker 3: personal data in docs | Done: the service-account mailbox is replaced by `agent@example.com` in the E2E docs, the live harness now redacts any e-mail address from the results it writes, `PENTEST_REPORT.md` is rewritten without the LAN address, local paths and account name (its open findings became a generic operator checklist), and test fixtures no longer carry a real name. The original machine-specific report is kept **outside** the repository. A CI job scans for tokens, mailboxes and private addresses. |
| Blocker 4: stale README | Done: status block rewritten (nearly 700 tests, what was live-tested, what was not, single instance, no migrations), stale "not tested against a live ERPNext order" lines corrected, limitations brought up to date. One correction to this review: the live database shows WhatsApp messages delivered through Meta's Cloud API on the author's own account, so the README says "delivered live on the author's own accounts" and that WhatsApp has not been tried against Meta's sandbox by anyone else; button replies are covered by fake-transport tests only. |
| Blocker 5: `policy set` on Python 3.10/3.11 | Fixed in code (`parse_cli`: what argparse leaves over is the field list), with tests. **Not run on 3.10 or 3.11 here** (only Python 3.14 is installed on this machine): the CI matrix is the check. |
| Blocker 6: default WhatsApp verify token | Already blank in `.env.example`. |
| `SECURITY.md`, `CONTRIBUTING.md`, CI, issue and pull-request templates | Done. Replace `REPLACE-WITH-OWNER` in `.github/ISSUE_TEMPLATE/config.yml` once the repository exists. |
| Authors and URLs in `pyproject.toml` | Left out on purpose: needs the owner's name, contact and repository URL. |
| `ui-design/` | Ignored for now (origin and licence of the design exports are not recorded). Remove the line from `.gitignore` to publish it. |
| Multi-replica and schema versioning | Stated in the README status block and limitations. |
| AUTH-02 service-account roles, ERPNext bound to localhost | Still the owner's decision (credential rotation and Docker settings). |

New with this follow-up: [DEVELOPING.md](DEVELOPING.md), the rules and audit for keeping the Frappe Marketplace port a swap of the edges.
