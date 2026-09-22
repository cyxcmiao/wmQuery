from auto_install import ensure_package

# 检测到没有安装时，自动通过 pip 安装
ensure_package("requests")
ensure_package("flask")

import json
import os
import re
import socket
import threading
import time
import webbrowser

import requests
from flask import Flask, jsonify, request, send_from_directory
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://api.warframe.market/v2"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

HEADERS = {
    "Accept": "application/json",
    "Language": "zh-hans",
    "Platform": "pc",
    "Crossplay": "true"
}


def make_session():
    """创建带自动重试的请求会话：连接被重置等瞬时网络错误会自动重试 3 次"""

    session = requests.Session()

    retry = Retry(
        total=3,
        backoff_factor=0.5,  # 重试间隔 0.5s, 1s, 2s
        status_forcelist=(429, 500, 502, 503, 504),
    )

    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    return session


SESSION = make_session()

# 物品列表本地缓存（存在脚本同目录）
CACHE_FILE = os.path.join(BASE_DIR, "items_cache.json")
CACHE_TTL_HOURS = 24  # 缓存有效时长（小时），超过后自动重新下载
CACHE_VERSION = 4  # 缓存结构版本，字段变化时递增以强制刷新旧缓存

# 赋能升到对应等级需要的数量（maxRank -> 个数）
RANK_NEED_COUNT = {0: 1, 1: 3, 2: 6, 3: 10, 4: 15, 5: 21}

# 持久化目录：查询历史、查询场景都存在 cache/ 下，每个场景一个 json 文件
CACHE_DIR = os.path.join(BASE_DIR, "cache")
HISTORY_FILE = os.path.join(CACHE_DIR, "query_history.json")
HISTORY_MAX = 10  # 最多保留条数

# 启动后加载的数据（供补全和查找使用）
ITEMS = []
NAME_LIST = []

WEB_DIR = BASE_DIR

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

        if cache.get("version") != CACHE_VERSION:
            return None  # 旧版本缓存结构不同，重新下载

        age_hours = (time.time() - cache["fetched_at"]) / 3600

        if max_age_hours is None or age_hours <= max_age_hours:
            return cache["data"]

    except (OSError, KeyError, TypeError, ValueError):
        pass  # 缓存损坏时忽略，当作没有缓存

    return None


def save_items_cache(items):
    """把物品列表写入本地缓存，只保留用到的字段减小文件体积

    赋能类（tags 含 arcane_enhancement 且不含 mod）保留 maxRank，
    用于筛选满级卖单；MOD 类（tags 含 mod）不保留 maxRank，查询时仍按 0 级计价。
    """

    slim_items = []

    for item in items:
        zh_data = item.get("i18n", {}).get("zh-hans")
        en_data = item.get("i18n", {}).get("en")

        if not (zh_data and en_data):
            continue

        slim = {
            "slug": item["slug"],
            "i18n": {
                "zh-hans": {"name": zh_data["name"]},
                "en": {"name": en_data["name"]},
            },
        }

        tags = item.get("tags") or []

        if "arcane_enhancement" in tags and "mod" not in tags:
            slim["maxRank"] = item.get("maxRank")

        slim_items.append(slim)

    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {"version": CACHE_VERSION, "fetched_at": time.time(), "data": slim_items},
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
        response = SESSION.get(
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

    response = SESSION.get(
        url,
        headers=HEADERS,
        timeout=10
    )

    response.raise_for_status()

    return response.json()["data"]


def load_history():
    """读取查询历史，文件不存在或损坏时返回空列表"""

    # 兼容旧版本：历史文件还在脚本根目录时，迁移到 cache/ 下
    old_history_file = os.path.join(BASE_DIR, "query_history.json")

    if not os.path.exists(HISTORY_FILE) and os.path.exists(old_history_file):
        try:
            os.replace(old_history_file, HISTORY_FILE)
        except OSError:
            pass

    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            history = json.load(f)

        if isinstance(history, list):
            return [h for h in history if isinstance(h, dict) and "name" in h]

    except (OSError, ValueError):
        pass  # 文件损坏时当作没有历史

    return []


def add_history(name, prices=None, need_count=None):
    """记录一次查询：同名条目移到最前并更新时间和全部价格，最多保留 HISTORY_MAX 条

    :param prices: 查询结果中前5卖单的白金价格列表（低到高），没有卖单时为空列表
    :param need_count: 赋能类升到满级需要的数量，其他物品为 None
    """

    history = [h for h in load_history() if h.get("name") != name]
    history.insert(0, {
        "name": name,
        "time": time.time(),
        "prices": prices or [],
        "need_count": need_count,
    })
    history = history[:HISTORY_MAX]

    ensure_cache_dir()

    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False)
    except OSError:
        pass  # 历史写失败不影响查询结果

    return history


@app.route("/")
def index():
    """查询页面"""

    return send_from_directory(WEB_DIR, "index.html")


@app.route("/api/history")
def api_history():
    """查询历史"""

    return jsonify(load_history())


# -------------------------
# 查询场景：cache/ 下每个场景一个 json 文件，文件名形如 scene_赏金.json
# -------------------------

# 场景ID即文件名主体，禁止文件系统非法字符和空白符
SCENE_ID_PATTERN = re.compile(r"^scene_[^\\/:*?\"<>|\s]{1,60}$")


def ensure_cache_dir():
    os.makedirs(CACHE_DIR, exist_ok=True)


