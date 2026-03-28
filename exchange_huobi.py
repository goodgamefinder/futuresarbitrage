"""
exchange_huobi.py
Huobi (HTX) Perpetual Swaps connector.
Returns a configured ccxt.huobi exchange instance set to swap mode.
"""

import ccxt


def create() -> ccxt.huobi:
    """
    Initialize and return a Huobi perpetual swap exchange object.
    No API keys required — public endpoints only.
    """
    exchange = ccxt.huobi({
        "enableRateLimit": True,
        "options": {
            "defaultType": "swap",
        },
    })
    return exchange
