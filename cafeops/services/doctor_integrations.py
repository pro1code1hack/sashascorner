"""`cafeops doctor`: the outside services and keys, each one configured / not configured /
MISCONFIGURED.

Split from `doctor.py` because these checks read only settings and files (no database)
and because going live is mostly this list: `docs/loyalty/GO-LIVE.md` sends the owner
here after filling `.env`.

The three words are the contract, and they map onto severities on purpose:

- **configured** -> OK. It is set and, as far as can be checked offline, usable.
- **not configured** -> INFO, or WARN where running without it costs something that
  cannot be recovered later (the QR key). Every integration here has a supported
  "absent" state; absence is a decision, not a fault.
- **MISCONFIGURED** -> WARN. Something is half set, points at a file that is not there
  or cannot be read, or contradicts another setting. This is the dangerous one: the
  feature looks switched on and fails at the moment somebody uses it.

Nothing here contacts a network service. `cafeops wallet doctor --online` does that for
Google; a Telegram or SMTP round trip is a test message, which is the operator's call.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from cafeops.config import settings
from cafeops.services.doctor import DoctorReport, Severity

__all__ = ["check_integrations"]

CONFIGURED = "configured"
NOT_CONFIGURED = "not configured"
MISCONFIGURED = "MISCONFIGURED"

#: A key shorter than this is guessable enough to forge a stamp QR offline.
MIN_QR_KEY_CHARS = 32
#: The pass certificate lasts a year; start nagging a month out (WALLET-SETUP.md).
CERT_WARN_DAYS = 30
_TELEGRAM_TOKEN = re.compile(r"^\d+:[A-Za-z0-9_-]{30,}$")
_SECRET_HINT = "python3 -c 'import secrets; print(secrets.token_urlsafe(48))'"


def _add(r: DoctorReport, sev: Severity, name: str, state: str, detail: str, fix: str = "") -> None:
    r.add(sev, name, f"{state}: {detail}", fix)


def check_integrations(r: DoctorReport) -> None:
    """Every outside service and secret the stack reads, in go-live order."""
    _qr_key(r)
    _public_urls(r)
    _mail(r)
    _sms(r)
    _recovery_summary(r)
    _apple_wallet(r)
    _google_wallet(r)
    _telegram(r)
    _lightspeed(r)
    _auto_stamp(r)


# ---- loyalty -----------------------------------------------------------------------


def _qr_key(r: DoctorReport) -> None:
    key = settings.loyalty_qr_key
    if not key:
        _add(
            r,
            Severity.WARN,
            "loyalty qr key",
            NOT_CONFIGURED,
            "CAFEOPS_LOYALTY_QR_KEY is unset, so card QR codes are signed with a key "
            "derived from the database URL. Moving the database would invalidate every card",
            f"set it BEFORE the first real member joins, and never change it: {_SECRET_HINT}",
        )
    elif len(key) < MIN_QR_KEY_CHARS:
        _add(
            r,
            Severity.WARN,
            "loyalty qr key",
            MISCONFIGURED,
            f"CAFEOPS_LOYALTY_QR_KEY is only {len(key)} characters; anyone who guesses it "
            "can print a card QR that scans",
            "if no real member has joined yet, replace it with a long random value: "
            f"{_SECRET_HINT}. After launch changing it voids every card, so weigh that first",
        )
    else:
        _add(r, Severity.OK, "loyalty qr key", CONFIGURED, "set, long enough")


def _is_local(host: str) -> bool:
    return host in {"127.0.0.1", "::1"} or host == "localhost" or host.endswith(".localhost")


def _origin_problem(url: str) -> str | None:
    parts = urlsplit(url)
    local = _is_local(parts.hostname or "")
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return f"{url!r} is not an absolute http(s) URL"
    if parts.scheme != "https" and not local:
        return f"{url!r} is plain http: Apple Wallet only calls an https webServiceURL"
    if parts.path not in {"", "/"} or parts.query:
        return f"{url!r} carries a path; it must be the bare origin (https://host)"
    return None


def _public_urls(r: DoctorReport) -> None:
    """Card links, recovery links, the pass's webServiceURL and image URLs all hang off
    these. They must be the public site's origin, and they must agree."""
    from cafeops.integrations.wallet.config import WalletSettings

    loyalty = settings.loyalty_public_url.rstrip("/")
    wallet = (WalletSettings().public_url or "").rstrip("/")
    site = settings.site_public_url.rstrip("/")
    problems = [p for p in (_origin_problem(loyalty), _origin_problem(wallet)) if p]
    host = urlsplit(loyalty).hostname
    domain = (os.environ.get("SITE_DOMAIN") or "").strip()
    if domain and host != domain:
        problems.append(
            f"CAFEOPS_LOYALTY_PUBLIC_URL's host {host!r} is not SITE_DOMAIN {domain!r}, "
            "so card and wallet links point away from the site Caddy serves"
        )
    if wallet != loyalty:
        problems.append(
            f"CAFEOPS_WALLET_PUBLIC_URL {wallet!r} differs from CAFEOPS_LOYALTY_PUBLIC_URL "
            f"{loyalty!r}; passes would call one host and the site would live on another"
        )
    site_host = urlsplit(site).hostname or ""
    # A dev checkout's SITE_PUBLIC_URL is the Astro dev server; only a real host counts.
    if site_host != host and not _is_local(site_host) and not _is_local(host or ""):
        problems.append(f"SITE_PUBLIC_URL {site!r} and the loyalty URL {loyalty!r} disagree")
    if problems:
        _add(
            r,
            Severity.WARN,
            "public url",
            MISCONFIGURED,
            "; ".join(problems),
            "set SITE_DOMAIN only and leave CAFEOPS_LOYALTY_PUBLIC_URL, "
            "CAFEOPS_WALLET_PUBLIC_URL and SITE_PUBLIC_URL unset -- docker-compose.yml "
            "derives all three as https://$SITE_DOMAIN",
        )
        return
    _add(
        r,
        Severity.OK,
        "public url",
        CONFIGURED,
        f"{loyalty} (cards, recovery links, wallet web service {wallet}/wallet/apple)",
    )


