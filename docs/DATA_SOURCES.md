# Data sources and licenses

## Deterministic scenario

Generated locally for software verification. It is synthetic and cannot establish market performance.

## CSV import

The user supplies the file and is responsible for its provenance, license, accuracy, corporate-action handling, timezone, survivorship bias, and completeness. GRANDE Alpha hashes the input for reproducibility but does not grant redistribution rights.

## Community remote adapter

Disabled by default. When enabled, it requests public chart data from an unsupported Yahoo endpoint. It is not an official, contracted, or guaranteed market-data feed and may change, throttle, omit, delay, or correct data. Do not redistribute cached data unless its license permits it. No broker or account data is included in these requests.

The full-history option requests daily QQQ, TQQQ, and SQQQ candles and retains only timestamps
present in all three series. The practical common inception is limited by the newest fund, not by
QQQ's older history. Provider-adjusted price history may encode splits or later corrections; a
content hash proves reproducibility, not economic correctness. Every run must record its own
observation window and hash. Dated reference results belong in the research records, not in the
product-wide source contract. The endpoint and any resulting snapshot are unsupported and may
become stale.

## Broker data

Available only after explicit broker permission and OAuth. Provider terms, disclosures, entitlement, latency, corrections, and availability apply. Do not treat displayed quotes as exchange-direct or suitable for institutional execution.

Every published performance result should identify source, time interval, timezone, adjustments, hash, costs, latency assumptions, rejected/partial-fill model, and whether the result is synthetic, historical, shadow, or live.

## Earnings and consensus observations

The optional Alpha Vantage adapter captures raw `EARNINGS` and `EARNINGS_ESTIMATES` JSON with the
requested symbol, dataset, response-completion time, and a context-bound SHA-256 digest. Set the API
key only in the `ALPHA_VANTAGE_API_KEY` environment variable; it is not written to the database or
printed by the command.

```powershell
.\cli.ps1 earnings fetch --database C:\private\earnings.db --symbol MSFT --dataset EARNINGS_ESTIMATES
```

The provider documents the estimates endpoint as including annual and quarterly EPS/revenue
estimates, analyst counts, and revision history, but does not publish a stable field-by-field JSON
schema. GRANDE Alpha therefore does not guess a normalization. An explicit normalized fact must be
bound to the captured raw hash, and the execution verifier requires that consensus was observed
before the announcement. Retrieval after an announcement cannot retroactively establish a
point-in-time consensus. Source access also does not grant redistribution rights.

Before any imported history enters final-evidence governance, use the exact CSV schema, provenance
manifest, no-upsampling checks, read-only audit, and one-use holdout checklist in
[Observed-data readiness](DATASET_READINESS.md). A valid hash proves that bytes are reproducible; it
does not prove that a source license permits the use or that one-minute/daily rows are five-second
observations.
