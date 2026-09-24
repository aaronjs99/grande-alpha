from __future__ import annotations

from .base import Repository


class SchemaRepository(Repository):
    def _initialize(self) -> None:
        with self._lock, self._connection:
            self._connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=FULL;
                PRAGMA foreign_keys=ON;
                CREATE TABLE IF NOT EXISTS quotes (
                    id INTEGER PRIMARY KEY,
                    observed_at TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    bid REAL NOT NULL,
                    ask REAL NOT NULL,
                    last REAL NOT NULL,
                    venue_timestamp TEXT NOT NULL,
                    bid_timestamp TEXT,
                    ask_timestamp TEXT,
                    batch_id TEXT,
                    batch_position INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_quotes_symbol_time ON quotes(symbol, observed_at);
                CREATE TABLE IF NOT EXISTS quote_batches (
                    batch_id TEXT PRIMARY KEY,
                    stream_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    schema_version INTEGER NOT NULL CHECK(schema_version IN (1,2)),
                    symbol_count INTEGER NOT NULL CHECK(symbol_count = 3)
                    ,validation_profile TEXT NOT NULL DEFAULT 'passive_unvalidated'
                    ,validation_version INTEGER NOT NULL DEFAULT 0
                    ,max_age_seconds REAL
                    ,max_skew_seconds REAL
                );
                CREATE TABLE IF NOT EXISTS bars (
                    id INTEGER PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    start_at TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    samples INTEGER NOT NULL,
                    UNIQUE(symbol, start_at)
                );
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    regime TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    reason TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS receipts (
                    id INTEGER PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    category TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS device_notifications (
                    id INTEGER PRIMARY KEY,
                    receipt_id INTEGER NOT NULL UNIQUE REFERENCES receipts(id),
                    created_at TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    acknowledged_at TEXT
                );
                CREATE TABLE IF NOT EXISTS standing_sessions (
                    authority_id TEXT PRIMARY KEY,
                    scope_digest TEXT NOT NULL,
                    stopped INTEGER NOT NULL DEFAULT 0 CHECK(stopped IN (0,1))
                );
                CREATE TABLE IF NOT EXISTS live_daily_risk (
                    account_number TEXT NOT NULL,
                    et_date TEXT NOT NULL,
                    peak_value REAL NOT NULL CHECK(peak_value >= 0),
                    last_value REAL NOT NULL CHECK(last_value >= 0),
                    loss_limit REAL NOT NULL CHECK(loss_limit > 0),
                    loss_latched INTEGER NOT NULL CHECK(loss_latched IN (0,1)),
                    PRIMARY KEY(account_number, et_date)
                );
                CREATE TABLE IF NOT EXISTS loss_recovery (
                    account_number TEXT PRIMARY KEY, scope_digest TEXT NOT NULL,
                    loss_day TEXT NOT NULL, paused_at TEXT NOT NULL,
                    eligible_at TEXT, cleared_at TEXT
                );
                CREATE TABLE IF NOT EXISTS mixed_daily_pnl (
                    account_number TEXT NOT NULL, et_date TEXT NOT NULL,
                    peak_pnl REAL NOT NULL, last_pnl REAL NOT NULL,
                    loss_limit REAL NOT NULL CHECK(loss_limit > 0),
                    loss_latched INTEGER NOT NULL CHECK(loss_latched IN (0,1)),
                    PRIMARY KEY(account_number,et_date)
                );
                CREATE TABLE IF NOT EXISTS shadow_checkpoints (
                    id INTEGER PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL CHECK(sequence >= 1),
                    recorded_at TEXT NOT NULL,
                    schema_version INTEGER NOT NULL CHECK(schema_version = 1),
                    session_key TEXT NOT NULL,
                    account_fingerprint TEXT NOT NULL,
                    strategy_fingerprint TEXT NOT NULL,
                    contract_fingerprint TEXT NOT NULL,
                    event TEXT NOT NULL,
                    previous_digest TEXT,
                    digest TEXT NOT NULL,
                    checkpoint_json TEXT NOT NULL,
                    UNIQUE(run_id,sequence),
                    UNIQUE(digest)
                );
                CREATE INDEX IF NOT EXISTS idx_shadow_checkpoints_latest
                    ON shadow_checkpoints(id DESC);
                CREATE INDEX IF NOT EXISTS idx_shadow_checkpoints_run_sequence
                    ON shadow_checkpoints(run_id,sequence);
                CREATE TABLE IF NOT EXISTS order_intents (
                    ref_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    broker_order_id TEXT,
                    broker_state TEXT,
                    account_number TEXT,
                    authority_id TEXT,
                    strategy_fingerprint TEXT,
                    authorized_notional REAL NOT NULL DEFAULT 0,
                    submission_started_at TEXT
                );
                CREATE TABLE IF NOT EXISTS broker_executions (
                    id INTEGER PRIMARY KEY,
                    recorded_at TEXT NOT NULL,
                    account_number TEXT NOT NULL,
                    execution_id TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    symbol TEXT NOT NULL CHECK(symbol IN ('TQQQ','SQQQ')),
                    side TEXT NOT NULL CHECK(side IN ('buy','sell')),
                    quantity REAL NOT NULL CHECK(quantity > 0),
                    price REAL NOT NULL CHECK(price > 0),
                    fees REAL NOT NULL CHECK(fees >= 0),
                    executed_at TEXT NOT NULL,
                    UNIQUE(account_number,execution_id)
                );
                CREATE INDEX IF NOT EXISTS idx_broker_executions_account_order
                    ON broker_executions(account_number,order_id,executed_at,execution_id);
                CREATE INDEX IF NOT EXISTS idx_broker_executions_account_symbol_time
                    ON broker_executions(account_number,symbol,executed_at,execution_id);
                CREATE TABLE IF NOT EXISTS research_fund (
                    id INTEGER PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    period TEXT NOT NULL,
                    realized_profit REAL NOT NULL,
                    fees REAL NOT NULL,
                    tax_reserve REAL NOT NULL,
                    contribution_rate REAL NOT NULL,
                    eligible_contribution REAL NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('planned','confirmed')),
                    confirmed_at TEXT,
                    confirmation_reference TEXT,
                    notes TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sandbox_runs (
                    run_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    data_source TEXT NOT NULL,
                    replay_start TEXT NOT NULL,
                    replay_end TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    metrics_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sandbox_fills (
                    id INTEGER PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES sandbox_runs(run_id) ON DELETE CASCADE,
                    filled_at TEXT NOT NULL,
                    symbol TEXT NOT NULL CHECK(symbol IN ('TQQQS','SQQQS')),
                    side TEXT NOT NULL CHECK(side IN ('buy','sell')),
                    quantity REAL NOT NULL,
                    price REAL NOT NULL,
                    commission REAL NOT NULL,
                    realized_pnl REAL,
                    reason TEXT NOT NULL,
                    cash_after REAL NOT NULL,
                    unsettled_cash_after REAL NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_sandbox_fills_run ON sandbox_fills(run_id,id);
                CREATE TABLE IF NOT EXISTS sandbox_execution_events (
                    id INTEGER PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES sandbox_runs(run_id) ON DELETE CASCADE,
                    event_at TEXT NOT NULL,
                    symbol TEXT NOT NULL CHECK(symbol IN ('TQQQS','SQQQS')),
                    side TEXT NOT NULL CHECK(side IN ('buy','sell')),
                    status TEXT NOT NULL,
                    requested_quantity REAL NOT NULL,
                    filled_quantity REAL NOT NULL,
                    reason TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sandbox_events_run
                    ON sandbox_execution_events(run_id,id);
                CREATE TABLE IF NOT EXISTS research_promotions (
                    id INTEGER PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    dataset_hash TEXT NOT NULL,
                    strategy_fingerprint TEXT NOT NULL,
                    policy_version INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('SHADOW_ONLY','LIVE_REVIEW_ELIGIBLE')),
                    source TEXT NOT NULL,
                    replay_end TEXT NOT NULL,
                    gates_json TEXT NOT NULL,
                    risk_envelope_json TEXT NOT NULL DEFAULT '{}',
                    provenance_hash TEXT NOT NULL DEFAULT '',
                    provenance_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_research_promotions_fingerprint_time
                    ON research_promotions(strategy_fingerprint,created_at DESC);
                CREATE TABLE IF NOT EXISTS research_trials (
                    id INTEGER PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    dataset_hash TEXT NOT NULL,
                    trial_fingerprint TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    UNIQUE(dataset_hash,trial_fingerprint)
                );
                CREATE INDEX IF NOT EXISTS idx_research_trials_dataset
                    ON research_trials(dataset_hash,id);
                CREATE TABLE IF NOT EXISTS research_holdouts (
                    id INTEGER PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    dataset_hash TEXT NOT NULL,
                    development_hash TEXT NOT NULL,
                    holdout_hash TEXT NOT NULL,
                    holdout_start TEXT NOT NULL,
                    holdout_end TEXT NOT NULL,
                    policy_version INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN (
                        'RESERVED','FROZEN','EVALUATING','CONSUMED','INVALID'
                    )),
                    selected_fingerprint TEXT,
                    evaluation_started_at TEXT,
                    consumed_at TEXT,
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    provenance_hash TEXT NOT NULL DEFAULT '',
                    development_quality_json TEXT NOT NULL DEFAULT '{}',
                    holdout_quality_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(dataset_hash,holdout_start,holdout_end)
                );
                """
            )
            promotion_columns = {
                row["name"]
                for row in self._connection.execute("PRAGMA table_info(research_promotions)").fetchall()
            }
            if "risk_envelope_json" not in promotion_columns:
                self._connection.execute(
                    "ALTER TABLE research_promotions ADD COLUMN risk_envelope_json TEXT NOT NULL DEFAULT '{}'"
                )
            if "holdout_id" not in promotion_columns:
                self._connection.execute("ALTER TABLE research_promotions ADD COLUMN holdout_id INTEGER")
            if "provenance_hash" not in promotion_columns:
                self._connection.execute(
                    "ALTER TABLE research_promotions ADD COLUMN provenance_hash TEXT NOT NULL DEFAULT ''"
                )
            if "provenance_json" not in promotion_columns:
                self._connection.execute(
                    "ALTER TABLE research_promotions ADD COLUMN provenance_json TEXT NOT NULL DEFAULT '{}'"
                )
            holdout_columns = {
                row["name"]
                for row in self._connection.execute("PRAGMA table_info(research_holdouts)").fetchall()
            }
            if "provenance_hash" not in holdout_columns:
                self._connection.execute(
                    "ALTER TABLE research_holdouts ADD COLUMN provenance_hash TEXT NOT NULL DEFAULT ''"
                )
            if "development_quality_json" not in holdout_columns:
                self._connection.execute(
                    "ALTER TABLE research_holdouts ADD COLUMN development_quality_json TEXT NOT NULL DEFAULT '{}'"
                )
            if "holdout_quality_json" not in holdout_columns:
                self._connection.execute(
                    "ALTER TABLE research_holdouts ADD COLUMN holdout_quality_json TEXT NOT NULL DEFAULT '{}'"
                )
            self._connection.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS idx_research_holdouts_hash
                ON research_holdouts(holdout_hash)"""
            )
            self._connection.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS idx_research_promotions_holdout
                ON research_promotions(holdout_id) WHERE holdout_id IS NOT NULL"""
            )
            sandbox_fill_columns = {
                row["name"] for row in self._connection.execute("PRAGMA table_info(sandbox_fills)").fetchall()
            }
            if "unsettled_cash_after" not in sandbox_fill_columns:
                self._connection.execute(
                    "ALTER TABLE sandbox_fills ADD COLUMN unsettled_cash_after REAL NOT NULL DEFAULT 0"
                )
            order_intent_columns = {
                row["name"] for row in self._connection.execute("PRAGMA table_info(order_intents)").fetchall()
            }
            intent_migrations = {
                "account_number": "TEXT",
                "authority_id": "TEXT",
                "strategy_fingerprint": "TEXT",
                "authorized_notional": "REAL NOT NULL DEFAULT 0",
                "submission_started_at": "TEXT",
            }
            for column, declaration in intent_migrations.items():
                if column not in order_intent_columns:
                    self._connection.execute(f"ALTER TABLE order_intents ADD COLUMN {column} {declaration}")
            self._connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_order_intents_account_submission
                ON order_intents(account_number,submission_started_at)"""
            )
            duplicate_order_binding = self._connection.execute(
                """SELECT account_number,broker_order_id,COUNT(*) AS n FROM order_intents
                WHERE account_number IS NOT NULL AND broker_order_id IS NOT NULL
                GROUP BY account_number,broker_order_id HAVING COUNT(*)>1 LIMIT 1"""
            ).fetchone()
            if duplicate_order_binding is not None:
                raise ValueError("Legacy order intents contain duplicate broker-order bindings")
            self._connection.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS idx_order_intents_account_order
                ON order_intents(account_number,broker_order_id)
                WHERE account_number IS NOT NULL AND broker_order_id IS NOT NULL"""
            )
            quote_columns = {
                row["name"] for row in self._connection.execute("PRAGMA table_info(quotes)").fetchall()
            }
            if "batch_id" not in quote_columns:
                self._connection.execute("ALTER TABLE quotes ADD COLUMN batch_id TEXT")
            if "batch_position" not in quote_columns:
                self._connection.execute("ALTER TABLE quotes ADD COLUMN batch_position INTEGER")
            if "bid_timestamp" not in quote_columns:
                self._connection.execute("ALTER TABLE quotes ADD COLUMN bid_timestamp TEXT")
            if "ask_timestamp" not in quote_columns:
                self._connection.execute("ALTER TABLE quotes ADD COLUMN ask_timestamp TEXT")
            quote_batch_columns = {
                row["name"] for row in self._connection.execute("PRAGMA table_info(quote_batches)").fetchall()
            }
            if "stream_id" not in quote_batch_columns:
                self._connection.execute(
                    "ALTER TABLE quote_batches ADD COLUMN stream_id TEXT NOT NULL DEFAULT ''"
                )
            for column, declaration in {
                "validation_profile": "TEXT NOT NULL DEFAULT 'passive_unvalidated'",
                "validation_version": "INTEGER NOT NULL DEFAULT 0",
                "max_age_seconds": "REAL",
                "max_skew_seconds": "REAL",
            }.items():
                if column not in quote_batch_columns:
                    self._connection.execute(f"ALTER TABLE quote_batches ADD COLUMN {column} {declaration}")
            quote_batch_sql = (
                str(
                    self._connection.execute(
                        "SELECT sql FROM sqlite_master WHERE type='table' AND name='quote_batches'"
                    ).fetchone()["sql"]
                )
                .replace(" ", "")
                .lower()
            )
            if "check(schema_version=1)" in quote_batch_sql:
                self._connection.execute(
                    """
                    CREATE TABLE quote_batches_v2_migration (
                        batch_id TEXT PRIMARY KEY,
                        stream_id TEXT NOT NULL,
                        observed_at TEXT NOT NULL,
                        schema_version INTEGER NOT NULL CHECK(schema_version IN (1,2)),
                        symbol_count INTEGER NOT NULL CHECK(symbol_count = 3),
                        validation_profile TEXT NOT NULL DEFAULT 'passive_unvalidated',
                        validation_version INTEGER NOT NULL DEFAULT 0,
                        max_age_seconds REAL,
                        max_skew_seconds REAL
                    )
                    """
                )
                self._connection.execute(
                    """
                    INSERT INTO quote_batches_v2_migration(
                        batch_id,stream_id,observed_at,schema_version,symbol_count,
                        validation_profile,validation_version,max_age_seconds,max_skew_seconds
                    ) SELECT batch_id,stream_id,observed_at,schema_version,symbol_count,
                        validation_profile,validation_version,max_age_seconds,max_skew_seconds
                      FROM quote_batches
                    """
                )
                self._connection.execute("DROP TABLE quote_batches")
                self._connection.execute("ALTER TABLE quote_batches_v2_migration RENAME TO quote_batches")
            self._connection.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS idx_quotes_batch_position
                ON quotes(batch_id,batch_position) WHERE batch_id IS NOT NULL"""
            )
            self._connection.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS idx_quotes_batch_symbol
                ON quotes(batch_id,symbol) WHERE batch_id IS NOT NULL"""
            )
