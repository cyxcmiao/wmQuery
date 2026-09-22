from auto_install import ensure_package

# 检测到没有安装时，自动通过 pip 安装
ensure_package("requests")
ensure_package("prompt_toolkit")

import requests
import unicodedata
import json
import os
import sys
import time

BASE_URL = "https://api.warframe.market/v2"

HEADERS = {
    "Accept": "application/json",
    "Language": "zh-hans",
    "Platform": "pc",
    "Crossplay": "true"
}

# 物品列表本地缓存（存在脚本同目录）
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "items_cache.json")
CACHE_TTL_HOURS = 24  # 缓存有效时长（小时），超过后自动重新下载


def load_items_cache(max_age_hours):
    """读取本地缓存的物品列表

    :param max_age_hours: 允许的缓存最长年龄（小时），None 表示不限制年龄
    :return: 物品列表，缓存不存在、损坏或太旧时返回 None
    """

    if not os.path.exists(CACHE_FILE):
        return None

    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)

        age_hours = (time.time() - cache["fetched_at"]) / 3600

        if max_age_hours is None or age_hours <= max_age_hours:
            return cache["data"]

    except (OSError, KeyError, TypeError, ValueError):
        pass  # 缓存损坏时忽略，当作没有缓存

    return None


def save_items_cache(items):
    """把物品列表写入本地缓存，只保留用到的字段减小文件体积"""

    slim_items = [
        {
            "slug": item["slug"],
            "i18n": {
                "zh-hans": {"name": item["i18n"]["zh-hans"]["name"]},
                "en": {"name": item["i18n"]["en"]["name"]},
            },
        }
        for item in items
        if item.get("i18n", {}).get("zh-hans") and item.get("i18n", {}).get("en")
    ]

    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {"fetched_at": time.time(), "data": slim_items},
                f,
                ensure_ascii=False
            )
    except OSError:
        pass  # 缓存写失败不影响本次查询


def get_all_items():
    """获取全部物品列表（同时用于 Tab 补全和查找），优先使用本地缓存"""

    cached = load_items_cache(CACHE_TTL_HOURS)

    if cached is not None:
        print(f"（使用本地缓存，缓存超过 {CACHE_TTL_HOURS} 小时后自动重新下载）")
        return cached

    url = f"{BASE_URL}/items"

    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=10
        )

        response.raise_for_status()

        items = response.json()["data"]

    except (requests.RequestException, ValueError):
        # 下载失败时退回过期的缓存，保证断网也能用
        stale = load_items_cache(None)

        if stale is None:
            raise

        print("物品列表下载失败，使用过期的本地缓存")
        return stale

    save_items_cache(items)

    return items


def find_item(items, chinese_name):
    """通过中文名称在物品列表中找到 Warframe Market 的物品"""

    for item in items:
        zh_data = item.get("i18n", {}).get("zh-hans")

        if not zh_data:
            continue

        if zh_data.get("name") == chinese_name:
            return item

    return None


def get_orders(item_slug):
    """查询指定物品的订单"""

    url = f"{BASE_URL}/orders/item/{item_slug}"

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=10
    )

    response.raise_for_status()

    return response.json()["data"]


def is_interactive_terminal():
    """当前是否运行在真实终端里（PyCharm/VSCode 运行窗口等会返回 False）"""

    return sys.stdin.isatty() and sys.stdout.isatty()


def input_item_name(item_names):
    """输入物品名称：真实终端下带 Tab 自动补全，其他环境用编号选择"""

    if not is_interactive_terminal():
        print("（当前不是真实终端，Tab 补全不可用，改为：输入部分名称后按编号选择）")
        return input_item_name_by_number(item_names)

    try:
        from prompt_toolkit import prompt
        from prompt_toolkit.completion import WordCompleter
    except ImportError:
        return input_item_name_by_number(item_names)

    completer = WordCompleter(
        item_names,
        sentence=True,      # 把整行输入当作待补全文本（物品名里含空格也能整句匹配）
        match_middle=True,  # 输入"斩铁"也能匹配到"镀层 斩铁"这类包含关系的名字
    )

    try:
        return prompt(
            "请输入物品名称：",
            completer=completer,
            complete_while_typing=True  # 输入过程中就显示候选列表
        ).strip()
    except Exception as error:
        # 补全库在当前终端初始化失败时，打印原因并退回编号选择
        print(f"（Tab 补全在此终端不可用：{error!r}，改为编号选择模式）")
        return input_item_name_by_number(item_names)


