"""SendGrid delivery for the digest email.

Talks to the v3 API with `requests` (already a dependency) rather than the
SendGrid SDK — one HTTP call, no extra package. The From address must be a
verified sender in the SendGrid account; errors surface as DigestSendError
with the API's response body so cron logs capture the reason.
"""

import requests

SENDGRID_ENDPOINT = "https://api.sendgrid.com/v3/mail/send"
DEFAULT_TIMEOUT_SECONDS = 15.0


class DigestError(RuntimeError):
    """Digest could not be produced (config, data, or delivery failure)."""


class DigestSendError(DigestError):
    """SendGrid rejected or could not be reached for the send."""


def send_digest_email(
    *,
    api_key: str | None,
    from_email: str,
    recipients: list[str],
    subject: str,
    html: str,
    from_name: str = "L&SD Beverage Dashboard",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> None:
    """Send one HTML email through SendGrid. Raises DigestSendError on failure.

    Configuration problems (missing key, no recipients) raise before any
    network call so misconfiguration is distinguishable from a SendGrid outage.
    """
    if not api_key:
        raise DigestSendError("SENDGRID_API_KEY is not set — cannot send the digest.")
    if not recipients:
        raise DigestSendError(
            "No recipients configured — set DIGEST_RECIPIENTS (comma-separated emails)."
        )

    payload = {
        "personalizations": [{"to": [{"email": r} for r in recipients]}],
        "from": {"email": from_email, "name": from_name},
        "subject": subject,
        "content": [{"type": "text/html", "value": html}],
    }
    try:
        response = requests.post(
            SENDGRID_ENDPOINT,
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise DigestSendError(f"SendGrid request failed: {exc}") from exc

    if response.status_code >= 400:
        raise DigestSendError(
            f"SendGrid rejected the send (HTTP {response.status_code}): "
            f"{response.text[:500]}"
        )


__all__ = ["DigestError", "DigestSendError", "send_digest_email"]
