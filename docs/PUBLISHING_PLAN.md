# Publishing plan: GitHub first, then the Frappe Cloud Marketplace

Status of the code on 18 Oct 2026 (reviewed from the repository folder): **295 automated tests pass**.
Inventory monitor, vendor selector (procurement), landed-cost comparison, demand calendar, approval
channels (console, inbox, Telegram, WhatsApp), the "Ask why" assistant and the web console exist.
Everything that talks to a live system (ERPNext, WhatsApp, Telegram) has only been tested against fakes.

---

## 1. What must be finished before anything is published

### Blockers (do these first)

| # | Item | Why it blocks | How to close it |
|---|---|---|---|
| B1 | **Vendor prices are compared in each vendor's own currency, with no conversion.** `erp/erpnext.py` reads the Purchase Order Item `rate` (document currency) and the scoring never looks at currency. | Your test catalogue has Indian vendors at Rs 10.10 and Vietnamese/Chinese vendors at 0.096 / 1.021 (their own currency). Unconverted, the import looks about 100x cheaper and **wins the comparison with a wrong "why this vendor" message**. This is the worst kind of bug for this product. | Read `base_rate` (company currency) and the PO `conversion_rate`; compare and display in company currency; show the original price as a note ("USD 0.096 at 83.2"). Add tests with a USD vendor vs an INR vendor. Test case D3 in the test plan proves the fix. |
| B2 | **Nothing has run against live WhatsApp.** Payload shapes follow Meta's docs but are unverified. | You cannot publish a "WhatsApp approvals" feature that has never delivered a message. | Run test plan section C with a real Meta sandbox number. Fix whatever Meta rejects. |
| B3 | **Draft Purchase Order creation has never run against a live ERPNext** (README: "Not tested against a live ERPNext order"). | It is the one write the agent makes. | Run test cases D5/D6. Check supplier, qty, rate, required-by date, tax template, and that the PO is Draft. |
| B4 | **Secrets and local files must not reach git.** `.env` exists in the folder (ignored by `.gitignore`, good). `tools/testdata/manifest.jsonl` (about 660 KB) and `tools/testdata/*.log` are not ignored. | A leaked WhatsApp token or ERPNext key is public the moment you push. | Add to `.gitignore` (section 2 below); run `git status` before the first commit; never `git add .` blindly. If a secret was ever committed, rotate it, do not just delete the file. |
| B5 | **Shipped default admin token.** `config/settings.erpnext.yaml` uses `${ERP_AGENTS_OWNER_TOKEN:-demo-owner-token}`. | Anyone who knows the default (it will be public) can change thresholds and answer approvals if the server is ever bound to a non-local address. | Remove the default, or make `serve` refuse to start with the default token unless `host` is `127.0.0.1`. |
| B6 | **No LICENSE file** (pyproject says GPL-3.0-or-later). | Without the file, nobody can legally use or contribute to the repo, and the Marketplace reviewers will ask. | `Invoke-WebRequest https://www.gnu.org/licenses/gpl-3.0.txt -OutFile LICENSE` |

### Should finish (before the Marketplace, fine for a first GitHub release if stated in the README)

| # | Item | Note |
|---|---|---|
| S1 | README is stale ("103 tests", mentions Telegram first, no ERPNext user-permission list in one place). | Rewrite as: what it is, 5-minute demo, connect ERPNext, channels, limits. Keep the long reference in `docs/`. |
| S2 | CI. | Use `.github/workflows/ci.yml` (included): pytest on Linux and Windows, Python 3.10-3.13. |
| S3 | `pyproject.toml` metadata: authors, URLs, classifiers, `license` as an SPDX string, `tools/` kept out of the wheel. | Needed for a clean `pip install` and for the Marketplace listing. |
| S4 | WhatsApp templates: document the exact template text and buttons (README has it) and add `docs/whatsapp-setup.md` with screenshots from your real setup. | Most support questions will be here. |
| S5 | Multi-warehouse reorder levels, sales and finance agents. | Not needed for v0.1. List them under "Roadmap". |
| S6 | The test-data generator (`tools/testdata`). | Valuable, but keep it in the repo only as a documented dev tool. Do not ship the generated manifest. Note its fictional rates are test values, not tariff advice (already in the file header). |

---

## 2. Phase 1: GitHub (about 1 day after the blockers)

1. **Clean the folder** (PowerShell, in the repository folder):
   ```powershell
   Add-Content .gitignore "`ntools/testdata/manifest.jsonl`ntools/testdata/*.log`n*.db`n.vscode/`n.env.*`n!.env.example"
   git init -b main
   git status          # read this list. No .env, no data/, no .venv, no manifest.jsonl
   ```
2. **Add** `LICENSE`, `.github/workflows/ci.yml`, `SECURITY.md` (how to report a problem privately), `CONTRIBUTING.md` (run tests, code style), and an issue template.
3. **First commit and push** (create an empty repo on github.com first, no README, no licence):
   ```powershell
   git add -A
   git commit -m "Initial release: inventory monitor, vendor selector, approvals"
   git remote add origin https://github.com/<you>/erp-agents.git
   git push -u origin main
   ```
