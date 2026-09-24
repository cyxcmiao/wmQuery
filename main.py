from auto_install import ensure_package

# 检测到没有安装时，自动通过 pip 安装
ensure_package("requests")
ensure_package("flask")

import json
import os
import re
import socket
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone

import requests
from flask import Flask, jsonify, request, send_from_directory
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://api.warframe.market/v2"

if getattr(sys, "frozen", False):
    # 打包成 exe 后：index.html 等资源释放到 _MEIPASS 临时目录，
    # 缓存和历史等数据文件存放在 exe 同目录，保证可持久保存
    RESOURCE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    BASE_DIR = os.path.dirname(sys.executable)
else:
    RESOURCE_DIR = BASE_DIR = os.path.dirname(os.path.abspath(__file__))

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

WEB_DIR = RESOURCE_DIR

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


STATS_BASE_URL = "https://api.warframe.market/v1"


def get_closed_daily_avg(item_slug, full_rank=None):
    """查询物品近90天已成交订单的按日中间价（warframe.market v1 统计接口，median 字段）

    :param full_rank: 赋能类传满级等级，只统计 mod_rank == 满级 的成交记录；
                      MOD 和其他物品传 None——成交记录带 mod_rank 的（MOD类）只统计0级，
                      不带 mod_rank 的（普通物品）不过滤等级
    :return: {UTC日期字符串: 当日成交中间价}
    """

    url = f"{STATS_BASE_URL}/items/{item_slug}/statistics"

    response = SESSION.get(url, headers=HEADERS, timeout=10)

    response.raise_for_status()

    closed = response.json()["payload"]["statistics_closed"].get("90days", [])

    # MOD 类物品的成交记录带 mod_rank：只统计 0 级
    has_rank_field = any("mod_rank" in entry for entry in closed)

    by_date = {}

    for entry in closed:
        if full_rank is not None:
            # 赋能的成交记录按 mod_rank 拆条，只看满级
            if entry.get("mod_rank") != full_rank:
                continue
        elif has_rank_field and entry.get("mod_rank", 0) != 0:
            continue

        median = entry.get("median")

        if median is None:
            continue

        day = entry["datetime"][:10]
        by_date.setdefault(day, []).append(median)

    # MOD 的成交记录按 mod_rank 拆成多条，合并为当日中间价（简单平均）
    return {day: sum(vals) / len(vals) for day, vals in by_date.items()}


def get_recent_closed_avg(item_slug, need_count=None, max_rank=None):
    """取昨天/前天/大前天（UTC日期）的成交中间价

    :param need_count: 赋能类折算为单个价格时需要的个数，其他物品为 None
    :param full_rank: 赋能类的满级等级；MOD 只统计0级成交，普通物品不过滤
    :return: [a, b, c]，某天没有成交时对应位置为 None；统计查询失败也返回 [None, None, None]，
             不影响挂单价格的展示
    """

    try:
        by_date = get_closed_daily_avg(item_slug, full_rank=max_rank)
    except (requests.RequestException, KeyError, TypeError, ValueError):
        return [None, None, None]

    now = datetime.now(timezone.utc)

    result = []

    for i in (1, 2, 3):
        day = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        avg = by_date.get(day)

        if avg is not None and need_count:
            avg /= need_count

        result.append(round(avg, 2) if avg is not None else None)

    return result


def filter_sell_orders(orders, max_rank):
    """筛选状态为“游戏内”的卖单

    赋能类只看满级（rank == maxRank）；
    MOD 只看0级和当前挂单中出现的最高等级（缓存里 MOD 没有满级等级，用最高 rank 推断），
    其余等级（如3级）的卖单不参与；
    其他物品没有等级概念，不过滤。
    """

    ranks = [order.get("rank") for order in orders if order.get("rank") is not None]
    mod_top_rank = max(ranks) if (max_rank is None and ranks) else None

    filtered = []

    for order in orders:
        if order["type"] != "sell":
            continue

        if order["user"]["status"] != "ingame":
            continue

        rank = order.get("rank")

        if max_rank is not None:
            if rank != max_rank:
                continue
        elif mod_top_rank is not None and rank not in (0, mod_top_rank):
            continue

        filtered.append(order)

    return filtered


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


def validate_scene_name(name):
    """校验场景名称，返回错误信息，通过时返回 None"""

    if not name:
        return "场景名称不能为空"

    if len(name) > 20:
        return "场景名称最多 20 个字符"

    if set(name) <= {"."}:
        return "场景名称不能只包含点号"

    if not SCENE_ID_PATTERN.match(f"scene_{name}"):
        return "名称不能包含 \\ / : * ? \" < > | 或空格等字符"

    return None


