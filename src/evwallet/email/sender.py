"""Email sender abstraction.

In production, ``get_sender()`` returns an SMTP sender that delivers
to a real relay (Mailgun, Postmark, Amazon SES, your own Postfix,
etc.). In dev / CI with no SMTP configured, it returns a console
sender that logs the email to stderr (so you can see the verification
code in the test output and copy-paste it).

To switch to a real SMTP relay, set:

    EVW_SMTP_HOST=smtp.mailgun.org
    EVW_SMTP_PORT=587
    EVW_SMTP_USERNAME=postmaster@mg.evwallet.hk
    EVW_SMTP_PASSWORD=...
    EVW_SMTP_FROM=hello@evwallet.hk
    EVW_SMTP_TLS=starttls   # or "ssl" or "none"

If any of those are missing in dev, the console sender is used and a
warning is logged at startup.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmailMessage_:
    subject: str
    body_text: str
    from_addr: str
    to_addr: str


class EmailSender(Protocol):
    """Send a single email. Implementations must be idempotent and
    raise on permanent failures (bad address, auth). Transient errors
    (SMTP 4xx) should be retried by the caller or surfaced."""

    def send(self, message: EmailMessage_) -> None: ...


class ConsoleSender:
    """Logs the email to stderr. Used in dev / CI when SMTP isn't set."""

    def send(self, message: EmailMessage_) -> None:
        # Print in a way that stands out in test output
        _log.warning(
            "\n"
            "================ EMAIL (console) ================\n"
            "From:    %s\n"
            "To:      %s\n"
            "Subject: %s\n"
            "\n"
            "%s\n"
            "=================================================",
            message.from_addr,
            message.to_addr,
            message.subject,
            message.body_text,
        )


class SmtpSender:
    """Sends via SMTP. Supports STARTTLS (port 587), SSL (port 465),
    and plaintext (port 25, only for trusted local relays)."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        from_addr: str,
        tls: str,  # "starttls" | "ssl" | "none"
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._from_addr = from_addr
        self._tls = tls

    def send(self, message: EmailMessage_) -> None:
        msg = EmailMessage()
        msg["From"] = message.from_addr
        msg["To"] = message.to_addr
        msg["Subject"] = message.subject
        msg.set_content(message.body_text)

        if self._tls == "ssl":
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(self._host, self._port, context=context) as s:
                if self._username and self._password:
                    s.login(self._username, self._password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(self._host, self._port) as s:
                s.ehlo()
                if self._tls == "starttls":
                    s.starttls(context=ssl.create_default_context())
                    s.ehlo()
                if self._username and self._password:
                    s.login(self._username, self._password)
                s.send_message(msg)


_sender: EmailSender | None = None


def get_sender() -> EmailSender:
    """Return the process-wide email sender, building it on first call.

    In dev / CI with no SMTP configured, returns a :class:`ConsoleSender`
    (logs to stderr). The dev sender means you can see the verification
    code in the logs and copy it manually.
    """
    global _sender
    if _sender is not None:
        return _sender

    from evwallet.config import get_settings

    settings = get_settings()
    host = settings.smtp_host
    from_addr = settings.smtp_from
    if host and from_addr:
        _sender = SmtpSender(
            host=host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            from_addr=from_addr,
            tls=settings.smtp_tls,
        )
        _log.info("email sender: SMTP host=%s port=%d tls=%s", host, settings.smtp_port, settings.smtp_tls)
    else:
        _sender = ConsoleSender()
        _log.warning(
            "email sender: CONSOLE (no EVW_SMTP_HOST/EVW_SMTP_FROM set). "
            "Verification codes will be logged to stderr — fine for dev, "
            "configure SMTP for production."
        )
    return _sender


def reset_sender_for_tests() -> None:
    """Clear the cached sender so tests can re-init with different env."""
    global _sender
    _sender = None
