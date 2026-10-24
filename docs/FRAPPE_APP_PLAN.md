# Plan: a Frappe app version (Frappe Cloud Marketplace)

**Status: a plan only. Nothing of the Frappe app exists.** No `hooks.py`, no DocTypes, no Frappe package, no Frappe CI. This repository is a **standalone Python service** (FastAPI, SQLite, an in-process scheduler) that talks to ERPNext over its REST API. It is **not** a Frappe app, it has **not** been submitted to the Frappe Marketplace, and it cannot be installed from the Marketplace. Licence: GPL-3.0-or-later (which the Marketplace accepts); that is a licence statement, not a listing.

Written 25 Oct 2026. The Marketplace and Frappe version facts come from `docs.frappe.io` as read on 21 Oct 2026 (see [DEVELOPING.md](DEVELOPING.md)); they change, so re-read the official pages before work starts and again before each release.

## 1. Where the boundary is

| | Standalone beta (this repository, today) | Frappe app (future, separate project step) |
|---|---|---|
| What it is | a Python service you run beside ERPNext | a bench app installed into an ERPNext site |
| Talks to ERPNext | REST with a service account's API key | in-process (`frappe.get_all`, `frappe.get_doc`) as the signed-in user |
| Stores its data | its own SQLite file | DocTypes in the site's database |
| Runs scheduled work | APScheduler thread inside `serve` | `scheduler_events` and `frappe.enqueue` |
| Users and permissions | ERPNext sign-in over REST, roles copied into a session | Frappe's own session, roles and DocType permissions |
| Installed by | `pip install`, a config file, `.env` | `bench get-app` / `bench install-app`, or Frappe Cloud |
| Secrets | `.env` | Password fields in a Settings DocType |
| Review | none | the Frappe Marketplace team reviews the app and each release |

The standalone service stays useful on its own (it is how the live tests were done and how a business without Frappe Cloud runs it). The Frappe app would **reuse the business logic** and **replace the edges**; it would not wrap the service. Do not describe the standalone service as Marketplace-ready, installable from the Marketplace, or "a Frappe app in progress".

## 2. What a Frappe app needs (and the repository has none of it)

| Needed | Why | State today |
|---|---|---|
| A bench app package: an app folder with the Python module, `modules.txt`, `hooks.py`, `patches.txt`, a `config/` folder, a `pyproject.toml` that declares the supported Frappe and ERPNext versions (`[tool.bench.frappe-dependencies]`; check the exact form in the version you target) | `bench` cannot install anything else | none |
| **Settings DocType** (Single) for global behaviour, with Password fields for tokens and hosted-model keys | a Marketplace requirement; replaces `settings.yaml` and `.env` | the settings are validated pydantic sections (`core/policy.py`), which map one-to-one onto fields |
| **DocTypes** for the stored records: Inventory Alert, Stock Notice, Approval Request (with deliveries and decisions), Purchase Proposal, Work Order, Settings Audit, Agent Task and Tool Call, Chat Log | replaces SQLite | `core/storage.py` holds the schema; each record type has a repository class |
| **Roles and permissions**: who may look, who may answer approvals, who may change settings, mapped to ERPNext's roles (Purchase User and Manager, Stock User and Manager, System Manager) and checked with `frappe.has_permission` | Frappe-native permissions replace the copied role list | `auth/` and `core/common.Actor` carry roles; the checks are in `require()` and `Toolkit.call` |
| **Background jobs**: the stock check, the 3-minute procurement pass, approval reminders and expiry, the agents (one task at a time) | no long-lived threads on Frappe Cloud | thread-based (`scheduler.py`, `agents/service.py`, the conversation pool) |
| **Webhook endpoints** as whitelisted guest methods with the existing signature checks; **Telegram in webhook mode only** (no poller thread) | Frappe Cloud gives public HTTPS, so no tunnel; no worker may run a poll loop | FastAPI routes; the signature code is channel code and moves as it is |
| **A Frappe ERP client** implementing `ERPClient` with in-process calls | no API key, faster, permission-checked | `erp/erpnext.py` is REST; `erp/base.py` is the interface |
| **Migrations**: `patches.txt` and patch modules for every schema change; install and uninstall hooks (the custom fields `custom_agent_ref`, approver and note on Purchase Order and Material Request, as fixtures or an `after_install` patch) | an app must upgrade and uninstall cleanly | one schema version, an upgrade mechanism with no migrations yet (`core/storage.py`); `erp-agents erp-setup` creates the custom fields by REST |
| **UI** in Desk: an approvals inbox, stock monitor, vendor comparison, Ask page, settings, as workspace, pages and list/form views | the single-file web console cannot be served by a bench app | `web/index.html` (one file of about 1,150 lines, about 1,000 of them script) |
| **Translations** hooks and `_()` around every user-visible string | Frappe convention, wanted for a Marketplace app | all text is built in message modules, in English, in one place each |
| **Current stable Frappe and ERPNext** support, with version-specific code branching on `frappe.__version__` | a Marketplace requirement | tested live on ERPNext v16 only; v16's own Python requirement must be checked (community reports said Python 3.14), and this project's CI matrix is 3.10 to 3.14 |
| **App-specific CI**: a Frappe bench in GitHub Actions running the app's tests (`bench run-tests`) and Semgrep with Frappe's rules | a Marketplace requirement | CI exists for the standalone service (pytest, bandit, pip-audit, a secrets scan) and tests nothing Frappe-specific |
| **Do not override base Frappe behaviour** (login, password reset); extend through hooks | a Marketplace requirement | not applicable yet |
| **Listing assets**: unique app name, 40 to 80 character description, long description without install steps, square logo (at least 200x200, no text), one category, support URL, privacy policy URL, a short demo video | the listing | the privacy statement is generated from code and tested (`agents/privacy.py`, [PRIVACY.md](PRIVACY.md)); everything else is missing; the repository name is undecided |

