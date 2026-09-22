from auto_install import ensure_package

# 检测到没有安装时，自动通过 pip 安装
ensure_package("requests")
ensure_package("flask")

import json
import os
import socket
import threading
import time
import webbrowser

import requests
from flask import Flask, jsonify, request, send_from_directory

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

# 启动后加载的数据（供补全和查找使用）
ITEMS = []
NAME_LIST = []

WEB_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, static_folder=None)


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
    """获取全部物品列表（同时用于补全和查找），优先使用本地缓存"""

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


@app.route("/")
def index():
    """查询页面"""

    return send_from_directory(WEB_DIR, "index.html")


@app.route("/api/items")
def api_items():
    """全部物品名称，供输入框自动补全"""

    return jsonify(NAME_LIST)


@app.route("/api/query")
def api_query():
    """查询指定物品的游戏内最低价卖单前5"""

    name = request.args.get("name", "").strip()

    if not name:
        return jsonify({"error": "请输入物品名称"}), 400

    item = find_item(ITEMS, name)

    if item is None:
        # 没找到时给出相似的物品名，方便纠正输入
        suggestions = [n for n in NAME_LIST if name in n][:8]

        return jsonify({
            "error": f"没有找到物品：{name}",
            "suggestions": suggestions
        }), 404

    orders = get_orders(item["slug"])

    # 只看游戏中玩家的0级卖单
    filtered = []

    for order in orders:

        if order["type"] != "sell":
            continue

        if order["user"]["status"] != "ingame":
            continue

        if order.get("rank", 0) != 0:
            continue

        filtered.append(order)

    # 按价格从低到高排序，取前5
    filtered.sort(key=lambda order: order["platinum"])

    top5 = [
        {
            "platinum": order["platinum"],
            "player": order["user"]["ingameName"],
            "quantity": order["quantity"],
        }
        for order in filtered[:5]
    ]

    return jsonify({
        "item": {
            "name": item["i18n"]["zh-hans"]["name"],
            "name_en": item["i18n"]["en"]["name"],
            "slug": item["slug"],
        },
        "orders": top5,
    })


def find_free_port(start):
    """从 start 开始找一个未被占用的端口"""

    port = start

    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                port += 1


def open_browser(url):
    """打开系统默认浏览器"""

    try:
        webbrowser.open(url)
    except Exception:
        pass  # 打不开浏览器时用户可手动访问打印出的地址


def main():
    global ITEMS, NAME_LIST

    print("正在加载物品列表...")

    ITEMS = get_all_items()

    # 去重，保持原有顺序
    NAME_LIST = list(dict.fromkeys(
        item["i18n"]["zh-hans"]["name"]
        for item in ITEMS
        if item.get("i18n", {}).get("zh-hans") and item["i18n"]["zh-hans"].get("name")
    ))

    print(f"物品列表加载完成，共 {len(NAME_LIST)} 个物品")

    port = find_free_port(8899)
    url = f"http://127.0.0.1:{port}"

    print(f"查询页面：{url} （关闭本窗口或按 Ctrl+C 退出）")

    # 稍等服务器启动后再打开浏览器
    threading.Timer(1.0, open_browser, args=(url,)).start()

    app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    main()
