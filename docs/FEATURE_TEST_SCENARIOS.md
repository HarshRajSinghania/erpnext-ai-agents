# Feature test scenarios

Test documents in ERPNext, made so that every purchasing follow-up and stock-intelligence feature has something real to show: **28 materials** (codes `TST-...`), four test vendors and three test customers. Stock positions below are as ERPNext held them on **26 Oct 2026** (`python -m tools.testdata.cli features report` prints them again).

The documents are ordinary ERPNext documents with back-dated posting dates: purchase orders, receipts, invoices and payments for the buying side; delivery notes (the sales the app measures usage from), one sales order, stock counts and item alternatives for the rest. About 610 documents. Nothing that existed before was changed.

## 1. The materials and their stock today

"Reserved" is stock held for a submitted sales order. "On order" is what open purchase orders still have to bring. Values are in the company currency.

### Delivery chasing: late purchase orders

| Material | On hand | Reserved | On order | Value | What it shows |
|---|---:|---:|---:|---:|---|
| TST-LATE-1 Hydraulic Oil ISO 68 | 140 L | 0 | 800 L | 29,400 | Order due 20 Oct, 6 days late, 200 of 1,000 L came. Stock lasts about 5 days, less than a new order takes: **Action needed** |
| TST-LATE-2 Hex Bolt SS304 M10x50 | 6,000 Nos | 0 | 6,800 | 36,000 | Order 4 days late for 5,000 but stock lasts about 58 days: **Info**. Two more orders are *not* late (one due yesterday, inside the one grace day; one not due until 30 Oct) and must not be listed |
| TST-LATE-3 Bubble Wrap Roll 50m | 45 Roll | 0 | 60 Roll | 39,600 | Order 3 days late, vendor has no contact: the request says no reminder can be sent |

### Goods receipt check: receipts that do not match

| Material | On hand | On order | Value | What it shows |
|---|---:|---:|---:|---|
| TST-GRN-1 Spur Gear Steel 20T | 764 Nos | 0 | 702,880 | Ordered 400, **460 received** (60 more than ordered) |
| TST-GRN-2 Welding Electrode E6013 | 1,070 Kg | 0 | 161,638 | Ordered 500 Kg, **75 Kg rejected** (15%): make sure you are not invoiced for them |
| TST-GRN-3 Pneumatic Cylinder 63mm | 116 Nos | 0 | 499,193 | Receipt **8% dearer** than the order (13,440 more); no invoice yet |
| TST-GRN-4 Proximity Sensor M18 | 272 Nos | 0 | 402,288 | Receipt matches, invoice **7% dearer**, **unpaid**: hold the payment |
| TST-GRN-5 Control Panel PCB | 204 Nos | 0 | 156,574 | Invoice **5% dearer** and **already paid**: ask for a credit note |
| TST-GRN-6 Industrial Adhesive 1kg | 610 Kg | 0 | 341,600 | Ordered 300, received 100 then 200: **nothing to flag** (the check must stay quiet) |

### Vendor scorecard

| Material | On hand | Value | What it shows |
|---|---:|---:|---|
| TST-SC-1 Brass Bar 25mm | 470 Kg | 291,224 | 12 orders over five months. **Alpha**: five orders, each a day early, steady price. **Beta**: six orders, on time only twice, prices up about 14%, one delivery 10% rejected. **Gamma**: one delivery |

### Quote requests

| Material | On hand | Value | What it shows |
|---|---:|---:|---|
| TST-RFQ-1 Industrial Adhesive 5kg | 130 Kg | 360,645 | Short on stock, so a quantity can be suggested. Alpha (e-mail and WhatsApp) and Beta (e-mail) can be asked; Delta (WhatsApp only) can be asked when the WhatsApp channel is set up; Gamma has no contact and is listed as "cannot be asked" |

### Demand forecast and season