def _mail(r: DoctorReport) -> None:
    s = settings
    fields = {
        "CAFEOPS_SMTP_HOST": s.smtp_host,
        "CAFEOPS_SMTP_FROM": s.smtp_from,
        "CAFEOPS_SMTP_USER": s.smtp_user,
        "CAFEOPS_SMTP_PASSWORD": s.smtp_password,
    }
    if not any(fields.values()):
        _add(
            r,
            Severity.INFO,
            "smtp",
            NOT_CONFIGURED,
            "no outbound email: recovery codes and campaign emails are not sent by mail",
            "optional: set CAFEOPS_SMTP_HOST/PORT/USER/PASSWORD/FROM from your mail provider",
        )
        return
    missing = [k for k in ("CAFEOPS_SMTP_HOST", "CAFEOPS_SMTP_FROM") if not fields[k]]
    if bool(s.smtp_user) != bool(s.smtp_password):
        missing.append("CAFEOPS_SMTP_PASSWORD" if s.smtp_user else "CAFEOPS_SMTP_USER")
    if s.smtp_from and "@" not in s.smtp_from:
        missing.append("CAFEOPS_SMTP_FROM (not an email address)")
    if missing:
        _add(
            r,
            Severity.WARN,
            "smtp",
            MISCONFIGURED,
            f"partly set; missing or wrong: {', '.join(missing)}",
            "set all of host, from, and user+password together (port defaults to 587)",
        )
        return
    _add(r, Severity.OK, "smtp", CONFIGURED, f"{s.smtp_host}:{s.smtp_port} as {s.smtp_from}")


