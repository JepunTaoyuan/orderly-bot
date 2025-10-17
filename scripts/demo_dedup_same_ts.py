#!/usr/bin/env python3
"""
Demo: Verify dedup behavior when multiple fills share the same timestamp/signature.

This script simulates two notifications-like fills that look identical (same orderId,
executedPrice, executedQuantity, executedTimestamp), ensuring only one is processed.

It checks that the bot's websocket dedup (processed_fills) and the OrderTracker's
global fill_ids set both prevent duplicate accumulation.
"""

import asyncio
import time
import sys
from pathlib import Path
from decimal import Decimal

# Ensure we can import 'src' package inside orderly_bot
ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from src.core.grid_bot import GridTradingBot  # type: ignore


class MockSignalGenerator:
    def __init__(self, ticker: str = "PERP_BTC_USDC"):
        self.ticker = ticker
        self.calls = []

    def on_order_filled(self, filled_signal):
        self.calls.append(filled_signal)


async def main():
    bot = GridTradingBot(
        account_id="demo_account",
        orderly_key="demo_key",
        orderly_secret="demo_secret",
        orderly_testnet=True,
    )
    bot.is_running = True
    bot.signal_generator = MockSignalGenerator()

    order_id = 888888
    price = Decimal("42500.50")
    quantity = Decimal("1.0")

    # Seed active order and tracker
    bot.active_orders[order_id] = {
        "price": float(price),
        "side": "BUY",
        "quantity": float(quantity),
    }
    bot.grid_orders[float(price)] = order_id
    bot.order_tracker.add_order(
        order_id=order_id,
        symbol="PERP_BTC_USDC",
        side="BUY",
        order_type="LIMIT",
        price=price,
        quantity=quantity,
    )

    # Same timestamp fills (simulate identical notifications payloads)
    ts = int(time.time() * 1000)
    qty = 0.6
    fill1 = {
        "order_id": order_id,
        "executed_price": float(price),
        "executed_quantity": qty,
        "side": "BUY",
        "fill_id": f"{order_id}_{price}_{qty}_{ts}",
    }
    fill2 = dict(fill1)  # identical, same fill_id

    print("=== Simulating duplicate fills with same timestamp ===")
    await bot._handle_order_filled_event(fill1)
    await bot._handle_order_filled_event(fill2)

    order_info = bot.order_tracker.get_order(order_id)
    pct = order_info.get_fill_percentage() if order_info else 0.0
    remaining = order_info.remaining_quantity if order_info else "N/A"
    calls = len(bot.signal_generator.calls)

    print(f"fills processed (expected 1): {len(order_info.fills) if order_info else 0}")
    print(f"fill_pct (expected 60.00%): {pct:.2f}%")
    print(f"remaining (expected 0.4): {remaining}")
    print(f"signal_generator calls (expected 0): {calls}")

    if order_info and len(order_info.fills) == 1 and calls == 0:
        print("PASS: Duplicate fill is ignored.")
    else:
        print("FAIL: Duplicate handling did not work as expected.")


if __name__ == "__main__":
    asyncio.run(main())