| Material | On hand | Reserved | Value | What it shows |
|---|---:|---:|---:|---|
| TST-SEAS-1 Corrugated Carton 5-ply | 2,500 Nos | 0 | 95,000 | 15 months of sales. Last year's festive weeks (24 Oct to 12 Nov) ran **1.8 times** the normal rate. The plain rule sees 20 days of cover; the forecast raises the use by 1.79 and says order now (suggested order about 6,770 Nos) |
| TST-SEAS-2 Hand Tool Set 120pc | 85 Set | 90 | 272,000 | A **submitted sales order for 90**, due 1 Nov. ERPNext reserves it, so the default shows "all stock reserved" (available -5). To see the forecast count the order itself, clear "reserved for sales orders" in this item's stock settings |

### Slow and dead stock

| Material | On hand | On order | Value | What it shows |
|---|---:|---:|---:|---|
| TST-DEAD-1 Industrial Solvent Thinner | 2,000 L | 0 | 260,000 | Last sold 200 days ago. **Discount or clear** |
| TST-DEAD-2 Cutting Fluid Soluble | 1,500 L | 0 | 270,000 | Received 24 days ago, never sold. **Return to vendor** (Alpha) |
| TST-SLOW-1 Safety Helmet | 900 Nos | 0 | 234,000 | Sells one a day, 900 on the shelf (about 890 days). **Stop reordering** |
| TST-SLOW-2 Self-adhesive Labels 50x25 | 2,000 Roll | 3,000 Roll | 420,000 | About 400 days of stock and 3,000 more on order. **Cut the open order** |

These four rank 4th, 9th, 11th and 12th of 92 items in the weekly list (the existing demo data already holds 71 dead items; the list shows the 15 that tie up the most money).

### Substitute items

| Material | On hand | Value | What it shows |
|---|---:|---:|---|
| TST-SUB-A1 Industrial Relay 24V 40A, brand X | 30 Nos | 12,600 | About 1.5 days of stock, a new order takes 7: **critical**. The alert names the approved alternatives |
| TST-SUB-A2 Industrial Relay, brand Y | 800 Nos | 336,000 | Alternative of A1, **in stock** (about 156 days) |
| TST-SUB-A3 Industrial Relay, brand Z | 0 | 0 | Alternative of A1, **out of stock itself** |
| TST-SUB-B1 Spiral Wound Gasket 4in | 20 Nos | 4,800 | Short, but **no alternative listed**: nothing is suggested |
| TST-SUB-C1 Deep Groove Ball Bearing 6204 | 900 Nos | 162,000 | Healthy, has an alternative: **none suggested** |
| TST-SUB-C2 Ball Bearing 6204, brand Y | 300 Nos | 54,000 | The alternative of C1 |

### Stock count assistant

| Material | On hand | On order | Value | What it shows |
|---|---:|---:|---:|---|
| TST-CNT-1 Spiral Wound Gasket 6in | 310 Nos | 40 | 74,400 | Counted 270 in the system, 310 on the shelf (+40). A goods receipt for 40 is **still a draft**; the same item was also over by 15 at the count on 11 Sep: **recurring difference** |
| TST-CNT-2 Pneumatic Cylinder 40mm | 20 Nos | 0 | 84,000 | System 240, counted 20: exactly **12 times less**, probably counted in boxes of 12 |
| TST-CNT-3 Proximity Sensor M12 | 70 Nos | 0 | 101,500 | System 90, counted 70, nothing explains it: **recount** |
| TST-CNT-4 Air Compressor 2HP | 40 Nos | 0 | 1,300,000 | Class A (high yearly usage value), **never counted** |
| TST-CNT-5 Control Panel Assembly | 40 Nos | 0 | 1,140,000 | Class A, last counted **300 days ago**: first on the count plan |

## 2. Vendors, contacts and customers

| Vendor | Reached by | Price against Alpha |
|---|---|---|
| TST Alpha Industrial Supplies Pvt Ltd | e-mail and WhatsApp (`alpha.orders@example.com`, +91 00000 00001) | base |
| TST Beta Traders | e-mail only (`beta.sales@example.com`) | 4% lower |
| TST Gamma Wholesale | **no contact at all** | 3% higher |
| TST Delta Hardware & Tools | WhatsApp only (+91 00000 00002) | 5% higher |

