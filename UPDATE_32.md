# Update 32 — Client workspace and account email

Version: 1.32.0. Cumulative from Update 31. This release implements the requested Client UI and email changes; it does not claim to complete the separate 49-item audit backlog.

1. **Client dashboard:** cards stay within the available width. Defects by Type has its own horizontal scrollbar and keyboard-focusable chart region. Header, sidebar and adjacent cards no longer move sideways with that chart. Vertical scrolling remains inside the dashboard content so all projects and cards stay reachable.
2. **Client image viewer:** RGB defect details now appear in a left-hand table beside the image, including component, defect type, position, severity, condition, resolution, author, date and comments. Details and history remain escaped and read-only. Image zoom/pan and previous/next behavior are preserved. Thermal measurement controls remain separate.
3. **Tower-wise overview:** viewport height is bounded. The left tower list scrolls independently. Header and line selector stay fixed. Long defect tables and AI summaries scroll inside their own panels instead of growing the entire page.
4. **New-user emails:** creating a user automatically attempts the one-time password setup email. User Management now reports whether a sender is configured and distinguishes missing configuration from delivery failure. Sender configuration is read when sending, and both welcome and reset messages use the same configuration. Failed delivery preserves the account and gives Admin a one-time setup-link fallback.

## Enable real emails

In your existing `.env`, set your own values (do not send credentials in chat):

```dotenv
GMAIL_ADDRESS=your-sender-address
GMAIL_APP_PASSWORD=your-app-password
MAIL_SENDER_NAME=Drogo Aerospace
PUBLIC_BASE_URL=https://your-portal-address
```

Use the actual address recipients can access. `http://127.0.0.1:5000` links only work on the computer running the application; another user's computer cannot use that address to reach your laptop.

Alternatively use a STARTTLS SMTP server with `SMTP_HOST`, `SMTP_PORT` (default 587), `SMTP_USERNAME`, `SMTP_PASSWORD` and optionally `SMTP_FROM`. SMTP variables override the Gmail aliases. Restart the web process after editing `.env`. “Configured” confirms settings exist, not that a delivery has succeeded. The create-user confirmation reports the send outcome. SMTP acceptance does not guarantee inbox placement.

Real email delivery was not attempted during testing. SMTP was mocked to check message generation, recipient selection, missing settings and send failures.

## Upgrade

Stop the existing web/worker processes. Back up the database and uploads using your existing process. Apply the cumulative ZIP with `scripts/upgrade_windows.ps1`, or update source through your established deployment process. Preserve `.env`, `.venv`, `instance` and `static/uploads`. No database migration is added by this release; existing head remains `20260901_0014`.

Example from the existing Windows application folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\upgrade_windows.ps1 -ReleaseZip "C:\Downloads\drogo_trans_pilot_cumulative_updates_1_to_32_v1.32.0.zip" -ApplicationPath (Get-Location).Path
```

Restart the application and reload browser tabs. Verify a Client with many defect types, a tower list with many towers, a marked RGB image, and creation of a test user using your configured sender.

## Verification

99 regression tests passed, including four new account-email tests using mocked SMTP. Updated Admin/Client map, Users, Client dashboard and tower-wise pages rendered successfully; inline JavaScript and Python syntax checks passed. A full visual browser check was unavailable because the browser download failed. Check the three layouts at your normal screen size after installation; no live server or real recipient mailbox was changed during testing.