## 3. What can be reused, and what has to change first

**Reusable as it is** (no web, scheduler, HTTP or SQL library in them; see the layer table in [DEVELOPING.md](DEVELOPING.md)): the stock analysis, vendor scoring, taxes and landed cost, the message builders, the approval models and service logic, the chat understanding and answer building, the agent runtime and its checks, the tools (`toolkit.py`), the language-model clients, the privacy generator, and most of the 1,100+ tests (they run against the `ERPClient` interface and a demo ERP).

**Has to change before any port**, and is worth doing in the standalone service first because each step is testable here:

1. **Put SQL behind repositories everywhere.** About a dozen calls still sit outside repository classes (the audit table in `DEVELOPING.md`); the repositories themselves are SQLite-specific. A storage interface with a SQLite implementation (today) and a DocType implementation (later) is the single largest enabler.
2. **Move in-memory state into storage or cache**: the chat throttle, the conversation's "waiting for an answer" memory, sign-in sessions, the one-at-a-time agent lock. A multi-worker site shares none of them.
3. **Turn threads into jobs**: `submit` and `on_submitted` start threads; each becomes a `frappe.enqueue` call. The task row already exists before the work starts, so polling by task id carries over.
4. **Decouple the ERP client interface from stock and procurement**, so another area can add its own methods (the interface today carries stock, usage, vendor-quote and purchase-order calls).

**Replaced**: the REST client, SQLite, the scheduler, FastAPI routes and the web console, `.env` and `settings.yaml`, the MCP endpoint (a whitelisted method behind Frappe's OAuth 2 provider: this is also what real MCP OAuth support needs, see [AGENTS.md](AGENTS.md)), the Telegram poller, and the local Ollama option (a local model is not available on Frappe Cloud; hosted providers need their key in a Password field and the owner's consent).

## 4. Work packages and effort

One experienced Frappe developer who knows this code, in working weeks. The ranges are wide on purpose: the first two packages are the least known.

