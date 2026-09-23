# Native desktop workspace

The unreleased desktop redesign keeps the existing Qt application and trading controller. It does
not add a browser server, remote fonts, a new runtime, or an OS scheduler.

## Navigation and presentation

| Destination | Purpose |
|---|---|
| Overview | Summary, next steps and nested readiness checks |
| Trading | Plain-language session status, four key metrics and relevant actions |
| Research | Virtual replay, evidence experiments and results |
| Activity | Positions, orders, event history and the optional capital ledger |

`Ctrl+1` through `Ctrl+4` follow this order; `Ctrl+5` opens readiness checks inside Overview.
Settings stays in the header; Tools reveals advanced
menus. The sidebar becomes a horizontal navigation row below 1,100 logical pixels. At smaller
widths, the header and metric cards wrap. Session and overview content scroll independently.
Tables retain adjustable columns and horizontal scrolling where the data genuinely requires it.

Research starts with four summary statistics. Detailed statistics and advanced assumptions are
collapsible; run/export buttons stay outside the scrolling form. Charts, quote tables, positions,
orders and readiness tables defer presentation updates while their page is hidden. Market history
still accumulates within its existing bound, and entering a page refreshes it from the latest state.
Broker polling and risk checks are not suspended by navigation. Market data and technical
strategy cards are collapsed by default. Recoverable operation errors appear in a dismissible
inline notice rather than blocking navigation with a modal. Consent and consequential-action
confirmations remain separate dialogs. The header describes actual activity (offline, connected,
simulating or trading active), not merely enabled capabilities.

## Controls are not permission

The consent-pattern review preserved separate session authorization, exact-ticket confirmation,
revocation, cancellation and liquidation. Stop / cancel opens the existing explicit scoped workflow;
it is not a claim that all orders are cancelled or positions closed. The redesign does not grant
unattended order authority, change risk limits, bypass evidence gates or turn a free/Pro plan into
trading permission. See [the autonomy handoff](GETTING_TO_AUTONOMY.md).

## Verification and limits

The new offline regression suite checks navigation, nested records, visible/non-overlapping header
controls, scrolling, disclosure controls, layout stability, hidden-page refresh behavior and optional
ledger routing. Sizes include 720×560, 900×1200, 1024×700, widths on both sides of the navigation
breakpoint, 1366×768 and 1920×1080. Existing responsive checks cover the session and order-confirmation
dialogs as well as readiness and settings.

The dated captures used a temporary database, fabricated account/quote data and a broker stub that
rejected trading calls. No credentials or live broker were used. The capture harness has since been
removed. Offscreen screenshots are historical layout evidence, not current multi-monitor/DPI,
screen-reader, performance or trading qualification. Historical UI audit screenshots describe their
dated version, not this redesign. No quantitative RAM reduction is claimed without measurement.
