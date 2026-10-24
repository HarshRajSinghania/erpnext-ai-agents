# End-to-end acceptance test cases: ERPNext inventory agent and WhatsApp approvals

**Purpose:** verify the deployed application from ERPNext read through inventory analysis, owner notification, WhatsApp reply handling, and (when enabled) procurement. This is a manual acceptance plan for an isolated staging ERPNext site and Meta WhatsApp test number. It does not authorize tests against production data.

**Status record:** record each case as `PASS`, `FAIL`, `BLOCKED`, or `NOT RUN`; include date, build/version, ERPNext/Frappe version, test operator, config profile (never secrets), and evidence location. Run cases in order. Do not proceed to live PO creation if a read, decision, or duplicate-delivery case fails.

## Safety and prerequisites

- Use a disposable/staging ERPNext site, a dedicated agent user, a WhatsApp test number, and an owner-controlled recipient number. Never use customer data or make a real purchase during acceptance testing.
- Back up the staging database and note the starting stock, open POs, and pending approvals. Create clearly named test items / vouchers or use the project's documented test-data fixture catalogue.
- Use a dedicated config. Confirm it selects `erpnext`, points at the staging URL, uses unique random API and app tokens, and has no demo-token fallback. Enable WhatsApp only after webhook signature verification is configured. Keep the API private or behind HTTPS.
- Start with `run-once --dry-run`. For each case that changes ERP data, record and reverse the exact fixture afterwards. Use `po_mode: off` until the draft-PO cases; then enable `draft` only in staging.
- Never paste tokens, API keys, phone numbers, or full webhook payloads containing personal data into the report. Redact them in screenshots and logs.

## Required evidence

For every case retain the case ID, timestamp, result, steps, expected and observed outcome, relevant run/alert/approval/proposal/PO IDs, and redacted logs or screenshots. For stock reconciliation record the ERPNext report, warehouse scope, item code, quantities, and comparison. Do not store secrets in evidence.

## 1. Configuration, startup, and API access

| ID | Priority | Procedure | Expected result |
|---|---|---|---|
| CFG-01 | P0 | Start using the staging config with ERPNext URL/key/secret missing, then with a deliberately invalid URL. | Missing required config fails clearly before a scheduled run; unreachable ERP is reported as unavailable. No silent switch to mock data. |
| CFG-02 | P0 | Inspect effective runtime config without printing secret values. Confirm `erp.type`, database path, host, channel state, recipients, auth roles, webhook secret and TLS/proxy route. | ERP is staging ERPNext; no default credential/token; owner token is non-empty and unique; exactly intended recipients/channels are enabled. |
| CFG-03 | P1 | Start the service and call health check with ERPNext available, then unavailable. | Liveness is distinguishable from ERP dependency health; failure is observable in logs/monitoring and does not expose credentials. |
| CFG-04 | P0 | Call protected APIs without a token, with a wrong token, and with valid viewer, approver, and admin tokens. | Missing/invalid token returns 401; valid roles are accepted only for their allowed actions; no token value appears in responses/logs. |
| CFG-05 | P1 | Attempt policy/system/procurement changes as viewer and approver, then admin. | Viewer/approver cannot change settings; admin can make a valid change; invalid fields/values receive a clear 4xx and do not persist. |
| CFG-06 | P1 | Use viewer token to read inventory, alerts, run records and procurement proposals; use approver token for inbox. | Read access matches the documented role matrix; approval content is not exposed to viewer-only access. |
| CFG-07 | P1 | Restart the service with a valid configuration. | Service starts once, scheduler starts once, no duplicate pollers/jobs, and the configured timezone/schedule are logged. |
| CFG-08 | P1 | Attempt a request with an excessively large list limit and oversized/invalid query values. | Request is bounded or rejected; service remains responsive. Record any unbounded endpoint as a release blocker. |

## 2. ERPNext connection and inventory data reconciliation

For all quantity comparisons use the same company, item, posting date, warehouse selection, and stock basis. The agent's available/position figures are configurable and may intentionally differ from a raw ERPNext balance; record the selected `stock_subtract` and `stock_add` columns.

