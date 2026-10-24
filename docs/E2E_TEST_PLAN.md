# End-to-end test plan: ERPNext + inventory agent + vendor selector + WhatsApp

Run these in order. Tick the **Result** column and note anything odd. Cases marked **(EXPECTED TO FAIL
today)** describe correct behaviour that the code does not have yet; they are your acceptance test for the fix.

Conventions: PowerShell, the repository folder, virtual environment active. `$cfg` is the live config.

```powershell
$cfg = "config\settings.erpnext.yaml"
erp-agents --config $cfg <command>
```

---

## 0. Setup (once)

### 0.1 ERPNext agent user
Create `agent@yourco.com` with a role that can **read** these doctypes and **create** Purchase Order:
Item, Item Supplier, Bin, Stock Ledger Entry, Supplier, Payment Terms Template (+ Detail), Purchase Order (+ Item),
Purchase Receipt (+ Item), Tax Rule, Purchase Taxes and Charges Template, Item Tax Template.
Generate its API key and secret (User > API Access).

### 0.2 WhatsApp Cloud API (Meta sandbox)
1. developers.facebook.com > Create app > type **Business** > add the **WhatsApp** product.
2. WhatsApp > API Setup: note the **Phone number ID** and the temporary **access token** (valid 24 hours; for a longer test create a System User token in Business Settings).
3. Add **your own phone** under "To" as a recipient and confirm the code Meta sends. The sandbox only delivers to numbers you add (up to 5).
4. App settings > Basic: copy the **App Secret**.
5. Send "hi" from your phone to the sandbox number. This opens the 24-hour window.
6. Public URL for the webhook (the server listens on 8100):
   ```powershell
   cloudflared tunnel --url http://127.0.0.1:8100      # prints https://<random>.trycloudflare.com
   ```
7. WhatsApp > Configuration > Webhook: callback `https://<random>.trycloudflare.com/webhooks/whatsapp`, verify token = your `WHATSAPP_VERIFY_TOKEN`; click Verify; subscribe to the **messages** field.
8. **Templates** (WhatsApp Manager > Message templates, category *Utility*, language `en`). Needed only for cases C9/C10; Meta approval can take minutes to hours, so create them first:
   - Inventory alert: body `Stock alert: {{1}}`; three quick-reply buttons in this order: `Approve`, `Remind me later`, `Ignore`.
   - Purchase order: body with one variable `{{1}}`; quick-reply buttons: `Create order`, `Remind me later`, `Reject`.

### 0.3 `.env`
```
ERPNEXT_API_KEY=...        ERPNEXT_API_SECRET=...
ERP_AGENTS_OWNER_TOKEN=<a long random string>
WHATSAPP_TOKEN=...         WHATSAPP_PHONE_NUMBER_ID=...
WHATSAPP_APP_SECRET=...    WHATSAPP_VERIFY_TOKEN=stockbot-verify-2026
OWNER_WHATSAPP_NUMBER=91XXXXXXXXXX        # digits, country code, no +
WHATSAPP_TEMPLATE_NAME=<inventory template>   WHATSAPP_PO_TEMPLATE_NAME=<PO template>
```

### 0.4 Start
```powershell
erp-agents --config $cfg erp-check
erp-agents --config $cfg serve          # leave running; console at http://127.0.0.1:8100
```

### 0.5 Test items
Pick these in ERPNext (Item list). They come from the deterministic catalogue; their suppliers and price
currencies are in `tools/testdata/catalog.py`.

| Code | What it is | Use it for |
|---|---|---|
| FAS-0002 Hex Bolt SS304 M8x40 | Two vendors: Bengaluru Tooling (India, Rs 10.10, 10 days) and Saigon Fastener (Vietnam, 0.096 in its currency, 36 days) | Domestic vs import comparison, duties, currency |
| FAS-0003 Hex Bolt SS304 M10x50 | Ludhiana Hardware (India, 3 days) vs Ningbo Hengda (China, 42 days) | China duty incl. anti-dumping |
| FAS-0001 Hex Bolt SS304 M6x25 | Domestic vendor only | Single-vendor message |
| FAS-0022 Anchor Bolt M10x90 | Import vendor only (Vietnam) | Import-only, duty on landed cost |

