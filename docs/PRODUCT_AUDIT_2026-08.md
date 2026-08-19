# Public product audit — August 2026

Scope: the packaged Windows first-run journey, research sandbox, permission surfaces, plans surface, and
public-support boundary. Evidence is limited to current generalized screenshots and source-backed behavior
tests. No broker account was connected and no order was placed.

## Current generalized product

![Community desktop workspace](images/audit/responsive-after-02-main-1366x768.png)

![Generalized settings and permissions](images/audit/responsive-after-06-settings-840x680.png)

![Local research sandbox](images/audit/responsive-after-05-sandbox-1366x768.png)

![Community and planned Pro plans](images/product/community-and-pro-plans.png)

## Journey health

| Step | Description | Before | After | Evidence and remaining risk |
|---:|---|---|---|---|
| 1 | Launch and understand product mode | Critical | Healthy | The current build opens disclosure-led onboarding and states that research mode has no broker, remote data, or real orders. |
| 2 | Choose optional capabilities | Missing | Healthy | Broker read access, remote market data, and the capital planning ledger are off by default; real orders cannot be enabled during onboarding. |
| 3 | Begin the primary research task | Poor | Healthy | Getting Started leads directly to the local sandbox. Trading cards and live authority are absent when broker permission is off. |
| 4 | Configure and inspect a sandbox run | Needs work | Improved | Source provenance and fictional aliases are explicit; remote community sources are disabled. The configuration surface is still dense and benefits from a large display. |
| 5 | Grant or revoke broker/live authority | Needs work | Improved | Independent settings, exact live-enable phrase, per-session confirmation, limits, receipts, stop/cancel, disconnect, and credential forgetting exist. No production broker connection was captured; connected-state layout uses an isolated fake broker and synthetic account data. |
| 6 | Understand privacy, support, and ownership | Missing | Improved | Privacy, security, support, license, notices, diagnostics, and non-affiliation language now exist. Public issues and the private GitHub Security Advisory form are documented with explicit no-secrets guidance; response times are not guaranteed. |
| 7 | Install and verify a public artifact | Missing | Improved | Wheel, Windows package, SBOM/checksum automation, CI, and dependency audit exist. Code signing and external legal/security review remain required before broad binary distribution. |

## Accessibility evidence

The screenshots show high-contrast text, text labels accompanying state colors, conventional controls,
and no color-only permission decision. Source inspection and Qt regression tests confirm that accessible
names are assigned to key onboarding, permission, and button controls. This audit did not independently
exercise Windows UI Automation or a screen reader, so exposed names, roles, traversal, and announcements
remain unverified.

This is not a WCAG conformance claim. Keyboard-only traversal order, focus visibility across every dense sandbox control, high-DPI scaling, Windows High Contrast, screen-reader announcements, zoom, and reduced-motion behavior still require dedicated testing. Small sandbox controls and information density remain the largest visible risks.

## Named evidence gaps

- No production broker account was connected, so connected dashboard and live-session dialogs were not captured.
- No real order, cancel race, market outage, credential-vault corruption, or account restriction was induced.
- No external penetration test, legal review, market-data licensing review, or assistive-technology audit has been completed.

The audit supports a community-preview release candidate, not a claim of production, regulatory, accessibility, or profitability readiness.
