"""Messages to vendors, and their replies.

A message to a vendor is sent only from the handler of a request a person answered with Send: nothing here runs on its own. The address comes from the supplier's contact
in the ERP at the moment of sending (`FollowUpERP.get_supplier_contact`) and is used for that one message:

  * it is never put into an approval request, a tool result, a log line or a language model's input;
  * the database keeps only a one-way hash of it, so a WhatsApp reply from that number can be matched to the message it answers;
  * e-mail goes through the ERP's own outgoing mail account (recorded on the document there), WhatsApp through the configured WhatsApp channel.

A vendor's reply is untrusted text: it is cleaned, capped, stored, and passed to the people who answered the request, labelled as the vendor's words. Nothing acts on it.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Optional

from erp_agents.core.common import iso, short_id, untrusted

log = logging.getLogger(__name__)
OUTSIDE_WINDOW = "(code 131047)"
MAX_REPLY = 1500
REPLY_WINDOW_DAYS = 30            # a reply is matched to a message sent at most this long ago


def whatsapp_number(raw: str, country_code: str = "") -> Optional[str]:
    """The ERP's mobile number as WhatsApp wants it: digits with the country code, no + or spaces. None when it cannot be told which country it is in."""
    text = (raw or "").strip()
    if not text:
        return None
    plus = text.startswith("+")
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    if plus:
        out = digits
    elif digits.startswith("00"):
        out = digits[2:]
    elif digits.startswith("0"):                # a trunk prefix: the country is not written, so it is only known from the settings
        if not country_code:
            return None
        out = country_code + digits[1:]
    elif len(digits) >= 11:                     # long enough to carry its country code already
        out = digits
    elif country_code:
        out = country_code + digits
    else:
        return None
    return out if 8 <= len(out) <= 15 else None


def address_hash(channel: str, address: str) -> str:
    return hashlib.sha256(f"{channel}:{address.strip().lower()}".encode()).hexdigest()


def masked(channel: str, address: str) -> str:
    """For the person who pressed Send: enough to recognise the address, not enough to copy it."""
    if channel == "email" and "@" in address:
        name, _, domain = address.partition("@")
        return f"{name[:2]}***@{domain}"
    return f"***{address[-4:]}" if len(address) > 4 else "***"


@dataclass
class Route:
    channel: str                  # email | whatsapp | none
    why: str = ""                 # for "none": what is missing


@dataclass
class SendOutcome:
    ok: bool
    channel: str = ""
    note: str = ""                # one sentence for the person: where it went, or why it could not be sent
    message_id: Optional[str] = None


