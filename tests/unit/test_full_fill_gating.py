#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
from decimal import Decimal
import time
import pytest

from src.core.grid_bot import GridTradingBot


class MockSignalGenerator:
    def __init__(self, ticker: str = "PERP_BTC_USDC"):
        self.ticker = ticker
        self.calls = []

    def on_order_filled(self, filled_signal):
        self.calls.append(filled_signal)


@pytest.mark.asyncio
@pytest.mark.parametrize("side,splits", [
    ("BUY", [0.4, 0.4, 0.2]),
    ("SELL", [0.25, 0.25, 0.25, 0.25]),
])
async def test_full_fill_gating_trigger_only_when_fully_filled(side, splits):
    """分批成交累積至完全成交後，才觸發下一步。支援 BUY/SELL 與不同切分策略。"""
    bot = GridTradingBot(
        account_id="test_account",
        orderly_key="test_key",
        orderly_secret="test_secret",
        orderly_testnet=True,
    )
    bot.is_running = True
    bot.signal_generator = MockSignalGenerator()

    order_id = 777777
    price = Decimal("42500.50")
    quantity = Decimal(str(sum(splits)))

    # 建立活躍訂單與追蹤器狀態
    bot.active_orders[order_id] = {
        "price": float(price),
        "side": side,
        "quantity": float(quantity),
    }
    bot.grid_orders[float(price)] = order_id
    bot.order_tracker.add_order(
        order_id=order_id,
        symbol="PERP_BTC_USDC",
        side=side,
        order_type="LIMIT",
        price=price,
        quantity=quantity,
    )

    # 依序注入分拆成交
    for i, qty in enumerate(splits, start=1):
        fill = {
            "order_id": order_id,
            "executed_price": float(price),
            "executed_quantity": qty,
            "side": side,
            "fill_id": f"{order_id}_{price}_{qty}_{int(time.time()*1000)+i}",
        }
        await bot._handle_order_filled_event(fill)

    # 斷言：僅在完全成交後才觸發下一步
    calls = len(bot.signal_generator.calls)
    assert calls == 1, "應僅在完全成交時觸發一次"

    # 完全成交後，該訂單應從 active_orders/grid_orders 移除
    assert order_id not in bot.active_orders
    assert float(price) not in bot.grid_orders