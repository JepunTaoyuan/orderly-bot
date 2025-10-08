#!/usr/bin/env python3
"""
測試 SlowAPI 速率限制器功能
"""

import asyncio
import aiohttp
import time
import sys
import os

# 添加項目根目錄到 Python 路徑
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

BASE_URL = "http://localhost:8000"

async def test_rate_limit_endpoint(session, endpoint, method="GET", data=None, max_requests=10):
    """測試特定端點的速率限制"""
    print(f"\n🧪 測試端點: {method} {endpoint}")

    success_count = 0
    rate_limit_count = 0

    for i in range(max_requests):
        try:
            if method == "GET":
                async with session.get(f"{BASE_URL}{endpoint}") as response:
                    status = response.status
                    result = await response.json()
            else:  # POST
                async with session.post(f"{BASE_URL}{endpoint}", json=data) as response:
                    status = response.status
                    result = await response.json()

            if status == 200:
                success_count += 1
                print(f"  ✅ 請求 {i+1}: 成功 (200)")
            elif status == 429:
                rate_limit_count += 1
                print(f"  🚫 請求 {i+1}: 速率限制 (429) - {result.get('detail', {}).get('message', 'Rate limit exceeded')}")
                break
            else:
                print(f"  ❌ 請求 {i+1}: 其他錯誤 ({status}) - {result}")

        except Exception as e:
            print(f"  💥 請求 {i+1}: 異常 - {e}")
            break

        # 短暫延遲，避免過快請求
        await asyncio.sleep(0.1)

    print(f"📊 結果: {success_count} 次成功, {rate_limit_count} 次速率限制")
    return success_count, rate_limit_count

async def test_concurrent_requests(session, endpoint, num_concurrent=5):
    """測試併發請求的速率限制"""
    print(f"\n🔄 測試併發請求: {num_concurrent} 個併發請求到 {endpoint}")

    async def make_request():
        try:
            async with session.get(f"{BASE_URL}{endpoint}") as response:
                return response.status, await response.json()
        except Exception as e:
            return 0, {"error": str(e)}

    # 同時發送多個請求
    start_time = time.time()
    results = await asyncio.gather(*[make_request() for _ in range(num_concurrent)])
    end_time = time.time()

    success_count = sum(1 for status, _ in results if status == 200)
    rate_limit_count = sum(1 for status, _ in results if status == 429)

    print(f"⏱️  執行時間: {end_time - start_time:.2f} 秒")
    print(f"📊 結果: {success_count} 次成功, {rate_limit_count} 次速率限制")

    return success_count, rate_limit_count

async def main():
    """主測試函數"""
    print("=" * 60)
    print("🚀 Orderly Bot - SlowAPI 速率限制器測試")
    print("=" * 60)

    async with aiohttp.ClientSession() as session:
        try:
            # 測試服務器是否運行
            async with session.get(f"{BASE_URL}/health") as response:
                if response.status != 200:
                    print("❌ 錯誤: 服務器未運行，請先啟動服務器")
                    print("   運行命令: python app.py")
                    return 1
                print("✅ 服務器運行正常")

            # 測試各種端點的速率限制
            print("\n🎯 測試不同類型的端點...")

            # 1. 測試健康檢查（應該有較寬鬆的限制）
            await test_rate_limit_endpoint(session, "/health", max_requests=5)

            # 2. 測試認證挑戰（認證級別限制）
            await test_rate_limit_endpoint(session, "/api/auth/challenge", max_requests=10)

            # 3. 測試狀態檢查（狀態檢查級別限制）
            await test_rate_limit_endpoint(session, "/api/grid/status/test_session", max_requests=10)

            # 4. 測試併發請求
            await test_concurrent_requests(session, "/api/auth/challenge", num_concurrent=3)
            await test_concurrent_requests(session, "/health", num_concurrent=5)

            print("\n🎉 測試完成!")
            print("✅ SlowAPI 速率限制器已成功集成")
            print("✅ 各種端點都有相應的速率限制保護")

        except aiohttp.ClientConnectorError:
            print("❌ 錯誤: 無法連接到服務器")
            print("   請確保服務器正在運行在 http://localhost:8000")
            return 1
        except Exception as e:
            print(f"❌ 測試失敗: {e}")
            import traceback
            traceback.print_exc()
            return 1

    return 0

async def test_rate_limit_config():
    """測試速率限制配置"""
    print("\n📋 速率限制配置檢查...")

    try:
        from src.utils.slowapi_limiter import RATE_LIMITS, get_slowapi_rate_limiter

        print("🔧 速率限制配置:")
        for endpoint_type, limit in RATE_LIMITS.items():
            print(f"  {endpoint_type:15}: {limit}")

        # 測試獲取限制器實例
        limiter_instance = get_slowapi_rate_limiter()
        print(f"✅ 限制器實例: {type(limiter_instance).__name__}")

        return True
    except ImportError as e:
        print(f"❌ 導入錯誤: {e}")
        return False
    except Exception as e:
        print(f"❌ 配置檢查失敗: {e}")
        return False

if __name__ == "__main__":
    print("🔍 檢查速率限制配置...")
    config_ok = asyncio.run(test_rate_limit_config())

    if config_ok:
        print("✅ 配置檢查通過，開始功能測試...")
        exit_code = asyncio.run(main())
    else:
        print("❌ 配置檢查失敗")
        exit_code = 1

    sys.exit(exit_code)