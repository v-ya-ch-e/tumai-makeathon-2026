"""Email notifications via Amazon SES.

Requires env vars: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION,
SES_FROM_EMAIL (defaults to noreply@doubleu.team).

Threshold is read from WG_NOTIFY_THRESHOLD (float, default 0.7).
All sends are fire-and-forget; failures are logged but never raised.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_FROM_EMAIL = os.environ.get("SES_FROM_EMAIL", "noreply@doubleu.team")
_NOTIFY_THRESHOLD = float(os.environ.get("WG_NOTIFY_THRESHOLD", "0.7"))


def _client():
    import boto3  # lazy import — boto3 is optional at module load time

    return boto3.client(
        "ses",
        region_name=os.environ.get("AWS_DEFAULT_REGION", "eu-central-1"),
    )


def _score_bar(score: float) -> str:
    filled = round(score * 10)
    return "█" * filled + "░" * (10 - filled)


def _build_body(
    listing_title: str,
    listing_url: str,
    score: float,
    match_reasons: list[str],
    hunt_id: str,
) -> tuple[str, str]:
    """Return (subject, plain-text body)."""
    pct = round(score * 100)
    subject = f"WG Hunter: {pct}% match – {listing_title or listing_url}"

    reasons_block = (
        "\n".join(f"  ✓ {r}" for r in match_reasons[:5])
        if match_reasons
        else "  (no highlights)"
    )

    body = f"""\
New high-scoring listing found by WG Hunter!

Score: {pct}% {_score_bar(score)}
Listing: {listing_title or "—"}
Link: {listing_url}

Why it scored well:
{reasons_block}

---
Hunt ID: {hunt_id}
Sent by WG Hunter via noreply@doubleu.team — you are receiving this because
you set a notification email in your hunt profile.
"""
    return subject, body


def send_high_score_alert(
    to_email: str,
    listing_title: str,
    listing_url: str,
    score: float,
    match_reasons: list[str],
    hunt_id: str,
) -> None:
    """Send a score-alert email via SES. Logs and swallows all errors."""
    subject, body = _build_body(listing_title, listing_url, score, match_reasons, hunt_id)
    try:
        client = _client()
        client.send_email(
            Source=_FROM_EMAIL,
            Destination={"ToAddresses": [to_email]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
            },
        )
        logger.info("Sent score-alert email to %s (score=%.2f, hunt=%s)", to_email, score, hunt_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to send score-alert email to %s: %s", to_email, exc)


def notify_if_high_score(
    to_email: Optional[str],
    listing_title: str,
    listing_url: str,
    score: float,
    match_reasons: list[str],
    hunt_id: str,
) -> None:
    """Send alert only when score >= WG_NOTIFY_THRESHOLD and to_email is set."""
    if not to_email:
        return
    if score < _NOTIFY_THRESHOLD:
        return
    send_high_score_alert(
        to_email=to_email,
        listing_title=listing_title,
        listing_url=listing_url,
        score=score,
        match_reasons=match_reasons,
        hunt_id=hunt_id,
    )


def send_test_email(to_email: str) -> None:
    """Quick smoke-test — call from a one-liner to verify SES is wired up."""
    send_high_score_alert(
        to_email=to_email,
        listing_title="Test listing – 2-Zimmer WG in Schwabing",
        listing_url="https://www.wg-gesucht.de/test",
        score=0.91,
        match_reasons=["Price within budget", "5 min commute to TUM", "Furnished"],
        hunt_id="test-hunt-0000",
    )