The addresses and numbers are placeholders that can never deliver. To receive a vendor message yourself, put **your own** e-mail address or WhatsApp number on that supplier's Contact in ERPNext. The app never stores a contact or shows it to a model; it reads it from ERPNext when you press Send.

The test sales go to three customers of their own (TST Retail Customer One, TST Industrial Customer Two, TST Distributor Three) with a credit limit nobody reaches, so no existing customer's credit is used up.

## 3. How to test each feature

Ask in Telegram, the web chat or the console, or run the same thing from the command line with `erp-agents --config config/settings.erpnext.yaml followup <action>` (`late`, `receipts`, `scorecard`, `forecast ITEM`, `slow`, `substitutes ITEM`, `counts`, `variances`). The chat and the agents use the same tools, so the answers are the same.

1. **Delivery chasing.** "Which deliveries are late?" Expect five orders: the three above plus two that were already late in the demo data (FAS-0001 and MET-0025). The daily check and the Telegram request show the stock effect for each and a message to the vendor with **Send reminder / Ask me later / Stop chasing**. LATE-3 has no Send button (no contact). The two orders that are not late must not appear.
2. **Goods receipt check.** "Which goods receipts don't match their order?" Expect seven receipts: GRN-1 to GRN-5, the rejected Brass Bar from the scorecard history, and one older rejection already in the demo data (FAS-0024). GRN-6 must **not** appear. Each says what to do before you pay.
3. **Vendor scorecard.** "How are my vendors performing?" shows the best 5 and the worst 5. All three test vendors sit at the bottom of the list (ranks 40 to 42 of 42) because the vendors already in the demo data deliver on time. Ask about one: "How is the vendor TST Beta Traders doing?" Expect Beta **48** (on time 30%, 6 days late on average, prices +8.2%, 4.5% rejected), Gamma **75**, Alpha **80**.
   Alpha's on-time figure is 60%, not 100%: its Brass Bar orders are early, but several of its other receipts in these scenarios arrived 1 to 3 days after the due date, and LATE-1 is late now. It is the best of the three, not a perfect vendor.
4. **Quote requests.** "Ask vendors for a price for TST-RFQ-1" through the purchasing agent or an MCP client, or `POST /api/v1/followup/quotes` with `{"item_code": "TST-RFQ-1", "qty": 500, "reason": "price check"}`. Expect a request with **Send requests / Cancel**, two vendors listed (Alpha, Beta), and "cannot ask" for Gamma (no contact) and Delta (WhatsApp is not set up). Nothing is sent until you press Send. After you have sent it, `python -m tools.testdata.cli features quote-replies --item TST-RFQ-1` posts the three vendors' Supplier Quotations (Alpha 2,700, Beta 2,600, Delta 2,950 per unit; change with `--alpha --beta --delta --qty`), and the comparison comes back to you when the replies are in or the reply date passes.
5. **Demand forecast with the season.** Ask "What is the forecast for TST-SEAS-1?" or run the stock check for it. Expect status **breach**, "raised x1.79 because the same days last year ran at 216/day against a normal 120.80/day", and a suggested order of about 6,770 Nos. For TST-SEAS-2 the default says all 85 sets are reserved; to see the forecast add the sales order of 90 on its own, stop subtracting reservations for that item: Settings > When to reorder, scope this item, "Promised to customers" off, or `PUT /api/v1/settings/policy/item/TST-SEAS-2` with `{"set": {"stock_subtract": []}}`.
6. **Slow and dead stock.** "Show slow and dead stock." The chat lists the ten items that tie up the most money, which includes SLOW-2 and DEAD-2; DEAD-1 and SLOW-1 are 11th and 12th, so they appear in the weekly list (sent on Mondays at 08:00, first 15) and in `followup slow` (first 25), each with its own action and "what the actions mean".
7. **Substitute items.** "Substitutes for TST-SUB-A1" lists A2 (in stock) and A3 (none left). The stock check for A1 sends a **critical** alert with an *Approved alternative* section; B1 (no alternative) and C1 (healthy) get none.
8. **Stock count assistant.** "What should we count this week?" TST-CNT-5 is first. TST-CNT-4 is **not** on the first list: dozens of other class-A items in the demo data have never been counted either and the list holds 15. Raise `counts_per_week` to 30 (see section 4) to see it. "Show count differences" explains the three counts of 25 Oct: CNT-1 (draft receipt, recurring), CNT-2 (12 times: boxes), CNT-3 (nothing explains it).