| ID | Priority | Procedure | Expected result |
|---|---|---|---|
| ERP-01 | P0 | Run `erp-check` using the dedicated staging agent account. | Authenticated connection succeeds; item count is plausible and matches enabled stock items within configured item groups. |
| ERP-02 | P0 | Compare at least five items across one and multiple warehouses against ERPNext Bin / Stock Balance using the same warehouse scope. | On-hand, reserved, ordered, indented, planned and projected quantities match their corresponding ERPNext fields within documented rounding. Missing Bin rows are treated as zero. |
| ERP-03 | P0 | Select a warehouse filter; compare one included and one excluded warehouse. Then clear the filter. | Included stock and usage are scoped consistently; excluded warehouse movements do not affect results; empty filter behavior is understood and documented. |
| ERP-04 | P0 | Compare outgoing Stock Ledger Entries over a known date range for each configured consumption voucher type. | Daily usage matches eligible, non-cancelled, negative ledger quantities. Transfers/production movements are counted only if explicitly configured. |
| ERP-05 | P1 | Include cancelled entries, positive entries, and an excluded voucher type in the fixture. | None contribute to consumption. Date boundaries are handled as configured and consistently. |
| ERP-06 | P1 | Compare item name, item group, UOM, creation date and lead time for several items. | ERP values map to agent records; absent optional fields are clearly treated as unknown/default rather than invented. |
| ERP-07 | P0 | Create an open submitted PO line, a partially received line, a fully received line, a draft PO and a cancelled PO with known schedule dates. | Only qualifying open submitted quantities affect arrival logic; partial remainder is considered; fully received/draft/cancelled lines are excluded. Earliest valid arrival is selected. |
| ERP-08 | P1 | Remove read permission for one optional doctype at a time (e.g. Purchase Order, Supplier, Purchase Receipt, Tax Rule). Run the affected preview. | The run continues where designed; unavailable values are reported as unknown with a reason, not guessed. Restore permissions after each check. |
| ERP-09 | P1 | Use an invalid API key, then a valid key with insufficient permissions. | Connection/permission errors are clear and actionable, with no traceback to the user, secret leakage, or false “healthy” result. |
| ERP-10 | P1 | Test a large catalogue spanning multiple ERP API pages and more than one item-code chunk. | No items or ledger rows are lost or duplicated at pagination/chunk boundaries; runtime and memory remain acceptable for the staging catalogue. |
| ERP-11 | P1 | Compare vendor history and foreign-currency PO rates across at least one domestic and one foreign supplier. | Prices used for ranking are expressed in the configured company currency using ERPNext base values/conversion; original currency/rate is clearly identified where shown. |
| ERP-12 | P1 | Compare the selected purchase tax rule/template and item-tax override with ERPNext for domestic and imported suppliers. | Applicable date/category/company/supplier conditions and supported charge calculations match ERPNext; unsupported or ambiguous tax setups are explicitly flagged, not silently omitted. |

## 3. Inventory analysis, alerts, and approval creation

