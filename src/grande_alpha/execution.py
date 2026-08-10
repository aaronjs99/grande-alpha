from __future__ import annotations

from dataclasses import dataclass

MARKET_HOURS = ("regular_hours", "extended_hours", "all_day_hours")
ORDER_TYPES = ("market", "limit")
TIME_IN_FORCE = ("gfd", "gtc")

MARKET_HOURS_LABELS = {
    "regular_hours": "Regular market (9:30 AM–4:00 PM ET)",
    "extended_hours": "Extended market (7:00 AM–8:00 PM ET)",
    "all_day_hours": "24 Hour Market (8:00 PM–8:00 PM ET)",
}
ORDER_TYPE_LABELS = {
    "market": "Market order",
    "limit": "Marketable limit order",
}
TIME_IN_FORCE_LABELS = {
    "gfd": "Good for day (GFD)",
    "gtc": "Good till canceled (GTC, up to 90 days)",
}


@dataclass(frozen=True)
class ExecutionProfile:
    market_hours: str = "regular_hours"
    order_type: str = "market"
    time_in_force: str = "gfd"
    limit_offset_bps: float = 10.0

    def validate(self) -> None:
        if self.market_hours not in MARKET_HOURS:
            raise ValueError(f"Unsupported trading session: {self.market_hours}")
        if self.order_type not in ORDER_TYPES:
            raise ValueError(f"Unsupported automatic order type: {self.order_type}")
        if self.time_in_force not in TIME_IN_FORCE:
            raise ValueError(f"Unsupported time in force: {self.time_in_force}")
        if not 0 <= self.limit_offset_bps <= 100:
            raise ValueError("Limit protection must be between 0 and 100 bps")
        if self.market_hours != "regular_hours" and self.order_type != "limit":
            raise ValueError("Extended and 24 Hour Market sessions require limit orders")
        if self.order_type == "market" and self.time_in_force != "gfd":
            raise ValueError("Market orders must be good for day")

    @property
    def whole_shares_required(self) -> bool:
        return self.order_type == "limit" or self.market_hours != "regular_hours"

    @property
    def label(self) -> str:
        return (
            f"{MARKET_HOURS_LABELS[self.market_hours]} · "
            f"{ORDER_TYPE_LABELS[self.order_type]} · {TIME_IN_FORCE_LABELS[self.time_in_force]}"
        )


def execution_profile(source: object) -> ExecutionProfile:
    profile = ExecutionProfile(
        market_hours=str(getattr(source, "market_hours", "regular_hours")),
        order_type=str(getattr(source, "order_type", "market")),
        time_in_force=str(getattr(source, "time_in_force", "gfd")),
        limit_offset_bps=float(getattr(source, "limit_offset_bps", 10.0)),
    )
    profile.validate()
    return profile
