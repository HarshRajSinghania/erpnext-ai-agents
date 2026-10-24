# Developing: GitHub now, Frappe Marketplace next

The project is published as open source (GPL-3.0-or-later) and is meant to become a **Frappe app** on the Frappe Cloud Marketplace
([PUBLISHING_PLAN.md](PUBLISHING_PLAN.md) §3). The port is a swap of the *edges*; the *core* is shared. Every change should keep that true. This page says what
the core and the edges are today, the rules for new code, and an audit of where the rules are not met yet.

## What the Marketplace requires (docs.frappe.io, checked 21 Oct 2026)

- The app is open source (MIT or a GPL-compatible licence), hosted on GitHub, and published by its owner. The name must be unique across the Marketplace.
- It supports the **current stable Frappe and ERPNext** version (v16 at the time of writing; v16 needs Python 3.14 according to community reports, so check the
  official requirements before each release). Version-specific code branches on `frappe.__version__`.
- A **passing GitHub Actions (or similar) CI is mandatory.** Frappe recommends running Semgrep with its `frappe/semgrep-rules` locally.
- **Do not override base Frappe functionality** (login and password-reset pages and the like): extend it through hooks.
- The app has a settings DocType for its global behaviour.
- Listing: short description of 40 to 80 characters (one sentence, not repeating the name), a long description without installation steps, a **square logo of at
  least 200x200 px without text**, one category, a **support URL**, a **privacy policy URL**, and a short demo video for the reviewers. The app must not steer
  users away from Frappe Cloud.
- The privacy policy has to say exactly what leaves the site: request summaries to WhatsApp and Telegram, and question text to a language model when the
  assistant is on. For the AI agents and the MCP server that statement is generated from code and tested (`agents/privacy.py`, `tests/test_privacy.py`); the
  draft policy to adapt is at the end of [PRIVACY.md](PRIVACY.md). The agent features are off by default, and a model on another machine is refused unless the
  owner allows it, which is what lets the policy say "nothing leaves your site unless you turn it on".

## The layers