| ID | Priority | Procedure | Expected result |
|---|---|---|---|
| INV-01 | P0 | Run a baseline `run-once --dry-run`; inspect run report, alerts, approvals, work orders, and WhatsApp. | Analysis is reported, but no persistent alert/approval/work order or outbound message is created. |
| INV-02 | P0 | Force a known minimum-quantity breach for a staging item; run live once. | One alert and one approval are created; reason, severity, stock figures, usage basis, thresholds, recommended quantity and confidence agree with `analyze`. |
| INV-03 | P0 | Run the same inventory check again without changing ERP data. | No duplicate alert or approval is produced. |
| INV-04 | P1 | Set only the minimum-quantity rule, then only days-cover, then lead-time rule; test each boundary just below, equal to and just above threshold. | Each rule fires at the documented boundary; disabled/null rules do not fire. |
| INV-05 | P1 | Create recent consumption surge, seasonal history, low-history/new item, zero usage and lumpy usage fixtures. | Forecast and confidence notes follow configured history requirements; no divide-by-zero or unsupported prediction occurs. |
| INV-06 | P0 | Include open POs that arrive before and after projected stock-out, including partial receipts. | Timely PO suppresses the relevant reorder signal when configured; late PO is called out; arrival dates and remaining amounts match ERPNext. |
| INV-07 | P1 | Vary reservation and incoming stock columns; change `stock_subtract` / `stock_add` policy. | Available and stock-position calculations follow policy; message explains which columns were counted and in which direction. |
| INV-08 | P1 | Configure global, group, and item overrides; query the effective policy and audit. | Resolution order is defaults → config → global → group → item; values and source are correct; changes are audited with actor/time/old/new. |
| INV-09 | P1 | Change a policy to invalid values (negative quantities, unknown stock column, invalid window relationship). | Update is rejected without partial persistence; prior effective setting remains. |
| INV-10 | P1 | Add a scoped calendar event and test an item within and outside its dates/group/item scope. | Only matching dates/items are adjusted; forecast, stock-out and quantity reasoning reflect the event; disabling/removing it restores baseline. |
| INV-11 | P0 | Snooze an open alert, run before and after snooze time. | It is suppressed before the due time and becomes eligible again afterwards according to policy. |
| INV-12 | P0 | Dismiss/ignore an alert, run before/after suppression, then increase severity. | Suppression is respected; a worsened condition can be escalated as documented; history remains intact. |
| INV-13 | P0 | Receive stock so the item recovers; run again. | Alert resolves; stale pending approval is cancelled with an explanatory note; no new reorder is proposed. |
| INV-14 | P1 | Approve an inventory reorder request. | Exactly one `REORDER_NEEDED` work order is stored with item, quantity, analysis snapshot and approval provenance. |
| INV-15 | P1 | Set max alerts per run lower than the number of eligible items. | Highest-priority items are handled first; remaining work is visible in run output and eligible on a later run. |
| INV-16 | P1 | Stop ERPNext during an inventory run; restore it and rerun. | Failure is recorded clearly; no misleading success or corrupt/partial alert state; next run recovers. |

## 4. WhatsApp setup, outbound messages, and inbound replies

Precondition: Meta test app/webhook is configured, HTTPS callback is reachable, `messages` subscription is enabled, owner number is an allowed recipient, and app secret verification is active. Keep one live test approval for each response case.

| ID | Priority | Procedure | Expected result |
|---|---|---|---|
| WA-01 | P0 | Complete Meta webhook verification with correct verify token and challenge; repeat with wrong token. | Correct token returns the challenge; wrong token is rejected; no secret appears in output. |
| WA-02 | P0 | POST validly shaped but unsigned JSON; then POST with invalid signature; then signed valid fixture. | Unsigned/invalid signature receives 401 and causes no state change; valid signed request is parsed and acknowledged. |
| WA-03 | P0 | Trigger an approval while owner is inside the 24-hour conversation window. | Owner receives the expected reasoning and action interaction; message identifies item/alert and matches stored approval facts. Delivery is recorded. |
| WA-04 | P1 | Use approval with four or more options, including Details. | WhatsApp uses a supported list interaction; labels/descriptions fit platform limits; every option maps to the correct action. |
| WA-05 | P1 | Use a configured approved template outside the 24-hour window. | Template is accepted, summary is accurate, button payloads map to the right actions, and template delivery is logged. |
| WA-06 | P1 | Test missing/unapproved template and expired access token. | Delivery failure is visible and recorded; service remains running; other enabled channels continue; retry behavior is bounded and observable. |
| WA-07 | P0 | Reply **Details** to an inventory request. | Details are returned; request remains pending; no alert/work-order status changes. |
| WA-08 | P0 | Reply **Start reorder/Approve** to an inventory request. | Reply is mapped to the correct approval; approval is decided once; alert becomes actioned and one work order is created. |
| WA-09 | P0 | Reply **Remind me later/Snooze** to an inventory request. | Correct snooze state/time is stored; no work order is created. |
| WA-10 | P0 | Reply **Ignore/Dismiss** to an inventory request. | Correct dismissed/suppressed state is stored; no work order is created. |
| WA-11 | P0 | Reply from a number not configured as a recipient. | No decision or state change occurs; unknown sender is safely ignored and logged without personal-data leakage. |
| WA-12 | P0 | Send ordinary free text (e.g. “ok”) and a malformed button payload. | No approval action is inferred; user gets the configured help response where supported; service does not error or change state. |
| WA-13 | P0 | Press a valid action twice; then decide the same request on WhatsApp and another channel concurrently. | First valid terminal decision wins; later presses report already answered; exactly one decision and domain effect are stored. |
| WA-14 | P1 | Replay the same signed webhook body and retry a webhook after a timeout. | Duplicate delivery does not repeat a decision or create duplicate work. Webhook returns promptly so Meta does not retry indefinitely. |
| WA-15 | P1 | Reply to expired, cancelled, superseded, or stale approval buttons. | No new action is applied; response explains that the request is no longer actionable. |
| WA-16 | P1 | Trigger approval with long item/vendor names and many tax/reason lines. | Text is safely truncated/escaped; no malformed markup; controls remain usable and callback payloads are within platform limits. |
| WA-17 | P1 | Set reminder/expiry thresholds in a short staging window; leave request unanswered. | Reminders are sent only up to the configured maximum; request expires; no action is auto-approved; expiry state is visible. |
| WA-18 | P1 | Disable WhatsApp delivery or revoke Meta token while console/inbox remains enabled. | Failure is recorded; scheduler and other channels continue; failed-delivery retry does not multiply messages without a bound. |

