"""
mailer.py — Sends account setup and password-reset emails via Gmail SMTP.

Configuration is read from environment variables (set them in .env):
    GMAIL_ADDRESS       — the Gmail address used to send mail (e.g. notifications@drogoaerospace.com or a gmail.com address)
    GMAIL_APP_PASSWORD  — a Gmail App Password (NOT the normal account password)
    MAIL_SENDER_NAME    — friendly display name shown to recipients (defaults to "Drogo Aerospace")

To generate a Gmail App Password: Google Account -> Security -> 2-Step Verification
must be ON -> App Passwords -> create one for "Mail".

If GMAIL_ADDRESS / GMAIL_APP_PASSWORD are not configured, send_welcome_email()
logs a warning and returns False instead of raising, so user creation never
fails just because mail isn't set up yet.
"""
import os
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

GMAIL_ADDRESS      = os.environ.get('GMAIL_ADDRESS', '')
GMAIL_APP_PASSWORD = os.environ.get('GMAIL_APP_PASSWORD', '')
SENDER_NAME         = os.environ.get('MAIL_SENDER_NAME', 'Drogo Aerospace')

SMTP_HOST = 'smtp.gmail.com'
SMTP_PORT = 587


def _is_configured() -> bool:
    return bool(GMAIL_ADDRESS and GMAIL_APP_PASSWORD)


# ── Startup visibility ──────────────────────────────────────────────────────
# Prints a clear, unmissable line in the server console at import time so it's
# obvious whether mail is ready, instead of only finding out via a failed popup.
if _is_configured():
    print(f"[mailer] Gmail sender configured: {GMAIL_ADDRESS} (welcome emails ENABLED)")
else:
    print(
        "[mailer] WARNING: GMAIL_ADDRESS / GMAIL_APP_PASSWORD are not set in .env. "
        "Welcome emails are DISABLED — Admin will receive a one-time setup-link fallback. "
        "Set both values in .env and restart the app to enable real emails."
    )


def send_welcome_email(to_email: str, username: str, setup_url: str) -> bool:
    """
    Sends a one-time password-setup link to a newly created user. Returns True
    if the email was sent, False otherwise
    (missing config or send failure — never raises, so it never blocks user
    creation in the UI).
    """
    if not _is_configured():
        logger.warning(
            "Mail not configured: set GMAIL_ADDRESS and GMAIL_APP_PASSWORD "
            "in .env to enable welcome emails. Skipping email to %s.", to_email
        )
        return False

    from html import escape
    safe_username = escape(username)
    safe_email = escape(to_email)
    safe_setup_url = escape(setup_url, quote=True)
    subject = "Set up your Drogo Aerospace account"

    text_body = f"""Welcome, {username}!

Your account has been created on the Drogo Aerospace platform.

Set your password using this one-time link:
{setup_url}

This link expires in 24 hours. No temporary password has been created or sent.

Welcome aboard — we're glad to have you with us.

Regards,
Team Drogo Aerospace
"""

    html_body = f"""\
<html>
  <body style="margin:0;padding:0;background:#f4f5f7;font-family:Segoe UI,Arial,sans-serif;">
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7;padding:32px 0;">
      <tr>
        <td align="center">
          <table width="480" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e4e6ea;">
            <tr>
              <td style="background:#3f4249;padding:22px 32px;">
                <span style="color:#ffffff;font-size:17px;font-weight:700;letter-spacing:.04em;">DROGO AEROSPACE</span>
              </td>
            </tr>
            <tr>
              <td style="padding:32px;">
                <h2 style="margin:0 0 6px;color:#1a1c20;font-size:20px;">Welcome, {safe_username}!</h2>
                <p style="margin:0 0 20px;color:#5a5f68;font-size:14px;line-height:1.6;">
                  Your account on the Drogo Aerospace platform has been created. Set your password to get started.
                </p>
                <table width="100%" cellpadding="0" cellspacing="0" style="background:#f7f8fa;border:1px solid #e4e6ea;border-radius:8px;margin-bottom:20px;">
                  <tr>
                    <td style="padding:16px 20px;">
                      <p style="margin:0 0 8px;font-size:12px;color:#8a8f99;text-transform:uppercase;letter-spacing:.06em;">Email</p>
                      <p style="margin:0;font-size:14px;color:#1a1c20;font-weight:600;">{safe_email}</p>
                    </td>
                  </tr>
                </table>
                <p style="margin:0 0 20px;color:#5a5f68;font-size:13px;line-height:1.6;">
                  This one-time link expires in 24 hours. No temporary password is sent by email.
                </p>
                <p style="margin:0 0 24px;"><a href="{safe_setup_url}" style="display:inline-block;background:#3f4249;color:#ffffff;text-decoration:none;font-size:13.5px;font-weight:600;padding:11px 22px;border-radius:8px;">Set My Password</a></p>
                <p style="margin:0;color:#8a8f99;font-size:12.5px;line-height:1.6;">
                  Welcome aboard — we're glad to have you with us.<br>
                  Regards,<br>Team Drogo Aerospace
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

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = f"{SENDER_NAME} <{GMAIL_ADDRESS}>"
    msg['To']      = to_email
    msg.attach(MIMEText(text_body, 'plain'))
    msg.attach(MIMEText(html_body, 'html'))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, [to_email], msg.as_string())
        logger.info("Welcome email sent to %s", to_email)
        print(f"[mailer] Welcome email sent to {to_email}")
        return True
    except smtplib.SMTPAuthenticationError as e:
        print(
            f"[mailer] AUTH FAILED sending to {to_email}: {e}\n"
            f"[mailer] -> Check GMAIL_ADDRESS is correct and GMAIL_APP_PASSWORD is a valid "
            f"16-character App Password (not your normal Gmail password)."
        )
        logger.exception("Gmail auth failed sending to %s", to_email)
        return False
    except Exception as e:
        print(f"[mailer] FAILED sending to {to_email}: {e}")
        logger.exception("Failed to send welcome email to %s", to_email)
        return False


def send_password_reset_email(to_email: str, username: str, reset_url: str) -> bool:
    """
    Sends a password-reset link to a user who requested one via
    "Forgot password?" on the login page. Same never-raises contract as
    send_welcome_email — returns False on missing config or send failure.
    """
    if not _is_configured():
        logger.warning(
            "Mail not configured: set GMAIL_ADDRESS and GMAIL_APP_PASSWORD "
            "in .env to enable password reset emails. Skipping email to %s.", to_email
        )
        return False

    from html import escape
    safe_username = escape(username)
    safe_reset_url = escape(reset_url, quote=True)
    subject = "Reset your Drogo Aerospace password"

    text_body = f"""Hi {username},

