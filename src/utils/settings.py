#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Centralized settings loaded from environment variables.
No hardcoded secrets. Raise early if required variables are missing when used.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    # Orderly credentials (required)
    orderly_key: str
    orderly_secret: str
    orderly_account_id: str

    # Environment flags
    orderly_testnet: bool = True

    # Server runtime
    uvicorn_host: str = "0.0.0.0"
    uvicorn_port: int = 8000

    # Database connection
    mongo_uri: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and cache settings from environment variables.

    Required:
      - ORDERLY_KEY
      - ORDERLY_SECRET
      - ORDERLY_ACCOUNT_ID

    Optional:
      - ORDERLY_TESTNET (default: true)
      - UVICORN_HOST (default: 0.0.0.0)
      - UVICORN_PORT (default: 8000)
    """
    key = os.getenv("ORDERLY_KEY")
    secret = os.getenv("ORDERLY_SECRET")
    account_id = os.getenv("ORDERLY_ACCOUNT_ID")

    missing = [
        name
        for name, val in (
            ("ORDERLY_KEY", key),
            ("ORDERLY_SECRET", secret),
            ("ORDERLY_ACCOUNT_ID", account_id),
        )
        if not val
    ]
    if missing:
        # We fail fast when credentials are actually needed by the client or websocket.
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}"
        )

    testnet = os.getenv("ORDERLY_TESTNET", "true").strip().lower() in {"1", "true", "yes", "on"}
    host = os.getenv("UVICORN_HOST", "0.0.0.0").strip() or "0.0.0.0"
    try:
        port = int(os.getenv("UVICORN_PORT", "8000"))
    except ValueError:
        port = 8000

    return Settings(
        orderly_key=key,
        orderly_secret=secret,
        orderly_account_id=account_id,
        orderly_testnet=testnet,
        uvicorn_host=host,
        uvicorn_port=port,
    )