def load_scene(scene_id):
    """读取单个场景文件的完整数据，ID 无效、文件不存在或损坏时返回 None"""

    if not SCENE_ID_PATTERN.match(scene_id):
        return None

    scene_path = os.path.join(CACHE_DIR, f"{scene_id}.json")

    try:
        with open(scene_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict) and data.get("id"):
            return data

    except (OSError, ValueError):
        pass  # 文件损坏时当作场景不存在

    return None


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
    error = validate_scene_name(name)

    if error:
        return jsonify({"error": error}), 400

    scene_id = f"scene_{name}"

    ensure_cache_dir()

    scene_path = os.path.join(CACHE_DIR, f"{scene_id}.json")

    if os.path.exists(scene_path):
        return jsonify({"error": f"场景「{name}」已存在"}), 409

    with open(scene_path, "w", encoding="utf-8") as f:
        # elements 存表格行，update_time 存上次查询时间，run_time 存单局时间，
        # drop_multiplier 存掉落倍率，均在场景页保存
        json.dump(
            {
                "id": scene_id,
                "name": name,
                "elements": [],
                "update_time": None,
                "run_time": None,
                "drop_multiplier": None,
            },
            f,
            ensure_ascii=False
        )

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


@app.route("/api/scenes/<scene_id>", methods=["GET"])
def api_scene_get(scene_id):
    """读取单个场景的完整数据（表格行、上次查询时间）"""

    data = load_scene(scene_id)

    if data is None:
        return jsonify({"error": "场景不存在"}), 404

    return jsonify({
        "id": data["id"],
        "name": data.get("name", ""),
        "elements": data.get("elements", []),
        "update_time": data.get("update_time"),
        "run_time": data.get("run_time"),
        "drop_multiplier": data.get("drop_multiplier"),
    })


