#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
測試運行器
自動設置環境變數並運行所有測試
"""

import os
import sys
import subprocess
import argparse
from pathlib import Path

# 硬編碼的測試環境變數（從 client.py 獲取）
TEST_ENV_VARS = {
    "ORDERLY_KEY": "ed25519:EpBR88faPoav78urb4MSeNxRaPTkxohXubgW5vBQwh1T",
    "ORDERLY_SECRET": "ed25519:FDoEfpUzcMKk5ZDd46Tk6seS6ed79jGmMVCSriQ2Jfqs",
    "ORDERLY_ACCOUNT_ID": "0x5e2cccd91ac05c8f1a9de15c629deffcf1de88abacf7bb7ac8d3b9d8e9317bb0",
    "TESTING": "true",
    "PYTHONPATH": str(Path(__file__).parent.parent)  # 添加項目根目錄到 Python 路徑
}

def setup_environment():
    """設置測試環境變數"""
    print("🔧 設置測試環境變數...")
    for key, value in TEST_ENV_VARS.items():
        os.environ[key] = value
        if key != "ORDERLY_SECRET":  # 不打印敏感信息
            print(f"   {key}={value}")
        else:
            print(f"   {key}=***隱藏***")
    print()

def run_command(cmd, description):
    """運行命令並處理結果"""
    print(f"🚀 {description}")
    print(f"   命令: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=os.environ.copy()
        )
        
        if result.returncode == 0:
            print(f"✅ {description} 成功")
            if result.stdout.strip():
                print(f"輸出:\n{result.stdout}")
        else:
            print(f"❌ {description} 失敗 (退出碼: {result.returncode})")
            if result.stderr.strip():
                print(f"錯誤:\n{result.stderr}")
            if result.stdout.strip():
                print(f"輸出:\n{result.stdout}")
        
        print("-" * 80)
        return result.returncode == 0
        
    except FileNotFoundError:
        print(f"❌ 找不到命令: {cmd[0]}")
        print("請確保已安裝 pytest")
        return False
    except Exception as e:
        print(f"❌ 運行命令時發生錯誤: {e}")
        return False

def install_dependencies():
    """安裝測試依賴"""
    dependencies = [
        "pytest",
        "pytest-asyncio",
        "httpx",
        "fastapi[all]",
        "python-multipart"
    ]
    
    print("📦 檢查並安裝測試依賴...")
    for dep in dependencies:
        cmd = [sys.executable, "-m", "pip", "install", dep]
        success = run_command(cmd, f"安裝 {dep}")
        if not success:
            print(f"警告: 無法安裝 {dep}，可能影響測試運行")
    
    return True

def check_project_structure():
    """檢查項目結構"""
    print("📁 檢查項目結構...")
    
    project_root = Path(__file__).parent.parent
    required_paths = [
        "src/api/server.py",
        "src/core/client.py",
        "src/core/grid_bot.py",
        "src/core/grid_signal.py",
        "src/utils/session_manager.py",
        "src/utils/logging_config.py",
        "src/utils/market_validator.py",
        "src/utils/order_tracker.py",
        "app.py"
    ]
    
    missing_files = []
    for path in required_paths:
        full_path = project_root / path
        if not full_path.exists():
            missing_files.append(path)
        else:
            print(f"   ✅ {path}")
    
    if missing_files:
        print("❌ 缺少以下文件:")
        for file in missing_files:
            print(f"   - {file}")
        return False
    
    print("✅ 項目結構檢查通過")
    print()
    return True

def run_unit_tests():
    """運行單元測試"""
    cmd = [
        sys.executable, "-m", "pytest", 
        "tests/test_components.py",
        "-v",
        "--tb=short",
        "--capture=no"
    ]
    return run_command(cmd, "運行單元測試")

def run_server_tests():
    """運行伺服器測試"""
    cmd = [
        sys.executable, "-m", "pytest", 
        "tests/test_server.py",
        "-v",
        "--tb=short",
        "--capture=no"
    ]
    return run_command(cmd, "運行伺服器測試")

def run_integration_tests():
    """運行集成測試"""
    cmd = [
        sys.executable, "-m", "pytest", 
        "tests/test_integration.py",
        "-v",
        "--tb=short",
        "--capture=no",
        "--run-integration"
    ]
    return run_command(cmd, "運行集成測試")

def run_performance_tests():
    """運行性能測試"""
    cmd = [
        sys.executable, "-m", "pytest", 
        "tests/test_integration.py::TestPerformance",
        "-v",
        "--tb=short",
        "--capture=no",
        "--run-performance"
    ]
    return run_command(cmd, "運行性能測試")

def run_all_tests():
    """運行所有測試"""
    cmd = [
        sys.executable, "-m", "pytest", 
        "tests/",
        "-v",
        "--tb=short",
        "--capture=no",
        "--run-integration",
        "--run-performance"
    ]
    return run_command(cmd, "運行所有測試")

def generate_coverage_report():
    """生成覆蓋率報告"""
    print("📊 生成測試覆蓋率報告...")
    
    # 首先安裝 coverage
    install_cmd = [sys.executable, "-m", "pip", "install", "pytest-cov"]
    if not run_command(install_cmd, "安裝 pytest-cov"):
        print("跳過覆蓋率報告生成")
        return False
    
    # 運行帶覆蓋率的測試
    cmd = [
        sys.executable, "-m", "pytest", 
        "tests/",
        "--cov=src",
        "--cov-report=html",
        "--cov-report=term",
        "--cov-report=xml",
        "--run-integration"
    ]
    
    success = run_command(cmd, "生成覆蓋率報告")
    
    if success:
        html_report = Path("htmlcov/index.html")
        if html_report.exists():
            print(f"📄 HTML 覆蓋率報告已生成: {html_report.absolute()}")
        
        xml_report = Path("coverage.xml")
        if xml_report.exists():
            print(f"📄 XML 覆蓋率報告已生成: {xml_report.absolute()}")
    
    return success

def main():
    """主函數"""
    parser = argparse.ArgumentParser(description="網格交易系統測試運行器")
    parser.add_argument(
        "--test-type", 
        choices=["unit", "server", "integration", "performance", "all"], 
        default="all",
        help="選擇要運行的測試類型"
    )
    parser.add_argument(
        "--install-deps", 
        action="store_true",
        help="安裝測試依賴"
    )
    parser.add_argument(
        "--coverage", 
        action="store_true",
        help="生成覆蓋率報告"
    )
    parser.add_argument(
        "--skip-structure-check", 
        action="store_true",
        help="跳過項目結構檢查"
    )
    
    args = parser.parse_args()
    
    print("🧪 網格交易系統測試運行器")
    print("=" * 50)
    
    # 設置環境
    setup_environment()
    
    # 安裝依賴（如果需要）
    if args.install_deps:
        install_dependencies()
    
    # 檢查項目結構（如果需要）
    if not args.skip_structure_check:
        if not check_project_structure():
            print("❌ 項目結構檢查失敗，請確保所有必要文件存在")
            sys.exit(1)
    
    # 切換到項目根目錄
    project_root = Path(__file__).parent.parent
    os.chdir(project_root)
    print(f"📂 工作目錄: {project_root.absolute()}")
    print()
    
    # 運行測試
    success = False
    
    if args.test_type == "unit":
        success = run_unit_tests()
    elif args.test_type == "server":
        success = run_server_tests()
    elif args.test_type == "integration":
        success = run_integration_tests()
    elif args.test_type == "performance":
        success = run_performance_tests()
    elif args.test_type == "all":
        success = run_all_tests()
    
    # 生成覆蓋率報告（如果需要）
    if args.coverage and success:
        generate_coverage_report()
    
    # 總結
    print("\n" + "=" * 50)
    if success:
        print("🎉 所有測試運行完成！")
        print("\n💡 提示:")
        print("   - 查看詳細的測試輸出以了解具體結果")
        print("   - 使用 --coverage 選項生成測試覆蓋率報告")
        print("   - 使用 --test-type 選項運行特定類型的測試")
    else:
        print("❌ 測試運行過程中發生錯誤")
        print("\n🔍 故障排除:")
        print("   - 檢查是否安裝了所有依賴項目")
        print("   - 確保項目結構完整")
        print("   - 查看上面的錯誤信息以獲得更多詳情")
        sys.exit(1)
    
    print("\n🔧 環境變數信息:")
    print(f"   ORDERLY_KEY: {TEST_ENV_VARS['ORDERLY_KEY'][:20]}...")
    print(f"   ORDERLY_ACCOUNT_ID: {TEST_ENV_VARS['ORDERLY_ACCOUNT_ID'][:20]}...")
    print("   ORDERLY_SECRET: ***隱藏***")
    print("   TESTING: true")

if __name__ == "__main__":
    main()
