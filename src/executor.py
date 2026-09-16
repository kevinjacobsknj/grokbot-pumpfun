"""Paper-only execution for research.

HARD SAFETY: LiveExecutor is permanently disabled. `build_executor` refuses
`mode: live` with ConfigError. No wallet signing, no transaction construction,
no broadcast, no Jito path.

PaperExecutor simulates hypothetical fills from observable curve/market data.
Signal/quote prices are NEVER treated as guaranteed fills — fills come from
curve math (fees + impact). If a realistic quote cannot be obtained, the
attempt is FAILED/UNTRADEABLE (no invented fills).
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from .curve import (
    TOTAL_SUPPLY,
    CurveState,
    buy_quote,
    price_from_reserves,
    sell_quote,
    state_from_any,
)
from .models import Config, ConfigError, Position, Token

log = logging.getLogger(__name__)

PAPER_TX = "paper"
DRY_RUN_TX = PAPER_TX  # backwards-compatible alias

TradeStatus = Literal[
    "FILLED",
    "FAILED",
    "UNTRADEABLE",
    "PARTIAL",
]

__all__ = [
    "TOTAL_SUPPLY",
    "PAPER_TX",
    "DRY_RUN_TX",
    "BaseExecutor",
    "DryRunExecutor",
    "PaperExecutor",
    "PaperTradeAttempt",
    "ExecutionResult",
    "LiveExecutor",
    "build_executor",
    "new_position",
    "price_from_reserves",
]


class ExecutionResult(BaseModel):
    """Outcome of one buy/sell attempt."""

    ok: bool
    tx_hash: str = ""
    price: float = 0.0  # average fill price, not the signal quote
    token_amount: float = 0.0
    sol_amount: float = 0.0
    fee_sol: float = 0.0
    impact_pct: float = 0.0
    error: str = ""
    status: TradeStatus = "FAILED"
    state_after: CurveState | None = Field(default=None)
    quoted_price: float = 0.0
    simulated_fill_price: float = 0.0
    estimated_slippage: float = 0.0
    route_info: dict[str, Any] = Field(default_factory=dict)


class PaperTradeAttempt(BaseModel):
    """Full audit record for one paper trade attempt (entry and/or exit)."""

    attempt_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    observation_id: str = ""
    mint: str = ""
    side: Literal["buy", "sell"] = "buy"
    status: TradeStatus = "FAILED"

    signal_timestamp: float = 0.0
    decision_timestamp: float = 0.0
    paper_order_timestamp: float = 0.0

    quoted_entry_price: float = 0.0
    simulated_fill_price: float = 0.0
    estimated_slippage: float = 0.0
    fees: float = 0.0
    route_quote_info: dict[str, Any] = Field(default_factory=dict)

    market_cap: float = 0.0
    liquidity: float = 0.0
    bonding_curve_state: dict[str, Any] = Field(default_factory=dict)
    position_size: float = 0.0

    exit_timestamp: float = 0.0
    exit_quote: float = 0.0
    simulated_exit_fill: float = 0.0
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    max_favorable_excursion: float = 0.0
    max_adverse_excursion: float = 0.0
    holding_time: float = 0.0

    error: str = ""
    tx_hash: str = ""
    token_amount: float = 0.0
    sol_amount: float = 0.0


class BaseExecutor:
    """Shared quoting / curve state helpers. No chain I/O beyond REST quotes."""

    def __init__(self, config: Config, client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self.market = config.market
        self._client = client
        self._owns_client = client is None
        self.last_attempt: PaperTradeAttempt | None = None

    async def __aenter__(self) -> BaseExecutor:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.config.data.rest_url,
                timeout=self.config.data.request_timeout,
            )
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _coin(self, mint: str) -> dict[str, Any]:
        if self._client is None:
            return {}
        try:
            resp = await self._client.get(f"/coins/{mint}")
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log.warning("quote data unavailable for %s: %s", mint, exc)
            return {}
        return data if isinstance(data, dict) else {}

    async def curve(self, mint: str, market_cap_sol: float = 0.0) -> CurveState | None:
        return state_from_any(await self._coin(mint), market_cap_sol)

    async def price(self, mint: str) -> float:
        state = await self.curve(mint)
        return state.spot_price if state else 0.0

    async def buy(self, token: Token, size_sol: float) -> ExecutionResult:
        raise NotImplementedError

    async def sell(self, position: Position, fraction: float = 1.0) -> ExecutionResult:
        raise NotImplementedError

    def plan_buy(self, state: CurveState, size_sol: float) -> ExecutionResult:
        quote = buy_quote(state, size_sol, self.market.trade_fee_pct)
        if not quote.ok:
            return ExecutionResult(ok=False, error=quote.reason, status="FAILED")
        if quote.impact_pct > self.market.max_price_impact_pct:
            return ExecutionResult(
                ok=False,
                error=(
                    f"price impact {quote.impact_pct:.2f}% above cap "
                    f"{self.market.max_price_impact_pct:.2f}%"
                ),
                impact_pct=quote.impact_pct,
                status="UNTRADEABLE",
                quoted_price=state.spot_price,
            )
        slippage = (
            ((quote.avg_price - state.spot_price) / state.spot_price * 100.0)
            if state.spot_price > 0
            else 0.0
        )
        return ExecutionResult(
            ok=True,
            price=quote.avg_price,
            token_amount=quote.tokens,
            sol_amount=size_sol,
            fee_sol=quote.fee_sol,
            impact_pct=quote.impact_pct,
            state_after=quote.state_after,
            status="FILLED",
            quoted_price=state.spot_price,
            simulated_fill_price=quote.avg_price,
            estimated_slippage=slippage,
            route_info={
                "venue": "pumpfun_bonding_curve",
                "fee_pct": self.market.trade_fee_pct,
                "sol_reserves": state.sol_reserves,
                "token_reserves": state.token_reserves,
            },
        )

    def plan_sell(self, state: CurveState, tokens: float) -> ExecutionResult:
        quote = sell_quote(state, tokens, self.market.trade_fee_pct)
        if not quote.ok:
            return ExecutionResult(ok=False, error=quote.reason, status="FAILED")
        slippage = (
            ((state.spot_price - quote.avg_price) / state.spot_price * 100.0)
            if state.spot_price > 0
            else 0.0
        )
        return ExecutionResult(
            ok=True,
            price=quote.avg_price,
            token_amount=tokens,
            sol_amount=quote.sol_out,
            fee_sol=quote.fee_sol,
            impact_pct=quote.impact_pct,
            state_after=quote.state_after,
            status="FILLED",
            quoted_price=state.spot_price,
            simulated_fill_price=quote.avg_price,
            estimated_slippage=slippage,
            route_info={
                "venue": "pumpfun_bonding_curve",
                "fee_pct": self.market.trade_fee_pct,
            },
        )

    @staticmethod
    def _portion(position: Position, fraction: float) -> float:
        fraction = max(0.0, min(1.0, fraction))
        tokens = position.token_amount * fraction
        if position.token_amount - tokens < position.token_amount * 0.01:
            tokens = position.token_amount
        return tokens

    @staticmethod
    def _curve_dict(state: CurveState | None) -> dict[str, Any]:
        if state is None:
            return {}
        return {
            "sol_reserves": state.sol_reserves,
            "token_reserves": state.token_reserves,
            "real_sol": state.real_sol,
            "spot_price": state.spot_price,
            "complete": state.complete,
            "progress": state.progress,
        }


class PaperExecutor(BaseExecutor):
    """Simulate hypothetical entries/exits from observable market data only."""

    def __init__(self, config: Config, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(config, client)
        self.attempts: list[PaperTradeAttempt] = []

    def _record(self, attempt: PaperTradeAttempt) -> PaperTradeAttempt:
        self.last_attempt = attempt
        self.attempts.append(attempt)
        return attempt

    async def buy(
        self,
        token: Token,
        size_sol: float,
        *,
        signal_timestamp: float = 0.0,
        decision_timestamp: float = 0.0,
        observation_id: str = "",
    ) -> ExecutionResult:
        now = time.time()
        attempt = PaperTradeAttempt(
            observation_id=observation_id,
            mint=token.mint,
            side="buy",
            signal_timestamp=signal_timestamp or token.created_timestamp or now,
            decision_timestamp=decision_timestamp or now,
            paper_order_timestamp=now,
            position_size=size_sol,
            market_cap=token.market_cap_sol,
            liquidity=token.sol_in_curve,
        )

        state = await self.curve(token.mint, token.market_cap_sol)
        attempt.bonding_curve_state = self._curve_dict(state)
        if state is not None:
            attempt.quoted_entry_price = state.spot_price
            attempt.liquidity = state.real_sol

        if state is None:
            attempt.status = "UNTRADEABLE"
            attempt.error = "curve state unavailable — no invented fill"
            self._record(attempt)
            log.warning(
                "[paper] buy %s UNTRADEABLE: curve state unknown", token.mint[:8]
            )
            return ExecutionResult(
                ok=False,
                error=attempt.error,
                status="UNTRADEABLE",
            )

        result = self.plan_buy(state, size_sol)
        attempt.quoted_entry_price = result.quoted_price or state.spot_price
        attempt.estimated_slippage = result.estimated_slippage
        attempt.fees = result.fee_sol
        attempt.route_quote_info = dict(result.route_info)

        if not result.ok:
            attempt.status = result.status if result.status != "FAILED" else (
                "UNTRADEABLE" if "impact" in result.error.lower() or "влияние" in result.error.lower()
                else "FAILED"
            )
            # Normalize Russian impact errors from older plan paths
            if "impact" in result.error.lower() or "влияние" in result.error.lower():
                attempt.status = "UNTRADEABLE"
            attempt.error = result.error
            self._record(attempt)
            log.warning("[paper] buy %s %s: %s", token.mint[:8], attempt.status, result.error)
            result.status = attempt.status
            return result

        result.tx_hash = PAPER_TX
        result.status = "FILLED"
        attempt.status = "FILLED"
        attempt.simulated_fill_price = result.price
        attempt.token_amount = result.token_amount
        attempt.sol_amount = result.sol_amount
        attempt.tx_hash = PAPER_TX
        attempt.bonding_curve_state = self._curve_dict(result.state_after) or attempt.bonding_curve_state
        self._record(attempt)
        log.info(
            "[paper] bought %s: %.4f SOL -> %.0f tokens @ %.12f "
            "(fee %.4f SOL, impact %.2f%%, slippage %.2f%%)",
            token.mint[:8],
            size_sol,
            result.token_amount,
            result.price,
            result.fee_sol,
            result.impact_pct,
            result.estimated_slippage,
        )
        return result

    async def sell(
        self,
        position: Position,
        fraction: float = 1.0,
        *,
        observation_id: str = "",
        peak_price: float | None = None,
        trough_price: float | None = None,
    ) -> ExecutionResult:
        now = time.time()
        attempt = PaperTradeAttempt(
            observation_id=observation_id,
            mint=position.mint,
            side="sell",
            signal_timestamp=position.opened_at,
            decision_timestamp=now,
            paper_order_timestamp=now,
            position_size=position.sol_spent,
            quoted_entry_price=position.entry_price,
            simulated_fill_price=position.entry_price,
        )

        state = await self.curve(position.mint)
        attempt.bonding_curve_state = self._curve_dict(state)
        if state is None:
            attempt.status = "UNTRADEABLE"
            attempt.error = "curve state unavailable — no invented fill"
            self._record(attempt)
            return ExecutionResult(ok=False, error=attempt.error, status="UNTRADEABLE")

        tokens = self._portion(position, fraction)
        if state.complete:
            # Graduated: no bonding-curve quote — mark UNTRADEABLE rather than invent AMM fill.
            attempt.status = "UNTRADEABLE"
            attempt.error = (
                "token graduated to Raydium — no realistic bonding-curve exit quote"
            )
            attempt.exit_timestamp = now
            attempt.exit_quote = state.spot_price
            attempt.holding_time = max(0.0, now - position.opened_at)
            self._record(attempt)
            log.warning(
                "[paper] sell %s UNTRADEABLE: graduated, no AMM quote",
                position.mint[:8],
            )
            return ExecutionResult(
                ok=False,
                error=attempt.error,
                status="UNTRADEABLE",
                quoted_price=state.spot_price,
            )

        result = self.plan_sell(state, tokens)
        attempt.exit_quote = result.quoted_price or state.spot_price
        attempt.fees = result.fee_sol
        attempt.estimated_slippage = result.estimated_slippage
        attempt.route_quote_info = dict(result.route_info)
        attempt.exit_timestamp = now
        attempt.holding_time = max(0.0, now - position.opened_at)

        if not result.ok:
            attempt.status = "FAILED"
            attempt.error = result.error
            self._record(attempt)
            result.status = "FAILED"
            return result

        result.tx_hash = PAPER_TX
        result.status = "FILLED" if fraction >= 0.99 or tokens >= position.token_amount * 0.99 else "PARTIAL"
        attempt.status = result.status
        attempt.simulated_exit_fill = result.price
        attempt.token_amount = result.token_amount
        attempt.sol_amount = result.sol_amount
        attempt.tx_hash = PAPER_TX

        gross = result.sol_amount + result.fee_sol
        cost_basis = position.sol_spent * (tokens / position.token_amount) if position.token_amount else 0.0
        attempt.gross_pnl = gross - cost_basis
        attempt.net_pnl = result.sol_amount - cost_basis

        ref_peak = peak_price if peak_price is not None else position.peak_price
        ref_trough = trough_price if trough_price is not None else (
            position.trough_price if position.trough_price > 0 else position.entry_price
        )
        if position.entry_price > 0:
            attempt.max_favorable_excursion = max(
                0.0, (ref_peak - position.entry_price) / position.entry_price
            )
            attempt.max_adverse_excursion = max(
                0.0, (position.entry_price - ref_trough) / position.entry_price
            )

        self._record(attempt)
        log.info(
            "[paper] sold %s: %.0f tokens -> %.4f SOL @ %.12f "
            "(fee %.4f SOL, impact %.2f%%)",
            position.mint[:8],
            tokens,
            result.sol_amount,
            result.price,
            result.fee_sol,
            result.impact_pct,
        )
        return result


# Backwards-compatible alias used throughout existing tests / pipeline.
DryRunExecutor = PaperExecutor


class LiveExecutor:
    """Permanently disabled. Instantiating or calling raises ConfigError.

    Paper-research builds have zero runtime transaction path. Do not implement
    signing, Keypair loading, or Jito submission here.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise ConfigError(
            "LiveExecutor permanently disabled in paper-research build — "
            "use PaperExecutor (mode: dry-run / paper)"
        )

    async def buy(self, *args: Any, **kwargs: Any) -> ExecutionResult:
        raise ConfigError("LiveExecutor permanently disabled")

    async def sell(self, *args: Any, **kwargs: Any) -> ExecutionResult:
        raise ConfigError("LiveExecutor permanently disabled")


def build_executor(config: Config, client: httpx.AsyncClient | None = None) -> BaseExecutor:
    """Return PaperExecutor. Live mode hard-fails."""
    if config.is_live:
        raise ConfigError(
            "build_executor refused mode: live — paper-research build has no "
            "real-money path; set mode: dry-run or mode: paper"
        )
    return PaperExecutor(config, client)


def new_position(token: Token, result: ExecutionResult, score: float) -> Position:
    return Position(
        mint=token.mint,
        symbol=token.symbol,
        creator=token.creator,
        entry_price=result.price,
        peak_price=result.price,
        trough_price=result.price,  # Initialize trough for MAE tracking
        sol_spent=result.sol_amount,
        token_amount=result.token_amount,
        opened_at=time.time(),
        tx_hash=result.tx_hash,
        score=score,
    )
