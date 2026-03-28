"""
exchange_kucoin.py
KuCoin Futures connector.
Returns a configured ccxt.kucoinfutures exchange instance.
"""

import ccxt


def create() -> ccxt.kucoinfutures:
    """
    Initialize and return a KuCoin Futures exchange object.
    No API keys required — public endpoints only.
    """
    exchange = ccxt.kucoinfutures({
        "enableRateLimit": True,
    })
    return exchange
