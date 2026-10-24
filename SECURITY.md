# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for a security problem.

Use GitHub's private reporting: on the repository page choose **Security → Report a vulnerability**. Describe what you found, how to reproduce it, and what an attacker could do with it. You will get an answer within a few days. A fix is prepared privately and released together with a note in the changelog; reporters are credited if they wish.

## What this project protects, and what it assumes

The Stock Assistant reads your ERP, asks people to approve purchases, and (once approved) creates **draft** documents in the ERP. It is designed so that:

- nothing is ordered or approved without a person pressing a button; free text typed into a chat can never approve anything;
- every API route refuses an unauthenticated caller, and the roles of ERPNext decide what a signed-in person may do;
- webhooks (WhatsApp, Telegram) are checked by signature or secret before anything is read;
- secrets live in the environment (`.env`), never in the repository or in logs (tokens are masked).

It assumes you run it behind your own network controls. Read [docs/PENTEST_REPORT.md](docs/PENTEST_REPORT.md) for what was tested, what was fixed, and the deployment checklist (service-account roles, which ports to expose, `server.remote_access`).

## Known properties and limits (read before you deploy)

- **HSTS and TLS belong to your TLS proxy.** The application speaks plain HTTP on the address it is given and sets the usual browser protections (CSP, frame, referrer and content-type headers), but it does **not** send `Strict-Transport-Security`: that header is only meaningful on an https response, so it is expected at the reverse proxy or tunnel that terminates TLS (for example `add_header Strict-Transport-Security "max-age=31536000; includeSubDomains"` in nginx). Never expose the server's own port to the internet without a TLS proxy in front.
- **MCP authentication is a static bearer token, not OAuth.** With `mcp.http: true` the `/mcp` endpoint accepts the same ERPNext sign-in tokens and settings-file access keys as the REST API (`Authorization: Bearer ...`); there is no OAuth flow, no per-client scopes and no token rotation beyond changing the key. Treat an MCP key like an administrator password, keep `mcp.http` off unless you need it, and prefer the in-process or stdio transport. Role checks, the order gate and the audit log apply to every tool call whichever way it arrived.
- **Access keys work only on the server machine by default**, in the demo as well as with ERPNext sign-in. `auth.access_keys: anywhere` accepts them from any address that can reach the server; start-up prints a warning while it is set.
- **Hosted language-model keys** (Claude, OpenAI, Gemini) are read from the environment, are sent only to the provider's own https host (never to a custom address, never after a redirect), and are never stored, logged or shown. "Check connection" ignores any address typed for a hosted provider and refuses to contact this machine or the private network on its behalf.
- **Outgoing requests that carry a credential do not follow redirects** (ERPNext, WhatsApp, Telegram, the hosted-model clients, the MCP client).
- **Text from outside** (item names, questions, tool arguments) is stripped of control and invisible characters and length-capped before it appears in a tool error or a log line.
- **Single instance.** SQLite and the in-process scheduler do not support several replicas.

## How this is tested

`tests/test_security_phase1.py` holds the regression tests for the 24 Oct 2026 review and the penetration-test ideas that came with it: a local capture server proving a key never goes to a second host or through a redirect, a plain question never being offered `prepare_order_request` (and the gate refusing it anyway), forged WhatsApp and Telegram webhooks being refused, and the access-key defaults. `bandit` and `pip-audit` are run with every change (see `docs/DEVELOPING.md`).

## Hardening you should do

1. Give the ERPNext account the app runs as only the roles it needs (Purchase User, Stock User, Accounts User, Accounts Manager); `erp-agents erp-check-access` tells you what is surplus.
2. Use long random values for `ERP_AGENTS_OWNER_TOKEN`, `WHATSAPP_VERIFY_TOKEN` and `TELEGRAM_WEBHOOK_SECRET`. Keys shorter than 24 characters are accepted from the local machine only.
3. Expose only the webhooks to the internet when staff use the console on the local network (`server.remote_access: webhooks_only`).
4. Run a single instance. SQLite and the in-process scheduler do not support several replicas.

## Supported versions

Security fixes go to the latest release (pre-1.0: the latest 0.x).