| # | Work package | Effort | Done when |
|---|---|---|---|
| 0 | Decisions: app name (unique on the Marketplace), repository, publisher account, target Frappe and ERPNext versions, whether the standalone service is kept | 2-3 days | written down |
| 1 | Storage interface and repository clean-up in the standalone service; in-memory state moved out; threads behind a job interface | 2-3 weeks | the whole suite passes on an in-memory fake storage as well as SQLite |
| 2 | Frappe app skeleton: package, `hooks.py`, Settings DocType with Password fields, roles, install hooks and custom fields, a bench running in a dev container | 1 week | the app installs on a local bench and uninstalls cleanly |
| 3 | `FrappeERPClient`: stock, usage history, vendor orders and quotations, taxes, document creation as the signed-in user | 2-3 weeks | the existing ERP-client tests pass against a bench site with test data |
| 4 | DocTypes and repositories on them; `patches.txt` for upgrades | 2 weeks | alerts, approvals and proposals round-trip on a site |
| 5 | Scheduler events and `frappe.enqueue` jobs; approvals, reminders, expiry; WhatsApp and Telegram webhooks as guest methods with the signature checks | 1.5-2 weeks | an alert is raised, sent, answered and turned into a draft purchase order on a site |
| 6 | Desk UI: approvals inbox with Change quantity, Change vendor and Cancel order; stock monitor; vendor comparison; Ask; settings | 3-4 weeks | the end-to-end flows of the live test plan pass in the Desk |
| 7 | Agents and MCP: jobs for the agent tasks; the HTTP endpoint behind Frappe's OAuth provider (protected-resource metadata, token validation, audience binding); hosted-model keys and consent | 2-3 weeks | an MCP client that discovers servers by itself can connect, and the agent checks pass on a site |
| 8 | Tests on Frappe (`bench run-tests`), app CI on a bench, Semgrep with Frappe's rules, support for the current stable version | 1.5-2 weeks | CI green on a clean bench |
| 9 | Translations, privacy policy and support pages, logo, description, demo video, a private-bench trial on Frappe Cloud, the Marketplace review and its fixes (the stated review time was 10 days) | 2-3 weeks | the listing is approved |

**Total: about 18 to 26 working weeks (roughly 4 to 6 months) for one developer**, 11 to 15 of them in packages 3 to 7 that cannot start until package 1 is done. The earlier estimate in [PUBLISHING_PLAN.md](PUBLISHING_PLAN.md) (3 to 5 weeks for the spike and the port) covered a feasibility spike and a thin slice, not the whole product as it stands now (chat, agents, MCP, three channels, vendor comparison with taxes, quotations and the Ask page). A spike that proves one DocType, one scheduled job and one approval round trip on a bench is about 1 to 1.5 weeks and is the right first step to replace these estimates with real ones.

## 5. Risks and unknowns

- **Frappe's version and Python requirements move.** Check the current stable Frappe, ERPNext and Python requirements before package 2, and re-check before each release; the app must support the current stable version to be listed.
- **Frappe Cloud limits**: no long-running threads, no local models, no custom ports. Anything in the standalone service that depends on those is dropped or redone (Telegram polling, Ollama, the in-process scheduler).
- **Permissions change meaning.** Today the service account reads everything and the approver's own session is used only to create the draft. In a Frappe app every read is the signed-in user's: a person who may not read Stock Ledger Entries gets less data. That is better security and a behaviour change to test.
- **The console is a rewrite**, not a port: the largest single package, and the one most likely to grow.
- **Review is outside our control**: the Marketplace team reviews the app and every release.
- **Data leaves the site** (WhatsApp, Telegram, hosted models): the privacy policy must say exactly what, and the agent features must stay off by default, as they are today.

## 6. What not to claim until it is true

- That the app is "on", "available on" or "installable from" the Frappe Marketplace or Frappe Cloud.
- That a Frappe version exists, is "in progress" or "coming soon" with a date.
- That the standalone service is Marketplace-ready; it passes its own tests, not Frappe's, and none of the Marketplace requirements above is met.
- Compatibility with any ERPNext version other than v16, which is the only one tried.

## 7. First steps, in order

1. The owner decides package 0 (name, repository, versions).
2. Do package 1 in this repository; it improves the standalone service whether or not the port happens.
3. Do the spike (one DocType, one scheduled job, one approval round trip on a local bench) and replace the estimates above with measured ones.
4. Only then commit to packages 2 to 9.