We received a request to reset your Drogo Aerospace account password.

Reset your password here: {reset_url}

This link expires in 1 hour. If you didn't request this, you can safely ignore this email — your password won't be changed.

Regards,
Team Drogo Aerospace
"""

    html_body = f"""\
<html>
  <body style="margin:0;padding:0;background:#f4f5f7;font-family:Segoe UI,Arial,sans-serif;">
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7;padding:32px 0;">
      <tr>
        <td align="center">
          <table width="480" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e4e6ea;">
            <tr>
              <td style="background:#3f4249;padding:22px 32px;">
                <span style="color:#ffffff;font-size:17px;font-weight:700;letter-spacing:.04em;">DROGO AEROSPACE</span>
              </td>
            </tr>
            <tr>
              <td style="padding:32px;">
                <h2 style="margin:0 0 6px;color:#1a1c20;font-size:20px;">Reset your password</h2>
                <p style="margin:0 0 24px;color:#5a5f68;font-size:14px;line-height:1.6;">
                  Hi {safe_username}, we received a request to reset your Drogo Aerospace account password. Click below to choose a new one.
                </p>
                <p style="margin:0 0 24px;"><a href="{safe_reset_url}" style="display:inline-block;background:#3f4249;color:#ffffff;text-decoration:none;font-size:13.5px;font-weight:600;padding:11px 22px;border-radius:8px;">Reset Password</a></p>
                <p style="margin:0 0 8px;color:#8a8f99;font-size:12.5px;line-height:1.6;">
                  This link expires in 1 hour. If you didn't request this, you can safely ignore this email — your password won't be changed.
                </p>
                <p style="margin:0;color:#8a8f99;font-size:12.5px;line-height:1.6;">
                  Regards,<br>Team Drogo Aerospace
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

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = f"{SENDER_NAME} <{GMAIL_ADDRESS}>"
    msg['To']      = to_email
    msg.attach(MIMEText(text_body, 'plain'))
    msg.attach(MIMEText(html_body, 'html'))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, [to_email], msg.as_string())
        logger.info("Password reset email sent to %s", to_email)
        print(f"[mailer] Password reset email sent to {to_email}")
        return True
    except smtplib.SMTPAuthenticationError as e:
        print(f"[mailer] AUTH FAILED sending to {to_email}: {e}")
        logger.exception("Gmail auth failed sending to %s", to_email)
        return False
    except Exception as e:
        print(f"[mailer] FAILED sending to {to_email}: {e}")
        logger.exception("Failed to send password reset email to %s", to_email)
        return False
