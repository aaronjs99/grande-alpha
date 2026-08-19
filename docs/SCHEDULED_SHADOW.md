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

# Start the installed task now and require a new owned child plus fresh heartbeat.
.\manage-shadow-schedule.ps1 -Start

# Stop only the exact current-user task and its identity-verified lifecycle child.
.\manage-shadow-schedule.ps1 -Stop

# Perform the same verified stop, then start a new owned instance.
.\manage-shadow-schedule.ps1 -Restart

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
- launches `python -m grande_alpha.app --auto-shadow` as a child that the hidden task host waits for;
- creates that child suspended inside an anonymous Windows Job Object, with job membership applied
  atomically by `PROC_THREAD_ATTRIBUTE_JOB_LIST` and `KILL_ON_JOB_CLOSE`; it records the exact PID and
  start time before resuming Python, so stopping or crashing the wrapper cannot strand a child in the
  launch-to-manifest gap;
- atomically records a local lifecycle manifest containing a random instance ID, exact source/runtime
  paths, current-user SID, wrapper/child PIDs, and both process start times;
- refuses a duplicate launch while an owned wrapper or child is still present, including a child
  orphaned by an externally interrupted Task Scheduler wrapper;
- restarts that foreground child after a nonzero exit with bounded 15-second-to-5-minute backoff,
  resetting to 15 seconds after a run that stayed alive for at least five minutes; exit code `0`
  remains a deliberate off switch until the next task start;
- asks Task Scheduler to restart the PowerShell supervisor up to three times at one-minute intervals
  if the wrapper itself fails;
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
appended to `%LOCALAPPDATA%\GRANDEAlpha\scheduled-shadow.log`. While the Qt event loop is responsive,
the application atomically refreshes
`%LOCALAPPDATA%\GRANDEAlpha\scheduled-shadow-heartbeat.json` every 60 seconds. The heartbeat contains
only local lifecycle metadata and explicit `read_only=true`, `broker_writes=false`, and
`live_authority=false` assertions. It also reports non-identifying runtime state: session-open,
connected, shadow-running, refresh/reconcile clocks, virtual equity/P&L, and virtual-fill count. It
contains no credentials, account/order IDs, real positions, or real account values.
The separate `%LOCALAPPDATA%\GRANDEAlpha\scheduled-shadow-lifecycle.json` contains only local process
ownership metadata. It exists so lifecycle commands can reject PID reuse and avoid broad process-name
or command-line scans.

## Start, stop, and restart

`-Start`, `-Stop`, and `-Restart` operate on the installed **current-user, limited-privilege** task and
do not request elevation. They first validate the entire Scheduled Task contract. A mismatched action,
principal, trigger, or setting stops the command before any task or process change.

Explicit `-Install` and `-Remove` can repair or remove a task whose trigger or non-identity settings
drifted, but only when the task still has the exact current-user, limited principal and exact validated
PowerShell/launcher/working-directory identity. An action, project path, user, logon type, or privilege
mismatch remains fail-closed and is never overwritten or unregistered as though it were this product.
For these two explicit maintenance operations only, an exact-identity task whose state is `Disabled`
is treated as repairable setting drift: any exact attributable runtime is first stopped, and the app
must then prove terminal lifecycle state with no known live heartbeat PID. Normal `-Start`, `-Stop`,
and `-Restart` remain strict and do not reinterpret a Disabled task.
Even when Task Scheduler says the task is absent, both commands inspect lifecycle and heartbeat state.
An exactly verified orphan can be stopped through the same scoped identity checks, including when its
heartbeat is stale. A live heartbeat PID that cannot be attributed exactly, including one reported by
contract-invalid heartbeat data, makes the command fail nonzero. `-Install` does not register a
replacement before that preflight succeeds, and `-Remove` never reports “already absent” while an
unresolved PID is known alive.

A child is controllable only when its lifecycle record, current-user SID, PID, process start time,
exact managed Python launcher/process executable, and anchored `-m grande_alpha.app --auto-shadow`
argument string all match. Windows virtual environments can retain a small launcher process in front
of the base Python application, so both members and their direct parent relationship are verified.
The wrapper identity receives equivalent checks against the exact PowerShell executable and
scheduled launcher arguments. There is no `python.exe` name scan, wildcard command-line termination,
or fallback that might target a separately launched application. If the heartbeat is unavailable,
the only discovery fallback is the recorded launcher's direct-child relationship, followed by the
same exact owner/executable/argv/start-order checks and a requirement for one unambiguous match. The
first restart from an older
launcher can be attributed only while its exact process chain still leads to the exact scheduled
PowerShell parent and a contract-valid heartbeat; an unprovable legacy orphan fails closed.

