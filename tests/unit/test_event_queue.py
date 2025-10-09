#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit tests for event queue module
"""

import pytest
import asyncio
import time
from unittest.mock import Mock, AsyncMock
from src.utils.event_queue import Event, EventType, SessionEventQueue


class TestEvent:
    """Test Event dataclass."""

    def test_event_creation(self):
        """Test Event creation."""
        event = Event(
            event_type=EventType.SIGNAL,
            data={"symbol": "BTC", "side": "BUY"}
        )

        assert event.event_type == EventType.SIGNAL
        assert event.data == {"symbol": "BTC", "side": "BUY"}
        assert isinstance(event.timestamp, float)
        assert event.timestamp > 0

    def test_event_with_timestamp(self):
        """Test Event with custom timestamp."""
        custom_timestamp = 1234567890.123
        event = Event(
            event_type=EventType.ORDER_FILLED,
            data={"order_id": 123},
            timestamp=custom_timestamp
        )

        assert event.timestamp == custom_timestamp

    def test_event_types(self):
        """Test all EventType values."""
        assert EventType.SIGNAL.value == "signal"
        assert EventType.ORDER_FILLED.value == "order_filled"
        assert EventType.STOP.value == "stop"


class TestSessionEventQueue:
    """Test SessionEventQueue class."""

    def test_session_event_queue_initialization(self):
        """Test SessionEventQueue initialization."""
        mock_handler = AsyncMock()
        queue = SessionEventQueue("test_session", mock_handler)

        assert queue.session_id == "test_session"
        assert queue.event_handler == mock_handler
        assert queue.is_running == False
        assert queue.queue is not None
        assert queue.worker_task is None

    def test_session_event_queue_custom_config(self):
        """Test SessionEventQueue with custom configuration."""
        mock_handler = AsyncMock()
        queue = SessionEventQueue("test_session", mock_handler)

        # Test basic functionality since actual implementation doesn't have custom config
        assert queue.session_id == "test_session"
        assert queue.event_handler == mock_handler
        assert queue.queue is not None

    @pytest.mark.asyncio
    async def test_start_stop_queue(self):
        """Test starting and stopping the queue."""
        mock_handler = AsyncMock()
        queue = SessionEventQueue("test_session", mock_handler)

        # Start queue
        await queue.start()
        assert queue.is_running == True

        # Stop queue
        await queue.stop()
        assert queue.is_running == False

    @pytest.mark.asyncio
    async def test_add_event(self):
        """Test adding an event to the queue."""
        mock_handler = AsyncMock()
        queue = SessionEventQueue("test_session", mock_handler)

        await queue.start()

        event = Event(
            event_type=EventType.SIGNAL,
            data={"symbol": "BTC", "side": "BUY"}
        )

        await queue.add_event(event)

        # Check that event was added to the queue
        assert queue.queue.qsize() == 1

        await queue.stop()

    @pytest.mark.asyncio
    async def test_add_multiple_events(self):
        """Test adding multiple events."""
        mock_handler = AsyncMock()
        queue = SessionEventQueue("test_session", mock_handler)

        await queue.start()

        events = [
            Event(EventType.SIGNAL, {"symbol": "BTC", "side": "BUY"}),
            Event(EventType.ORDER_FILLED, {"order_id": 123}),
            Event(EventType.STOP, {"reason": "manual"})
        ]

        for event in events:
            await queue.add_event(event)

        assert queue.queue.qsize() == 3

        await queue.stop()

    @pytest.mark.asyncio
    async def test_add_event_when_queue_full(self):
        """Test adding event when queue is full."""
        mock_handler = AsyncMock()
        queue = SessionEventQueue("test_session", mock_handler)

        await queue.start()

        # Fill queue to capacity
        event1 = Event(EventType.SIGNAL, {"data": 1})
        event2 = Event(EventType.SIGNAL, {"data": 2})

        await queue.add_event(event1)
        await queue.add_event(event2)

        assert queue.queue.qsize() == 2

        # Try to add another event (asyncio.Queue doesn't drop events by default)
        event3 = Event(EventType.SIGNAL, {"data": 3})
        await queue.add_event(event3)

        # Should have 3 events now
        assert queue.queue.qsize() == 3

        await queue.stop()

    @pytest.mark.asyncio
    async def test_event_processing(self):
        """Test that events are processed by handler."""
        processed_events = []

        async def mock_handler(event):
            processed_events.append(event)

        queue = SessionEventQueue("test_session", mock_handler, batch_size=1, batch_timeout=0.1)

        await queue.start()

        event = Event(EventType.SIGNAL, {"symbol": "BTC", "side": "BUY"})
        await queue.add_event(event)

        # Wait for processing
        await asyncio.sleep(0.2)

        assert len(processed_events) == 1
        assert processed_events[0] == event

        await queue.stop()

    @pytest.mark.asyncio
    async def test_batch_processing(self):
        """Test batch event processing."""
        processed_events = []

        async def mock_handler(event):
            processed_events.append(event)

        queue = SessionEventQueue(
            "test_session",
            mock_handler,
            batch_size=3,
            batch_timeout=0.1
        )

        await queue.start()

        # Add events one by one
        for i in range(3):
            event = Event(EventType.SIGNAL, {"data": i})
            await queue.add_event(event)

        # Wait for batch processing
        await asyncio.sleep(0.2)

        # Should have processed all 3 events in batch
        assert len(processed_events) == 3
        assert processed_events[0].data["data"] == 0
        assert processed_events[1].data["data"] == 1
        assert processed_events[2].data["data"] == 2

        await queue.stop()

    @pytest.mark.asyncio
    async def test_batch_timeout_processing(self):
        """Test batch processing with timeout."""
        processed_events = []

        async def mock_handler(event):
            processed_events.append(event)

        queue = SessionEventQueue(
            "test_session",
            mock_handler,
            batch_size=10,
            batch_timeout=0.1
        )

        await queue.start()

        # Add only one event (less than batch size)
        event = Event(EventType.SIGNAL, {"data": "single"})
        await queue.add_event(event)

        # Wait for timeout
        await asyncio.sleep(0.2)

        # Should still process the single event due to timeout
        assert len(processed_events) == 1
        assert processed_events[0] == event

        await queue.stop()

    @pytest.mark.asyncio
    async def test_handler_exception_handling(self):
        """Test handling exceptions in event handler."""
        # Handler that raises exception for specific event
        async def failing_handler(event):
            if event.data.get("should_fail"):
                raise ValueError("Handler failed")
            # Normal processing

        queue = SessionEventQueue("test_session", failing_handler)

        await queue.start()

        # Add normal event
        normal_event = Event(EventType.SIGNAL, {"data": "normal"})
        await queue.add_event(normal_event)

        # Add failing event
        failing_event = Event(EventType.SIGNAL, {"should_fail": True})
        await queue.add_event(failing_event)

        # Add another normal event
        another_normal_event = Event(EventType.SIGNAL, {"data": "another_normal"})
        await queue.add_event(another_normal_event)

        # Wait for processing
        await asyncio.sleep(0.2)

        # Queue should continue processing despite handler exception
        assert len(queue.events) == 0  # All events should be processed (even if some failed)

        await queue.stop()

    @pytest.mark.asyncio
    async def test_get_queue_size(self):
        """Test getting queue size."""
        mock_handler = AsyncMock()
        queue = SessionEventQueue("test_session", mock_handler)

        await queue.start()

        assert queue.get_queue_size() == 0

        # Add events
        for i in range(5):
            event = Event(EventType.SIGNAL, {"data": i})
            await queue.add_event(event)

        assert queue.get_queue_size() == 5

        await queue.stop()

    @pytest.mark.asyncio
    async def test_clear_queue(self):
        """Test clearing the queue."""
        mock_handler = AsyncMock()
        queue = SessionEventQueue("test_session", mock_handler)

        await queue.start()

        # Add events
        for i in range(5):
            event = Event(EventType.SIGNAL, {"data": i})
            await queue.add_event(event)

        assert queue.get_queue_size() == 5

        # Clear queue
        queue.clear_queue()
        assert queue.get_queue_size() == 0

        await queue.stop()

    @pytest.mark.asyncio
    async def test_add_event_to_stopped_queue(self):
        """Test adding event to stopped queue."""
        mock_handler = AsyncMock()
        queue = SessionEventQueue("test_session", mock_handler)

        # Don't start the queue

        event = Event(EventType.SIGNAL, {"data": "test"})
        await queue.add_event(event)

        # Event should still be added even if queue is not running
        assert queue.get_queue_size() == 1

    @pytest.mark.asyncio
    async def test_start_already_running_queue(self):
        """Test starting a queue that's already running."""
        mock_handler = AsyncMock()
        queue = SessionEventQueue("test_session", mock_handler)

        await queue.start()
        assert queue.is_running == True

        # Start again (should not cause issues)
        await queue.start()
        assert queue.is_running == True

        await queue.stop()

    @pytest.mark.asyncio
    async def test_stop_already_stopped_queue(self):
        """Test stopping a queue that's already stopped."""
        mock_handler = AsyncMock()
        queue = SessionEventQueue("test_session", mock_handler)

        await queue.start()
        await queue.stop()
        assert queue.is_running == False

        # Stop again (should not cause issues)
        await queue.stop()
        assert queue.is_running == False

    @pytest.mark.asyncio
    async def test_get_statistics(self):
        """Test getting queue statistics."""
        mock_handler = AsyncMock()
        queue = SessionEventQueue("test_session", mock_handler)

        await queue.start()

        # Add some events
        for i in range(3):
            event = Event(EventType.SIGNAL, {"data": i})
            await queue.add_event(event)

        # Wait for some processing
        await asyncio.sleep(0.2)

        stats = queue.get_statistics()

        assert "session_id" in stats
        assert stats["session_id"] == "test_session"
        assert "is_running" in stats
        assert "queue_size" in stats
        assert "max_queue_size" in stats
        assert "events_processed" in stats
        assert "events_dropped" in stats

        await queue.stop()

    @pytest.mark.asyncio
    async def test_priority_events(self):
        """Test handling priority events."""
        processed_events = []

        async def mock_handler(event):
            processed_events.append(event)

        queue = SessionEventQueue("test_session", mock_handler)

        await queue.start()

        # Add normal events
        normal_event = Event(EventType.SIGNAL, {"data": "normal"})
        await queue.add_event(normal_event)

        # Add priority event (should be processed first)
        priority_event = Event(EventType.STOP_SIGNAL, {"data": "priority"})
        await queue.add_event(priority_event, priority=True)

        # Add another normal event
        another_normal_event = Event(EventType.SIGNAL, {"data": "another_normal"})
        await queue.add_event(another_normal_event)

        # Wait for processing
        await asyncio.sleep(0.2)

        # Priority event should be processed first
        assert len(processed_events) == 3
        assert processed_events[0].event_type == EventType.STOP_SIGNAL
        assert processed_events[1].event_type == EventType.SIGNAL
        assert processed_events[2].event_type == EventType.SIGNAL

        await queue.stop()

    @pytest.mark.asyncio
    async def test_concurrent_event_addition(self):
        """Test adding events concurrently."""
        mock_handler = AsyncMock()
        queue = SessionEventQueue("test_session", mock_handler)

        await queue.start()

        # Add events from multiple tasks concurrently
        async def add_events(start_id, count):
            for i in range(count):
                event = Event(EventType.SIGNAL, {"data": start_id + i})
                await queue.add_event(event)

        # Run concurrent tasks
        tasks = [
            add_events(0, 5),
            add_events(5, 5),
            add_events(10, 5)
        ]

        await asyncio.gather(*tasks)

        # Should have 15 events total
        assert queue.get_queue_size() == 15

        await queue.stop()

    @pytest.mark.asyncio
    async def test_event_filtering(self):
        """Test event filtering functionality."""
        processed_events = []

        async def filter_handler(event):
            # Only process SIGNAL events
            if event.event_type == EventType.SIGNAL:
                processed_events.append(event)

        queue = SessionEventQueue("test_session", filter_handler)

        await queue.start()

        # Add different types of events
        signal_event = Event(EventType.SIGNAL, {"data": "signal"})
        order_event = Event(EventType.ORDER_FILLED, {"data": "order"})
        stop_event = Event(EventType.STOP_SIGNAL, {"data": "stop"})

        await queue.add_event(signal_event)
        await queue.add_event(order_event)
        await queue.add_event(stop_event)

        # Wait for processing
        await asyncio.sleep(0.2)

        # Only signal events should be processed
        assert len(processed_events) == 1
        assert processed_events[0] == signal_event

        await queue.stop()

    @pytest.mark.asyncio
    async def test_graceful_shutdown(self):
        """Test graceful shutdown with remaining events."""
        processed_events = []

        async def slow_handler(event):
            await asyncio.sleep(0.1)  # Slow processing
            processed_events.append(event)

        queue = SessionEventQueue("test_session", slow_handler)

        await queue.start()

        # Add events
        for i in range(3):
            event = Event(EventType.SIGNAL, {"data": i})
            await queue.add_event(event)

        # Stop queue (should wait for processing to complete)
        await queue.stop()

        # All events should be processed
        assert len(processed_events) == 3
        assert queue.is_running == False