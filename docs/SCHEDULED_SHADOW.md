# Scheduled weekday live shadow

GRANDE Alpha can install one per-user Windows Scheduled Task that launches the application at
**6:20 AM local time, Monday through Friday**, with the `--auto-shadow` startup mode. This is an
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
- runs at 6:20 AM in the computer's local time zone on weekdays;
- does not start late when a scheduled launch was missed;
- requests permission to wake an already logged-in sleeping computer at the trigger time;
- may start and continue while the computer is on battery power;
- does not depend on Windows reporting an available network before launch; the application's own
  broker/readiness checks remain authoritative;
- ignores a new trigger while the previous scheduled instance is still active;
- uses validated absolute paths to Windows PowerShell, this source directory, and
  `scheduled-shadow.ps1`;
- launches `pythonw -m grande_alpha.app --auto-shadow` and waits for the application to exit.

The task requests `WakeToRun=True`, but Windows power policy, disabled wake timers, hibernation, or
hardware settings can still prevent a wake. If the computer is powered off or the user is not logged
on at 6:20 AM, that day's launch is intentionally skipped; `StartWhenAvailable=False` prevents a
late catch-up. The schedule does not compensate for exchange holidays or early closes, so the
application's broker/readiness gates must fail closed when current session data is unavailable.
Because battery starts are allowed and the task does not stop when power switches to battery, make
sure the computer has enough charge for the monitored session.

## Operational behavior

`--auto-shadow` is a shadow-only startup contract. The application must connect read access,
reconcile account state, wait for the regular-session boundary, require fresh QQQ/TQQQ/SQQQ data,
and pass its current auto-shadow readiness checks before starting virtual fills. A successful
Windows task launch alone does not mean shadow started.

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

Confirm the reported user, `InteractiveToken` logon, `Limited` run level, 6:20 AM weekday trigger,
`StartWhenAvailable=False`, and `MultipleInstances=IgnoreNew`. Do not manually run the task as a
substitute for the application's readiness gates. Also confirm `WakeToRun=True` if the computer may
sleep before the trigger, `DisallowStartIfOnBatteries=False`, `StopIfGoingOnBatteries=False`, and
`RunOnlyIfNetworkAvailable=False`.
