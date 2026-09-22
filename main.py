from auto_install import ensure_package

# 检测到没有安装 requests 时，自动通过 pip 安装
ensure_package("requests")

import requests

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

    print()
    print("游戏中玩家最低价前5：")
    print("-" * 50)

    for index, order in enumerate(top5, start=1):

        print(
            f"{index}. "
            f"{order['platinum']} 白金 | "
            f"{order['user']['ingameName']} | "
            f"数量 {order['quantity']} | "
            f"等级 {order.get('rank', 0)}"
        )


if __name__ == "__main__":
    main()
