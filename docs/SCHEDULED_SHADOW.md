# Scheduled weekday live shadow

GRANDE Alpha can install one per-user Windows Scheduled Task that launches the application Monday
through Friday with the `--auto-shadow` startup mode. The installer derives a local trigger from the
computer's Windows time-zone ID so launch occurs between **7:00 and 9:20 AM ET**:

| Windows time-zone ID | Local trigger |
|---|---:|
| `Eastern Standard Time` | 9:20 AM |
| `Central Standard Time` | 8:20 AM |
| `Mountain Standard Time` | 7:20 AM |
| `US Mountain Standard Time` (Arizona) | 6:20 AM |
| `Pacific Standard Time` | 6:20 AM |

Arizona does not observe daylight saving time, so its 6:20 AM trigger is 8:20 AM ET during Eastern
standard time and 9:20 AM ET during Eastern daylight time. Installation is intentionally limited to
these Windows time-zone IDs; the watchdog extends past the 4:00 PM ET close. Unsupported zones fail
without registering or changing a task. This is an
optional convenience for monitored engineering sessions. It does not authorize orders, establish
an investment edge, or guarantee that the market is open or that the broker will be reachable.

## One-time setup

Use **GRANDE Alpha Shadow Schedule** from the Start Menu after `install-local.ps1`, or run one of
these commands from the installed source directory:

```powershell
# Inspect the intended definition without changing Task Scheduler.
.\manage-shadow-schedule.ps1 -Definition

# Install the task, or refresh the same task after moving/updating the source folder.
.\manage-shadow-schedule.ps1 -Install

# Show its current state and next run.
.\manage-shadow-schedule.ps1 -Status

# Remove only the GRANDE Alpha task.
.\manage-shadow-schedule.ps1 -Remove
```

Install and removal are idempotent: repeating either operation leaves one task or no task,
respectively. `install-local.ps1` never enables the schedule automatically.

## Task boundary

The installed definition:

- belongs to the current Windows user and runs only in that user's interactive session;
- is registered in Task Scheduler's existing root folder, so setup does not depend on creating a
  custom machine-wide folder;
- uses limited privileges and stores no Windows password;
- runs at the mapped local time shown above on weekdays;
- refuses installation outside the explicitly supported continental-U.S. Windows time zones;
- does not start late when a scheduled launch was missed;
- requests permission to wake an already logged-in sleeping computer at the trigger time;
- may start and continue while the computer is on battery power;
- does not depend on Windows reporting an available network before launch; the application's own
  broker/readiness checks remain authoritative;
- ignores a new trigger while the previous scheduled instance is still active;
- uses validated absolute paths to Windows PowerShell, this source directory, and
  `scheduled-shadow.ps1`;
- launches `pythonw -m grande_alpha.app --auto-shadow` and waits for the application to exit;
- keeps that single read-only process alive between sessions with no Task Scheduler time limit;
- retries transient broker read failures with bounded 15-second-to-5-minute backoff during an
  eligible regular session, then returns to an idle wait after the close.

The task requests `WakeToRun=True`, but Windows power policy, disabled wake timers, hibernation, or
hardware settings can still prevent a wake. If the computer is powered off or the user is not logged
on at the mapped local trigger time, that day's launch is intentionally skipped;
`StartWhenAvailable=False` prevents a late catch-up. The schedule does not compensate for exchange
holidays or early closes, so the
application's broker/readiness gates must fail closed when current session data is unavailable.
Because battery starts are allowed and the task does not stop when power switches to battery, make
sure the computer has enough charge for the monitored session.

## Operational behavior

`--auto-shadow` is a shadow-only startup contract. The application must connect read access,
reconcile account state, wait for the regular-session boundary, require fresh QQQ/TQQQ/SQQQ data,
and pass its current auto-shadow readiness checks before starting virtual fills. A successful
Windows task launch alone does not mean shadow started.

Scheduled mode applies a **process-local, non-persistent** Regular market / Market order / GFD /
cash T+1 shadow profile and forces the real-order capability off. This prevents a saved normal-app
extended-hours, limit, or GTC preference from breaking the next scheduled observation. The original
settings file is not modified, and an audit receipt records both the saved and effective route. The
broker is still wrapped in the structural read-only facade.

The default runtime champion is **CASH / hold**. With that default, scheduled shadow still validates
quotes, causal bars, receipts, account reconciliation, and the read-only boundary, but it requests
no leveraged position and should record zero virtual fills. A different supported runtime policy
must be selected deliberately in Settings; that selection is a research choice, not a profit claim
or live-order authorization.

Keep the GUI visible and monitored. If OAuth needs renewed consent, the account or quotes disagree,
another app instance is already running, or any readiness gate fails, the correct outcome is a
visible blocked state rather than a late or partially checked start. Runtime launcher results are
appended to `%LOCALAPPDATA%\GRANDEAlpha\scheduled-shadow.log`.

## Verification

After installation:

```powershell
.\manage-shadow-schedule.ps1 -Status
Get-ScheduledTask -TaskPath '\' -TaskName 'GRANDE Alpha Live Shadow'
```

Confirm the reported user, `InteractiveToken` logon, `Limited` run level, zone-derived weekday trigger,
`StartWhenAvailable=False`, and `MultipleInstances=IgnoreNew`. Do not manually run the task as a
substitute for the application's readiness gates. Also confirm `WakeToRun=True` if the computer may
sleep before the trigger, `DisallowStartIfOnBatteries=False`, `StopIfGoingOnBatteries=False`, and
`RunOnlyIfNetworkAvailable=False`. If the computer moves to an unsupported time zone, remove the
task or reinstall only after returning to a supported zone; the task does not rewrite its trigger
while traveling.

`-Status` is a read-only contract verifier, not a presence check. It requires exactly one action and
one enabled Monday-Friday trigger, the mapped local time, the exact validated executable, arguments,
and working directory, the current interactive limited user, the documented wake/battery/network/
overlap settings, and the 14-hour execution limit. Any mismatch prints `INVALID / UNSAFE` and exits
nonzero; it never describes a merely present or partially matching task as safe.
The supervisor is a 24/7 process, not a 24/7 market. QQQ, TQQQ, and SQQQ virtual execution remains
restricted to valid regular equity sessions. Overnight, weekends, and exchange holidays are idle;
they never create virtual fills or order authority. A transient HTTP 502/503/504 or transport failure
ends the current virtual run, records a receipt, disconnects read access, and triggers a fresh bounded
read-only reconnect rather than continuing with stale account or quote state.