def list_scenes():
    """读取 cache/ 下所有场景文件，优先按 order 字段排序，其次按修改时间"""

    if not os.path.isdir(CACHE_DIR):
        return []

    scenes = []

    for fname in os.listdir(CACHE_DIR):
        if not (fname.startswith("scene_") and fname.endswith(".json")):
            continue

        scene_path = os.path.join(CACHE_DIR, fname)

        try:
            with open(scene_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, dict) and data.get("id") and data.get("name"):
                order = data.get("order")
                scenes.append({
                    "id": data["id"],
                    "name": data["name"],
                    "_order": order if isinstance(order, int) else None,
                    "_mtime": os.path.getmtime(scene_path),
                })

        except (OSError, ValueError):
            pass  # 损坏的场景文件直接跳过

    # 有 order 的按 order 排，没有的按修改时间排在后面
    scenes.sort(key=lambda s: (
        s["_order"] if s["_order"] is not None else 10 ** 12,
        s["_mtime"],
    ))

    return [{"id": s["id"], "name": s["name"]} for s in scenes]


@app.route("/api/scenes", methods=["GET"])
def api_scenes():
    """全部查询场景"""

    return jsonify(list_scenes())


@app.route("/api/scenes", methods=["POST"])
def api_scenes_create():
    """新增查询场景，文件名形如 scene_赏金.json"""

    name = (request.get_json(silent=True) or {}).get("name", "").strip()

    if not name:
        return jsonify({"error": "场景名称不能为空"}), 400

    if len(name) > 20:
        return jsonify({"error": "场景名称最多 20 个字符"}), 400

    if set(name) <= {"."}:
        return jsonify({"error": "场景名称不能只包含点号"}), 400

    scene_id = f"scene_{name}"

    if not SCENE_ID_PATTERN.match(scene_id):
        return jsonify({"error": "名称不能包含 \\ / : * ? \" < > | 或空格等字符"}), 400

    ensure_cache_dir()

    scene_path = os.path.join(CACHE_DIR, f"{scene_id}.json")

    if os.path.exists(scene_path):
        return jsonify({"error": f"场景「{name}」已存在"}), 409

    with open(scene_path, "w", encoding="utf-8") as f:
        # elements 字段预留给该场景页面的后续内容
        json.dump({"id": scene_id, "name": name, "elements": []}, f, ensure_ascii=False)

    return jsonify({"id": scene_id, "name": name}), 201


@app.route("/api/scenes/order", methods=["POST"])
def api_scenes_order():
    """保存拖动后的场景顺序：把位置写入每个场景文件的 order 字段"""

    ids = (request.get_json(silent=True) or {}).get("ids")

    if not isinstance(ids, list) or not ids:
        return jsonify({"error": "无效的排序数据"}), 400

    for index, scene_id in enumerate(ids):

        if not isinstance(scene_id, str) or not SCENE_ID_PATTERN.match(scene_id):
            return jsonify({"error": "无效的场景ID"}), 400

        scene_path = os.path.join(CACHE_DIR, f"{scene_id}.json")

        if not os.path.exists(scene_path):
            continue  # 列表里有已删除的场景时跳过

        try:
            with open(scene_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            data["order"] = index

            with open(scene_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)

        except (OSError, ValueError):
            pass  # 单个场景排序失败不影响其他场景

    return jsonify(list_scenes())


@app.route("/api/scenes/<scene_id>", methods=["DELETE"])
def api_scenes_delete(scene_id):
    """删除查询场景"""

    if not SCENE_ID_PATTERN.match(scene_id):
        return jsonify({"error": "无效的场景ID"}), 400

    scene_path = os.path.join(CACHE_DIR, f"{scene_id}.json")

    if os.path.exists(scene_path):
        try:
            os.remove(scene_path)
        except OSError:
            return jsonify({"error": "删除失败"}), 500

    return jsonify({"ok": True})


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

    try:
        orders = get_orders(item["slug"])
    except requests.RequestException:
        # 网络异常（含自动重试后仍失败）：返回明确错误，而不是 500 崩掉
        return jsonify({
            "error": "连接 Warframe Market 失败（已自动重试），请稍后再试"
        }), 502

    # 赋能类只看满级卖单（rank == maxRank），MOD 和其他物品只看0级
    max_rank = item.get("maxRank")

    filtered = []

    for order in orders:

        if order["type"] != "sell":
            continue

        if order["user"]["status"] != "ingame":
            continue

        if max_rank is None:
            # MOD 按 0 级（未安装）计价
            if order.get("rank", 0) != 0:
                continue
        elif order.get("rank") != max_rank:
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

    # 记录查询历史（只在查询成功时记录，保存前5卖单的全部价格）
    history = add_history(
        item["i18n"]["zh-hans"]["name"],
        [order["platinum"] for order in top5],
        RANK_NEED_COUNT.get(max_rank) if max_rank is not None else None,
    )

    return jsonify({
        "item": {
            "name": item["i18n"]["zh-hans"]["name"],
            "name_en": item["i18n"]["en"]["name"],
            "slug": item["slug"],
            # 赋能类显示升到满级需要的数量（如 maxRank=5 需要 21 个）
            "need_count": RANK_NEED_COUNT.get(max_rank) if max_rank is not None else None,
        },
        "orders": top5,
        "history": history,
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

    ensure_cache_dir()

    print("正在加载物品列表...")

    try:
        ITEMS = get_all_items()
    except requests.RequestException:
        print("Warframe Market 连接失败，且没有可用的本地缓存")
        print("请检查网络后重新运行")
        sys.exit(1)

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
