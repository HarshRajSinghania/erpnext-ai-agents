# End-to-end test results

> This is the record of **one** live run, on 22 Oct 2026, and the totals below are that run's. The code has changed since and the live suite has not been re-run: treat the table as history, not as the current state. Current automated result (no live system involved): 1,127 passed and 1 skipped (a POSIX-only permission test) on Windows with Python 3.14, 25 Oct 2026.

Run: 2026-10-22 17:15, live ERPNext (company Test Trading Co); the agent ran in-process with an in-memory database and a fake WhatsApp transport (nothing was sent to Meta or your phone). ERPNext was only read, except one Draft Purchase Order created by PO-05 and deleted afterwards.

Totals: **BLOCKED 8**, **FAIL 1**, **NOT RUN 8**, **PASS 87**.

P0 cases not passing: OPS-05

| ID | Prio | Case | Result | What was observed |
|---|---|---|---|---|
| CFG-01 | P0 | Missing or invalid ERP configuration | PASS | missing secret -> clear ValueError naming erp.api_secret; unreachable ERP -> healthcheck False (no silent mock) |
| CFG-02 | P0 | Effective config: no default credentials | PASS | erp.type=erpnext, owner token is non-default and long |
| AUTH-01 | P0 | What people may do follows ERPNext's own permission table (read live) | PASS | live table: Accounts User -> look only; Purchase Manager (not in the Item table) -> look + decide; System Manager alone -> settings but not decide; Customer -> none. Service account 'agent@example.com' reads as settings |
| AUTH-02 | P1 | The service account holds only what the app needs | FAIL | the service account 'agent@example.com' holds 38 roles it does not need, including high-risk ones (HR Manager, Script Manager, System Manager, Website Manager); a stolen key could change anything in ERPNext. Smallest set that covers the app: Purchase User, Accounts User, Stock User, Accounts Manager. Run: erp-agents erp-check-access |
| CFG-03 | P1 | Health: liveness vs ERP dependency | PASS | anonymous: only ok; signed in: ERP up -> erp_reachable true; ERP down -> still 200, erp_reachable false, no secrets in body |
| SEC-01 | P0 | Hardening from the penetration test: no public API docs, browser protections, request size limits, demo key refused from outside | PASS | docs/openapi 404; X-Frame-Options DENY + CSP pinned to the console script; 2 MB webhook and 300 KB API bodies -> 413; the well-known demo key is refused from outside the server machine; the live owner key is 24+ characters |
| CFG-04 | P0 | Authentication and roles on protected APIs | PASS | none/wrong token -> 401; viewer, approver and admin tokens accepted for reads |
| CFG-05 | P1 | Settings changes need admin; invalid values rejected without persisting | PASS | viewer/approver 403, admin 200; negative qty, unknown field and unknown stock column -> 4xx and nothing persisted |
| CFG-06 | P1 | Read access follows the role matrix | PASS | viewer reads alerts/runs/proposals/work orders; approvals inbox is approver-only |
| CFG-09 | P0 | The live configuration names the company when ERPNext has more than one, so orders are not created in the service account's default company | PASS | ERPNext has 1 companies (Test Trading Co); the configuration sets erp.company = 'Test Trading Co'; a client without a company refuses to create an order instead of guessing |
| CFG-07 | P1 | Scheduler starts once | PASS | exactly three jobs registered once: ['approval_maintenance', 'inventory_check', 'procurement_check'] |
| CFG-08 | P1 | Large / invalid list limits are bounded | PASS | as expected |
| ERP-01 | P0 | Connection and item count | PASS | 202 stock items, equal to ERPNext's enabled stock items |
| ERP-02 | P0 | Stock figures match ERPNext Bin (one and several warehouses) | PASS | 5 items: on hand, reserved, on order, requested, planned equal the Bin totals; unknown item = zero/absent |
| ERP-03 | P0 | Warehouse filter scopes stock and usage | PASS | Stores-only equals total (all stock is in Stores); an empty warehouse gives 0 stock and 0 usage |
| ERP-04 | P0 | Daily usage equals outgoing, non-cancelled ledger quantities | PASS | 2 usage days / 12,600 units for FAS-0004 (Jul-Sep 2026) match the ledger exactly |
| ERP-05 | P1 | Cancelled/positive/excluded entries ignored | NOT RUN | Needs fixtures that create and cancel documents in ERPNext; the exclusion logic is covered by tests/test_erpnext_client.py::test_consumption_aggregates_outgoing_rows_per_day. |
| ERP-06 | P1 | Item attributes map correctly | PASS | name, group, UOM and lead time equal the Item record |
| ERP-07 | P0 | Open purchase orders drive arrival dates | PASS | 6 items with open submitted PO lines: next_arrival = earliest schedule_date of qualifying lines |
| ERP-08 | P1 | Optional doctype permission removed | BLOCKED | Requires changing the agent user's roles in ERPNext. Not changed on your system. Covered by unit tests of the degraded paths. |
| ERP-09 | P1 | Invalid key / insufficient permission | PASS | wrong secret -> healthcheck False and a short ERPNextError without the secret |
| ERP-10 | P1 | Pagination and chunking lose nothing | PASS | 202 items (no duplicates), 2184 Item Price rows across pages with unique names |
| ERP-11 | P1 | Foreign-currency prices compared in company currency | PASS | USD vendor price 2.67 INR (order currency USD, ERPNext rate 0.030); never the raw foreign number |
| ERP-12 | P1 | Tax template chosen = ERPNext Tax Rule logic | PASS | China -> 'Import - China (MFN + anti-dumping) - TTC' (with anti-dumping), Vietnam -> 'Import - ASEAN (FTA) - TTC', domestic -> 'Domestic Purchase - Inter-state - TTC'; item tax template HS 73181500 / GST 18% - TTC |
| INV-01 | P0 | Dry run has no side effects | PASS | analysed 3 items; no alert, approval, work order or message created |
| INV-02 | P0 | Forced breach -> one alert and one approval | PASS | alert 60eaefda19 + approval 5360479745; message carries on-hand 6549 and the reasoning sections; sent to the (fake) WhatsApp recipient |
| INV-03 | P0 | Re-run creates no duplicates | PASS | second run: still one alert, one approval, no new message |
| INV-08 | P1 | Policy layers, sources and audit | PASS | item 15 beats group 12 beats global 9; each value reports its source; changes are in the audit trail |
| INV-09 | P1 | Invalid policy values rejected | NOT RUN | Covered live by CFG-05 (negative quantity, unknown field and unknown stock column all rejected with nothing persisted). |
| INV-11 | P0 | Snooze suppresses until due, then re-alerts | PASS | no new approval inside the snooze; a new one after the snooze elapsed (clock advanced 8 days) |
| INV-12 | P0 | Dismiss is respected | PASS | alert status dismissed; same condition does not re-alert |
| INV-13 | P0 | Recovery resolves the alert and cancels the stale approval | PASS | approval cancelled with a note, alert resolved, no work order (recovery simulated by clearing the threshold, not by receiving stock) |
| INV-14 | P1 | Approving a reorder stores exactly one work order | PASS | one REORDER_NEEDED work order (order quantity left to the procurement step); a second approve -> 409 |
| INV-15 | P1 | Max alerts per run | PASS | 2 alerts in the first run; the next run picks up the next 2 |
| INV-16 | P1 | ERP down during a run, then recovered | PASS | failure recorded in the run history, nothing partial, next run recovered |
| INV-17 | P1 | 'Stock monitor is down' notice after repeated failed runs, closed on recovery | PASS | after 3 failed runs one notice reached the owner (fake WhatsApp + inbox); no repeat on the 4th failure; no secrets or traceback in it (closing on recovery is covered by tests/test_monitor_down.py) |
| INV-18 | P0 | Stock alert (information only) is sent before the reorder request; answering it orders nothing | PASS | FAS-0004 (6549 on hand, level LOW): alert created and delivered before the reorder request; a second run announced nothing new; 'Got it' recorded, no work order, ERPNext still holds 726 purchase orders, the reorder request is still waiting |
| INV-19 | P1 | Stock in a Transit warehouse is reported apart from on-hand stock, and the owner's choice decides what it counts as | NOT RUN | no stock sits in a Transit warehouse in this ERPNext (nothing to classify) |
| WA-01 | P0 | Webhook handshake | PASS | right token echoes the challenge; wrong token 403 |
| WA-02 | P0 | Signature enforcement | PASS | unsigned and wrongly-signed posts -> 401 with no state change |
| WA-03 | P0 | Outbound approval inside the window | PASS | reasoning text followed by an interactive message to the owner's number (fake transport) |
| WA-04 | P1 | Four options use a list; every option maps | PASS | list with 4 rows: ['approve', 'details', 'dismiss', 'snooze']; titles within 24 characters |
| WA-05 | P1 | Template used outside the window | PASS | window-closed error -> falls back to the approved template with 3 quick-reply buttons |
| WA-06 | P1 | Failed delivery is recorded and does not stop the service | PASS | expired token -> whatsapp delivery recorded as failed (failed), inbox delivery sent, API healthy |
| WA-07 | P0 | Details keeps the request pending | PASS | details returned; still pending; no work order |
| WA-11 | P0 | Reply from a stranger is ignored | PASS | unknown sender: 200 to Meta, no decision, no work order |
| WA-12 | P0 | Free text and malformed payloads infer nothing | PASS | 'ok' -> the read-only chat explains what it can do (it cannot act); garbage button id and broken JSON change nothing; the request is still pending |
| WA-13 | P0 | Double press / concurrent channels: first decision wins | PASS | one decision (approve), one work order; the second press and the app's later dismiss are refused as already answered |
| WA-14 | P1 | Replayed webhook body | PASS | replays acknowledged with 200 but change nothing |
| WA-15 | P1 | Buttons on cancelled approvals do nothing | PASS | pressing approve on a cancelled request: no work order, status unchanged |
| WA-16 | P1 | Long names and many lines stay within limits | PASS | text <= 4096, interactive body <= 1024, row titles <= 24, ids <= 200 |
| WA-17 | P1 | Reminders are bounded and nothing is auto-approved | PASS | one reminder then expiry at 3h; status expired; no work order (1 messages after the first reminder window) |
| WA-08 | P0 | Reply Start reorder on the phone | NOT RUN | Covered by the INV-14 + WA-13 flow through the real webhook path. A real phone press needs your Meta sandbox, which this run does not touch. |
| WA-09 | P0 | Snooze via WhatsApp | NOT RUN | Same path as WA-13 with the 'snooze' action; snooze behaviour verified in INV-11. |
| WA-10 | P0 | Dismiss via WhatsApp | NOT RUN | Same path as WA-13 with the 'dismiss' action; behaviour verified in INV-12. |
| WA-18 | P1 | Revoked token with other channels on | BLOCKED | WA-06 covers the failed-delivery path. Revoking your live Meta token was not attempted. |
| PO-01/02 | P0 | Approved reorder -> one vendor proposal, ranking in company currency | PASS | proposal 2f5516e7dc: winner Mumbai Metal Traders; vendors compared in INR: Mumbai Metal Traders 23.44 total 2,343,858,282, Ningbo Hengda Fasteners Co Ltd 20.92 total 2,527,051,006 |
| PO-03 | P1 | Missing data is asked for, never invented | PASS | FAS-0002 (no priced vendor history) and FAS-0001 (no order quantity) both become 'needs your decision' requests; no quote or quantity invented |
| PO-04 | P1 | po_mode off records the decision but writes nothing | PASS | approve recorded; Purchase Order count in ERPNext unchanged |
| PO-05 | P0 | po_mode draft creates exactly one Draft PO with the right content | PASS | PUR-ORD-2026-00738: Draft, supplier Mumbai Metal Traders, currency INR, base rate 23.44 vs proposal 23.44, template 'Domestic Purchase - Intra-state - TTC', vendor note present |
| PO-07 | P0 | Lost response after ERPNext accepted the POST: retry must not create a second PO | PASS | answer lost after ERPNext stored the order; two retries both returned the same draft PUR-ORD-2026-00739; exactly 1 PO exists for the reference |
| PO-08 | P1 | Reject and snooze a proposal | PASS | dismiss: proposal rejected; snooze: proposal snoozed; no Purchase Order created |
| PO-09 | P1 | Duplicate approval of a proposal | PASS | second approve -> 409; still exactly one PO |
| PO-10 | P1 | Procurement settings reflected and audited | PASS | setting saved and audited; existing proposal snapshot unchanged (proposal ranking stored per proposal) |
| PO-11 | P1 | Tax lookup failure is flagged, not hidden | PASS | message states duties and taxes could not be read/are missing for the vendors |
| PO-12 | P1 | Vendor note and required-by date | PASS | PO terms equal the approved note exactly; required-by 2026-10-28 |
| PR-01 | P0 | A purchase requisition (Material Request) is created as a draft with the recommendation, and is safe to retry | PASS | MAT-MR-2026-00006: Draft, type Purchase, reference and approver stored, the recommendation in its note; a retry returned the same document |
| TRACE-01 | P0 | Approved in the console under a real ERPNext login: ERPNext shows that person as the creator (order and requisition), and enforces their permissions | PASS | Purchase Order PUR-ORD-2026-00739: owner = agent@example.com; Material Request MAT-MR-2026-00006: owner = agent@example.com; both are drafts with the approver recorded |
| TRACE-02 | P0 | Strict mode: an approval with no personal ERPNext session creates nothing and waits; ERPNext's own permissions apply to the person | PASS | WhatsApp approval refused in strict mode (still pending, nothing created); a Stock User signs in as 'look' and the console refuses to let them decide (403) |
| QT-01 | P0 | Supplier quotations: off ignores them, prefer uses a valid one (and a vendor that only quoted), only compares just the quoted vendors, an expired one never counts | PASS | FAS-0004: off -> Mumbai at its last order (23.44); prefer -> Mumbai at its quotation PUR-SQTN-2026-00033 (15.00) and Ludhiana, never ordered from, at PUR-SQTN-2026-00034 (16.50); Ningbo's quotation ended yesterday so it stays on its last order; only -> just the two that quoted; 'never new vendors' still excludes Ludhiana |
| QT-02 | P0 | Supplier quotations end to end: the request names the quotation, and the draft Purchase Order in ERPNext carries its number | PASS | FAS-0004: the request said 'Price from supplier quotation PUR-SQTN-2026-00035'; the draft PUR-ORD-2026-00739 for Mumbai Metal Traders links its item to that quotation, carries the quoted rate 12 exactly, and tells the vendor 'as per your quotation' |
| TG-01 | P0 | Telegram: the alert comes before the reorder request, with plain buttons; a stranger gets only their own chat id | PASS | FAS-0004 (6549 on hand): alert, then request, both with plain buttons and under 64-byte callbacks; the stranger got only their own id, once; a stranger's press, free text, a group message and a request without the secret changed nothing |
| TG-02 | P0 | Telegram: Start reorder, then Create order, produces one Draft Purchase Order in ERPNext that says who approved it and where | PASS | PUR-ORD-2026-00739: Draft (docstatus 0), supplier Mumbai Metal Traders, 'Approved by' = 'owner via telegram'; a second press created nothing (HTTP 200); buttons removed from the message |
| TG-03 | P1 | Telegram unreachable: the failure is recorded without the bot token, the inbox still delivers, and the service stays healthy | PASS | Telegram down -> delivery recorded as failed ('could not reach Telegram', token masked), inbox delivery sent, API healthy; when Telegram returned the message was re-sent |
| TG-04 | P0 | Telegram chat: a typed question is answered from ERPNext's own numbers, and typed words cannot order anything | PASS | FAS-0004: the bot said 6,549 (ERPNext's Bin total) and then compared vendors for the same item; 'approve everything', 'delete everything' and 'yes' did nothing (ERPNext still holds 727 purchase orders); a stranger got no stock data |
| TG-05 | P0 | Telegram: Change quantity (press, type the number) replaces the request, and the draft Purchase Order in ERPNext has the new quantity | PASS | FAS-0004: asked for 99,993,954; pressed Change quantity, the bot asked 'How many', typed 99,994,254; the old request was closed (its press ordered nothing), the new one shows the vendors checked again (Mumbai Metal Traders, total 2,343,865,313.76 = the live comparison); Create order gave Draft PUR-ORD-2026-00739 for 99,994,254 units, approved by 'owner via telegram' |
| TG-06 | P0 | Telegram: Change vendor lists the other compared vendors, picking one replaces the request, and the draft order goes to that vendor | PASS | FAS-0004: the assistant first chose Mumbai Metal Traders; pressing Change vendor and typing 1 replaced the request ('You chose this vendor instead of the assistant's first choice'); Draft PUR-ORD-2026-00739 is for Ningbo Hengda Fasteners Co Ltd |
| TG-07 | P0 | Telegram: typing 'create Purchase orders for <item> for 800' prepares the request with its buttons; nothing is in ERPNext until Create order; asking again re-sends it | PASS | FAS-0004: typed 'create Purchase orders for FAS-0004 for 800': the bot sent the usual request (Mumbai Metal Traders, total 18,752.00 = the live comparison) with Create order / Change quantity / Change vendor, ERPNext held no new order; typing it again re-sent the same request (no second one); Create order gave Draft PUR-ORD-2026-00739 for 800 |
| TG-08 | P0 | An order for an item whose ERPNext minimum order quantity is above the suggested quantity is raised to the minimum, and ERPNext accepts it | PASS | FGC-0003: the analysis suggests 2 but ERPNext's minimum order quantity is 5: the request is for 5 (and says why) and Create order gave Draft PUR-ORD-2026-00739 for 5; ERPNext accepted it |
| CHAT-01 | P0 | 'What is running low?' agrees with the stock analysis of the live ERPNext data | PASS | 202 items: 4 out of stock, 28 needing attention; the table lists the same items as the Your stock page |
| CHAT-02 | P0 | 'How much <item> do we have?' equals ERPNext's own Bin and the analysis | PASS | FAS-0004: ERPNext Bin total 6,549 equals the answer; asking by full name finds the same item; a vague name asks which one |
| CHAT-03 | P0 | 'Compare vendors' gives the same ranking, in company currency, as the Buying page | PASS | best vendor Ludhiana Hardware Industries Pvt Ltd at 82,225.00 total for 5,500 Nos; every other vendor's reason is the ranking's own: Ningbo Hengda Fasteners Co Ltd |
| CHAT-04 | P0 | A purchase order number gets ERPNext's own status, vendor, progress and lines | PASS | PUR-ORD-2026-00737: To Receive and Bill, 0% received, vendor Busan Steel Industries Co Ltd: all equal to ERPNext's record |
| CHAT-05 | P1 | Questions about orders by item, period and status, and about requisitions, are answered from ERPNext | PASS | 2 order(s) for FAS-0004 in the last month, equal to a direct ERPNext query; overdue and requisition questions answered (4 order lines exist for the item overall) |
| CHAT-06 | P0 | A requisition created through the app can be asked about by number, with who created and approved it; the chat itself changes nothing | PASS | MAT-MR-2026-00006: draft requisition found by number with its creator and 'approved by owner via whatsapp'; five questions later ERPNext still holds 727 POs and 5 requisition(s) |
| CHAT-07 | P0 | 'Open POs for a material with the quantity and the vendor': the vendor, ordered and received quantities equal ERPNext's own | PASS | MET-0025: 3 open orders in ERPNext; the answer lists the same 3 with each vendor, ordered and received quantity equal to ERPNext's (e.g. PUR-ORD-2026-00737: Busan Steel Industries Co Ltd, 7,000 Kg) |
| OPS-01 | P0 | Pending approval survives a restart and is answered once | PASS | after reopening the database the approval was pending; answered once -> one work order |
| OPS-02 | P1 | Business state persists across restart | PASS | alerts/work orders/proposals/audit rows unchanged after reopening: (1, 1, 1, 2) |
| OPS-03 | P1 | Schedule and timezone | NOT RUN | Needs the running service across a schedule boundary. The cron/timezone parsing and live reschedule are covered by unit tests. |
| OPS-04 | P1 | Two replicas | BLOCKED | Not enforced by the code (SQLite + in-process scheduler); multi-replica deployment is unsupported. Document it rather than test it. |
| OPS-05 | P0 | Backup and restore | BLOCKED | Documented procedure not exercised here (needs a restore into a separate environment). The database is one SQLite file; a copy was opened successfully in OPS-01/OPS-02. |
| OPS-06 | P1 | Schema upgrade | BLOCKED | No prior schema version exists to upgrade from; the code has no explicit migration or version check. Treat as a gap before the first schema change. |
| OPS-07 | P1 | Network interruption | NOT RUN | ERPNext unreachability covered by INV-16/CFG-03; Meta outage by WA-06. A real network cut was not performed. |
| OPS-08 | P0 | No secrets in responses or errors | PASS | (analysis/run answered [500, 200] when the ERP credentials are wrong) no ERP secret, token or webhook secret in health, analysis, run reports, preview errors or outbound messages; no tracebacks |
| OPS-09 | P1 | Bad requests get clean 4xx | PASS | unknown channel 404, unknown approval 404, invalid action 4xx, malformed JSON 422, oversized body handled; service still healthy |
| OPS-10 | P1 | Public exposure through the proxy | BLOCKED | Needs the real tunnel/firewall. NOTE: the cloudflared tunnel is public and /ui and /api are on the same host; the API needs a strong owner token (see CFG-02). |
| OPS-11 | P1 | Disk full / locked database | BLOCKED | Destructive on the host; not attempted. |
| OPS-12 | P1 | OS service restart, log rotation, backup schedule | BLOCKED | Deployment concerns; none exist yet on this machine. |
| E2E-01 | P0 | Alert -> approve -> vendor proposal -> approve -> Draft PO | PASS | one flow: alert -> WhatsApp approve (webhook) -> proposal 2f5516e7dc -> approve -> Draft PUR-ORD-2026-00738 (docstatus 0); every step above passed |
| E2E-02 | P0 | Snooze then answer later | PASS | no re-alert during the snooze; after it, a new request is approved once -> one work order |
| E2E-03 | P1 | Reject at the proposal stage | PASS | see PO-08: rejecting the proposal creates no Purchase Order and closes the proposal |
| E2E-04 | P0 | Stock recovers before the owner responds | PASS | see INV-13 and WA-15: approval cancelled, alert resolved, stale button has no effect, no work order or PO |
| E2E-05 | P0 | ERP down, then WhatsApp down | PASS | see INV-16 and WA-06: both failures are visible and recoverable without false alerts or duplicates |
| E2E-06 | P1 | Restart with a pending request | PASS | see OPS-01: pending request survives restart and is answered once |