| Layer | Modules today | In the Frappe app |
|---|---|---|
| **Core** (pure Python: no web, scheduler, HTTP or SQL library) | `inventory/analysis.py`, `procurement/scoring.py`, `inventory/messages.py`, `procurement/messages.py`, `assistant/facts.py`, `assistant/verify.py`, `assistant/chat.py` (`understand`, answer building), `approvals/models.py`, `core/policy.py`, **`toolkit.py`** (the agents' tools), `agents/runtime.py` (the loop and its checks), `agents/inventory_agent.py`, `agents/purchase_agent.py`, `agents/privacy.py` | kept as is (shared library) |
| **Ports** (interfaces the core talks to) | `erp/base.py` (`ERPClient`), `approvals/channels/base.py` (`Channel`), `auth/` (`AuthProvider`), `assistant/llm.py` (`LLMClient`), **`agents/llm.py` (`ToolChatClient`: Ollama, Claude, OpenAI, Gemini; keys come in through `config.py` only)**, the `*Repo` classes and `ApprovalService` / `SettingsStore` for storage, **`agents/store.py` (`AgentLog`)** | kept; implementations change |
| **Edges** (replaced) | `erp/erpnext.py` (REST and API key), `core/storage.py` (SQLite), `scheduler.py` (APScheduler, Telegram poller), `api/app.py` (FastAPI), `web/index.html` (console), `config.py` and `.env`, **`mcp_server.py` and `agents/bridge.py` (MCP over stdio and HTTP), `agents/service.py` (threads)** | `frappe.get_all/get_doc` in process; DocTypes; `hooks.py` `scheduler_events` and `frappe.enqueue`; whitelisted methods (the MCP endpoint behind Frappe's OAuth provider); Desk workspace and pages; Settings DocType with Password fields |

## Rules for new code

1. **No SQL outside a repository class.** A repository owns one kind of record (`ApprovalService`, `AlertRepo`, `ProposalRepo`, `NoticeRepo`, `WorkOrderQueue`,
   `SettingsStore`). The port replaces each of them with DocType calls, so a stray `db.execute` somewhere else is work nobody will find.
2. **The core imports no `fastapi`, `requests`, `apscheduler`, `sqlite3` or `threading`.** Talk to the world through a port.
3. **A job is a plain function** (`run(...)`) that does one pass and returns. It never sleeps or loops. The scheduler decides when it runs (APScheduler today,
   `scheduler_events` and `frappe.enqueue` in Frappe). Work that can take long (a model call, a message send) must be possible to put on a queue: do not add
   new in-process worker threads or in-memory state that must survive between calls.
4. **Webhooks first.** Frappe Cloud has public HTTPS, so inbound messages arrive by webhook. Polling (Telegram) is for the standalone service only.
5. **Settings and secrets come in through the settings object**, never `os.environ` in application code (only `config.py` reads the environment), and are never
   logged. In Frappe they become a Single DocType with Password fields.
6. **Permissions are ERPNext's.** Roles come from the auth provider and from ERPNext's own permission table; do not add a user table. In Frappe this becomes
   `frappe.has_permission`.
7. **The ERP is written only through `ERPClient`, and only as a draft with the approver recorded.** Custom fields the app needs are declared in one list, which
   becomes fixtures or a patch in the Frappe app.
8. **User-facing text is built in the message modules**, in plain English, so one translation layer (`_()`) can wrap it later. No sentences assembled inside
   routes. Numbers come from code and ERP data.
9. **Extend, never override.** Anything in the Frappe version that Frappe already provides (sign-in, permissions, notifications, background jobs) uses Frappe's own.
10. **Dependencies must be GPL-compatible and installable on Frappe Cloud** (pure Python or wheels); nothing that needs a local service. A local model (Ollama) is
    an optional adapter, off by default.
11. **Tests stay in two groups**: pure-Python tests for the core and the ports (they keep running everywhere, and in CI on Linux and Windows) and, later, Frappe
    integration tests. A change without a test does not merge.
12. **No personal data or real addresses** in code, tests, docs or logs: the CI scan fails on them.
13. **What an agent may know or do is a tool in `toolkit.py`, and nothing else.** A tool has a role (what ERPNext lets the caller do) and an effect: it reads, or it
    prepares an approval request for a person. No tool orders, approves, pays or changes a setting. Its result carries no customer or staff personal data and fits
    a small model's context (`tests/test_toolkit.py`, `tests/test_privacy.py`). The MCP server and our own agents publish and use the same list.
14. **A model's words are checked, its numbers are never trusted.** Anything an agent writes passes `agents/runtime.py` (`grounding_problems`: numbers and codes must
    be in the tools' results) and the job's own rules, and has a deterministic fallback that is shown instead when it fails. The part of an answer that decides
    something (the verdict, the table, the order of items) is computed by the program; the model words and comments.

## Audit (21 Oct 2026): where the rules are not met yet

SQL outside the repository classes (rule 1), about a dozen calls, each easy to move behind a small class:

| Where | What | Move to |
|---|---|---|
| `inventory/agent.py` (3), `procurement/agent.py` (1), `api/app.py` (1) | the `agent_runs` log | a `RunLog` repository |
| `inventory/agent.py` | a read of `approvals` by kind; an update of `alerts.reasoning_text` | `ApprovalService.latest_of_kind`, `AlertRepo.set_reasoning` |
| `assistant/chat.py` (2), `assistant/service.py` (3) | the `assistant_answers` cache; `chat_log` | an `AnswerCache` and a `ChatLog` repository |

Done already: the conversation no longer reads the `deliveries` table itself (`ApprovalService.approval_for_message`).

Threads and in-memory state (rules 2 and 3): the chat throttle (`assistant/chat.py`), the conversation worker pool and its "waiting for an answer" memory
(`assistant/conversation.py`), the sign-in sessions (`auth/sessions.py`), the storage lock (`core/storage.py`) and the scheduler. In a multi-worker Frappe site
these must live in the cache or the database, and the conversation's follow-up work becomes a queued job.
`agents/service.py` adds to that list: one agent task runs at a time behind a lock, and `submit` / `on_submitted` start a background thread so a web request or a
new approval never waits minutes for a model. Each of those becomes a `frappe.enqueue` job (the task row already exists before the work starts, so polling by task
id carries over unchanged). The tools, the runtime and the checks have no threads of their own.

HTTP libraries (rule 2) are confined to the edges: `erp/erpnext.py`, `auth/erpnext.py`, the Telegram and WhatsApp channels and `assistant/llm.py`. `fastapi` is
imported only by `api/app.py`, `apscheduler` only by `scheduler.py`, and the environment is read only by `config.py`: those rules hold.

## Before the first public push (status in [GITHUB_READINESS_REVIEW.md](GITHUB_READINESS_REVIEW.md))

Done: `LICENSE` (GPL-3.0), SPDX licence in `pyproject.toml`, complete `.gitignore`, `SECURITY.md`, `CONTRIBUTING.md`, CI workflow, issue and pull-request templates,
README brought up to date, personal data removed from the docs and tests (the machine-specific pen-test report is kept outside the repository), the
Python 3.10/3.11 `policy set` bug fixed.

Needs the owner: the GitHub account or organisation, author name and contact for `pyproject.toml`, whether `ui-design/` may be published, `git init` and the first
commit (read `git status` first), branch protection, and a decision on the AUTH-02 service-account roles.
