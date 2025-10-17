#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import time
from decimal import Decimal
import pytest

from src.core.grid_bot import GridTradingBot


class MockSignalGenerator:
    def __init__(self, ticker: str = "PERP_BTC_USDC"):
        self.ticker = ticker
        self.calls = []

    def on_order_filled(self, filled_signal):
        self.calls.append(filled_signal)


@pytest.mark.asyncio
async def test_dedup_same_timestamp_ignore_duplicate():
    """當兩筆 notifications 內容完全一致（相同 fill_id）時，只處理一次。"""
    bot = GridTradingBot(
        account_id="test_account",
        orderly_key="test_key",
        orderly_secret="test_secret",
        orderly_testnet=True,
    )
    bot.is_running = True
    bot.signal_generator = MockSignalGenerator()

    order_id = 900001
    price = Decimal("42500.50")
    quantity = Decimal("1.0")

    # 建立活躍訂單與追蹤器狀態
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

    ts = int(time.time() * 1000)
    qty = 0.6
    fill = {
        "order_id": order_id,
        "executed_price": float(price),
        "executed_quantity": qty,
        "side": "BUY",
        "fill_id": f"{order_id}_{price}_{qty}_{ts}",
    }

    # 連續送入相同 fill（相同 fill_id）
    await bot._handle_order_filled_event(fill)
    await bot._handle_order_filled_event(fill)

    order_info = bot.order_tracker.get_order(order_id)
    assert order_info is not None
    assert len(order_info.fills) == 1, "重複 fill 應被忽略，只保留一筆"
    assert pytest.approx(order_info.get_fill_percentage(), 0.01) == 60.0
    assert len(bot.signal_generator.calls) == 0, "未完全成交時不應觸發下一步"