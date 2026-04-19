"""Email notifications via Amazon SES.

Requires env vars: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION,
SES_FROM_EMAIL (defaults to noreply@doubleu.team).

Threshold is read from WG_NOTIFY_THRESHOLD (float, default 0.9). A match triggers
an email when `score >= WG_NOTIFY_THRESHOLD`.
All sends are fire-and-forget; failures are logged but never raised.

Batching: the matcher calls `send_digest_email` with every high-scoring listing
it has queued for the user — one SES send per flush, rate-limited by the caller.
"""

from __future__ import annotations

import html
import logging
import os
from dataclasses import dataclass
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

_FROM_EMAIL = os.environ.get("SES_FROM_EMAIL", "noreply@doubleu.team")
_NOTIFY_THRESHOLD = float(os.environ.get("WG_NOTIFY_THRESHOLD", "0.9"))


@dataclass(frozen=True)
class DigestItem:
    """One listing entry inside a batched notification email.

    `listing_id` is not rendered in the email body; it exists so the matcher
    can track which listings have already been delivered and avoid queueing
    the same one into a later digest.
    """

    listing_id: str
    listing_title: str
    listing_url: str
    score: float
    match_reasons: list[str]


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
    username: str,
) -> tuple[str, str, str]:
    """Return (subject, plain-text body, HTML body)."""
    pct = round(score * 100)
    subject = f"WG Hunter: {pct}% match – {listing_title or listing_url}"

    reasons = match_reasons[:5]
    text_reasons = (
        "\n".join(f"  ✓ {r}" for r in reasons) if reasons else "  (no highlights)"
    )

    text_body = f"""\
New high-scoring listing found by WG Hunter!

Score: {pct}% {_score_bar(score)}
Listing: {listing_title or "—"}
Link: {listing_url}

Why it scored well:
{text_reasons}

---
User: {username}
Sent by WG Hunter via {_FROM_EMAIL} — you are receiving this because
you set a notification email in your profile.
"""

    safe_title = html.escape(listing_title or "—")
    safe_url = html.escape(listing_url, quote=True)
    safe_username = html.escape(username)
    safe_from = html.escape(_FROM_EMAIL)
    if reasons:
        reasons_html = "".join(
            f"<li style=\"margin:4px 0;\">{html.escape(r)}</li>" for r in reasons
        )
        reasons_block = f"<ul style=\"padding-left:18px;margin:8px 0;\">{reasons_html}</ul>"
    else:
        reasons_block = "<p style=\"color:#6b7280;margin:8px 0;\">No highlights were recorded for this match.</p>"

    bar_width_pct = max(0, min(100, round(score * 100)))
    html_body = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<title>{html.escape(subject)}</title>
