# Data sources and licenses

## Deterministic scenario

Generated locally for software verification. It is synthetic and cannot establish market performance.

## CSV import

The user supplies the file and is responsible for its provenance, license, accuracy, corporate-action handling, timezone, survivorship bias, and completeness. GRANDE Alpha hashes the input for reproducibility but does not grant redistribution rights.

## Community remote adapter

Disabled by default. When enabled, it requests public chart data from an unsupported Yahoo endpoint. It is not an official, contracted, or guaranteed market-data feed and may change, throttle, omit, delay, or correct data. Do not redistribute cached data unless its license permits it. No broker or account data is included in these requests.

## Broker data

Available only after explicit broker permission and OAuth. Provider terms, disclosures, entitlement, latency, corrections, and availability apply. Do not treat displayed quotes as exchange-direct or suitable for institutional execution.

Every published performance result should identify source, time interval, timezone, adjustments, hash, costs, latency assumptions, rejected/partial-fill model, and whether the result is synthetic, historical, shadow, or live.