@app.route("/api/scenes/<scene_id>", methods=["PUT"])
def api_scene_save(scene_id):
    """保存场景内容：表格行、上次查询时间，支持同时改名

    改名时场景ID和 json 文件名同步更新（scene_旧名.json -> scene_新名.json）。
    """

    data = load_scene(scene_id)

    if data is None:
        return jsonify({"error": "场景不存在"}), 404

    body = request.get_json(silent=True) or {}
    new_id = scene_id

    if "name" in body:
        name = str(body.get("name") or "").strip()
        error = validate_scene_name(name)

        if error:
            return jsonify({"error": error}), 400

        data["name"] = name
        new_id = f"scene_{name}"

        if new_id != scene_id and os.path.exists(os.path.join(CACHE_DIR, f"{new_id}.json")):
            return jsonify({"error": f"场景「{name}」已存在"}), 409

    if "elements" in body:
        elements = body["elements"]

        if not isinstance(elements, list):
            return jsonify({"error": "无效的表格数据"}), 400

        clean = []

        for row in elements:
            if not isinstance(row, dict):
                continue

            closed = row.get("closed")

            if isinstance(closed, list):
                # 新格式：三天成交均价 [昨天,前天,大前天]，缺失为 None
                clean_closed = []

                for v in closed[:3]:
                    clean_closed.append(v if isinstance(v, (int, float)) and not isinstance(v, bool) else None)

                closed = clean_closed
            elif isinstance(closed, (int, float)) and not isinstance(closed, bool):
                closed = [closed, None, None]  # 更早版本只存了昨天的均价
            else:
                closed = None

            clean.append({
                "name": str(row.get("name", "")).strip()[:100],
                "rate": str(row.get("rate", "")).strip()[:20],
                "standing": str(row.get("standing", "")).strip()[:20],
                "custom": str(row.get("custom", "")).strip()[:20],
                # wm 为两行文本（统计数据+挂单价），放宽截断长度
                "wm": str(row.get("wm", "")).strip()[:120],
                "closed": closed,
            })

        data["elements"] = clean

    if "update_time" in body:
        update_time = body["update_time"]

        if isinstance(update_time, (int, float)) or update_time is None:
            data["update_time"] = update_time
        else:
            return jsonify({"error": "无效的查询时间"}), 400

    if "run_time" in body:
        run_time = body["run_time"]

        # 单局时间（分钟）：数字或空，0 及负数视为无效
        if run_time is None:
            data["run_time"] = None
        elif isinstance(run_time, (int, float)) and run_time > 0:
            data["run_time"] = run_time
        else:
            return jsonify({"error": "无效的单局时间"}), 400

    if "drop_multiplier" in body:
        drop_multiplier = body["drop_multiplier"]

        # 掉落倍率：数字或空，0 及负数视为无效，空值计算时按 1
        if drop_multiplier is None:
            data["drop_multiplier"] = None
        elif isinstance(drop_multiplier, (int, float)) and drop_multiplier > 0:
            data["drop_multiplier"] = drop_multiplier
        else:
            return jsonify({"error": "无效的掉落倍率"}), 400

    try:
        if new_id != scene_id:
            os.replace(
                os.path.join(CACHE_DIR, f"{scene_id}.json"),
                os.path.join(CACHE_DIR, f"{new_id}.json"),
            )

        data["id"] = new_id

        ensure_cache_dir()

        with open(os.path.join(CACHE_DIR, f"{new_id}.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    except OSError:
        return jsonify({"error": "保存失败"}), 500

    return jsonify({
        "id": data["id"],
        "name": data.get("name", ""),
        "elements": data.get("elements", []),
        "update_time": data.get("update_time"),
        "run_time": data.get("run_time"),
        "drop_multiplier": data.get("drop_multiplier"),
    })


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

    # 赋能只看满级；MOD 只看0级和当前挂单最高等级
    max_rank = item.get("maxRank")

    filtered = filter_sell_orders(orders, max_rank)

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

    # 近三日（UTC日期）成交均价 a/b/c 与挂单均价 d、最低3个卖单 d1/d2/d3
    # （赋能类均已折算为单个价格）
    need_count = RANK_NEED_COUNT.get(max_rank) if max_rank is not None else None

    def to_unit(price):
        return round(price / need_count, 2) if need_count else price

    top3 = [order["platinum"] for order in filtered[:3]]

    closed = get_recent_closed_avg(item["slug"], need_count, max_rank)

    wm_avg = round(sum(top3) / len(top3) / (need_count or 1), 2) if top3 else None

    return jsonify({
        "item": {
            "name": item["i18n"]["zh-hans"]["name"],
            "name_en": item["i18n"]["en"]["name"],
            "slug": item["slug"],
            # 赋能类显示升到满级需要的数量（如 maxRank=5 需要 21 个）
            "need_count": need_count,
        },
        "orders": top5,
        "wm_avg": wm_avg,
        "top3": [to_unit(p) for p in top3],
        "closed": closed,
        "history": history,
    })


@app.route("/api/price")
def api_price():
    """按物品名称查询场景表格用的价格：游戏中卖单最低3个的平均价

    赋能类先查满级价格，再除以升满级所需数量折算为单个赋能的价格。
    """

    name = request.args.get("name", "").strip()

    if not name:
        return jsonify({"error": "请输入物品名称"}), 400

    item = find_item(ITEMS, name)

    if item is None:
        suggestions = [n for n in NAME_LIST if name in n][:8]
        return jsonify({
            "error": f"没有找到物品：{name}",
            "suggestions": suggestions
        }), 404

    try:
        orders = get_orders(item["slug"])
    except requests.RequestException:
        return jsonify({
            "error": "连接 Warframe Market 失败（已自动重试），请稍后再试"
        }), 502

    # 与 /api/query 相同的筛选规则：赋能只看满级，MOD 只看0级和当前挂单最高等级
    max_rank = item.get("maxRank")
    need_count = RANK_NEED_COUNT.get(max_rank) if max_rank is not None else None

    filtered = filter_sell_orders(orders, max_rank)

    filtered.sort(key=lambda order: order["platinum"])

    top3 = [order["platinum"] for order in filtered[:3]]

    # 近三日（UTC日期）成交中间价 a/b/c：赋能只看满级、MOD只看0级，赋能已折算为单个价格
    closed = get_recent_closed_avg(item["slug"], need_count, max_rank)

    if not top3:
        return jsonify({
            "name": item["i18n"]["zh-hans"]["name"],
            "need_count": need_count,
            "prices": [],
            "avg": None,
            "closed": closed,
        })

    # 赋能折算为单个价格：均价按原始价计算，展示价格逐个折算并至多保留两位小数
    def to_unit(price):
        return round(price / need_count, 2) if need_count else price

    avg = sum(top3) / len(top3)

    return jsonify({
        "name": item["i18n"]["zh-hans"]["name"],
        "need_count": need_count,
        "prices": [to_unit(p) for p in top3],
        "avg": round(avg / need_count, 2) if need_count else round(avg, 2),
        "closed": closed,
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