## 5. Procurement and draft purchase-order path (staging only)

Set `po_mode: off` until the initial cases pass. Enable `draft` only on staging with a test supplier/item and a user able to inspect the resulting PO. Never automatically submit a PO.

| ID | Priority | Procedure | Expected result |
|---|---|---|---|
| PO-01 | P0 | Approve reorder; wait for procurement cycle or run procurement. | One proposal is created for the approved work order; recommendation and approval reference are consistent. |
| PO-02 | P1 | Inspect vendor ranking for multiple suppliers with known price, lead, reliability, payment terms, tax, currency. | Ranking and explanation use the displayed facts and configured weights; unknown data is labeled; no unconverted foreign price wins incorrectly. |
| PO-03 | P1 | Test supplier with no price/history, too few vendors, excluded new vendor, and missing order quantity. | App asks for owner input with the specific missing data; it does not invent a quote, quantity, lead time, or tax amount. |
| PO-04 | P1 | Set `po_mode: off`, approve vendor proposal. | Decision is recorded; no ERPNext write is made; work order/proposal reaches the documented terminal state. |
| PO-05 | P0 | In staging with `po_mode: draft`, approve the PO proposal. Inspect ERPNext document. | Exactly one PO is created for correct supplier/item/UOM/qty/rate/currency/required-by date/tax template/note; it remains Draft (`docstatus=0`) and is not submitted. |
| PO-06 | P0 | Remove Purchase Order create permission and approve a test proposal. Restore permission and retry after checking ERPNext. | Failure is reported clearly. Retry succeeds only after confirming no PO was created by the first request; no duplicate draft can result. |
| PO-07 | P0 | Simulate a lost/timeout response after ERPNext accepts the POST (staging proxy or controlled fake). | On retry, app reconciles the created PO using a stable reference/idempotency mechanism and does not create a second PO. If this behavior is unsupported, mark P0 FAIL and do not enable automated draft creation. |
| PO-08 | P1 | Reject and snooze procurement proposal. | Reject closes the work order with no PO; snooze delays re-prompt and does not create a PO. |
| PO-09 | P1 | Exercise duplicate button/reply and concurrent approval for one proposal. | At most one terminal proposal outcome and one PO; repeated replies return already answered. |
| PO-10 | P1 | Change procurement scoring weights, tax settings, vendor note, and fallback quantity. | New proposals reflect saved effective settings and audit; existing proposal snapshot remains explainable and does not silently mutate. |
| PO-11 | P1 | Force ERPNext tax lookup failure or unsupported tax rule. | Proposal flags the tax uncertainty or excludes vendor per policy; no hidden tax assumption. |
| PO-12 | P1 | Review purchase order vendor note and required-by date for a late vendor. | Note matches approved preview, contains only configured fields, and urgent statement appears only under its documented condition. |

## 6. Persistence, scheduler, recovery, and security

