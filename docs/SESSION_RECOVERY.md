# Approval, readiness and recovery

Session approval is independent of market hours. You can approve a bounded session
outside the entry window without fresh execution quotes. Account identity, reconciled
positions/orders, limits, unresolved-order checks and evidence requirements still apply.
The displayed expiry is unchanged: the legacy desktop grant expires the same Eastern day.
Outside market hours approval does not automatically schedule execution. Start within the
permitted window, while approval is valid, with fresh account data and venue quotes.

Read-only readiness checks keep account reconciliation separate from quote readiness.
Stale or unavailable market quotes do not erase a successful account refresh. They do
remain a block on execution. Refreshing a quote never changes its provider timestamp.

If an earlier simulation cannot resume, the desktop offers a separate simulation with
an explicit confirmation. All prior checkpoints remain unchanged; the old run is recorded
as interrupted, not successfully completed. Results must not be pooled across those runs.
Corrupt checkpoint chains still fail closed and require investigation.

Exit stops local execution, cancels local background tasks and attempts bounded transport
cleanup. It does not cancel broker orders or sell holdings. Existing orders may fill after
exit; check the broker directly. Durable order records survive for startup reconciliation.
Unavailable broker connectivity does not trap the application open.

The Session page shows Start only after approval and Sell holdings only when tracked
TQQQ/SQQQ holdings exist. Selling still requires its existing review and confirmation.