## 4. Set up before you test

- **Outgoing e-mail.** ERPNext has no outgoing Email Account here, so a reminder sent by e-mail fails and the request stays open. Add one (Email Account > Enable Outgoing) to test the Send buttons for real.
- **WhatsApp.** Set `whatsapp_country_code` (for example `91`) and configure the WhatsApp channel to reach Delta; without the channel Delta is listed as "cannot be asked".
- **Follow-up settings** are not on the web Settings screen. Put them in the `followup:` section of `config/settings.yaml` (see `config/settings.example.yaml`), or change them while the app runs with `PUT /api/v1/settings/module/followup` and `{"set": {"counts_per_week": 30}}` (administrator). The ones used here: `counts_per_week` (15), `receipt_lookback_days` (14), `chase_grace_days` (1), `whatsapp_country_code`, `vendor_channel`.
- **Your own address on the contacts** (section 2), or the messages go to placeholders.
- **Models off or on.** None of the above needs an AI model; the agents only add wording.

## 5. How long each scenario stays visible

Dates are relative to the day of the first run, 26 Oct 2026. Orders stay late until they are received, so LATE-1 to LATE-3 stay in the list and the days late grow. The rest age out:

| Scenario | Visible until about | Why |
|---|---|---|
| Goods receipt check | 6 Nov | looks at receipts of the last 14 days (`receipt_lookback_days`) |
| Seasonal jump (SEAS-1) | early November | the forecast window (lead time plus safety days) must overlap last year's 24 Oct to 12 Nov |
| Sales order of SEAS-2 | 1 Nov | the order is due then |
| Count differences | the following weeks | only counts posted since the last pass are new |
| Slow and dead stock, substitutes, counts plan, scorecard | long | measured over months |

## 6. The commands

```
python -m tools.testdata.cli features plan                  what would be created and every stock position (reads and writes nothing)
python -m tools.testdata.cli features run [--only late,grn] create it (safe to run again: it finds what exists)
python -m tools.testdata.cli features report                every material's stock position as ERPNext holds it now
python -m tools.testdata.cli features check                 the app's own tools, read only, on what is in ERPNext now
python -m tools.testdata.cli features quote-replies --item TST-RFQ-1
python -m tools.testdata.cli features redo-sales            cancel and re-post the test delivery notes (only the ones this tool made)
```

Scenarios: `late`, `grn`, `scorecard`, `rfq`, `seasonal`, `slow`, `subs`, `counts`. Every document carries a marker `TD/F1-<key>` in an ordinary field and is listed in `tools/testdata/manifest.jsonl`; the day of the first run is kept in `tools/testdata/features_state.json`. Neither file is committed.

## 7. Things to know

- **ERPNext settings were switched off for a moment.** Receipts and invoices at a price different from the order are refused by default ("Maintain same rate" in Buying Settings, "Over billing allowance" in Accounts Settings). The tool switched each off only while posting GRN-3 to GRN-5 and restored it afterwards in a `finally` block (read back and checked). The three items that carry a dearer invoice also have a small item-level over-billing allowance.
- **Stock value takes up to an hour to settle.** Back-dated postings queue a *Repost Item Valuation* in ERPNext, which its hourly job processes. Right after `run`, stock values (and the slow-stock ranking, which uses them) can be stale. The values here were read after the queue was empty.
- **Test data is not production data.** Do not run `features run` against a live company.