Force an alert on demand without waiting for stock to fall: set a high minimum quantity on one item.
```powershell
erp-agents --config $cfg policy set --scope item --key FAS-0002 min_qty=99999999
erp-agents --config $cfg policy clear --scope item --key FAS-0002 min_qty      # undo
```

---

## A. Connection and data accuracy

| ID | Test | Steps | Expected | Result |
|---|---|---|---|---|
| A1 | ERPNext reachable | `erp-check` | `healthcheck: OK`; item count equals ERPNext stock-item count | |
| A2 | Stock matches ERPNext | For 5 items compare `erp-agents analyze <item>` "on hand" with **Stock Projected Qty** (not Stock Balance: it is a background report) | On-hand equal for the same warehouse scope; reserved and on-order equal to the Bin | |
| A3 | Missing permission is reported, not hidden | Remove *Purchase Order* read from the agent role; `procurement run --dry-run` | A clear message that data is unknown; no invented dates or prices. Restore the permission | |
| A4 | Wrong key | Put a wrong secret; `erp-check` | Clean failure message (no stack-trace spam); server still starts | |
| A5 | Health endpoint | open `http://127.0.0.1:8100/health` | OK | |

## B. Inventory agent

| ID | Test | Steps | Expected | Result |
|---|---|---|---|---|
| B1 | Dry run has no side effects | `run-once --dry-run`; then `approvals list` and `alerts` | Report of what would happen; no new alerts, no approvals, nothing on WhatsApp | |
| B2 | Threshold alert | Force FAS-0002 (above); `run-once` | One alert and one approval; message has Situation, Why you are seeing this, How usage was estimated, Suggested action, Confidence; numbers equal `analyze FAS-0002` | |
| B3 | Early warning from history | Pick an item that is not under any threshold but whose usage rose recently (`run-once --dry-run` shows "early warning" items) | Message names the driver (recent surge, seasonal or plain pace) with both figures | |
| B4 | No duplicates | Run `run-once` again | No second approval for the same item | |
| B5 | Snooze | Press **Remind in Nd** | Alert snoozed; not re-raised before the date | |
| B6 | Ignore | Press **Ignore** | Not raised again for the dismiss period unless it gets worse | |
| B7 | Escalation | While an alert is open, raise the item's usage (large Delivery Note) or the min_qty; run | Re-raised only if severity increased (e.g. HIGH to CRITICAL) | |
| B8 | Recovery | Receive stock (Purchase Receipt or Stock Entry) above thresholds; run | Alert resolves; the pending approval is cancelled with a note (check `approvals list` and the alert's status) | |
| B9 | Threshold layers | `policy set` global, then group, then item; `policy show --scope item --key FAS-0002` | Effective values and **where each came from**; `policy audit` lists who/when/old to new | |
| B10 | Live reschedule | Change `check_cron` in Configuration > Approvals & schedule | Takes effect without restart (log line "inventory schedule is now") | |
| B11 | Demand calendar | Add a festival event (+50%) covering the next 10 days for the Fasteners group | Days of cover and stock-out date shrink; message states both figures; the suggested quantity basis names the event | |
| B12 | Stock basis | Change `stock_subtract` (e.g. include reserved for production) | Available / position change; the approval message lists every column and whether it was used | |
| B13 | Role limits | Use a viewer token for `PUT /api/v1/settings/policy/...` | 403 | |

## C. WhatsApp channel

Precondition: server running, tunnel up, webhook verified (section 0.2), your phone has written to the sandbox number in the last 24 hours.

| ID | Test | Steps | Expected | Result |
|---|---|---|---|---|
| C1 | Handshake | `Invoke-WebRequest "http://127.0.0.1:8100/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=<token>&hub.challenge=777"` | Body `777`, 200 | |
| C2 | Wrong verify token | same URL with a different token | 403 | |
| C3 | Forged webhook | `POST /webhooks/whatsapp` with any JSON and no/invalid `X-Hub-Signature-256` | 401 "bad signature"; nothing processed | |
| C4 | Outbound inside window | Force an alert (B2) | Phone receives the full reasoning as text, then an interactive message. Inventory alerts have 4 options (Start reorder, Remind in Nd, Ignore, Details), so WhatsApp shows a **list** ("Choose action"), not buttons; buttons appear only in the template message (3 quick-replies) | |
| C5 | Details is non-terminal | Press **Details** | Every figure and where each threshold came from; the request stays open; you can still decide | |
| C6 | Approve | Press **Start reorder** | Confirmation reply; approval status decided; a REORDER_NEEDED work order exists (`GET /api/v1/work-orders`) | |
| C7 | Snooze / Ignore by button | New alert; press **Remind in Nd**; another; **Ignore** | Matching alert states (B5/B6) | |
| C8 | Second press | Press a button again after deciding, or answer on console and then on WhatsApp | "Already answered"; exactly one decision recorded; the first answer wins | |
| C9 | Stranger | Send a message from a number that is not in `recipients` (or temporarily change `OWNER_WHATSAPP_NUMBER`) | Ignored; log "ignored whatsapp action from unknown sender"; no state change | |
| C10 | Free text | Reply to the bot with "ok" | Reply "Please answer with the buttons under the request message (or in the app)." | |
| C11 | Outside 24h window, template set | Wait 24 hours since your last message (or use a number that never wrote); raise an alert | Template message arrives with the summary and three quick-reply buttons; pressing **Approve** decides the approval | |
| C12 | Outside window, no template | Clear `WHATSAPP_TEMPLATE_NAME`, same situation | Delivery recorded as failed (see approval details / log); console and inbox still show it; `retry_failed_deliveries` re-sends when the window reopens | |
| C13 | Reminder and expiry | Set `reminder_after_hours=1`, `approval_expiry_hours=2`, `max_reminders=1` (minimum allowed is 1 hour); leave one request unanswered | One WhatsApp reminder after 1 hour; after 2 hours the request is marked expired (the maintenance job runs every 10 minutes); it is **never auto-approved**, and no work order or PO is created | |
| C14 | Two channels, one decision | Console, inbox and WhatsApp all enabled; decide in the web console | WhatsApp buttons afterwards answer "Already answered" | |
| C15 | Long message limits | Choose an item with many vendors/tax lines | Message is cut cleanly (4096-character limit), buttons still sent, button titles at most 20 characters | |
| C16 | Meta rejects payload | Use an expired access token | Delivery failure is logged and recorded; the server keeps running | |

## D. Vendor selector (procurement)

Precondition: an approved reorder exists (C6) or `POST /api/v1/procurement/run`.

| ID | Test | Steps | Expected | Result |
|---|---|---|---|---|
| D1 | Proposal appears | Approve a reorder for FAS-0002; wait 3 minutes or `procurement run` | A proposal in `procurement proposals`; a WhatsApp message **Purchase order: Hex Bolt SS304 M8x40 from ...** | |
| D2 | Message content | Read it | Recommendation (qty, vendor, unit price, goods total, landed total with duties), Stock position, **Why this vendor** (score of 100, cost, delivery, on-time record, credit days), **Other vendors** each with why not, How vendors were scored (weights), Note to vendor | |
| D3 | **Currency (EXPECTED TO FAIL today)** | FAS-0002: compare Bengaluru Rs 10.10 / 10 days vs Saigon 0.096 (foreign currency) / 36 days | Both prices converted to company currency (Rs) at the order's rate before scoring and display; the foreign price shown as a note. Today the foreign number is compared as if it were rupees | |
| D4 | Duties per country | Run FAS-0003 (China) and FAS-0022 (Vietnam); open Details | Customs duty, anti-dumping (China only), freight, insurance and GST lines come from the ERPNext template chosen by Tax Rule for each supplier's tax category; amounts follow the template rows | |
| D5 | Create order | Press **Create order** | Draft Purchase Order in ERPNext: right supplier, item, qty, rate, `required_by` = today + vendor lead time, taxes template = the one shown, vendor note in Terms and Conditions; **docstatus 0 (not submitted)**; proposal status "ordered" with the PO name | |
| D6 | ERP refuses | Remove *Create* on Purchase Order from the agent role; press **Create order** | Owner is told the ERP did not accept it; decision rolled back (still pending); restore permission, press again: PO created once | |
| D7 | No priced vendor | An item with no PO history and no Item Supplier rate | "Needs your decision" message listing exactly what is missing; options Got it / Remind / ... | |
| D8 | No quantity | An item with no usage; leave `fallback_qty` empty | Manual message saying quantity could not be worked out; set `fallback_qty` and run again: proposal is made | |
| D9 | Reject | Press **Reject** | Proposal rejected; work order closed; no PO | |
| D10 | Snooze | Press **Remind in 2d** | Proposal snoozed; re-asked after the period (advance the clock or wait) | |
| D11 | Weights | Set cost weight 100 and others 0, then delivery 100 | Winner and "why not" lines change accordingly; weights printed in the message | |
| D12 | Guardrails | Turn on `exclude_new_vendors`; set `min_vendors` above the number of vendors | Vendors with no history excluded with the reason; a clear message when fewer vendors than required | |
| D13 | Late delivery | Item whose best vendor arrives after the stock-out date | Message states "N days AFTER you run out"; the urgent sentence is added to the vendor note | |
| D14 | Tax lookup failure | Remove *Tax Rule* read permission | Message says duties and taxes may be missing, per vendor; with `no_tax_setup: exclude` those vendors are not recommended | |

## E. End-to-end journeys (the real acceptance tests)

**E1 Happy path (about 30 minutes):**
1. Pick FAS-0002. Record on-hand from ERPNext.
2. Create a Delivery Note that takes the item down close to the threshold (or force the alert as in section 0.5).
3. `run-once`. WhatsApp: alert with reasoning. Press **Start reorder**.
4. Within 3 minutes WhatsApp: purchase-order proposal. Check the landed cost and why-not lines against the ERPNext tax templates by hand.
5. Press **Create order**. In ERPNext open the new draft PO, review, **Submit** it.
6. Create the Purchase Receipt against the PO. Stock goes up.
7. `run-once`. **Expected:** the alert is resolved and no new one is raised; during the PO wait, no duplicate alert (reorder cooldown), and the late-PO rule uses the PO's `schedule_date`.

**E2 Snooze path:** alert > Remind > nothing before the date > alert again afterwards.

**E3 Reject path:** alert > Start reorder > proposal > Reject > nothing is ordered; the item is re-evaluated at the next run according to the dismiss period.

**E4 Failure path:** stop ERPNext (`docker compose ... stop`) during `run-once`. **Expected:** the run reports the error, no crash, no partial alerts; start ERPNext and the next run works.

**E5 Restart:** leave an approval pending, stop and start `serve`. **Expected:** it is still pending and answerable on WhatsApp.

## F. Security and robustness

| ID | Test | Expected | Result |
|---|---|---|---|
| F1 | API without a token | 401 | |
| F2 | Viewer token on settings PUT or on a decision | 403 | |
| F3 | Replay the same webhook body twice | Second is "Already answered"; one decision | |
| F4 | `ERP_AGENTS_OWNER_TOKEN` unset | Today the demo token works. After fixing blocker B5: the server refuses to start with the default token on a non-local address | |
| F5 | Secrets in logs | Search the server log for the tokens and the secret | None present | |
| F6 | SQL / markup in item names | An item named `x'); DROP TABLE alerts;--` and `<b>` | Rendered as plain text; nothing breaks | |

## G. "Ask why" assistant (optional, needs Ollama)

| ID | Test | Expected | Result |
|---|---|---|---|
| G1 | Off by default | `GET /api/v1/assistant/status` says disabled; `ask` refused | |
| G2 | On, question about a request | "Why not the Vietnam vendor?" answered using only numbers from the request, citing fact ids | |
| G3 | Unanswerable | "What will the steel price be next month?" returns exactly: "I do not have enough information in the recorded data to answer that question." | |
| G4 | Repeatability | Same question twice returns the same text (stored) | |

---

## Reporting a failure

For each failed case record: ID, what you pressed, what you expected, what happened, the approval id
(`erp-agents approvals list`; use `--status` to filter), and the relevant lines of the server log. With those I can
reproduce and fix it without guessing.
