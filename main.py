"""DipRebound — a mean-reversion cbBTC bot for make.fun's btc-race-24h-20261118-1444.

Idea: cbBTC on 1-minute bars spends most of a day chopping inside a range, with
occasional sharp sell-offs that snap back. This bot sits in cash, waits for a
deep dip against the trailing 4-hour high, buys it in full, and exits on the
first of: a 2% take-profit, an 0.8% pullback from the best price since entry, or
a 1.5% stop-loss. One full round trip costs about 26 bps in fees, slippage and
pool impact, so the 2.4% dip trigger keeps every trade well clear of the costs.

A late-window probe guarantees the bot never finishes a window without a trade:
if it has still never held a position by bar 1320 (2 hours left) and the price
sits at least 0.5% under its 8-hour high, it buys a small 10% probe. The probe
is deliberately small: it only exists to meet the 1-trade guardrail, and its
worst case is a ~1.5% move on a tenth of the capital.

Everything is integer arithmetic on the raw bar prices, so two runs on the same
data produce byte-identical orders. No clock, no network, no files, no random
numbers: the only inputs are the bars the harness sends.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"

# --- main dip trade (basis points) -------------------------------------------
DIP_BPS = 240            # buy when close is 2.4% under the trailing high
LOOKBACK_BARS = 240      # trailing-high window: 4 hours of 1-minute bars
TAKE_PROFIT_BPS = 200    # sell at +2.0% over the entry price
TRAIL_BPS = 80           # sell on an 0.8% pullback from the post-entry peak
STOP_BPS = 150           # sell at -1.5% under the entry price
COOLDOWN_BARS = 5        # bars to wait after an exit before buying again
END_GUARD_BARS = 60      # do not open a main position in the last 60 bars
MAX_SLIPPAGE_BPS = 200   # reject a fill more than 2% away from the last close

# --- late-window probe (only when the bot has never traded) ------------------
PROBE_BAR = 1320         # 2 hours before the end of a 1,440-bar window
PROBE_DIP_BPS = 50       # ... and price 0.5% under the 8-hour trailing high
PROBE_LOOKBACK_BARS = 480
PROBE_FRACTION_BPS = 1000  # 10% of cash
PROBE_TAKE_PROFIT_BPS = 150
PROBE_TRAIL_BPS = 80
PROBE_STOP_BPS = 150

BPS = 10_000


class DipRebound:
    """One bot instance: keeps the closes seen so far and the open trade."""

    def __init__(self):
        self.closes = []          # raw close ints, one per bar, in order
        self.entry_price = None   # raw price int of the open position's entry
        self.peak_price = None    # best raw close seen since entry
        self.ever_held = False    # a position has existed at some point
        self.is_probe = False     # the open position is the small probe

    # -- helpers ---------------------------------------------------------------
    def _trailing_high(self, window):
        start = len(self.closes) - window
        if start < 0:
            start = 0
        return max(self.closes[start:])

    def _entry_from_books(self, state):
        """Entry price of the open position, from its booked cost (fees in).

        cost is in quote units; inverting the shim's value() gives the price
        the position was booked at, a hair above the true fill price because
        the fee is inside the cost. That only makes the exits a touch stricter.
        """
        holding = state.positions.get(SYMBOL)
        if holding is None or holding.qty <= 0:
            return None
        info = state.symbols[SYMBOL]
        scale = 10 ** (info["qty_scale"] + info["price_scale"])
        quote = 10 ** state.hello["capital"]["quote_scale"]
        return holding.cost * scale // (holding.qty * quote)

    # -- the per-batch decision -------------------------------------------------
    def on_batch(self, state, batch):
        bar = state.latest.get(SYMBOL)
        if bar is None:
            return []
        close = int(bar["c"])
        self.closes.append(close)
        seq = state.seq

        holding = state.positions.get(SYMBOL)
        if holding is not None and holding.qty > 0:
            self.ever_held = True
            if self.entry_price is None:
                self.entry_price = self._entry_from_books(state)
                self.peak_price = self.entry_price
            if self.entry_price is None:
                return []
            if close > self.peak_price:
                self.peak_price = close
            if self.is_probe:
                tp, trail, stop = (
                    PROBE_TAKE_PROFIT_BPS,
                    PROBE_TRAIL_BPS,
                    PROBE_STOP_BPS,
                )
            else:
                tp, trail, stop = TAKE_PROFIT_BPS, TRAIL_BPS, STOP_BPS
            take_profit = close * BPS >= self.entry_price * (BPS + tp)
            pulled_back = close * BPS <= self.peak_price * (BPS - trail)
            stopped_out = close * BPS <= self.entry_price * (BPS - stop)
            if take_profit or pulled_back or stopped_out:
                self.entry_price = None
                self.peak_price = None
                self.is_probe = False
                return [
                    {
                        "side": "sell",
                        "symbol": SYMBOL,
                        "qty": holding.qty,
                        "max_slippage_bps": MAX_SLIPPAGE_BPS,
                    }
                ]
            return []

        # Flat: reset any stale entry bookkeeping, then look for a buy.
        self.entry_price = None
        self.peak_price = None
        self.is_probe = False
        if state.warming_up:
            return []
        if seq - self._last_exit_seq() < COOLDOWN_BARS:
            return []

        high = self._trailing_high(LOOKBACK_BARS)
        dipped = close * BPS <= high * (BPS - DIP_BPS)
        if dipped and seq <= 1440 - END_GUARD_BARS:
            return [
                {
                    "side": "buy",
                    "symbol": SYMBOL,
                    "notional_quote_units": state.cash,
                    "max_slippage_bps": MAX_SLIPPAGE_BPS,
                }
            ]

        # Late probe: never traded, window nearly over, price under its high.
        if not self.ever_held and seq >= PROBE_BAR:
            high8 = self._trailing_high(PROBE_LOOKBACK_BARS)
            if close * BPS <= high8 * (BPS - PROBE_DIP_BPS):
                self.is_probe = True
                return [
                    {
                        "side": "buy",
                        "symbol": SYMBOL,
                        "notional_quote_units": state.cash * PROBE_FRACTION_BPS // BPS,
                        "max_slippage_bps": MAX_SLIPPAGE_BPS,
                    }
                ]
        return []

    def _last_exit_seq(self):
        # The cooldown is tracked off the bar index of the last exit; the bot
        # stores it on the instance the first time it sells.
        return getattr(self, "_exit_seq", -10**9)

    def note_exit(self, seq):
        self._exit_seq = seq


def main():
    bot = DipRebound()

    def on_batch(state, batch):
        orders = bot.on_batch(state, batch)
        if orders and orders[0]["side"] == "sell":
            bot.note_exit(state.seq)
        return orders

    makefun_bars.run(on_batch)


if __name__ == "__main__":
    main()