def _sms(r: DoctorReport) -> None:
    s = settings
    fields = {
        "CAFEOPS_TWILIO_ACCOUNT_SID": s.twilio_account_sid,
        "CAFEOPS_TWILIO_AUTH_TOKEN": s.twilio_auth_token,
        "CAFEOPS_TWILIO_FROM_NUMBER": s.twilio_from_number,
    }
    if not any(fields.values()):
        _add(
            r,
            Severity.INFO,
            "twilio",
            NOT_CONFIGURED,
            "no SMS: recovery codes go by email or staff",
        )
        return
    missing = [k for k, v in fields.items() if not v]
    if s.twilio_account_sid and not s.twilio_account_sid.startswith("AC"):
        missing.append("CAFEOPS_TWILIO_ACCOUNT_SID (an Account SID starts with AC)")
    if s.twilio_from_number and not s.twilio_from_number.startswith("+"):
        missing.append("CAFEOPS_TWILIO_FROM_NUMBER (needs E.164, e.g. +447...)")
    if missing:
        _add(
            r,
            Severity.WARN,
            "twilio",
            MISCONFIGURED,
            f"partly set; missing or wrong: {', '.join(missing)}",
            "set all three from the Twilio console, or none",
        )
        return
    _add(r, Severity.OK, "twilio", CONFIGURED, f"SMS from {s.twilio_from_number}")


def _recovery_summary(r: DoctorReport) -> None:
    if settings.smtp_configured or settings.twilio_configured:
        return
    r.add(
        Severity.INFO,
        "loyalty recovery",
        "with neither SMTP nor Twilio, a customer who loses their phone recovers the card "
        "at the till (scanner > Find member). Supported, just slower",
    )


# ---- wallets ------------------------------------------------------------------------


def _unreadable(path: Path | None) -> str | None:
    """None when `path` is a readable file; else why not (mount, typo, permissions)."""
    if path is None:
        return None
    if not path.is_file():
        return f"{path} does not exist (is ./secrets/wallet mounted, and the name right?)"
    try:
        with path.open("rb") as fh:
            fh.read(1)
    except OSError as exc:
        return (
            f"{path} cannot be read ({exc.strerror}); in docker the app runs as uid 1000, "
            "so `sudo chown -R 1000:1000 secrets/wallet`"
        )
    return None


def _cert_expiry(path: Path) -> tuple[datetime, str]:
    from cryptography import x509

    data = path.read_bytes()
    cert = (
        x509.load_pem_x509_certificate(data)
        if b"-----BEGIN" in data
        else x509.load_der_x509_certificate(data)
    )
    return cert.not_valid_after_utc, cert.subject.rfc4514_string()


