"""
簡化的 CORS 配置
"""

import os
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI


def configure_cors(app: FastAPI):
    """配置 CORS 設置"""

    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
    debug_mode = os.getenv("DEBUG", "false").lower() == "true"

    origins = [frontend_url]

    if debug_mode:
        origins.extend([
            "http://localhost:5173",
            "http://localhost:5174",
            "http://localhost:5175",
        ])

    additional_origins = os.getenv("CORS_ORIGINS", "")
    if additional_origins:
        origins.extend([origin.strip() for origin in additional_origins.split(",") if origin.strip()])

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(set(origins)),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