The scheduled wrapper requires Windows 10 or newer process-creation job-list support. If Windows
cannot atomically create the suspended child in the private kill-on-close job, launch fails before
Python application code is resumed. The base Python process and other non-breakaway descendants
inherit that same containment boundary. This is independent of Task Scheduler's own process state
and requires no administrator privilege.

`-Stop` makes a best-effort normal Windows close request to the exact verified GUI child and allows up
to 10 seconds for that path before stopping the validated task wrapper. The total application-stop
window is 30 seconds. If the same PID/start-time/executable/argv/owner identity is still present, it
terminates only that exact process. This is not a guarantee that the GUI
will accept the close request or write a final `stopped` checkpoint. Existing durable shadow
checkpoints and audit data are never deleted, truncated, or reset; after a forced termination, normal
same-session recovery uses the latest successfully committed active checkpoint.

`-Start` will not create a duplicate. If the manifest proves an orphaned task child, it uses the same
scoped stop path first. Success requires Task Scheduler to report `Running`, a verified wrapper/
launcher/application chain, and a fresh heartbeat from that exact application process within 45
seconds. `-Restart` composes the verified
stop and start operations. `-Remove` and a refresh through `-Install` also run the scoped lifecycle
shutdown for an identity-verified existing instance so unregistering or replacing the wrapper cannot
strand its child.
An explicit stop also cancels a Task Scheduler `Queued` instance and requires the task to reach exact
`Ready` state before reporting success; `Queued`, `Disabled`, and other scheduler states never fall
through as if they were already off.

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

`-Status` is a read-only contract, ownership, and runtime-health verifier, not a presence check. It requires exactly one action and
one enabled Monday-Friday trigger, the mapped local time, the exact validated executable, arguments,
and working directory, the current interactive limited user, the documented wake/battery/network/
overlap settings, the unlimited execution-time contract, and the three-at-one-minute wrapper restart
policy. Any mismatch prints `INVALID / UNSAFE`
and exits nonzero; it never describes a merely present or partially matching task as safe. When the
task reports `Running`, `-Status` separately prints `EVENT_LOOP_FRESH` liveness and an operational
state of `ACTIVE`, `WAITING`, or `DEGRADED`. A fresh event-loop timer never by itself means the broker
or shadow loop is healthy: an open session without connected, active, current refresh/reconcile state
is `DEGRADED` and makes `-Status` exit nonzero. Outside an open session, a fresh process may truthfully
report `WAITING` without failing. A missing, invalid, stopped, dead-process, or older-than-180-second
heartbeat also fails a running task's status. A task that has not yet reached its first trigger may
legitimately report a missing heartbeat while still passing its static installation contract.
PID evidence is extracted and checked before timestamps or runtime fields are parsed. Therefore a
malformed timestamp/runtime payload cannot hide a live heartbeat PID. A present heartbeat with a
malformed or non-numeric PID is itself unsafe and blocks lifecycle mutation rather than being treated
as a missing heartbeat.
Task Scheduler `Ready` is reported as `OFF` only when there is no verified live wrapper/child and no
heartbeat PID known alive, including a stale or contract-invalid heartbeat. `Ready` plus a verified child is `ORPHANED` and exits nonzero;
`Running` without a verified child is `STARTING`, `RETRYING`, or `UNVERIFIED`, never silently healthy.
Only exact `Ready`/off and `Running`/healthy combinations can pass status; `Queued`, `Disabled`, and
all other scheduler states exit nonzero.
The supervisor is a 24/7 process, not a 24/7 market. QQQ, TQQQ, and SQQQ virtual execution remains
restricted to valid regular equity sessions. Overnight, weekends, and exchange holidays are idle;
they never create virtual fills or order authority. A transient HTTP 502/503/504 or transport failure
ends the current virtual run, records a receipt, disconnects read access, and triggers a fresh bounded
read-only reconnect rather than continuing with stale account or quote state.