def _apple_wallet(r: DoctorReport) -> None:
    from cafeops.integrations.wallet.config import WalletSettings

    cfg = WalletSettings()
    ids = {
        "CAFEOPS_WALLET_APPLE_PASS_TYPE_ID": cfg.apple_pass_type_id,
        "CAFEOPS_WALLET_APPLE_TEAM_ID": cfg.apple_team_id,
    }
    if not any(ids.values()):
        # Default file paths (docker-compose.yml points them into /secrets/wallet) are
        # not intent: without the two ids nobody has started on Apple yet.
        _add(
            r,
            Severity.INFO,
            "apple wallet",
            NOT_CONFIGURED,
            "no 'Add to Apple Wallet' button; iPhone users keep the web card",
            "docs/loyalty/WALLET-SETUP.md, then drop pass.pem, pass.key, wwdr.pem in "
            "secrets/wallet/ and set the two ids",
        )
        return
    problems = [f"{k} unset" for k, v in ids.items() if not v]
    files = {
        "CAFEOPS_WALLET_APPLE_CERT_PATH": cfg.apple_cert_path,
        "CAFEOPS_WALLET_APPLE_KEY_PATH": cfg.apple_key_path,
        "CAFEOPS_WALLET_APPLE_WWDR_PATH": cfg.apple_wwdr_path,
    }
    for env, path in files.items():
        if path is None:
            problems.append(f"{env} unset")
        elif (why := _unreadable(path)) is not None:
            problems.append(f"{env}: {why}")
    if not problems:
        try:
            from cafeops.integrations.wallet.apple import signing_material

            material = signing_material(cfg)
            subject = material.cert.subject.rfc4514_string()
            if cfg.apple_pass_type_id and cfg.apple_pass_type_id not in subject:
                problems.append("the certificate is not for this Pass Type ID")
            if cfg.apple_team_id and f"OU={cfg.apple_team_id}" not in subject:
                problems.append("the certificate's team (OU) is not the Team ID")
        except Exception as exc:  # a bad key password, a PEM that is not a key, ...
            problems.append(f"signing material does not load: {type(exc).__name__}: {exc}")
    if problems:
        _add(
            r,
            Severity.WARN,
            "apple wallet",
            MISCONFIGURED,
            "; ".join(problems),
            "`cafeops wallet doctor` has the detail; docs/loyalty/WALLET-SETUP.md steps 4-6",
        )
        return
    assert cfg.apple_cert_path is not None and cfg.apple_wwdr_path is not None
    notes: list[str] = []
    severity = Severity.OK
    for label, path in (("pass certificate", cfg.apple_cert_path), ("WWDR", cfg.apple_wwdr_path)):
        expires, _subject = _cert_expiry(path)
        days = (expires - datetime.now(UTC)).days
        notes.append(f"{label} expires {expires:%Y-%m-%d} ({days} days)")
        if days < CERT_WARN_DAYS:
            severity = Severity.WARN
    sandbox = " APNs SANDBOX (development iPhones only)" if cfg.apple_apns_use_sandbox else ""
    _add(
        r,
        severity,
        "apple wallet",
        CONFIGURED if severity is Severity.OK else f"{CONFIGURED}, EXPIRING",
        f"{cfg.apple_pass_type_id}, team {cfg.apple_team_id}; {'; '.join(notes)}{sandbox}",
        "" if severity is Severity.OK else "renew: WALLET-SETUP.md 'Renewal', then restart",
    )


def _google_wallet(r: DoctorReport) -> None:
    from cafeops.integrations.wallet.config import WalletSettings

    cfg = WalletSettings()
    if not cfg.google_issuer_id:
        _add(
            r,
            Severity.INFO,
            "google wallet",
            NOT_CONFIGURED,
            "no 'Add to Google Wallet' button; Android users keep the web card",
            "docs/loyalty/WALLET-SETUP.md 'Google Wallet', then drop google-sa.json in "
            "secrets/wallet/ and set CAFEOPS_WALLET_GOOGLE_ISSUER_ID",
        )
        return
    problems: list[str] = []
    if not cfg.google_issuer_id.isdigit():
        problems.append("CAFEOPS_WALLET_GOOGLE_ISSUER_ID is not the numeric issuer id")
    path = cfg.google_service_account_path
    if path is None:
        problems.append("CAFEOPS_WALLET_GOOGLE_SERVICE_ACCOUNT_PATH unset")
    elif (why := _unreadable(path)) is not None:
        problems.append(f"CAFEOPS_WALLET_GOOGLE_SERVICE_ACCOUNT_PATH: {why}")
    else:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not (data.get("client_email") and data.get("private_key")):
                problems.append(f"{path} is not a service-account key (no client_email/key)")
        except ValueError:
            problems.append(f"{path} is not JSON")
    if problems:
        _add(
            r,
            Severity.WARN,
            "google wallet",
            MISCONFIGURED,
            "; ".join(problems),
            "`cafeops wallet doctor --online` checks the credentials against Google",
        )
        return
    _add(
        r,
        Severity.OK,
        "google wallet",
        CONFIGURED,
        f"issuer {cfg.google_issuer_id}, class {cfg.google_class_id}. The class must be "
        "created (`cafeops wallet google-class-sync`) and approved by Google",
    )


# ---- ops integrations ---------------------------------------------------------------


