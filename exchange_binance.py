"""
exchange_binance.py
Binance USDM Futures connector.
Returns a configured ccxt.binanceusdm exchange instance.
"""

import ccxt


def create() -> ccxt.binanceusdm:
    """
    Initialize and return a Binance USD-M Futures exchange object.
    No API keys required — public endpoints only.
    """
    exchange = ccxt.binanceusdm({
        "enableRateLimit": True,
    })
    return exchange
