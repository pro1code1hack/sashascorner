"""Outbound email (SMTP) and SMS (Twilio), for recovery codes and campaign emails.

Both are optional (CONTRACT §0): with neither configured, recovery answers `ask_staff` and
campaigns reach wallets only. Nothing here runs inside a database transaction -- the
services return an `Outgoing` and the caller sends it after commit, because holding
SQLite's single writer across an SMTP handshake would stall every till.

Failures are logged and swallowed by `deliver`: a recovery code that did not go out is
answered by the customer asking again, and a campaign email that bounced must not undo
the wallet message that did go.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

import httpx

from cafeops.config import settings

__all__ = ["Outgoing", "deliver", "deliver_all", "send_email", "send_sms"]

log = logging.getLogger("cafeops.loyalty.messaging")


@dataclass(frozen=True, slots=True)
class Outgoing:
    """One message to send after commit. `channel` is "email" or "sms"."""

    channel: str
    to: str
    subject: str
    body: str
    #: RFC 8058 one-click unsubscribe target, for marketing email only.
    unsubscribe_url: str | None = None


def send_email(to: str, subject: str, body: str, *, unsubscribe_url: str | None = None) -> None:
    if not settings.smtp_configured:
        raise RuntimeError("SMTP is not configured")
    host = settings.smtp_host or ""
    msg = EmailMessage()
    msg["From"] = settings.smtp_from or ""
    msg["To"] = to
    msg["Subject"] = subject
    if unsubscribe_url:
        # Mail clients show their own "unsubscribe" button from these; PECR wants
        # unsubscribing to be simple, and this is the simplest there is.
        msg["List-Unsubscribe"] = f"<{unsubscribe_url}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg.set_content(body)
    context = ssl.create_default_context()
    if settings.smtp_port == 465:
        with smtplib.SMTP_SSL(host, settings.smtp_port, context=context, timeout=20) as smtp:
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password or "")
            smtp.send_message(msg)
        return
    with smtplib.SMTP(host, settings.smtp_port, timeout=20) as smtp:
        smtp.starttls(context=context)
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password or "")
        smtp.send_message(msg)


def send_sms(to: str, body: str) -> None:
    if not settings.twilio_configured:
        raise RuntimeError("Twilio is not configured")
    sid = settings.twilio_account_sid or ""
    response = httpx.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        auth=(sid, settings.twilio_auth_token or ""),
        data={"To": to, "From": settings.twilio_from_number or "", "Body": body},
        timeout=20,
    )
    response.raise_for_status()


def deliver(item: Outgoing) -> bool:
    """Send one message. True on success; failures are logged, never raised."""
    try:
        if item.channel == "email":
            send_email(item.to, item.subject, item.body, unsubscribe_url=item.unsubscribe_url)
        elif item.channel == "sms":
            send_sms(item.to, item.body)
        else:
            raise ValueError(f"unknown channel {item.channel!r}")
    except Exception as exc:
        # The address is personal data; the log gets the channel and the error only.
        log.warning("loyalty %s send failed: %s", item.channel, exc)
        return False
    return True


def deliver_all(items: list[Outgoing]) -> int:
    return sum(1 for item in items if deliver(item))
