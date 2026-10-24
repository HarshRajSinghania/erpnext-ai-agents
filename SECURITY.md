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

## Hardening you should do

1. Give the ERPNext account the app runs as only the roles it needs (Purchase User, Stock User, Accounts User, Accounts Manager); `erp-agents erp-check-access` tells you what is surplus.
2. Use long random values for `ERP_AGENTS_OWNER_TOKEN`, `WHATSAPP_VERIFY_TOKEN` and `TELEGRAM_WEBHOOK_SECRET`. Keys shorter than 24 characters are accepted from the local machine only.
3. Expose only the webhooks to the internet when staff use the console on the local network (`server.remote_access: webhooks_only`).
4. Run a single instance. SQLite and the in-process scheduler do not support several replicas.

## Supported versions

Security fixes go to the latest release (pre-1.0: the latest 0.x).
