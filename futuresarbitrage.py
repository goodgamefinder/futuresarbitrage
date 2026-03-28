"""
futuresarbitrage.py
Cross-exchange futures spread scanner — Binance / KuCoin / Huobi.

Scans all perpetual futures pairs available on all three exchanges,
finds pairs where the same contract trades at a materially different
price across venues, verifies real orderbook liquidity at the target
position size, and saves qualifying opportunities to SQLite.

NOTE: This is the open-source lite version (Binance, KuCoin, Huobi).
The full version has no exchange limits — any ccxt-supported exchange
can be added with a single line. It also includes funding rate analysis,
interval-aware daily/monthly P&L projections, and combined spread +
funding scoring. Contact @smmgotop on Telegram for the full version.

Usage:
  python3 futuresarbitrage.py

Tune the constants in main() to adjust thresholds and position size.
"""

import ccxt
import pandas as pd
import sqlite3
import hashlib
from datetime import datetime
from itertools import combinations

import exchange_binance
import exchange_kucoin
import exchange_huobi


class FuturesArbitrageScanner:
    """
    Scans three exchanges for cross-exchange price spreads on perpetual
    futures contracts. Filters by minimum spread and orderbook liquidity.
    """

    EXCHANGE_LOADERS = {
        "binance": exchange_binance.create,
        "kucoin":  exchange_kucoin.create,
        "huobi":   exchange_huobi.create,
    }

    def __init__(self, min_spread: float, liquidity_check: float, db_path: str):
        """
        Args:
            min_spread:      Minimum price spread (%) to report an opportunity.
            liquidity_check: Position size in USDT used to probe orderbook depth.
            db_path:         Path to the SQLite database file.
        """
        self.min_spread      = min_spread
        self.liquidity_check = liquidity_check
        self.db_path         = db_path
        self.exchange_names  = list(self.EXCHANGE_LOADERS.keys())
        self.exchanges       = {}

        self._init_database()
        self._init_exchanges()

    # ── Initialisation ────────────────────────────────────────────────────────

    def _init_exchanges(self):
        """Load each exchange module and create the ccxt instance."""
        print("Initialising exchanges...")
        for name, loader in self.EXCHANGE_LOADERS.items():
            try:
                self.exchanges[name] = loader()
                print(f"  ✅ {name.upper()}")
            except Exception as e:
                print(f"  ❌ {name.upper()}: {e}")

    def _init_database(self):
        """Create the opportunities table if it does not already exist."""
        conn = sqlite3.connect(self.db_path)
        cur  = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS arbitrage_opportunities (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                unique_hash      TEXT UNIQUE,
                pair             TEXT,
                buy_exchange     TEXT,
                sell_exchange    TEXT,
                buy_price_ticker REAL,
                sell_price_ticker REAL,
                buy_price_real   REAL,
                sell_price_real  REAL,
                spread_ticker_pct REAL,
                spread_real_pct  REAL,
                found_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _hash(self, pair: str, buy: str, sell: str) -> str:
        """MD5 hash used as a deduplication key in the database."""
        return hashlib.md5(f"{pair}_{buy}_{sell}".encode()).hexdigest()

    def _normalize_symbol(self, symbol: str) -> str:
        """
        Strip exchange-specific suffixes so the same asset can be matched
        across venues (e.g. BTC/USDT:USDT → BTC/USDT).
        """
        return symbol.split(':')[0].split('-')[0]

    # ── Market / ticker loading ───────────────────────────────────────────────

    def _load_markets_and_tickers(self):
        """
        Fetch active swap/future markets and all tickers from each exchange
        in a single batch call per exchange (minimises API round-trips).
        """
        markets_data = {}
        tickers_data = {}

        print("\nLoading markets and tickers...")
        for name, ex in self.exchanges.items():
            try:
                markets = ex.fetch_markets()
                futures = {
                    m['symbol']: m
                    for m in markets
                    if m.get('active') and m.get('type') in ('swap', 'future')
                }
                markets_data[name] = futures
                print(f"  {name.upper()}: fetching tickers for {len(futures)} pairs...")

                all_tickers = ex.fetch_tickers()
                tickers_data[name] = {
                    sym: t for sym, t in all_tickers.items() if sym in futures
                }
                print(f"  ✅ {name.upper()}: {len(futures)} markets, {len(tickers_data[name])} tickers")

            except Exception as e:
                print(f"  ❌ {name.upper()}: {e}")
                markets_data[name] = {}
                tickers_data[name] = {}

        return markets_data, tickers_data

    def _find_common_pairs(self, markets_data: dict) -> dict:
        """
        Build a normalised symbol index and return only pairs that are
        traded on at least two of the three exchanges.
        """
        normalized = {}
        for name, markets in markets_data.items():
            normalized[name] = {}
            for symbol, market in markets.items():
                norm = self._normalize_symbol(symbol)
                normalized[name][norm] = {"original": symbol, "market": market}

        all_symbols = set()
        for syms in normalized.values():
            all_symbols.update(syms.keys())

        common = {}
        for norm in all_symbols:
            present = {
                name: normalized[name][norm]
                for name in self.exchange_names
                if name in normalized and norm in normalized[name]
            }
            if len(present) >= 2:
                common[norm] = present

        return common

    # ── Orderbook liquidity check ─────────────────────────────────────────────

    def _check_liquidity(self, exchange_name: str, symbol: str,
                         side: str, amount_usd: float) -> dict:
        """
        Walk the orderbook and compute the volume-weighted average fill
        price for a position of `amount_usd` USDT.

        Returns a dict with keys:
          success   (bool)
          avg_price (float)  — only present when success=True
          reason    (str)    — only present when success=False
        """
        try:
            ex        = self.exchanges[exchange_name]
            orderbook = ex.fetch_order_book(symbol, limit=20)
            orders    = orderbook['asks'] if side == 'buy' else orderbook['bids']

            cumulative_usd    = 0.0
            cumulative_amount = 0.0
            weighted_price    = 0.0

            for order in orders:
                # Orderbook entries can be [price, amount] lists or dicts
                if isinstance(order, (list, tuple)) and len(order) >= 2:
                    price, amount = float(order[0]), float(order[1])
                elif isinstance(order, dict):
                    price  = float(order.get('price', 0))
                    amount = float(order.get('amount', 0))
                else:
                    continue

                if not price or not amount:
                    continue

                order_usd = price * amount

                if cumulative_usd + order_usd <= amount_usd:
                    cumulative_usd    += order_usd
                    cumulative_amount += amount
                    weighted_price    += price * amount
                else:
                    remaining_usd     = amount_usd - cumulative_usd
                    remaining_amount  = remaining_usd / price
                    cumulative_usd    += remaining_usd
                    cumulative_amount += remaining_amount
                    weighted_price    += price * remaining_amount
                    break

            if cumulative_amount > 0:
                return {
                    "success":   True,
                    "avg_price": weighted_price / cumulative_amount,
                    "total_usd": cumulative_usd,
                }
            return {"success": False, "reason": "Insufficient liquidity in orderbook"}

        except Exception as e:
            return {"success": False, "reason": str(e)}

    # ── Database persistence ──────────────────────────────────────────────────

    def _save_opportunity(self, opp: dict) -> bool:
        """
        Insert a new arbitrage opportunity.
        Silently skips duplicates (same pair + exchange direction already stored).
        Returns True if the record was inserted, False if it already existed.
        """
        conn = sqlite3.connect(self.db_path)
        cur  = conn.cursor()
        unique_hash = self._hash(opp['Pair'], opp['Buy Exchange'], opp['Sell Exchange'])
        try:
            cur.execute("""
                INSERT INTO arbitrage_opportunities (
                    unique_hash, pair, buy_exchange, sell_exchange,
                    buy_price_ticker, sell_price_ticker,
                    buy_price_real,   sell_price_real,
                    spread_ticker_pct, spread_real_pct
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                unique_hash,
                opp['Pair'],
                opp['Buy Exchange'],
                opp['Sell Exchange'],
                opp['Buy Price (ticker)'],
                opp['Sell Price (ticker)'],
                opp['Buy Price (real)'],
                opp['Sell Price (real)'],
                opp['Spread Ticker %'],
                opp['Spread Real %'],
            ))
            conn.commit()
            print("     💾 NEW PAIR — saved to database")
            return True
        except sqlite3.IntegrityError:
            print("     ⏭️  Already in database, skipping")
            return False
        finally:
            conn.close()

    # ── Main scan ─────────────────────────────────────────────────────────────

    def scan(self) -> list:
        """
        Run a full scan:
          1. Load markets + tickers from all three exchanges.
          2. Find pairs common to at least two exchanges.
          3. For each pair, compare prices across all exchange pairs.
          4. When spread exceeds min_spread, verify orderbook liquidity.
          5. Save qualifying opportunities and return them as a list of dicts.
        """
        markets_data, tickers_data = self._load_markets_and_tickers()
        common_pairs = self._find_common_pairs(markets_data)

        print(f"\n{'='*80}")
        print("🔍 SCANNING FOR ARBITRAGE OPPORTUNITIES")
        print(f"{'='*80}\n")
        print(f"Common pairs found across exchanges: {len(common_pairs)}")

        # Print per-exchange pair counts
        counts = {}
        for pair_data in common_pairs.values():
            for ex in pair_data:
                counts[ex] = counts.get(ex, 0) + 1
        for ex, n in sorted(counts.items()):
            print(f"  • {ex.upper()}: {n} pairs")
        print()

        opportunities = []
        total = len(common_pairs)

        for idx, (norm_symbol, exchange_data) in enumerate(common_pairs.items(), 1):
            if len(exchange_data) < 2:
                continue

            available = list(exchange_data.keys())
            print(f"[{idx}/{total}] {norm_symbol}  ({', '.join(ex.upper() for ex in available)})")

            # Collect latest prices from each exchange
            prices = {}
            for ex_name in available:
                try:
                    symbol = exchange_data[ex_name]['original']
                    if symbol not in tickers_data[ex_name]:
                        continue
                    price = tickers_data[ex_name][symbol].get('last')
                    if price:
                        prices[ex_name] = price
                        print(f"  {ex_name.upper()}: ${price:.8f}")
                except Exception as e:
                    print(f"  ⚠️  {ex_name.upper()}: {e}")

            if len(prices) < 2:
                print("  ❌ Not enough price data\n")
                continue

            found_spread = False

            # Check every exchange pair combination
            for ex1, ex2 in combinations(prices.keys(), 2):
                p1, p2    = prices[ex1], prices[ex2]
                spread_pct = ((p2 - p1) / p1) * 100

                if abs(spread_pct) < self.min_spread:
                    continue

                found_spread = True
                print(f"\n  🎯 SPREAD FOUND: {abs(spread_pct):.3f}% between {ex1.upper()} and {ex2.upper()}")

                # Determine direction
                if spread_pct > 0:
                    buy_ex,  sell_ex  = ex1, ex2
                    buy_price, sell_price = p1, p2
                else:
                    buy_ex,  sell_ex  = ex2, ex1
                    buy_price, sell_price = p2, p1
                    spread_pct = abs(spread_pct)

                print(f"     Buy:  {buy_ex.upper()}  @ ${buy_price:.8f}")
                print(f"     Sell: {sell_ex.upper()} @ ${sell_price:.8f}")

                # Verify orderbook liquidity at the configured position size
                print(f"\n  📊 Checking orderbook depth (${self.liquidity_check})...")

                buy_sym  = exchange_data[buy_ex]['original']
                sell_sym = exchange_data[sell_ex]['original']

                buy_liq  = self._check_liquidity(buy_ex,  buy_sym,  'buy',  self.liquidity_check)
                if not buy_liq['success']:
                    print(f"     ❌ {buy_ex.upper()} (buy): {buy_liq['reason']}")
                    continue
                print(f"     ✅ {buy_ex.upper()} (buy): avg fill ${buy_liq['avg_price']:.8f}")

                sell_liq = self._check_liquidity(sell_ex, sell_sym, 'sell', self.liquidity_check)
                if not sell_liq['success']:
                    print(f"     ❌ {sell_ex.upper()} (sell): {sell_liq['reason']}")
                    continue
                print(f"     ✅ {sell_ex.upper()} (sell): avg fill ${sell_liq['avg_price']:.8f}")

                real_buy  = buy_liq['avg_price']
                real_sell = sell_liq['avg_price']
                real_spread = ((real_sell - real_buy) / real_buy) * 100

                print(f"\n  💰 REAL SPREAD (after orderbook): {real_spread:.3f}%")

                if real_spread < self.min_spread:
                    print(f"     ❌ Real spread < {self.min_spread}% after orderbook check")
                    continue

                print(f"\n  ✅ ALL CHECKS PASSED")
                print(f"     Spread (ticker): {spread_pct:.3f}%")
                print(f"     Spread (real):   {real_spread:.3f}%")
                print(f"  {'='*80}\n")

                opp = {
                    'Pair':              norm_symbol,
                    'Buy Exchange':      buy_ex.upper(),
                    'Sell Exchange':     sell_ex.upper(),
                    'Buy Price (ticker)':  round(buy_price,  8),
                    'Sell Price (ticker)': round(sell_price, 8),
                    'Buy Price (real)':    round(real_buy,   8),
                    'Sell Price (real)':   round(real_sell,  8),
                    'Spread Ticker %':     round(spread_pct, 3),
                    'Spread Real %':       round(real_spread, 3),
                }
                opportunities.append(opp)
                self._save_opportunity(opp)

            if not found_spread:
                print(f"  ⏭️  Spread < {self.min_spread}%\n")

        print(f"\n{'='*80}")
        print(f"🏁 SCAN COMPLETE  |  Checked: {total}  |  Found: {len(opportunities)}")
        print(f"{'='*80}\n")

        return opportunities

    # ── Results display ───────────────────────────────────────────────────────

    def display_results(self, opportunities: list):
        """Print a formatted summary table sorted by real spread descending."""
        print(f"\n{'='*120}")
        print("📊 ARBITRAGE RESULTS")
        print(f"{'='*120}\n")

        if not opportunities:
            print("❌ No opportunities found")
            return

        df = pd.DataFrame(opportunities)
        df = df.sort_values('Spread Real %', ascending=False)

        pd.set_option('display.max_columns', None)
        pd.set_option('display.width',       None)
        pd.set_option('display.max_colwidth', None)

        print(f"✅ Opportunities found: {len(df)}\n")
        print(df.to_string(index=False))

        best = df.iloc[0]
        print(f"\n{'='*120}")
        print("💰 SUMMARY:")
        print(f"  • Average spread (ticker): {df['Spread Ticker %'].mean():.3f}%")
        print(f"  • Average spread (real):   {df['Spread Real %'].mean():.3f}%")
        print(f"  • Best opportunity:        {best['Pair']}")
        print(f"    - Buy:  {best['Buy Exchange']}  @ ${best['Buy Price (real)']:.8f}")
        print(f"    - Sell: {best['Sell Exchange']} @ ${best['Sell Price (real)']:.8f}")
        print(f"    - Real spread: {best['Spread Real %']:.3f}%")
        print(f"{'='*120}")
        print(f"\n⏰ Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"💾 Results saved to: {self.db_path}")


def main():
    print("🚀 Cross-Exchange Futures Arbitrage Scanner")
    print("   Exchanges: Binance · KuCoin · Huobi")
    print("=" * 60)

    # ── Configuration ─────────────────────────────────────────────
    MIN_SPREAD      = 0.3    # Minimum spread (%) to consider an opportunity
    LIQUIDITY_CHECK = 100.0  # Position size in USDT for orderbook depth check
    DB_PATH         = "arbitrage.db"
    # ─────────────────────────────────────────────────────────────

    print(f"\n  Settings:")
    print(f"  • Minimum spread:    {MIN_SPREAD}%")
    print(f"  • Liquidity check:   ${LIQUIDITY_CHECK} USDT")
    print(f"  • Database:          {DB_PATH}")
    print()

    scanner = FuturesArbitrageScanner(
        min_spread=MIN_SPREAD,
        liquidity_check=LIQUIDITY_CHECK,
        db_path=DB_PATH,
    )

    try:
        opportunities = scanner.scan()
        scanner.display_results(opportunities)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
