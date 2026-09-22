from auto_install import ensure_package

# 检测到没有安装 requests 时，自动通过 pip 安装
ensure_package("requests")

import requests
import unicodedata

BASE_URL = "https://api.warframe.market/v2"

HEADERS = {
    "Accept": "application/json",
    "Language": "zh-hans",
    "Platform": "pc",
    "Crossplay": "true"
}


def find_item(chinese_name):
    """通过中文名称找到 Warframe Market 的物品 slug"""

    url = f"{BASE_URL}/items"

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=10
    )

    response.raise_for_status()

    items = response.json()["data"]

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

    item_name = input("请输入物品名称：").strip()

    # -------------------------
    # 1. 中文名称 -> Market物品
    # -------------------------

    item = find_item(item_name)

    if item is None:
        print("没有找到这个物品")
        return

    print()
    print("找到物品：")
    print("中文名称：", item["i18n"]["zh-hans"]["name"])
    print("英文名称：", item["i18n"]["en"]["name"])
    print("slug：", item["slug"])

    # -------------------------
    # 2. 获取订单
    # -------------------------

    orders = get_orders(item["slug"])

    # -------------------------
    # 3. 筛选订单
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
    # 4. 按价格从低到高排序
    # -------------------------

    filtered_orders.sort(
        key=lambda order: order["platinum"]
    )

    # -------------------------
    # 5. 取前5
    # -------------------------

    top5 = filtered_orders[:5]

    print_top5(top5)


if __name__ == "__main__":
    main()
