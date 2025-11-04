#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Grid Trading Bot - Entry Point
網格交易機器人主入口點
"""

import os
import uvicorn

def main():
    """主程式入口點"""
    print("🚀 啟動 Grid Trading Bot Server...")
    print("📊 Orderly 網格交易 MVP 系統")
    print("=" * 50)
    
    # 啟動 FastAPI 服務器
    uvicorn.run(
        "src.api.server:app",  # 使用字符串導入以支持 reload
        host=os.getenv("UVICORN_HOST", "0.0.0.0"),
        port=int(os.getenv("UVICORN_PORT", "8001")),
        reload=True,
        log_level="info"
    )

if __name__ == "__main__":
    main()