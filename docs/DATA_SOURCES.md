# Data sources and licenses

## Deterministic scenario

Generated locally for software verification. It is synthetic and cannot establish market performance.

## CSV import

The user supplies the file and is responsible for its provenance, license, accuracy, corporate-action handling, timezone, survivorship bias, and completeness. GRANDE Alpha hashes the input for reproducibility but does not grant redistribution rights.

## Community remote adapter

Disabled by default. When enabled, it requests public chart data from an unsupported Yahoo endpoint. It is not an official, contracted, or guaranteed market-data feed and may change, throttle, omit, delay, or correct data. Do not redistribute cached data unless its license permits it. No broker or account data is included in these requests.

The full-history option requests daily QQQ, TQQQ, and SQQQ candles and retains only timestamps
present in all three series. The practical common inception is limited by the newest fund, not by
QQQ's older history. The frozen 2026-08-09 cache contains 4,147 aligned daily observations from
2010-02-11 through 2026-08-07 and SHA-256 prefix `da00f6f963bb1cbc`. Provider-adjusted price
history may encode splits or later corrections; the content hash proves reproducibility, not
economic correctness. The endpoint and this snapshot are unsupported and may become stale.

## Broker data

Available only after explicit broker permission and OAuth. Provider terms, disclosures, entitlement, latency, corrections, and availability apply. Do not treat displayed quotes as exchange-direct or suitable for institutional execution.

Every published performance result should identify source, time interval, timezone, adjustments, hash, costs, latency assumptions, rejected/partial-fill model, and whether the result is synthetic, historical, shadow, or live.

Before any imported history enters final-evidence governance, use the exact CSV schema, provenance
manifest, no-upsampling checks, read-only audit, and one-use holdout checklist in
[Observed-data readiness](DATASET_READINESS.md). A valid hash proves that bytes are reproducible; it
does not prove that a source license permits the use or that one-minute/daily rows are five-second
observations.
