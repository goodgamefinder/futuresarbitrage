# futuresarbitrage.py

> Scan Binance, KuCoin, and Huobi simultaneously for cross-exchange price spreads on perpetual futures — no API keys, no paid data.

> 💬 **I build custom trading bots, arbitrage systems, and automation tools.**  
> Reach out on Telegram: [@smmgotop](https://t.me/smmgotop)

---

## ⚠️ Lite vs Full version

This is the **open-source lite version**. It works with Binance, KuCoin, and Huobi out of the box.

The **full version** has no exchange limits — any exchange supported by ccxt can be added with a single line. It also includes funding rate analysis, interval-aware daily/monthly P&L projections, and a combined spread + funding score.

💬 Contact [@smmgotop](https://t.me/smmgotop) on Telegram for the full version or a custom build.

---

## What is cross-exchange futures arbitrage?

When the same perpetual futures contract trades at different prices on two exchanges, a **spread** exists. Traders can exploit this spread by:

- **Buying** the contract on the cheaper exchange (going long)
- **Selling** the same contract on the more expensive exchange (going short)

Because both legs are hedged against each other, the position is **market-neutral** — your profit comes from the price difference converging, not from guessing market direction.

---

## Why spreads exist

| Reason | Explanation |
|---|---|
| **Different liquidity** | Low-liquidity exchanges show wider bid-ask spreads and larger price dislocations |
| **Different user bases** | Regional exchanges often price the same asset differently due to local demand |
| **Funding rate divergence** | Perpetual contracts use funding to anchor price to spot; when funding diverges across venues, prices diverge too |
| **Different contract specs** | Coin-margined vs USDT-margined contracts can carry a premium |
| **Latency** | Price updates propagate at different speeds; the gap can persist for minutes on smaller pairs |

---

## How to trade a spread step by step

### 1. Find the spread
Run the scanner. It outputs every pair where the price difference exceeds your `MIN_SPREAD` threshold, verified against real orderbook depth — not just last-trade price.

```
🎯 SPREAD FOUND: 0.84% between KUCOIN and BINANCE
   Buy:  KUCOIN  @ $0.18412000
   Sell: BINANCE @ $0.18566000
   Real spread (after orderbook): 0.71%
```

### 2. Check position size and fees
Before entering, estimate your all-in cost:

```
Gross spread:       0.71%
Taker fee (×2):   - 0.10%   (0.05% each side on Binance; varies on others)
Slippage buffer:  - 0.05%
─────────────────────────
Net spread:         0.56%
```

A spread below ~0.20% after fees is rarely worth the execution risk.

### 3. Enter both legs simultaneously
Open both positions as close in time as possible to lock in the spread:

```
Leg A — Buy  10 contracts of XYZUSDT on KuCoin  (long)
Leg B — Sell 10 contracts of XYZUSDT on Binance (short)
```

Use **market orders** for speed, or **limit orders** close to the current price if the spread is wide enough to absorb some waiting time.

### 4. Wait for convergence
The spread typically closes when:
- Arbitrageurs on other venues notice the gap and trade it away
- The funding rate on the more expensive side forces the price down
- A macro move hits both exchanges and prices re-anchor to spot

Most pure price spreads close within **minutes to hours** on liquid pairs.

### 5. Close both legs together
When the spread has narrowed to near zero (or reversed), close both positions:

```
Close Leg A — Sell on KuCoin
Close Leg B — Buy  on Binance
```

Profit = spread captured − fees − slippage.

---

## Risks to manage

| Risk | Mitigation |
|---|---|
| **Execution risk** | Legs may not fill simultaneously; use low-spread, high-volume pairs |
| **Exchange risk** | Keep positions open as short as possible; withdraw profits regularly |
| **Margin risk** | Each leg requires separate margin; ensure both accounts are funded before entering |
| **Funding flip** | If funding turns negative on your long leg, it erodes profit — monitor actively |
| **Liquidity gap** | The scanner probes real orderbook depth — avoid pairs that fail the liquidity check |
| **API downtime** | Have manual access to both exchanges ready as a fallback |

---

## Architecture

```
futuresarbitrage.py  (main scanner)
    │
    ├── exchange_binance.py   →  ccxt.binanceusdm   (USDM perpetuals)
    ├── exchange_kucoin.py    →  ccxt.kucoinfutures  (USDT perpetuals)
    └── exchange_huobi.py     →  ccxt.huobi (swap)   (USDT perpetuals)
                │
                └── FuturesArbitrageScanner
                        │
                        ├── _load_markets_and_tickers()
                        │     Batch fetch — one call per exchange
                        │
                        ├── _find_common_pairs()
                        │     Normalise symbols, find pairs on ≥2 exchanges
                        │
                        ├── scan()  ─── per-pair loop
                        │     ├── Compare ticker prices (all combinations)
                        │     ├── Filter by MIN_SPREAD
                        │     └── _check_liquidity()
                        │           Walk orderbook → weighted avg fill price
                        │           Filter by real spread after slippage
                        │
                        ├── _save_opportunity()
                        │     INSERT OR IGNORE into SQLite
                        │
                        └── display_results()
                              Sorted DataFrame + best opportunity summary
```

---

## Example output

```
🚀 Cross-Exchange Futures Arbitrage Scanner
   Exchanges: Binance · KuCoin · Huobi
════════════════════════════════════════════════════════════

  Settings:
  • Minimum spread:    0.3%
  • Liquidity check:   $100 USDT
  • Database:          arbitrage.db

Initialising exchanges...
  ✅ BINANCE
  ✅ KUCOIN
  ✅ HUOBI

Loading markets and tickers...
  BINANCE: fetching tickers for 412 pairs...
  ✅ BINANCE: 412 markets, 412 tickers
  KUCOIN:  fetching tickers for 280 pairs...
  ✅ KUCOIN:  280 markets, 280 tickers
  HUOBI:   fetching tickers for 318 pairs...
  ✅ HUOBI:   318 markets, 318 tickers

════════════════════════════════════════════════════════════
🔍 SCANNING FOR ARBITRAGE OPPORTUNITIES
════════════════════════════════════════════════════════════

Common pairs found across exchanges: 187

[47/187] XYZ/USDT  (BINANCE, KUCOIN, HUOBI)
  BINANCE: $0.18566000
  KUCOIN:  $0.18412000
  HUOBI:   $0.18571000

  🎯 SPREAD FOUND: 0.836% between KUCOIN and BINANCE

     Buy:  KUCOIN  @ $0.18412000
     Sell: BINANCE @ $0.18566000

  📊 Checking orderbook depth ($100.0)...
     ✅ KUCOIN  (buy):  avg fill $0.18419000
     ✅ BINANCE (sell): avg fill $0.18560000

  💰 REAL SPREAD (after orderbook): 0.766%

  ✅ ALL CHECKS PASSED
     Spread (ticker): 0.836%
     Spread (real):   0.766%
     💾 NEW PAIR — saved to database
  ════════════════════════════════════════════════════════════

════════════════════════════════════════════════════════════
🏁 SCAN COMPLETE  |  Checked: 187  |  Found: 4
════════════════════════════════════════════════════════════

📊 ARBITRAGE RESULTS

 Pair     Buy Exchange  Sell Exchange  Spread Ticker %  Spread Real %
XYZ/USDT       KUCOIN        BINANCE            0.836          0.766
ABC/USDT       HUOBI         BINANCE            0.611          0.540
...

💰 SUMMARY:
  • Average spread (ticker): 0.694%
  • Average spread (real):   0.621%
  • Best opportunity:        XYZ/USDT
    - Buy:  KUCOIN  @ $0.18419000
    - Sell: BINANCE @ $0.18560000
    - Real spread: 0.766%

⏰ Completed: 2025-06-01 11:42:07
💾 Results saved to: arbitrage.db
```

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/goodgamefinder/futuresarbitrage.git
cd futuresarbitrage
```

### 2. Create and activate a virtual environment (recommended)

```bash
python3 -m venv venv
source venv/bin/activate        # Linux / macOS
venv\Scripts\activate.bat       # Windows
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Run

```bash
python3 futuresarbitrage.py
```

---

## Configuration

All tuneable parameters are at the top of `main()` in `futuresarbitrage.py`:

| Constant | Default | Description |
|---|---|---|
| `MIN_SPREAD` | `0.3` | Minimum price spread (%) to report. Lower = more results but more noise |
| `LIQUIDITY_CHECK` | `100.0` | Position size in USDT used to probe orderbook depth |
| `DB_PATH` | `arbitrage.db` | Path to the SQLite results database |

---

## Project structure

```
futuresarbitrage.py    Main scanner class and entry point
exchange_binance.py    Binance USDM Futures connector module
exchange_kucoin.py     KuCoin Futures connector module
exchange_huobi.py      Huobi Perpetual Swaps connector module
requirements.txt
arbitrage.db           SQLite results database (created on first run)
```

---

## Database schema

```sql
CREATE TABLE arbitrage_opportunities (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    unique_hash       TEXT UNIQUE,          -- deduplication key
    pair              TEXT,
    buy_exchange      TEXT,
    sell_exchange     TEXT,
    buy_price_ticker  REAL,
    sell_price_ticker REAL,
    buy_price_real    REAL,                 -- avg fill price from orderbook
    sell_price_real   REAL,
    spread_ticker_pct REAL,
    spread_real_pct   REAL,
    found_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

Each opportunity is stored once per direction (KUCOIN→BINANCE is distinct from BINANCE→KUCOIN). Re-running the scanner will not create duplicate rows.

---

## License

MIT