class VendorMessenger:
    def __init__(self, app: Any) -> None:
        self.app = app

    @property
    def cfg(self) -> Any:
        return self.app.settings.followup()

    def _contact(self, supplier: str) -> Optional[Any]:
        read = getattr(self.app.erp, "get_supplier_contact", None)
        if read is None:
            return None
        try:
            return read(supplier)
        except Exception as exc:  # noqa: BLE001 - an unreadable contact means "no address", never a guess
            log.warning("could not read the contact of supplier %s: %s", supplier, type(exc).__name__)
            return None

    def _whatsapp(self) -> Optional[Any]:
        return (getattr(self.app, "channels", None) or {}).get("whatsapp")

    def _order(self) -> list[str]:
        return {"email_first": ["email", "whatsapp"], "whatsapp_first": ["whatsapp", "email"], "email_only": ["email"], "whatsapp_only": ["whatsapp"]}[self.cfg.vendor_channel]

    def _usable(self, contact: Optional[Any]) -> list[tuple[str, str]]:
        """(channel, address) in the order the settings prefer, only those that can be used now."""
        if contact is None:
            return []
        out = []
        for ch in self._order():
            if ch == "email" and contact.email and "@" in contact.email and getattr(self.app.erp, "send_email", None) is not None:
                out.append(("email", contact.email.strip()))
            elif ch == "whatsapp" and contact.mobile and self._whatsapp() is not None:
                number = whatsapp_number(contact.mobile, self.cfg.whatsapp_country_code)
                if number:
                    out.append(("whatsapp", number))
        return out

    def route(self, supplier: str) -> Route:
        """How a message to this supplier would go, without saying where to (for the request a person answers)."""
        contact = self._contact(supplier)
        usable = self._usable(contact)
        if usable:
            return Route(usable[0][0])
        if contact is None:
            return Route("none", "there is no e-mail address or mobile number on the supplier's contact in ERPNext")
        missing = []
        if "email" in self._order():
            missing.append("no e-mail address" if not contact.email else "e-mail cannot be sent through this ERP connection")
        if "whatsapp" in self._order():
            missing.append("no mobile number" if not contact.mobile else "WhatsApp is not set up here" if self._whatsapp() is None
                           else "the mobile number has no country code (set one in the follow-up settings)")
        return Route("none", "; ".join(missing))

    def send(self, purpose: str, ref_id: str, supplier: str, subject: str, text: str, doc_type: str, doc_name: str, approval_id: Optional[str], sent_by: str) -> SendOutcome:
        """Send one message to one supplier, trying the channels the settings allow in order. Records the attempt (without the address)."""
        usable = self._usable(self._contact(supplier))
        if not usable:
            return SendOutcome(False, note=f"not sent: {self.route(supplier).why}")
        errors = []
        for channel, address in usable:
            ok, err = self._send_one(channel, address, subject, text, doc_type, doc_name)
            mid = self._record(purpose, ref_id, supplier, channel, address, ok, err, approval_id, sent_by)
            if ok:
                where = "by e-mail through ERPNext" if channel == "email" else "by WhatsApp"
                caveat = ("" if channel == "email" or self.cfg.whatsapp_vendor_template else
                          " (WhatsApp delivers it only if they wrote to you in the last 24 hours; set a vendor template in the follow-up settings to reach them any time)")
                return SendOutcome(True, channel, f"sent {where} to {masked(channel, address)}{caveat}", mid)
            errors.append(f"{channel}: {untrusted(err or 'failed', 160)}")
        return SendOutcome(False, note="not sent: " + "; ".join(errors))

    def _send_one(self, channel: str, address: str, subject: str, text: str, doc_type: str, doc_name: str) -> tuple[bool, Optional[str]]:
        try:
            if channel == "email":
                res = self.app.erp.send_email([address], subject, text, doc_type, doc_name)
                return res.ok, res.error
            wa = self._whatsapp()
            template = self.cfg.whatsapp_vendor_template
            if template:
                res = wa.send_template_text(address, template, text)
                if res.ok:
                    return True, None
                log.warning("the WhatsApp vendor template could not be sent (%s); trying a normal message", untrusted(res.error or "", 160))
            res = wa.send_text(address, text)
            return res.ok, res.error
        except Exception as exc:  # noqa: BLE001
            return False, type(exc).__name__

    def _record(self, purpose: str, ref_id: str, supplier: str, channel: str, address: str, ok: bool, error: Optional[str], approval_id: Optional[str], sent_by: str) -> str:
        mid = short_id(10)
        self.app.db.execute("INSERT INTO vendor_messages(id, purpose, ref_id, supplier, channel, address_hash, status, error, approval_id, sent_by, sent_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (mid, purpose, ref_id, supplier, channel, address_hash(channel, address), "sent" if ok else "failed", untrusted(error, 300) if error else None,
                             approval_id, sent_by, iso(self.app.clock())))
        return mid

    def sent(self, purpose: str, ref_id: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.app.db.query("SELECT id, supplier, channel, status, error, approval_id, sent_at FROM vendor_messages WHERE purpose=? AND ref_id=? ORDER BY sent_at",
                                                     (purpose, ref_id))]

    def replies(self, purpose: str, ref_id: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.app.db.query("SELECT supplier, channel, text, received_at FROM vendor_replies WHERE purpose=? AND ref_id=? ORDER BY id", (purpose, ref_id))]

    # ---------------------------------------------------------------- replies (registered with the core's approvals as a handler of unregistered senders)
    def take_reply(self, event: Any, channel: Any) -> bool:
        """A WhatsApp message from a number we wrote to: keep it and pass it to the people who answered the request. Not ours = False (the core answers as usual)."""
        if getattr(event, "channel", "") != "whatsapp" or not event.text:
            return False
        h = address_hash("whatsapp", re.sub(r"\D", "", event.sender or ""))
        since = iso(self.app.clock() - timedelta(days=REPLY_WINDOW_DAYS))
        row = self.app.db.query_one("SELECT * FROM vendor_messages WHERE address_hash=? AND status='sent' AND sent_at>=? ORDER BY sent_at DESC LIMIT 1", (h, since))
        if row is None:
            return False
        text = untrusted(event.text, MAX_REPLY)
        self.app.db.execute("INSERT INTO vendor_replies(message_id, supplier, purpose, ref_id, channel, text, received_at) VALUES (?,?,?,?,?,?,?)",
                            (row["id"], row["supplier"], row["purpose"], row["ref_id"], "whatsapp", text, iso(self.app.clock())))
        what = {"chase": f"about order {row['ref_id']}", "quote": "to your request for a price"}.get(row["purpose"], "")
        note = (" If it is a price, enter it as a Supplier Quotation in ERPNext: the comparison reads prices from there, never from a message." if row["purpose"] == "quote" else
                " If it gives a new delivery date, update the purchase order in ERPNext.")
        if row["approval_id"]:
            self.app.approvals.tell_recipients(row["approval_id"], f"Reply from vendor {row['supplier']} {what} (their words, not checked):\n\"{text}\"{note}")
        log.info("kept a reply from supplier %s (%s %s)", row["supplier"], row["purpose"], row["ref_id"])
        return True
