"""Native desktop notifications, with no dependency and no daemon of our own.

Every platform already has a notification system that survives sleep, respects
Do Not Disturb and has a permission model. Shipping a tray icon and a
notification library to reimplement that badly would cost tens of megabytes of
resident memory on a machine that is supposed to not notice we are running.
"""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess

log = logging.getLogger(__name__)

SYSTEM = platform.system()


def _run(argv: list[str]) -> bool:
    try:
        subprocess.run(argv, check=True, capture_output=True, timeout=10)
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("notification failed (%s): %s", argv[0], exc)
        return False


def _applescript_quote(text: str) -> str:
    """AppleScript string literals escape with backslashes, and a stray quote
    from a place name would otherwise turn the rest of an event title into
    code. Feed text is not trusted input."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def notify(title: str, body: str, *, urgent: bool = False) -> bool:
    """Posts one notification. Returns whether the platform accepted it."""
    if SYSTEM == "Darwin":
        sound = "Sosumi" if urgent else "Submarine"
        script = (
            f'display notification "{_applescript_quote(body)}" '
            f'with title "{_applescript_quote(title)}" sound name "{sound}"'
        )
        return _run(["osascript", "-e", script])

    if SYSTEM == "Linux":
        if not shutil.which("notify-send"):
            log.warning("notify-send is not installed: no desktop notification")
            return False
        return _run(["notify-send", "--urgency", "critical" if urgent else "normal", title, body])

    if SYSTEM == "Windows":
        # PowerShell's toast API, quoted as a here-string so feed text cannot
        # close the literal and become a command.
        script = (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications,"
            " ContentType = WindowsRuntime] > $null; "
            "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
            "[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
            "$n = $t.GetElementsByTagName('text'); "
            f"$n.Item(0).AppendChild($t.CreateTextNode(@'\n{title}\n'@)) > $null; "
            f"$n.Item(1).AppendChild($t.CreateTextNode(@'\n{body}\n'@)) > $null; "
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
            "'SOSForge').Show([Windows.UI.Notifications.ToastNotification]::new($t))"
        )
        return _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script])

    log.warning("no notification backend for %s", SYSTEM)
    return False
