#!/usr/bin/env python3
"""
Demo: Verify full-fill gating for grid bot using notifications-like fills.

This script simulates partial fills accumulating into a full fill and checks that
the bot only triggers the next-step (signal_generator.on_order_filled) once the
order is fully filled. It avoids any real API/network calls.
"""

import asyncio
import time
import sys
import argparse
from pathlib import Path
from decimal import Decimal

# Ensure we can import 'src' package inside orderly_bot
ROOT = Path(__file__).resolve().parents[1]  # points to orderly_bot/
sys.path.append(str(ROOT))  # add orderly_bot/ so 'src' becomes importable

from src.core.grid_bot import GridTradingBot  # type: ignore


class MockSignalGenerator:
    """Simple mock to capture on_order_filled calls."""

    def __init__(self, ticker: str = "PERP_BTC_USDC"):
        self.ticker = ticker
        self.calls = []

    def on_order_filled(self, filled_signal):
        self.calls.append(filled_signal)


def parse_args():
    parser = argparse.ArgumentParser(description="Demo full-fill gating with notifications-like fills")
    parser.add_argument("--side", choices=["BUY", "SELL"], default="BUY", help="Order side to simulate")
    parser.add_argument("--splits", default="0.4,0.4,0.2", help="Comma-separated fill quantities")
    parser.add_argument("--price", type=float, default=42500.50, help="Order price to simulate")
    parser.add_argument("--quantity", type=float, default=None, help="Original order quantity; default is sum(splits)")
    return parser.parse_args()


async def main():
    args = parse_args()
    # Instantiate bot with dummy credentials (no network call will be made)
    bot = GridTradingBot(
        account_id="demo_account",
        orderly_key="demo_key",
        orderly_secret="demo_secret",
        orderly_testnet=True,
    )
    bot.is_running = True

    # Inject the mock signal generator
    bot.signal_generator = MockSignalGenerator()

    # Prepare a tracked grid order with original quantity
    order_id = 123456
    price = Decimal(str(args.price))
    splits = [float(x) for x in args.splits.split(",") if x.strip()]
    quantity = Decimal(str(args.quantity if args.quantity is not None else sum(splits)))

    # Simulate active order mappings just like a placed grid order
    bot.active_orders[order_id] = {
        "price": float(price),
        "side": args.side,
        "quantity": float(quantity),
    }
    bot.grid_orders[float(price)] = order_id

    # Add the order into OrderTracker with the original quantity
    bot.order_tracker.add_order(
        order_id=order_id,
        symbol="PERP_BTC_USDC",
        side=args.side,
        order_type="LIMIT",
        price=price,
        quantity=quantity,
    )

    # Simulate fills according to splits
    fills = splits

    print("=== Simulating partial fills until fully filled ===")
    for i, qty in enumerate(fills, start=1):
        fill_data = {
            "order_id": order_id,
            "executed_price": float(price),
            "executed_quantity": qty,
            "side": args.side,
            # Unique fill_id for dedup semantics
            "fill_id": f"{order_id}_{price}_{qty}_{int(time.time()*1000)+i}",
        }

        await bot._handle_order_filled_event(fill_data)

        order_info = bot.order_tracker.get_order(order_id)
        pct = order_info.get_fill_percentage() if order_info else 0.0
        remaining = order_info.remaining_quantity if order_info else "N/A"
        calls = len(bot.signal_generator.calls)
        print(
            f"[step {i}] executed_quantity={qty} | calls={calls} | fill_pct={pct:.2f}% | remaining={remaining}"
        )

    # Final verification prints
    print("\n=== Result ===")
    print(f"signal_generator calls: {len(bot.signal_generator.calls)} (expect 1)")
    print(
        f"active_orders has order_id? {order_id in bot.active_orders} (expect False after full fill)"
    )
    print(
        f"grid_orders has price? {float(price) in bot.grid_orders} (expect False after full fill)"
    )

    if (
        len(bot.signal_generator.calls) == 1
        and (order_id not in bot.active_orders)
        and (float(price) not in bot.grid_orders)
    ):
        print("PASS: Full-fill gating works.")
    else:
        print("FAIL: Check gating behavior.")


if __name__ == "__main__":
    asyncio.run(main())