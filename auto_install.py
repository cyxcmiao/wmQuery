"""

from auto_install import ensure_package

# 检测到没有安装 requests 时，自动通过 pip 安装
ensure_package("requests")

"""


"""依赖自动安装工具模块"""

import importlib.util
import subprocess
import sys


def ensure_package(package_name):
    """检测指定库是否已安装，未安装则自动通过 pip 安装

    :param package_name: 库的名称（与 import 时使用的名称一致，如 requests）
    """

    # 在当前解释器中查找该库，找不到说明没有安装
    if importlib.util.find_spec(package_name) is not None:
        return

    print()
    print(f"未检测到 {package_name} 库，正在自动安装...")

    # 使用 sys.executable 保证安装到当前正在运行的 Python（即虚拟环境）
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", package_name]
    )

    if result.returncode != 0:
        print()
        print(f"{package_name} 安装失败，请检查网络后手动安装：")
        print(f"  {sys.executable} -m pip install {package_name}")
        sys.exit(1)

    print()
    print(f"{package_name} 安装完成")
    print()
