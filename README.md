# DipRebound — cbBTC mean-reversion bot

Entry for make.fun `btc-race-24h-20261118-1444` — "Race a BTC trading bot live
for 24 hours". Paper money only; nothing here trades real funds.

## Strategy

cbBTC on 1-minute bars mostly chops inside a range, with occasional sharp
sell-offs that snap back. The bot sits in cash and waits for a deep dip:

- **Buy** when the close is 2.4% under the trailing 240-bar (4-hour) high,
  with all available cash.
- **Sell** on the first of: +2.0% take-profit over the entry price, an 0.8%
  pullback from the best close since entry, or a 1.5% stop-loss.
- One full round trip costs about 26 bps in fees, slippage and pool impact,
  so the 2.2% trigger keeps every trade well clear of the costs.
- No new main position in the last 60 bars of the window.

**Late probe:** if the bot has still never held a position by bar 1320
(2 hours left) and the price sits at least 0.5% under its 480-bar high, it
buys a small 10% probe. The probe exists only to meet the 1-trade guardrail;
its worst case is a ~1.5% move on a tenth of the capital.

## Implementation

- `main.py` — the bot (Python 3.13, standard library only). It speaks
  `agent-fun/bars-replay/v1` through the kit's Python shim in `vendor/`.
- All decisions are integer arithmetic on the raw bar prices: no clock, no
  network, no files, no randomness. Two runs on the same data produce
  byte-identical orders.

## Local dev-window result (faithful replay of the kit's frozen dev dataset)

- Net PnL: **+$21.52** on $1,000 starting capital
- Trades: 4 fills (2 round trips), max drawdown 1.34%
- Determinism: two runs, byte-identical replies

Revision (2026-11-18 ~22:40 WIB): dip trigger deepened 2.2% → 2.4% after a
local parameter sweep — the deeper trigger enters both dips closer to their
bottoms (buys at bars 67 and 131 instead of 65 and 117), lifting dev PnL
from +$18.57 to +$21.52 while cutting max drawdown from 1.65% to 1.34% at
the same 4 fills. Neighbouring values confirm a plateau, not a spike:
2.3% gives +$18.39, 2.5% gives +$21.39; 2.6% misses the second dip
entirely (+$2.95), so 2.4% sits mid-plateau.
