"""
Slippage Controller — staggered repricing within a budget.

Reprices unfilled orders closer to market in controlled steps:
  Attempt 1: 0.05% worse
  Attempt 2: 0.15% worse (cumulative)
  Attempt 3: 0.30% worse (cumulative)

After max_attempts, the order is escalated to Slack.
"""


class SlippageController:
    """Manages repricing budget for unfilled orders."""

    # Cumulative slippage steps as fraction of original limit price
    DEFAULT_STEPS = [0.0005, 0.0015, 0.003]

    def __init__(self, max_attempts: int = 3, max_slippage_pct: float = 0.003):
        """
        Args:
            max_attempts: Maximum reprice attempts before escalation.
            max_slippage_pct: Max cumulative slippage as decimal (0.003 = 0.3%).
        """
        self.max_attempts = max_attempts
        self.max_slippage_pct = max_slippage_pct
        self.step_schedule = self.DEFAULT_STEPS[:max_attempts]

    def can_reprice(self, ctx) -> bool:
        """True if the order has remaining reprice attempts."""
        return ctx.reprice_attempt < self.max_attempts

    def next_price(self, ctx, current_market_price: float) -> float:
        """Compute the next repriced limit.

        Sells: lower the limit closer to market (accept worse fill).
        Buys: raise the limit closer to market (pay more).

        The new price is bounded — it never crosses through the current
        market price (we don't want to place a limit that would immediately
        execute at a loss beyond our budget).

        Args:
            ctx: ExecutionContext with original_limit_price and reprice_attempt.
            current_market_price: Latest consensus price.

        Returns:
            New limit price (float, rounded to 2 decimals).
        """
        attempt = ctx.reprice_attempt  # 0-indexed, next step
        if attempt >= len(self.step_schedule):
            step = self.step_schedule[-1]
        else:
            step = self.step_schedule[attempt]

        original = ctx.original_limit_price
        action = ctx.action

        if action in ('sell', 'limit_sell', 'stop_limit_sell'):
            # Sells: lower the limit (accept less)
            new_price = original * (1 - step)
            # Don't cross below market
            new_price = max(new_price, current_market_price)
        elif action in ('buy', 'limit_buy'):
            # Buys: raise the limit (pay more)
            new_price = original * (1 + step)
            # Don't cross above market
            new_price = min(new_price, current_market_price)
        else:
            new_price = original

        return round(new_price, 2)

    def record_reprice(self, ctx, new_price: float):
        """Record a reprice attempt on the context.

        Increments the attempt counter and updates the current limit price.
        """
        ctx.reprice_attempt += 1
        ctx.current_limit_price = new_price
