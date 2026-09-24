"""Broker-isolated mixed-portfolio research. Targets are not executable orders."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from grande_alpha.configuration.json_inputs import load_json
from grande_alpha.data.earnings import _number, _time, screen


@dataclass(frozen=True)
class AllocationPolicy:
    # Research starting points, not optimized parameters or personal risk advice.
    max_invested_fraction: float = .8
    max_stock_fraction: float = .6
    max_etf_fraction: float = .2
    max_single_stock_fraction: float = .2
    max_sector_fraction: float = .4
    max_gross_leverage_proxy: float = 1.0
    volatility_floor: float = .01
    max_daily_volatility: float = .10
    min_regime_confidence: float = .6
    max_spread_bps: float = 20
    max_signal_age_seconds: float = 30
    max_risk_age_days: float = 3
    max_stock_holding_days: float = 20
    max_etf_holding_days: float = 2
    rebalance_band_fraction: float = .03

    def validate(self) -> None:
        for item in fields(self):
            value = _number(getattr(self, item.name), item.name, positive=True)
            if ("fraction" in item.name or item.name == "min_regime_confidence") and value > 1:
                raise ValueError(f"{item.name} must be at most one")
        if self.volatility_floor > self.max_daily_volatility:
            raise ValueError("Volatility floor exceeds maximum")


def template() -> dict[str, Any]:
    from grande_alpha.data.earnings import template as earnings_template

    return {
        "capital_usd": None,
        "policy": asdict(AllocationPolicy()),
        "earnings": earnings_template(),
        "market": {key: None for key in (
            "observed_at", "available_at", "regime", "confidence", "daily_volatility", "spread_bps",
        )},
        "stock_risk": [{key: None for key in ("symbol", "sector", "daily_volatility", "available_at")}],
        "holdings": [],
    }


def plan(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {
        "capital_usd", "policy", "earnings", "market", "stock_risk", "holdings",
    }:
        raise ValueError("Exact mixed-portfolio request schema is required")
    capital = _number(payload["capital_usd"], "capital_usd", positive=True)
    raw_policy = payload["policy"]
    if not isinstance(raw_policy, dict) or set(raw_policy) != {f.name for f in fields(AllocationPolicy)}:
        raise ValueError("Every allocation policy field must be supplied")
    policy = AllocationPolicy(**raw_policy)
    policy.validate()
    earnings = screen(payload["earnings"])
    now = _time(earnings["as_of"], "as_of")
    if any(row["status"] == "INVALID_INPUT" for row in earnings["results"]):
        raise ValueError("Invalid earnings rows must be corrected before allocation")
    market = payload["market"]
    if not isinstance(market, dict) or set(market) != {
        "observed_at", "available_at", "regime", "confidence", "daily_volatility", "spread_bps",
    }:
        raise ValueError("Exact market observation fields are required")
    observed = _time(market["observed_at"], "observed_at")
    available = _time(market["available_at"], "available_at")
    if not observed <= available <= now:
        raise ValueError("Market observation contains future information")
    if market["regime"] not in {"bull", "bear", "neutral"}:
        raise ValueError("Market regime must be bull, bear or neutral")
    confidence = _number(market["confidence"], "confidence")
    spread = _number(market["spread_bps"], "spread_bps")
    volatility = _number(market["daily_volatility"], "daily_volatility", positive=True)
    if not 0 <= confidence <= 1 or spread < 0:
        raise ValueError("Invalid market confidence or spread")
    risk_rows = payload["stock_risk"]
    if not isinstance(risk_rows, list) or len(risk_rows) > 10_000:
        raise ValueError("stock_risk must be a bounded list")
    risk = {}
    for row in risk_rows:
        if not isinstance(row, dict) or set(row) != {"symbol", "sector", "daily_volatility", "available_at"}:
            raise ValueError("Exact stock-risk fields are required")
        if (not isinstance(row["symbol"], str) or not row["symbol"]
                or not isinstance(row["sector"], str) or not row["sector"].strip()
                or row["symbol"] in risk):
            raise ValueError("Stock risk requires unique symbols and nonempty sectors")
        timestamp = _time(row["available_at"], "risk available_at")
        if timestamp > now:
            raise ValueError("Stock risk contains future information")
        risk[row["symbol"]] = (row["sector"], _number(row["daily_volatility"], "volatility", positive=True),
                                (now-timestamp).total_seconds()/86400)
    holdings = payload["holdings"]
    if not isinstance(holdings, list) or len(holdings) > 10_000:
        raise ValueError("holdings must be a bounded list")
    existing, exits = {}, {}
    for row in holdings:
        if not isinstance(row, dict) or set(row) != {"symbol", "market_value", "opened_at", "thesis_valid"}:
            raise ValueError("Exact holding fields are required")
        symbol = row["symbol"]
        if not isinstance(symbol, str) or not symbol or symbol in existing:
            raise ValueError("Holdings must have unique symbols")
        amount = _number(row["market_value"], "holding value")
        if amount < 0 or type(row["thesis_valid"]) is not bool:
            raise ValueError("Long-only holdings and explicit thesis status are required")
        opened = _time(row["opened_at"], "opened_at")
        if opened > now:
            raise ValueError("Holding cannot open in the future")
        age = (now-opened).total_seconds()/86400
        horizon = policy.max_etf_holding_days if symbol in {"TQQQ", "SQQQ"} else policy.max_stock_holding_days
        existing[symbol] = amount
        if not row["thesis_valid"] or age >= horizon:
            exits[symbol] = "THESIS_INVALID" if not row["thesis_valid"] else "HOLDING_HORIZON_REACHED"
    if sum(existing.values()) > capital + 1e-8:
        raise ValueError("Holdings exceed supplied total portfolio capital")
    scores, exclusions = {}, {}
    seen = set()
    for row in earnings["results"]:
        symbol = row["symbol"]
        if symbol in seen:
            raise ValueError("Multiple earnings events for one symbol require explicit event selection")
        seen.add(symbol)
        if symbol in {"TQQQ", "SQQQ"}:
            raise ValueError("Leveraged ETFs cannot also be earnings-stock candidates")
        if row["status"] != "RESEARCH_CANDIDATE" or symbol in exits:
            exclusions[symbol] = exits.get(symbol, "EARNINGS_SCREEN_FAILED")
            continue
        if symbol not in risk:
            exclusions[symbol] = "MISSING_STOCK_RISK"
            continue
        _, vol, age = risk[symbol]
        if age > policy.max_risk_age_days or vol > policy.max_daily_volatility:
            exclusions[symbol] = "STALE_OR_EXCESSIVE_STOCK_RISK"
            continue
        metrics = row["metrics"]
        limits = payload["earnings"]["thresholds"]
        strength = min(3, metrics["price_scaled_surprise_bps"]/limits["min_surprise_bps"])
        strength *= min(3, metrics["post_announcement_momentum_bps"]/limits["min_momentum_bps"])
        scores[symbol] = strength * policy.volatility_floor/max(vol, policy.volatility_floor)
    etf = {"bull": "TQQQ", "bear": "SQQQ", "neutral": None}[market["regime"]]
    etf_score = 0.0
    if (etf is not None and etf not in exits and confidence >= policy.min_regime_confidence
            and (now-observed).total_seconds() <= policy.max_signal_age_seconds
            and spread <= policy.max_spread_bps and volatility <= policy.max_daily_volatility):
        etf_score = confidence * policy.volatility_floor/max(volatility, policy.volatility_floor)
    elif etf:
        exclusions[etf] = exits.get(etf, "ETF_SIGNAL_UNQUALIFIED")
    # Scores are deterministic heuristics, not probabilities or estimated returns.
    stock_score = sum(scores.values())/len(scores) if scores else 0.0
    total_score = stock_score + etf_score
    stock_budget = min(policy.max_stock_fraction, policy.max_invested_fraction*stock_score/total_score) if total_score else 0
    etf_budget = min(policy.max_etf_fraction, policy.max_invested_fraction*etf_score/total_score) if total_score else 0
    weights, sectors = {}, {}
    for symbol in sorted(scores, key=lambda s: (-scores[s], s)):
        sector = risk[symbol][0]
        weight = min(stock_budget*scores[symbol]/sum(scores.values()), policy.max_single_stock_fraction,
                     max(0, policy.max_sector_fraction-sectors.get(sector, 0)))
        weights[symbol] = weight
        sectors[sector] = sectors.get(sector, 0) + weight
    if etf_score:
        weights[etf] = etf_budget
    gross_proxy = sum(w*(3 if s in {"TQQQ", "SQQQ"} else 1) for s, w in weights.items())
    scale = min(1, policy.max_gross_leverage_proxy/gross_proxy) if gross_proxy else 1
    targets = {s: round(capital*w*scale, 8) for s, w in weights.items() if w > 0}
    deltas = []
    for symbol in sorted(set(existing) | set(targets)):
        delta = targets.get(symbol, 0)-existing.get(symbol, 0)
        deltas.append({"symbol": symbol, "target_usd": targets.get(symbol, 0), "delta_usd": round(delta, 8),
                       "review_rebalance": symbol in exits or abs(delta) >= capital*policy.rebalance_band_fraction,
                       "exit_reason": exits.get(symbol)})
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return {
        "schema_version": 1, "status": "RESEARCH_TARGETS_ONLY", "as_of": now.isoformat(),
        "input_sha256": hashlib.sha256(canonical.encode()).hexdigest(), "policy": asdict(policy),
        "targets_usd": targets, "cash_target_usd": round(capital-sum(targets.values()), 8),
        "gross_leverage_proxy": gross_proxy*scale, "rebalance_reviews": deltas, "exclusions": exclusions,
        "authority_granted": False, "live_eligible": False, "backtest_performed": False,
        "limitations": ["Heuristic scores are not predicted returns", "No covariance or hedge-offset credit",
                        "Targets are not fills; no unsettled sale proceeds may fund orders",
                        "Risk metadata and earnings provenance are user-supplied and unverified"],
    }


def command_plan(args: Any) -> int:
    print(json.dumps(plan(load_json(Path(args.input), max_bytes=8_000_000)), indent=2, allow_nan=False))
    return 0


def command_template(args: Any) -> int:
    print(json.dumps(template(), indent=2))
    return 0
