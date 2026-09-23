# Support

GRANDE Alpha is a community preview provided without guaranteed support or uptime.

## Where to ask

| Question | Destination |
|---|---|
| Reproducible app bug | [Bug report](https://github.com/aaronjs99/grande-alpha/issues/new?template=bug_report.yml) |
| Product or documentation idea | [Feature request](https://github.com/aaronjs99/grande-alpha/issues/new?template=feature_request.yml) |
| Security vulnerability | [Private security advisory](https://github.com/aaronjs99/grande-alpha/security/advisories/new) |
| Broker account, authentication, execution, settlement, or outage | The broker's official support |
| Financial, legal, tax, employment, residency, or eligibility question | An appropriately qualified professional |

Search [existing issues](https://github.com/aaronjs99/grande-alpha/issues) before opening a new one.

## Before reporting a bug

1. Reproduce the issue in research mode if possible.
2. Run `./verify.ps1` from a source checkout.
3. Note the GRANDE Alpha version, Windows version, Python version, and exact safe reproduction steps.
4. If useful, export **File → Export redacted diagnostics** and inspect the JSON yourself.
5. Remove all personal, financial, legal-status, tax, credential, account, position, order, and
   licensed-data information.

Redaction remains the reporter's responsibility. Free-form text can contain details the diagnostic
exporter cannot recognize.

## Never post publicly

- passwords, OAuth tokens, credentials, or callback URLs containing secrets;
- account identifiers, balances, positions, order identifiers, or broker receipts;
- tax, employment, residency, immigration, or legal-status information;
- proprietary or licensed market data; or
- an unreviewed diagnostic export or screenshot.

Provide the minimum redacted information needed to reproduce the software problem. GitHub requires
an account to create an issue; reading public issues does not require one.