def input_item_name_by_number(item_names):
    """编号选择模式的输入：输入部分名称 -> 列出所有匹配 -> 按编号选择"""

    text = input("请输入物品名称（可只输入一部分）：").strip()

    if not text:
        return text

    matches = [name for name in item_names if text in name]

    if not matches:
        return text

    if len(matches) == 1:
        print(f"匹配到唯一物品：{matches[0]}")
        return matches[0]

    shown = matches[:15]

    print(f"匹配到 {len(matches)} 个物品：")

    for number, name in enumerate(shown, start=1):
        print(f"  {number}. {name}")

    if len(matches) > 15:
        print("  （候选过多，只显示前 15 个，请输入更精确的名称）")
        return text

    choice = input("请输入编号选择，直接回车则按原文查找：").strip()

    if choice.isdigit() and 1 <= int(choice) <= len(shown):
        return shown[int(choice) - 1]

    return text


def display_width(text):
    """计算字符串在终端里的显示宽度：中文等全角字符算 2 格，其他算 1 格"""

    width = 0

    for char in text:

        if unicodedata.combining(char):
            continue

        if unicodedata.east_asian_width(char) in ("W", "F"):
            width += 2
        else:
            width += 1

    return width


def pad_text(text, width, align="left"):
    """把文本补空格到指定显示宽度，保证中英文混排也能对齐"""

    spaces = max(width - display_width(text), 0)

    if align == "right":
        return " " * spaces + text

    return text + " " * spaces


def print_top5(orders):
    """以对齐的表格形式打印前5订单"""

    headers = ["#", "价格(白金)", "玩家", "数量", "等级"]
    aligns = ["left", "right", "left", "right", "right"]

    rows = [
        [
            str(index),
            str(order["platinum"]),
            order["user"]["ingameName"],
            str(order["quantity"]),
            str(order.get("rank", 0)),
        ]
        for index, order in enumerate(orders, start=1)
    ]

    if not rows:
        print("没有符合条件的订单")
        return

    # 每列取最大显示宽度作为列宽
    widths = [
        max(display_width(row[column]) for row in rows + [headers])
        for column in range(len(headers))
    ]

    def format_row(cells):
        padded = [
            pad_text(cell, widths[column], aligns[column])
            for column, cell in enumerate(cells)
        ]
        return "  ".join(padded).rstrip()

    header_line = format_row(headers)

    print()
    print("游戏中玩家最低价前5：")
    print("-" * display_width(header_line))
    print(header_line)

    for row in rows:
        print(format_row(row))


def main():

    # -------------------------
    # 1. 加载物品列表（用于 Tab 补全和查找）
    # -------------------------

    print("正在加载物品列表...")

    items = get_all_items()

    item_names = [
        item["i18n"]["zh-hans"]["name"]
        for item in items
        if item.get("i18n", {}).get("zh-hans")
    ]

    # 去重，保持原有顺序
    item_names = list(dict.fromkeys(item_names))

    # -------------------------
    # 2. 输入物品名称（Tab 自动补全）
    # -------------------------

    item_name = input_item_name(item_names)

    item = find_item(items, item_name)

    if item is None:
        print("没有找到这个物品")
        return

    print()
    print("找到物品：")
    print("中文名称：", item["i18n"]["zh-hans"]["name"])
    print("英文名称：", item["i18n"]["en"]["name"])
    print("slug：", item["slug"])

    # -------------------------
    # 3. 获取订单
    # -------------------------

    orders = get_orders(item["slug"])

    # -------------------------
    # 4. 筛选订单
    # -------------------------

    filtered_orders = []

    for order in orders:

        # 只看卖单
        if order["type"] != "sell":
            continue

        # 只看游戏中的玩家
        if order["user"]["status"] != "ingame":
            continue

        # MOD只看0级
        if order.get("rank", 0) != 0:
            continue

        filtered_orders.append(order)

    # -------------------------
    # 5. 按价格从低到高排序
    # -------------------------

    filtered_orders.sort(
        key=lambda order: order["platinum"]
    )

    # -------------------------
    # 6. 取前5
    # -------------------------

    top5 = filtered_orders[:5]

    print_top5(top5)


if __name__ == "__main__":
    main()
