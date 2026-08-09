# Privacy

GRANDE Alpha is local-first. The project does not operate a telemetry, analytics, advertising, or cloud-sync service.

## Data stored locally

Application configuration, receipts, research runs, sandbox fills, and optional personal-ledger entries are stored under `%LOCALAPPDATA%\GRANDEAlpha`. OAuth credentials are stored through the Windows credential vault via `keyring`. They are not included in the SQLite database or configuration JSON.

Quotes, bars, and derived signals are pruned according to the retention setting. Order records, receipts, sandbox evidence, and optional ledger records are not automatically deleted because they may be needed for audit or tax records. Users control the local directory and should apply their own retention and backup policy.

## Data sent to third parties

Nothing is sent merely by launching the app or using deterministic/CSV research.

- Enabling **Broker connection** permits the app to exchange authentication and account/trading data with the configured broker MCP. The provider may expose data from multiple accounts even though writes are restricted to the Agentic account.
- Enabling **Community remote market data** permits symbol, interval, and time-range requests to an unsupported Yahoo chart endpoint. No broker or account data is included.
- Opening external documentation uses the system browser and is governed by the destination's privacy policy.

## Diagnostics

Diagnostic export is user-initiated. Known account, authorization, token, order, reference, and long identifier fields are redacted. Review the JSON before sharing it; free-form text can still contain information the software cannot recognize.

## Revocation and deletion

Disable a capability in **Settings & Permissions** to revoke it. The app disconnects the broker when broker permission is removed, stops order activity when real-order permission is removed, and can forget stored OAuth credentials. To delete all local application data, close the app, back up any records you need, and remove `%LOCALAPPDATA%\GRANDEAlpha`.

Privacy questions should use the channel described in [SUPPORT.md](SUPPORT.md). Do not include credentials, full account numbers, tax documents, or unredacted diagnostics in a public issue.