4. **Protect `main`**: Settings > Branches > require the CI check before merging.
5. **Tag a pre-release**: `git tag v0.1.0-beta.1 && git push --tags`, then create a GitHub Release. Write "tested against fakes, live-tested on ERPNext v16 and WhatsApp sandbox: yes/no" honestly in the notes.
6. **Do not publish to PyPI yet.** Wait until the WhatsApp and ERPNext live tests (section C and D of the test plan) pass.

---

## 3. Phase 2: from a Python service to a Frappe app

The Marketplace lists **Frappe apps** (a bench app that installs into a site). Frappe Cloud cannot run
your separate `erp-agents serve` process, SQLite file, Telegram poller or local Ollama. So the
Marketplace version is a **port**, not an upload. The good news: your design (an `ERPClient` interface,
channel-neutral approvals, plain-maths analysis) makes the port mostly a swap of the edges.

| Today | In the Frappe app |
|---|---|
| `erp/erpnext.py` (REST + API key) | `FrappeERPClient` using `frappe.get_all` / `frappe.get_doc` in-process. No API key, faster, permission-checked. |
| SQLite (`storage.py`) | DocTypes: Agent Settings (Single), Inventory Alert, Approval Request, Purchase Proposal, Work Order, Settings Audit. |
| APScheduler | `hooks.py` `scheduler_events` (cron) and `frappe.enqueue` for the 3-minute procurement pass. |
| `/webhooks/whatsapp` (FastAPI) | A whitelisted guest method, `/api/method/erp_agents.webhooks.whatsapp`, with the same signature check. Frappe Cloud already gives you public HTTPS, which removes the tunnel step. |
| Tokens in `.env` | `Password` fields in Agent Settings. |
| `web/index.html` console | Desk workspace plus list/form views; the approval inbox as a Desk page. |
| Telegram polling thread | Webhook mode only. |
| Ollama "Ask why" | Optional, off by default; local Ollama is not available on Frappe Cloud. |

Keep the core (`analysis.py`, `scoring.py`, `taxes.py`, `messages.py`, approvals, channels) as the
shared library. Two repos or one monorepo with `erp_agents_core/` and `erp_agents_frappe/`:

1. **Spike (about 1 week)**: `bench new-app erp_agents`, one DocType, one scheduled job that runs the inventory analysis through `FrappeERPClient` and writes Inventory Alerts. Prove it on a local bench (frappe_docker devcontainer).
2. **Approvals and WhatsApp** in the app; run the whole test plan again on the app (sections B to E).
3. **Procurement** and draft PO creation inside the app.
4. **Hardening**: Frappe roles (Agent Admin, Agent Approver), translations hooks, install/uninstall cleanly, patches for upgrades, Frappe v15 and v16 support.
5. **Frappe Cloud private-bench trial**: install from your GitHub repo on a trial site and re-run the end-to-end test.

> Verified again on 21 Oct 2026 against docs.frappe.io: a **passing CI is mandatory**, the app must not override base Frappe functionality, Semgrep with the Frappe rules is recommended, and custom apps declare the Frappe versions they support in `pyproject.toml`. The rules for new code that follow from this are in [DEVELOPING.md](DEVELOPING.md).

### Marketplace submission (from Frappe's guidelines)

- Frappe Cloud account, then Settings > Profile > **Become a Publisher**.
- Marketplace tab > **+ Add App** > **Add from GitHub** > pick the Frappe version > **Add to Marketplace** (starts as Draft).
- Listing: unique app name; title; one-sentence short description (40-80 characters); long description without installation steps; **square logo at least 200x200 px, no text**; one category; **support URL**; **privacy policy URL**; a **short demo video**.
- Must support the current stable Frappe/ERPNext version and must not steer users away from Frappe Cloud.
- Publish a release; the Frappe team reviews the app and each release (stated review time: 10 days).

Privacy policy matters here: the app sends stock and supplier summaries to WhatsApp/Telegram (and to a
language model if the assistant is on). Say exactly what leaves the site.

---

## 4. Suggested order and rough effort

| Step | Effort | Done when |
|---|---|---|
| 1. Fix B1 (currency) with tests | 0.5 day | Test D3 passes |
| 2. Live WhatsApp + ERPNext runs (test plan C, D, E) | 2-3 days | All C/D/E cases pass or have a filed issue |
| 3. B4-B6, S1-S3, push to GitHub, beta release | 1 day | CI green on `main` |
| 4. Collect feedback for 1-2 weeks | - | - |
| 5. Frappe app spike and port | 3-5 weeks | Test plan passes on a Frappe Cloud trial site |
| 6. Marketplace listing and review | 1-2 weeks | Listing approved |

Publish on GitHub early; submit to the Marketplace only after the port passes the same end-to-end test.