def _telegram(r: DoctorReport) -> None:
    token, chat = settings.telegram_bot_token, settings.telegram_owner_chat_id
    if not token and chat is None:
        _add(
            r,
            Severity.INFO,
            "telegram",
            NOT_CONFIGURED,
            "no bot: alerts, the 19:30 loyalty summary and fraud alerts are logged, "
            "not sent. `cafeops bot-preview` renders every flow locally",
            "optional: a token from @BotFather and the owner's chat id",
        )
        return
    problems: list[str] = []
    if not token:
        problems.append("CAFEOPS_TELEGRAM_BOT_TOKEN unset")
    elif not _TELEGRAM_TOKEN.match(token):
        problems.append("CAFEOPS_TELEGRAM_BOT_TOKEN does not look like <digits>:<secret>")
    if chat is None:
        problems.append("CAFEOPS_TELEGRAM_OWNER_CHAT_ID unset")
    if problems:
        _add(
            r,
            Severity.WARN,
            "telegram",
            MISCONFIGURED,
            "; ".join(problems) + ". Both are needed; `bot-run` exits and nothing is sent",
            "set both, then `docker compose up -d bot scheduler`",
        )
        return
    _add(r, Severity.OK, "telegram", CONFIGURED, f"owner chat {chat}")


def _lightspeed(r: DoctorReport) -> None:
    s = settings
    fields = {
        "CAFEOPS_LIGHTSPEED_CLIENT_ID": s.lightspeed_client_id,
        "CAFEOPS_LIGHTSPEED_CLIENT_SECRET": s.lightspeed_client_secret,
        "CAFEOPS_LIGHTSPEED_REFRESH_TOKEN": s.lightspeed_refresh_token,
        "CAFEOPS_LIGHTSPEED_BUSINESS_ID": s.lightspeed_business_id,
    }
    if not any(fields.values()):
        _add(
            r,
            Severity.INFO,
            "lightspeed",
            NOT_CONFIGURED,
            "running on fixtures. Sales are whatever was seeded or imported by hand",
        )
        return
    missing = [k for k, v in fields.items() if not v]
    if missing:
        _add(
            r,
            Severity.WARN,
            "lightspeed",
            MISCONFIGURED,
            f"partly set; missing {', '.join(missing)}. The client needs all four, so the "
            "nightly sync still replays fixtures",
            "set all four from the Lightspeed developer portal, or none",
        )
        return
    _add(r, Severity.OK, "lightspeed", CONFIGURED, f"business {s.lightspeed_business_id}")


def _auto_stamp(r: DoctorReport) -> None:
    """Lightspeed auto-stamping (SPEC Phase 3, `CAFEOPS_LOYALTY_AUTO_STAMP`). Read by
    attribute so the check still runs on a build that predates the setting."""
    enabled: bool | None = getattr(settings, "loyalty_auto_stamp", None)
    if enabled is None:
        r.add(
            Severity.INFO,
            "loyalty auto-stamp",
            f"{NOT_CONFIGURED}: this build has no Lightspeed auto-stamping; staff stamp "
            "every drink from the scanner",
        )
        return
    if not enabled:
        _add(
            r,
            Severity.INFO,
            "loyalty auto-stamp",
            NOT_CONFIGURED,
            "CAFEOPS_LOYALTY_AUTO_STAMP is off; staff stamp every drink from the scanner",
            "turn it on only once the till attaches a customer to sales",
        )
        return
    if not settings.lightspeed_configured:
        _add(
            r,
            Severity.WARN,
            "loyalty auto-stamp",
            MISCONFIGURED,
            "CAFEOPS_LOYALTY_AUTO_STAMP is on but Lightspeed is not configured, so no sale "
            "can add a stamp",
            "set the four CAFEOPS_LIGHTSPEED_* credentials, or turn auto-stamp off",
        )
        return
    window = getattr(settings, "loyalty_pos_dedupe_minutes", None)
    _add(
        r,
        Severity.OK,
        "loyalty auto-stamp",
        CONFIGURED,
        "receipts that name a till customer add stamps"
        + (f"; a scan within {window} min of the receipt counts once" if window else ""),
    )