</head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#111827;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:24px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.08);">
          <tr>
            <td style="padding:24px 28px 8px 28px;">
              <div style="font-size:13px;letter-spacing:0.08em;text-transform:uppercase;color:#6366f1;font-weight:600;">WG Hunter</div>
              <h1 style="margin:8px 0 4px 0;font-size:22px;line-height:1.3;color:#111827;">New {pct}% match for you</h1>
              <p style="margin:0 0 16px 0;color:#4b5563;font-size:14px;">A fresh wg-gesucht listing just cleared your match threshold.</p>
            </td>
          </tr>
          <tr>
            <td style="padding:0 28px 8px 28px;">
              <div style="background:#eef2ff;border:1px solid #e0e7ff;border-radius:10px;padding:16px;">
                <div style="font-size:13px;color:#4338ca;font-weight:600;margin-bottom:6px;">Match score</div>
                <div style="display:flex;align-items:center;gap:10px;">
                  <div style="flex:1;height:10px;background:#e0e7ff;border-radius:999px;overflow:hidden;">
                    <div style="width:{bar_width_pct}%;height:100%;background:linear-gradient(90deg,#6366f1,#8b5cf6);"></div>
                  </div>
                  <div style="font-weight:700;color:#4338ca;font-size:16px;min-width:48px;text-align:right;">{pct}%</div>
                </div>
              </div>
            </td>
          </tr>
          <tr>
            <td style="padding:16px 28px 0 28px;">
              <div style="font-size:12px;letter-spacing:0.06em;text-transform:uppercase;color:#6b7280;font-weight:600;">Listing</div>
              <div style="font-size:17px;font-weight:600;color:#111827;margin:4px 0 12px 0;">{safe_title}</div>
              <a href="{safe_url}" style="display:inline-block;background:#4f46e5;color:#ffffff;text-decoration:none;font-weight:600;padding:10px 18px;border-radius:8px;font-size:14px;">View on wg-gesucht →</a>
            </td>
          </tr>
          <tr>
            <td style="padding:20px 28px 4px 28px;">
              <div style="font-size:12px;letter-spacing:0.06em;text-transform:uppercase;color:#6b7280;font-weight:600;">Why it scored well</div>
              {reasons_block}
            </td>
          </tr>
          <tr>
            <td style="padding:16px 28px 24px 28px;border-top:1px solid #f3f4f6;">
              <p style="margin:0;color:#6b7280;font-size:12px;line-height:1.5;">
                Sent to {safe_username} by WG Hunter via <strong>{safe_from}</strong>.<br/>
                You are receiving this because you saved a notification email in your profile.
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""
    return subject, text_body, html_body


def send_high_score_alert(
    to_email: str,
    listing_title: str,
    listing_url: str,
    score: float,
    match_reasons: list[str],
    username: str,
) -> None:
    """Send a score-alert email via SES. Logs and swallows all errors."""
    subject, text_body, html_body = _build_body(
        listing_title, listing_url, score, match_reasons, username
    )
    try:
        client = _client()
        client.send_email(
            Source=_FROM_EMAIL,
            Destination={"ToAddresses": [to_email]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {
                    "Text": {"Data": text_body, "Charset": "UTF-8"},
                    "Html": {"Data": html_body, "Charset": "UTF-8"},
                },
            },
        )
        logger.info("Sent score-alert email to %s (score=%.2f, user=%s)", to_email, score, username)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to send score-alert email to %s: %s", to_email, exc)


def notify_if_high_score(
    to_email: Optional[str],
    listing_title: str,
    listing_url: str,
    score: float,
    match_reasons: list[str],
    username: str,
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
        username=username,
    )


def send_test_email(to_email: str) -> None:
    """Quick smoke-test — call from a one-liner to verify SES is wired up."""
    send_high_score_alert(
        to_email=to_email,
        listing_title="Test listing – 2-Zimmer WG in Schwabing",
        listing_url="https://www.wg-gesucht.de/test",
        score=0.91,
        match_reasons=["Price within budget", "5 min commute to TUM", "Furnished"],
        username="test-user",
    )


