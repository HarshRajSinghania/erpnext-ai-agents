"""Shared handling of user actions arriving from any channel (webhook or polling)."""
from __future__ import annotations

import logging
from typing import Any, Optional

from .channels.base import Channel
from .models import DecisionResult, InboundEvent
from .render import render_plain
from ..core.common import untrusted
from .router import ApprovalRouter
from .service import ApprovalService

log = logging.getLogger(__name__)


def process_inbound(event: InboundEvent, router: ApprovalRouter, service: ApprovalService, conversation: Optional[Any] = None) -> Optional[DecisionResult]:
    """`conversation` (optional) lets a registered person type to the assistant: questions, a reply to a request, the answer to "how many?"."""
    channel: Optional[Channel] = router.channels.get(event.channel)
    if channel is None:
        return None
    recipient = router.recipient_for(event.channel, event.sender)
    if event.text and not event.approval_id and channel.handle_command(event, recipient is not None):
        return None
    if recipient is None:
        log.warning("ignored %s action from unknown sender %s", untrusted(event.channel, 20), untrusted(event.sender, 40))
        channel.acknowledge(event, "You are not registered to answer these requests.")
        return None

    if event.approval_id and event.action:
        actor = router.actor_for(recipient, via=event.channel)
        result = service.decide(event.approval_id, event.action, actor, event.channel, event.params, address=event.sender, defer=True)
        if result.status == "info":
            channel.send_text(event.sender, render_plain(result.approval.request, sections=result.info_sections, with_title=False))
            channel.acknowledge(event, "Details sent")
        elif result.status == "need_input":                           # e.g. Change quantity: ask, and take the person's next message as the answer
            channel.send_text(event.sender, result.prompt or result.message)
            if conversation is not None:
                conversation.expect(event.channel, event.sender, event.approval_id, event.action, result.choices)
            if event.ack_token:                                           # a button press with a spinner to dismiss (Telegram); the question itself is already sent
                channel.acknowledge(event, "Type your answer in this chat")
        else:
            channel.acknowledge(event, result.message)
        if result.after is not None:                                  # a revised request follows the reply that says it is on its way, and comes last
            result.after()
        return result

    if event.text:
        if conversation is not None:
            conversation.submit(event, recipient, channel)
        else:
            channel.acknowledge(event, "Please answer with the buttons under the request message (or in the app).")
    return None
