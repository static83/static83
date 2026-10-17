"""Settings, read once from the environment (.env is loaded if present)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv is optional
    pass

ROOT = Path(__file__).resolve().parent.parent
ROLES_DIR = ROOT / "crew" / "roles"
RUNS_DIR = ROOT / "runs"

ROLE_ORDER = ["SCOUT", "ANALYST", "SKEPTIC", "QUANT", "RISK", "JOURNAL"]

# 8-K items SCOUT is allowed to hand off. Everything else is routine.
EDGAR_ITEMS = {
    "1.01": "Entry into a material definitive agreement",
    "2.01": "Completion of acquisition or disposition of assets",
    "2.02": "Results of operations and financial condition",
    "5.02": "Departure or appointment of directors or officers",
    "7.01": "Regulation FD disclosure",
    "8.01": "Other events",
}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass
class Settings:
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    model: str = field(default_factory=lambda: _env("DOTS_MODEL", "claude-opus-5-5"))
    alpaca_key: str = field(default_factory=lambda: _env("ALPACA_API_KEY"))
    alpaca_secret: str = field(default_factory=lambda: _env("ALPACA_SECRET_KEY"))
    alpaca_paper_url: str = field(
        default_factory=lambda: _env("ALPACA_PAPER_URL", "https://paper-api.alpaca.markets")
    )
    alpaca_data_url: str = field(
        default_factory=lambda: _env("ALPACA_DATA_URL", "https://data.alpaca.markets")
    )
    edgar_user_agent: str = field(
        default_factory=lambda: _env("EDGAR_USER_AGENT", "dots-crew contact@example.com")
    )
    start_balance: float = field(default_factory=lambda: float(_env("DOTS_START_BALANCE", "37")))
    max_candidates: int = field(default_factory=lambda: int(_env("DOTS_MAX_CANDIDATES", "3")))
    max_rounds: int = field(default_factory=lambda: int(_env("DOTS_MAX_ROUNDS", "4")))
    min_market_cap: float = field(
        default_factory=lambda: float(_env("DOTS_MIN_MARKET_CAP_USD", "300000000"))
    )

    @property
    def alpaca_configured(self) -> bool:
        return bool(self.alpaca_key and self.alpaca_secret)

    @property
    def paper_only(self) -> bool:
        """Refuse to run against anything that is not Alpaca's paper endpoint."""
        return "paper-api.alpaca.markets" in self.alpaca_paper_url


settings = Settings()