def _build_digest_body(
    items: list[DigestItem], username: str
) -> tuple[str, str, str]:
    """Return (subject, plain-text body, HTML body) for a batched digest email."""
    n = len(items)
    top_pct = round(max(i.score for i in items) * 100) if items else 0
    if n == 1:
        subject = f"WG Hunter: new {top_pct}% match – {items[0].listing_title or items[0].listing_url}"
    else:
        subject = f"WG Hunter: {n} new matches (top {top_pct}%)"

    lines: list[str] = [
        "WG Hunter found new high-scoring listings for you!",
        "",
        f"Matches: {n} (top score {top_pct}%)",
        "",
    ]
    for item in items:
        pct = round(item.score * 100)
        lines.append(f"• {pct}% {_score_bar(item.score)} – {item.listing_title or '—'}")
        lines.append(f"  {item.listing_url}")
        for r in item.match_reasons[:3]:
            lines.append(f"    ✓ {r}")
        lines.append("")
    lines.append("---")
    lines.append(f"User: {username}")
    lines.append(
        f"Sent by WG Hunter via {_FROM_EMAIL} — you are receiving this because "
        "you set a notification email in your profile."
    )
    text_body = "\n".join(lines)

    safe_username = html.escape(username)
    safe_from = html.escape(_FROM_EMAIL)

    cards: list[str] = []
    for item in items:
        pct = round(item.score * 100)
        bar_width_pct = max(0, min(100, pct))
        safe_title = html.escape(item.listing_title or "—")
        safe_url = html.escape(item.listing_url, quote=True)
        reasons = item.match_reasons[:5]
        if reasons:
            reasons_html = "".join(
                f"<li style=\"margin:3px 0;\">{html.escape(r)}</li>" for r in reasons
            )
            reasons_block = (
                f"<ul style=\"padding-left:18px;margin:6px 0 0 0;color:#374151;font-size:13px;\">{reasons_html}</ul>"
            )
        else:
            reasons_block = ""
        cards.append(
            f"""
            <tr><td style="padding:0 28px 16px 28px;">
              <div style="border:1px solid #e5e7eb;border-radius:10px;padding:14px 16px;">
                <div style="display:flex;align-items:center;gap:10px;">
                  <div style="flex:1;height:8px;background:#e0e7ff;border-radius:999px;overflow:hidden;">
                    <div style="width:{bar_width_pct}%;height:100%;background:linear-gradient(90deg,#6366f1,#8b5cf6);"></div>
                  </div>
                  <div style="font-weight:700;color:#4338ca;font-size:14px;min-width:42px;text-align:right;">{pct}%</div>
                </div>
                <div style="font-size:15px;font-weight:600;color:#111827;margin:8px 0 4px 0;">{safe_title}</div>
                <a href="{safe_url}" style="display:inline-block;color:#4f46e5;text-decoration:none;font-weight:600;font-size:13px;">View on wg-gesucht →</a>
                {reasons_block}
              </div>
            </td></tr>
            """
        )
    cards_html = "".join(cards)
    heading = (
        f"{n} new {'match' if n == 1 else 'matches'} for you"
        if n >= 1
        else "New matches for you"
    )
    html_body = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<title>{html.escape(subject)}</title>
</head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#111827;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:24px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.08);">
          <tr>
            <td style="padding:24px 28px 8px 28px;">
              <div style="font-size:13px;letter-spacing:0.08em;text-transform:uppercase;color:#6366f1;font-weight:600;">WG Hunter</div>
              <h1 style="margin:8px 0 4px 0;font-size:22px;line-height:1.3;color:#111827;">{heading}</h1>
              <p style="margin:0 0 16px 0;color:#4b5563;font-size:14px;">Fresh wg-gesucht listings just cleared your match threshold.</p>
            </td>
          </tr>
          {cards_html}
          <tr>
            <td style="padding:16px 28px 24px 28px;border-top:1px solid #f3f4f6;">
              <p style="margin:0;color:#6b7280;font-size:12px;line-height:1.5;">
                Sent to {safe_username} by WG Hunter via <strong>{safe_from}</strong>.<br/>
                You are receiving this because you saved a notification email in your profile.
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""
    return subject, text_body, html_body


def send_digest_email(
    to_email: str,
    items: Iterable[DigestItem],
    username: str,
) -> bool:
    """Send a single digest email containing every queued match. Returns True on
    a successful SES call. Logs and swallows all errors (returns False)."""
    items_list = list(items)
    if not items_list:
        return False
    subject, text_body, html_body = _build_digest_body(items_list, username)
    try:
        client = _client()
        client.send_email(
            Source=_FROM_EMAIL,
            Destination={"ToAddresses": [to_email]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {
                    "Text": {"Data": text_body, "Charset": "UTF-8"},
                    "Html": {"Data": html_body, "Charset": "UTF-8"},
                },
            },
        )
        logger.info(
            "Sent digest email to %s (n=%d, user=%s, top=%.2f)",
            to_email,
            len(items_list),
            username,
            max(i.score for i in items_list),
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to send digest email to %s: %s", to_email, exc)
        return False