| ID | Priority | Procedure | Expected result |
|---|---|---|---|
| OPS-01 | P0 | Leave approval pending, restart service, then reply. | Pending state and delivery references persist; reply is accepted exactly once. |
| OPS-02 | P1 | Restart after recording an alert, policy change, audit row, work order, and proposal. | All business state persists and remains queryable; no duplicate scheduled run side effect on startup. |
| OPS-03 | P1 | Verify schedule around configured timezone, daylight-saving boundary where applicable, and restart/reschedule after admin change. | Runs occur at intended local time once; schedule change is validated and takes effect without duplicate jobs. |
| OPS-04 | P1 | Run two app processes against the same staging database or attempt two replicas. | Deployment prevents duplicate scheduler/Telegram poller processing, or explicitly enforces a single active worker. If not, multi-replica deployment is unsupported and must be documented. |
| OPS-05 | P0 | Back up database; restore to isolated staging copy; start service and inspect state. | Documented backup/restore works; no loss of approvals/audit/work orders; restore is verified before production operations. |
| OPS-06 | P1 | Upgrade a copy of the database from the prior application schema version. | Explicit migration runs once and preserves data; unsupported schema versions fail fast with actionable error rather than silently starting. |
| OPS-07 | P1 | Interrupt ERPNext/Meta network during reads and sends, then recover connectivity. | Bounded timeouts/retries; errors visible; no process crash, endless retry, duplicate approval, or false success; recovery succeeds. |
| OPS-08 | P0 | Search logs, API responses, run reports and exception messages after intentionally invalid credentials. | No API secret, WhatsApp token, full authorization header, or unredacted personal data is emitted. |
| OPS-09 | P1 | Try unknown channel, unknown sender, invalid approval ID/action, expired action, malformed JSON, and oversized request. | Appropriate 4xx response; no stack trace or state change; service remains healthy. |
| OPS-10 | P1 | Check public routes and externally reachable interface through the intended proxy/firewall. | Only intended endpoints are exposed; external API traffic uses HTTPS; administrative endpoints require authentication; health endpoint leaks no sensitive deployment detail. |
| OPS-11 | P1 | Fill or lock the SQLite disk/database in staging; restart and recover. | Failure is visible and bounded; no silent data loss/corruption; documented recovery restores service. |
| OPS-12 | P1 | Verify operating-system service restart, shutdown, log rotation, and backup schedule. | Service exits cleanly, restarts without duplicate work, logs rotate, backups are restorable, and alerts exist for failed scheduled runs. |

## 7. Full acceptance journeys

| ID | Priority | Journey | Pass criteria |
|---|---|---|---|
| E2E-01 | P0 | ERPNext stock/usage fixture → dry run → threshold alert → WhatsApp delivery → owner approves → work order → vendor proposal → owner approves → draft PO. | Every figure matches source data and stored snapshot; each approval is delivered and answered once; one draft PO exists and remains unsubmitted; full audit trail is traceable by IDs. |
| E2E-02 | P0 | Same as E2E-01, but owner snoozes inventory alert, then answers after snooze. | No premature re-alert; later action proceeds once; no duplicate work order/PO. |
| E2E-03 | P1 | Inventory alert → approve reorder → proposal → reject proposal. | No PO is created; proposal/work-order state is terminal as documented; no automatic re-proposal before configured suppression expires. |
| E2E-04 | P0 | Inventory alert → recover stock before owner responds. | Alert resolves and pending WhatsApp approval is cancelled; pressing stale button has no side effect; no procurement work order/PO is created. |
| E2E-05 | P0 | ERPNext unavailable during run, restored; then WhatsApp unavailable during send, restored. | Failures are visible and recoverable; no false-positive inventory values, duplicate notifications, or lost pending decisions. |
| E2E-06 | P1 | Create pending request, restart app, answer via WhatsApp, then inspect state from API/inbox and ERPNext. | State agrees across all surfaces and ERPNext; first answer wins and outcome is traceable. |

## Release gate

- **No P0 failures or blocked P0 cases.** Any blocked case must have an owner and a documented reason; live procurement remains disabled until PO idempotency/reconciliation passes.
- All ERPNext comparisons are reconciled against the staging source for documented item and warehouse scopes.
- WhatsApp webhook verification, recipient identity, all approval actions, replay/duplicate protection, expiry, and restart persistence pass.
- Draft PO is confirmed as Draft and never submitted by the agent. No test produced duplicate POs or unapproved writes.
- No credentials or personal data appear in test evidence. Backup restoration, deployment boundary, and monitoring are documented.
- Attach failed-case evidence and remediation issues to the release record; rerun affected cases after fixes.
