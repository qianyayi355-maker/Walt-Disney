"""数据层：历史数据、实时接口、排队时间模型与最优路线求解。

本模块被 pages/app.py 调用，也可以单独运行做自检：

    python3 data_layer.py

数据来源
--------
1. 历史数据（随项目打包的 CSV）：四个园区的逐日客流、项目平均/最大排队时长、
   连锁客流指数、Lightning Lane 分类与分流指数。
2. 实时数据（ThemeParks.wiki 公开接口）：项目当前排队分钟数、开放状态、
   每个项目当天的运营时段（含早享 Early Entry 与特别活动场次）。
3. 静态地理数据（data/attraction_meta.json）：项目经纬度，用于估算步行时间。
"""

from __future__ import annotations

import difflib
import hashlib
import json
import math
import pathlib
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import streamlit as st

BASE_DIR = pathlib.Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
API = "https://api.themeparks.wiki/v1"
ET = ZoneInfo("America/New_York")

# 应用运行必需的历史数据文件（缺任何一个都无法工作）
REQUIRED_DATA_FILES = (
    "calendar_enriched.csv",
    "ride_stats.csv",
    "park_stats.csv",
    "chained_index.csv",
    "ll_classification.csv",
    "ll_split_index.csv",
)

# 景点经纬度缓存：缺了实时看板照常工作，只是无法规划路线
GEO_CACHE_FILE = "attraction_meta.json"


def missing_data_files(required: tuple[str, ...] = REQUIRED_DATA_FILES) -> list[str]:
    """返回缺失的必需数据文件名，供页面在启动时给出可读的提示。"""
    return [name for name in required if not (DATA_DIR / name).exists()]


def diagnose(park: str) -> dict:
    """部署排错用：报告数据目录、坐标缓存与实时接口的真实状态。

    这几项正好覆盖了 Streamlit Cloud 上最常见的三种失败：
    文件没传全、坐标缓存读不出来、容器访问不了外网。
    """
    meta = load_meta()
    checked = list(REQUIRED_DATA_FILES) + [GEO_CACHE_FILE]
    exists = {name: (DATA_DIR / name).exists() for name in checked}
    try:
        resp = requests.get(f"{API}/entity/{PARKS[park]['api_id']}/live", timeout=8)
        live = f"可用（HTTP {resp.status_code}）" if resp.status_code == 200 \
            else f"不可用（HTTP {resp.status_code}）"
    except requests.RequestException as exc:
        live = f"不可用（{type(exc).__name__}，可能是容器无法访问外网）"
    return {
        "项目根目录": str(BASE_DIR),
        "数据目录": str(DATA_DIR),
        "数据目录存在": "是" if DATA_DIR.exists() else "否",
        "缺失的数据文件": "、".join(k for k, v in exists.items() if not v) or "无",
        "坐标缓存条目数": len(meta),
        f"「{park}」的景点条目数": sum(1 for v in meta.values() if v.get("park_name") == park),
        "实时接口": live,
    }

# ---------------------------------------------------------------- 园区定义
PARKS: dict[str, dict] = {
    "Magic Kingdom": {
        "api_id": "75ea578a-adc8-4116-a54d-dccb60765ef9",
        "icon": "🏰",
        "color": "#1f6feb",
        "gate": (28.41764, -81.58117),
    },
    "Epcot": {
        "api_id": "47f90d2c-e191-4239-a466-5892ef59a88b",
        "icon": "🌐",
        "color": "#8250df",
        "gate": (28.37470, -81.54934),
    },
    "Hollywood Studios": {
        "api_id": "288747d1-8b4f-4a64-867e-ea7c9b27bad8",
        "icon": "🎬",
        "color": "#bf3989",
        "gate": (28.35765, -81.55834),
    },
    "Animal Kingdom": {
        "api_id": "1c84a229-8862-4648-9c71-378ddd2c7693",
        "icon": "🦁",
        "color": "#2da44e",
        "gate": (28.35538, -81.59009),
    },
}
PARK_NAMES = list(PARKS)

# ---------------------------------------------------------------- 参数假设
WALK_SPEED_M_PER_MIN = 70.0   # 园内步行速度（含人流减速）
WALK_DETOUR = 1.35            # 直线距离 → 实际路径的绕行系数
MIN_WALK_MIN = 1.0

# 项目体验时长（分钟，不含排队）。按名称关键字覆盖，其余按类型取默认值。
DURATION_DEFAULT = {"RIDE": 5.0, "SHOW": 22.0, "MEET": 14.0, "OTHER": 6.0}
DURATION_OVERRIDES = {
    "kilimanjaro safaris": 22, "spaceship earth": 15, "living with the land": 15,
    "hall of presidents": 25, "carousel of progress": 21, "american adventure": 30,
    "philharmagic": 12, "laugh floor": 15, "country bear": 15, "enchanted tales": 20,
    "turtle talk": 15, "it's tough to be a bug": 9, "muppet vision": 15, "indy": 25,
    "beauty and the beast": 25, "frozen sing": 30, "finding nemo": 30, "lion king": 30,
    "festival of the lion king": 30, "winged encounters": 15, "feathered friends": 20,
    "rise of the resistance": 18, "smugglers run": 8, "flight of passage": 9,
    "navi river journey": 5, "tiana's bayou adventure": 12, "splash mountain": 12,
    "pirates of the caribbean": 9, "haunted mansion": 8, "small world": 11,
    "jungle cruise": 10, "soarin": 6, "remy": 8, "frozen ever after": 8,
    "test track": 6, "mission: space": 8, "guardians of the galaxy": 6,
    "tower of terror": 5, "rock 'n' roller": 3, "slinky dog": 4, "toy story midway": 6,
    "expedition everest": 5, "seven dwarfs": 4, "space mountain": 5,
    "big thunder": 4, "peoplemover": 10, "people mover": 10, "swiss family": 12,
    "under the sea": 7, "winnie the pooh": 4, "dumbo": 2, "tea party": 2,
    "astro orbiter": 2, "magic carpets": 2, "barnstormer": 2, "speedway": 5,
    "buzz lightyear": 4, "toy story mania": 6, "star tours": 6, "muppet vision": 15,
    "monsters inc": 15, "triceratops": 3, "dinosaur": 4, "primeval whirl": 2,
    "kali river": 5, "gran fiesta": 8, "three caballeros": 8, "mexico": 8,
    "seas with nemo": 8, "the seas": 8, "figment": 6, "imagination": 6,
    "wonders of life": 6, "canada": 18, "china": 14, "france": 18, "japan": 8,
    "morocco": 8, "norway": 8, "germany": 8, "italy": 8, "united kingdom": 8,
    "star wars launch bay": 10, "one man's dream": 10, "walt disney": 10,
}

# 日内排队强度曲线：以“开园后小时数”为横轴，1.0 为全天平均强度。
# 依据主题公园常见的日内规律——开园瞬间最低、午前快速上升、午后达到峰值、
# 傍晚回落、闭园前最低。属于经验假设，界面中由“峰值系数”滑块调节。
_INTRADAY_KNOTS_H = [-1.0, 0.0, 0.75, 2.0, 3.5, 5.0, 6.5, 8.0, 9.5, 11.0, 12.0, 13.0]
_INTRADAY_KNOTS_V = [0.50, 0.62, 0.86, 1.06, 1.15, 1.20, 1.18, 1.08, 0.98, 0.88, 0.72, 0.45]

LL_CUT_RATIO = 0.65  # Lightning Lane 相对普通队列的排队时长缩减比例（行业经验值）


# ================================================================ 历史数据
@st.cache_data(show_spinner=False)
def load_calendar() -> pd.DataFrame:
    df = pd.read_csv(DATA_DIR / "calendar_enriched.csv", parse_dates=["date"])
    return df


@st.cache_data(show_spinner=False)
def load_ride_stats() -> pd.DataFrame:
    return pd.read_csv(DATA_DIR / "ride_stats.csv")


@st.cache_data(show_spinner=False)
def load_park_stats() -> pd.DataFrame:
    return pd.read_csv(DATA_DIR / "park_stats.csv")


@st.cache_data(show_spinner=False)
def load_ll_class() -> pd.DataFrame:
    return pd.read_csv(DATA_DIR / "ll_classification.csv")


@st.cache_data(show_spinner=False)
def load_ll_split() -> pd.DataFrame:
    return pd.read_csv(DATA_DIR / "ll_split_index.csv")


@st.cache_data(show_spinner=False)
def load_chained() -> pd.DataFrame:
    return pd.read_csv(DATA_DIR / "chained_index.csv")


@st.cache_data(show_spinner=False)
def _read_json(path_str: str, mtime: float) -> dict:
    """mtime 参与缓存键：文件新增或修改后缓存自动失效。"""
    return json.loads(pathlib.Path(path_str).read_text(encoding="utf-8"))


def load_meta() -> dict:
    """读取景点经纬度缓存。

    注意这里把文件的修改时间作为缓存键的一部分：曾经用无 TTL 的缓存直接缓存
    整个函数，结果在文件缺席时把"空字典"缓存住了，之后补上文件也读不出来。
    """
    path = DATA_DIR / "attraction_meta.json"
    if not path.exists():
        return {}
    try:
        return _read_json(str(path), path.stat().st_mtime)
    except (json.JSONDecodeError, OSError):
        return {}


# ================================================================ 工具函数
def _norm(name: str) -> str:
    """把项目名称规范化，用于把实时数据和历史数据对上。"""
    s = str(name).lower().replace("’", "'").replace("–", "-").replace("~", " ")
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^a-z0-9' ]", " ", s)
    s = re.sub(r"\b(the|a|an|at|with|of|and|de)\b", " ", s)
    s = s.replace("'", "")
    return re.sub(r"\s+", " ", s).strip()


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def walk_minutes(p1, p2, speed: float = WALK_SPEED_M_PER_MIN) -> float:
    dist = haversine_m(p1[0], p1[1], p2[0], p2[1]) * WALK_DETOUR
    return max(MIN_WALK_MIN, dist / max(speed, 1.0))


def intraday_factor(hours_since_open: float, peak: float = 1.0) -> float:
    """日内排队强度系数。peak>1 放大午间高峰，<1 相对平缓。"""
    base = float(np.interp(hours_since_open, _INTRADAY_KNOTS_H, _INTRADAY_KNOTS_V))
    if base > 1.0:
        base = 1.0 + (base - 1.0) * peak
    return max(base, 0.2)


def ride_duration(name: str, entity_type: str, attraction_type: str | None) -> float:
    n = str(name).lower()
    for key, val in DURATION_OVERRIDES.items():
        if key in n:
            return float(val)
    if n.startswith("meet ") or "meet " in n or "character" in n:
        return DURATION_DEFAULT["MEET"]
    if entity_type == "SHOW":
        return DURATION_DEFAULT["SHOW"]
    if attraction_type == "RIDE" or entity_type == "ATTRACTION":
        return DURATION_DEFAULT["RIDE"]
    return DURATION_DEFAULT["OTHER"]


def category_of(name: str, entity_type: str) -> str:
    n = str(name).lower()
    if n.startswith("meet ") or "meet " in n or "character greeting" in n:
        return "见面会"
    if entity_type == "SHOW":
        return "演出"
    if any(k in n for k in ("coaster", "mountain", "dash", "everest", "tower of terror",
                            "guardians", "test track", "rise of the resistance",
                            "mine train", "tron", "mission: space", "dinosaur",
                            "indiana jones")):
        return "刺激项目"
    if any(k in n for k in ("tour", "trail", "exploration", "safari", "river", "cruise",
                            "boat", "railway", "railroad", "carousel", "peoplemover",
                            "people mover", "exhibit", "aquarium", "gallery")):
        return "观光"
    return "亲子项目"


# ================================================================ 实时数据
@st.cache_data(ttl=45, show_spinner=False)
def _fetch_live(park_id: str, stamp: str) -> dict | None:
    """stamp 只用于让缓存键随刷新周期变化。"""
    try:
        r = requests.get(f"{API}/entity/{park_id}/live", timeout=8)
        if r.status_code == 200:
            return r.json()
    except requests.RequestException:
        return None
    return None


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(ET)
    except ValueError:
        return None


@dataclass
class Snapshot:
    """某一时刻的园区状态，实时与模拟两种来源共用同一结构。"""

    park: str
    when: datetime
    source: str                      # "live" 或 "simulated"
    rides: pd.DataFrame
    hours: dict = field(default_factory=dict)
    message: str = ""
    windows: dict = field(default_factory=dict)   # entity_id -> (开, 关)

    @property
    def is_live(self) -> bool:
        return self.source == "live"


def _historical_join(park: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """把历史平均/最大排队与 LL 分类挂到项目名上。"""
    rs = load_ride_stats()
    rs = rs[(rs.park_name == park) & (rs.year == "all_time")]
    if rs.empty:
        empty = pd.DataFrame(columns=["name", "hist_avg", "hist_max", "key"])
        return empty, pd.DataFrame(columns=["key", "ll_class"])

    wide = (rs.pivot_table(index="ride_name", columns="metric", values="minutes",
                           aggfunc="max").reset_index())
    out = wide.rename(columns={"ride_name": "name",
                               "ride_avg_wait": "hist_avg",
                               "ride_max_wait": "hist_max"})
    for c in ("hist_avg", "hist_max"):
        if c not in out.columns:
            out[c] = np.nan
    out = out[["name", "hist_avg", "hist_max"]].copy()
    out["key"] = out.name.map(_norm)
    out = out.drop_duplicates("key", keep="first")

    ll = load_ll_class()
    if not ll.empty:
        ll = ll.copy()
        ll["key"] = ll.ride_name.map(_norm)
        ll = ll.drop_duplicates("key")
        ll = ll[["key", "ll_class"]]
    else:
        ll = pd.DataFrame(columns=["key", "ll_class"])
    return out.reset_index(drop=True), ll.reset_index(drop=True)


def _attach_history(names: list[str], park: str) -> pd.DataFrame:
    hist, ll = _historical_join(park)
    keys = [_norm(n) for n in names]
    df = pd.DataFrame({"name": names, "key": keys})

    merged = df.merge(hist[["key", "hist_avg", "hist_max"]], on="key", how="left")
    unmatched = merged.hist_avg.isna()
    if unmatched.any() and not hist.empty:
        pool = hist.key.tolist()
        fixed = []
        for k in merged.loc[unmatched, "key"]:
            m = difflib.get_close_matches(k, pool, n=1, cutoff=0.86)
            fixed.append(m[0] if m else None)
        merged.loc[unmatched, "hist_avg"] = [
            (hist.loc[hist.key == k, "hist_avg"].iloc[0] if k else np.nan) for k in fixed
        ]
        merged.loc[unmatched, "hist_max"] = [
            (hist.loc[hist.key == k, "hist_max"].iloc[0] if k else np.nan) for k in fixed
        ]

    merged = merged.merge(ll, on="key", how="left")
    merged["ll_class"] = merged.ll_class.fillna("unknown")
    return merged


def _park_crowd_today(park: str, day: date) -> float:
    cal = load_calendar()
    hit = cal[(cal.park_name == park) & (cal.date.dt.date == day)]
    if not hit.empty:
        return float(hit.crowd_level_pct.iloc[0])
    # 没有当天记录时用同月历史均值
    same = cal[(cal.park_name == park) & (cal.month == day.month)]
    return float(same.crowd_level_pct.mean()) if not same.empty else 55.0


def _default_hours(park: str, day: date) -> dict:
    """无网络时的运营时段估算：以历史运营时长与早享/晚间活动标记为准。"""
    cal = load_calendar()
    hit = cal[(cal.park_name == park) & (cal.date.dt.date == day)]
    minutes, early, evening = 720.0, 0, 0
    if not hit.empty:
        minutes = float(hit.operating_minutes.iloc[0] or 720)
        early = int(hit.has_early_entry.iloc[0])
        evening = int(hit.has_extended_evening.iloc[0])
    op = datetime.combine(day, time(9, 0), tzinfo=ET)
    if early:
        op -= timedelta(minutes=30)
    return {
        "open": op,
        "close": op + timedelta(minutes=minutes),
        "early_entry": (op, op + timedelta(minutes=30)) if early else None,
        "extended_evening": (op + timedelta(minutes=minutes),
                             op + timedelta(minutes=minutes + 120)) if evening else None,
        "special_event": None,
        "source": "estimated",
    }


def get_snapshot(park: str, force_live: bool = True, ttl_bucket: int = 0) -> Snapshot:
    """取得园区当前快照；实时接口不可用时自动退回基于历史数据的模拟。"""
    now = datetime.now(ET)
    day = now.date()

    if force_live:
        stamp = f"{ttl_bucket}"
        raw = _fetch_live(PARKS[park]["api_id"], stamp)
    else:
        raw, stamp = None, "off"

    if raw:
        snap = _snapshot_from_live(park, raw, now)
        if not snap.rides.empty:
            return snap
        msg = "实时接口返回为空"
    else:
        msg = "无法连接实时接口"
    snap = _snapshot_simulated(park, now, msg)
    return snap


def _snapshot_from_live(park: str, raw: dict, now: datetime) -> Snapshot:
    meta = load_meta()
    rows, wins, hours_types = [], {}, {}
    park_status = "CLOSED"
    for item in raw.get("liveData", []):
        if item.get("entityType") == "PARK":
            park_status = item.get("status", park_status)
            continue
        if item.get("entityType") not in ("ATTRACTION", "SHOW"):
            continue
        info = meta.get(item["id"], {})
        # 没有经纬度缓存时也保留该项目：看板靠排队数据就能工作，
        # 只有路线规划需要坐标（届时会把这些项目排除并提示）。
        lat, lon = info.get("latitude"), info.get("longitude")
        queue = item.get("queue") or {}
        standby = (queue.get("STANDBY") or {}).get("waitTime")
        return_time = queue.get("RETURN_TIME") or {}
        windows = {}
        for oh in item.get("operatingHours") or []:
            s, e = _parse_dt(oh.get("startTime")), _parse_dt(oh.get("endTime"))
            if not s or not e:
                continue
            hours_types.setdefault(oh["type"], []).append((s, e))
            if oh["type"] in ("Operating", "Early Entry", "Special Ticketed Event"):
                windows[oh["type"]] = (s, e)
        showtimes = [_parse_dt(s.get("startTime")) for s in item.get("showtimes") or []]
        showtimes = sorted(t for t in showtimes if t)
        if "Operating" in windows:
            wins[item["id"]] = windows["Operating"]
        elif windows:  # 只参加特别活动的项目
            wins[item["id"]] = sorted(windows.values(), key=lambda w: w[0])[0]
        # 仅限特别活动（如万圣节派对）开放的项目，白天游客进不去
        event_only = bool(windows) and "Operating" not in windows and "Early Entry" not in windows
        rows.append({
            "entity_id": item["id"],
            "name": item["name"],
            "entity_type": item["entityType"],
            "attraction_type": info.get("attraction_type"),
            "status": item.get("status", "UNKNOWN"),
            "wait": standby,
            "ll_return_state": return_time.get("state"),
            "ll_return_start": _parse_dt(return_time.get("returnStart")),
            "showtimes": showtimes,
            "has_early_entry": "Early Entry" in windows,
            "has_special_event": "Special Ticketed Event" in windows,
            "event_only": event_only,
            "lat": lat, "lon": lon,
        })

    rides = pd.DataFrame(rows)
    if rides.empty:
        return Snapshot(park, now, "live", rides, {})

    hist = _attach_history(rides.name.tolist(), park)
    rides = rides.merge(hist[["name", "hist_avg", "hist_max", "ll_class"]], on="name", how="left")

    # 当前等待：开放但未公布等待时间的项目用历史值兜底
    crowd = _park_crowd_today(park, now.date())
    med = float(rides.hist_avg.median()) if rides.hist_avg.notna().any() else 30.0
    base_fallback = rides.hist_avg.where(rides.hist_avg.notna(), med * 0.5)
    fallback = (base_fallback * (crowd / 55.0)).clip(lower=5)
    rides["wait_now"] = rides.wait.astype("float")
    missing = rides.wait_now.isna() & (rides.status == "OPERATING")
    rides.loc[missing, "wait_now"] = fallback[missing].round()
    rides["open_now"] = rides.status.isin(["OPERATING", "DOWN"])
    rides["down"] = rides.status.eq("DOWN")
    rides["has_window"] = rides.entity_id.isin(set(wins.keys()))
    rides["has_geo"] = rides.lat.notna() & rides.lon.notna()
    rides["duration"] = [ride_duration(n, t, a) for n, t, a in
                         zip(rides.name, rides.entity_type, rides.attraction_type)]
    rides["category"] = [category_of(n, t) for n, t in zip(rides.name, rides.entity_type)]

    # 园区运营时段：取所有项目 Operating 窗口的并集
    op = hours_types.get("Operating", [])
    hours = {"open": None, "close": None, "early_entry": None,
             "extended_evening": None, "special_event": None, "source": "live"}
    if op:
        hours["open"] = min(s for s, _ in op)
        hours["close"] = max(e for _, e in op)
    if hours_types.get("Early Entry"):
        ee = hours_types["Early Entry"]
        hours["early_entry"] = (min(s for s, _ in ee), max(e for _, e in ee))
    if hours_types.get("Extended Evening"):
        ee = hours_types["Extended Evening"]
        hours["extended_evening"] = (min(s for s, _ in ee), max(e for _, e in ee))
    if hours_types.get("Special Ticketed Event"):
        ee = hours_types["Special Ticketed Event"]
        hours["special_event"] = (min(s for s, _ in ee), max(e for _, e in ee))

    return Snapshot(park, now, "live", rides, hours, park_status, windows=wins)


def _snapshot_simulated(park: str, now: datetime, message: str) -> Snapshot:
    """离线回退：用历史平均排队 + 当日客流 + 日内曲线 + 确定性噪声生成实时感数据。"""
    message = f"{message}，已切换为离线模拟数据"
    hist, ll = _historical_join(park)
    meta = load_meta()
    park_meta = {k: v for k, v in meta.items() if v.get("park_name") == park}
    by_key = {_norm(v["name"]): (k, v) for k, v in park_meta.items()}

    hours = _default_hours(park, now.date())
    crowd = _park_crowd_today(park, now.date())
    h = (now - hours["open"]).total_seconds() / 3600.0
    day_factor = intraday_factor(h) / float(np.mean(_INTRADAY_KNOTS_V))

    rows = []
    for _, r in hist.iterrows():
        match = by_key.get(r.key)
        if match:
            eid, info = match
        else:
            # 没有坐标缓存也要保留该项目：看板可用，只是不参与路线规划
            eid = f"csv-{r.key}"
            info = {"name": r["name"], "entity_type": "ATTRACTION",
                    "attraction_type": None, "latitude": None, "longitude": None}
        seed = int(hashlib.md5(f"{eid}{now:%Y%m%d%H%M}".encode()).hexdigest()[:8], 16)
        rng = np.random.default_rng(seed)
        base = float(r.hist_avg) * (crowd / 55.0) * day_factor
        wait = float(max(0, np.round(base * rng.uniform(0.88, 1.12))))
        is_open = hours["open"] <= now <= hours["close"]
        rows.append({
            "entity_id": eid,
            "name": info["name"],
            "entity_type": info.get("entity_type", "ATTRACTION"),
            "attraction_type": info.get("attraction_type"),
            "status": "OPERATING" if is_open else "CLOSED",
            "wait": wait,
            "ll_return_state": None, "ll_return_start": None,
            "showtimes": [],
            "has_early_entry": bool(hours["early_entry"]),
            "has_special_event": False,
            "event_only": False,
            "lat": info.get("latitude"), "lon": info.get("longitude"),
            "hist_avg": float(r.hist_avg), "hist_max": float(r.hist_max),
        })
    rides = pd.DataFrame(rows)
    if rides.empty:
        return Snapshot(park, now, "simulated", rides, hours, message)
    rides["key"] = rides.name.map(_norm)
    rides = rides.merge(ll, on="key", how="left")
    rides["ll_class"] = rides.ll_class.fillna("unknown")
    rides["wait_now"] = rides.wait.astype("float")
    rides.loc[rides.wait_now.isna(), "wait_now"] = rides.loc[rides.wait_now.isna(), "hist_avg"]
    rides["open_now"] = rides.status.eq("OPERATING")
    rides["down"] = False
    rides["has_window"] = False
    rides["has_geo"] = rides.lat.notna() & rides.lon.notna()
    rides["duration"] = [ride_duration(n, t, a) for n, t, a in
                         zip(rides.name, rides.entity_type, rides.attraction_type)]
    rides["category"] = [category_of(n, t) for n, t in zip(rides.name, rides.entity_type)]
    return Snapshot(park, now, "simulated", rides, hours, message)


def filter_snapshot(snap: Snapshot, include_event_only: bool = False,
                    categories: list[str] | None = None,
                    fixed_only: bool = False,
                    recorded_only: bool = False,
                    geo_only: bool = False) -> Snapshot:
    """按用户口径筛选可规划的项目，返回新的快照。"""
    rides = snap.rides
    if rides.empty:
        return snap
    mask = pd.Series(True, index=rides.index)
    if not include_event_only and "event_only" in rides.columns:
        mask &= ~rides.event_only.fillna(False).astype(bool)
    if fixed_only and "has_window" in rides.columns:
        mask &= rides.has_window.fillna(False).astype(bool)
    if geo_only and "has_geo" in rides.columns:
        # 没有经纬度就没法估算步行时间，只能排除在路线之外
        mask &= rides.has_geo.fillna(False).astype(bool)
    if recorded_only:
        # 有历史排队记录、或此刻有实时排队的项目，才算真正要排队的项目
        recorded = rides.hist_avg.notna() | rides.wait_now.notna()
        mask &= recorded | rides.entity_type.eq("SHOW")
    if categories:
        mask &= rides.category.isin(categories)
    return replace(snap, rides=rides[mask].reset_index(drop=True))


def _mean_intraday(day_hours: float, peak: float = 1.0) -> float:
    """运营时段内日内系数的平均值，用于把“全天平均排队”还原到某个时刻。"""
    xs = np.linspace(0.0, max(day_hours, 1.0), 60)
    return float(np.mean([intraday_factor(x, peak) for x in xs]))


def anchor_mode(snap: Snapshot, when_start: datetime) -> str:
    """选择排队外推的锚点。

    live    —— 为当天剩余时段做计划，直接以刚取到的实时排队为基准外推；
    history —— 为次日或已闭园之后的时段做计划，以历史平均排队为基准，
               再按目标日客流水平和日内曲线折算。
    """
    op, cl = snap.hours.get("open"), snap.hours.get("close")
    if when_start.date() != snap.when.date():
        return "history"
    if cl is not None and when_start > cl:
        return "history"
    if op is not None and when_start < op - timedelta(hours=3):
        return "history"
    return "live"


@st.cache_data(show_spinner=False)
def crowd_reference(park: str) -> float:
    """该园区历史客流水平均值，作为客流倍率的参照。"""
    cal = load_calendar()
    s = cal[cal.park_name == park]
    return float(s.crowd_level_pct.mean()) if not s.empty else 55.0


# ================================================================ 排队时间模型
def estimate_wait(snap: Snapshot, ride: pd.Series, when: datetime, peak: float = 1.0,
                  mode: str = "live", crowd_mult: float = 1.0,
                  day_hours: float = 10.0) -> float:
    """估算某个时刻该项目的排队分钟数。"""
    base_open = snap.hours.get("open") or when.replace(hour=9, minute=0, second=0, microsecond=0)
    # 运营时段按日期对齐，否则跨天计划会把“距开园小时数”算成 24 小时以上
    op_when = base_open + timedelta(days=(when.date() - base_open.date()).days)
    f_when = intraday_factor((when - op_when).total_seconds() / 3600.0, peak)

    if mode == "history":
        base = ride.get("hist_avg")
        if pd.isna(base):
            base = ride.get("wait_now")
        base = 25.0 if pd.isna(base) else float(base)
        base = base * crowd_mult
        est = base * f_when / max(_mean_intraday(day_hours, peak), 1e-6)
    else:
        op_now = base_open + timedelta(days=(snap.when.date() - base_open.date()).days)
        f_now = intraday_factor((snap.when - op_now).total_seconds() / 3600.0, peak)
        base = ride.get("wait_now")
        if pd.isna(base):
            base = ride.get("hist_avg")
        base = 25.0 if pd.isna(base) else float(base)
        est = base * f_when / max(f_now, 1e-6)

    cap = ride.get("hist_max")
    cap = 180.0 if pd.isna(cap) else float(cap)
    return float(np.clip(est, 0, max(cap * 1.15, 60)))


# ================================================================ 路线规划
@dataclass
class PlanContext:
    rides: pd.DataFrame
    gate: tuple
    start_dt: datetime
    end_dt: datetime
    snapshot: Snapshot | None = None
    peak: float = 1.0
    day_shift: int = 0
    wait_mode: str = "live"
    crowd_mult: float = 1.0
    day_hours: float = 10.0
    walk_speed: float = WALK_SPEED_M_PER_MIN
    break_start: datetime | None = None
    break_minutes: int = 0
    windows: dict = field(default_factory=dict)


def _simulate(order: list[int], ctx: PlanContext) -> dict:
    """按给定顺序推演一天的行程，返回时间表与是否可行。"""
    t = ctx.start_dt
    loc = ctx.gate
    rows, took_break, feasible = [], False, True
    for idx in order:
        r = ctx.rides.iloc[idx]
        pos = (float(r.lat), float(r.lon))
        arrive = t + timedelta(minutes=walk_minutes(loc, pos, ctx.walk_speed))
        walk = (arrive - t).total_seconds() / 60.0

        # 项目当天开放窗口
        win = ctx.windows.get(r.entity_id)
        if win:
            w_open, w_close = win
        else:
            w_open, w_close = ctx.start_dt, ctx.end_dt
        if arrive < w_open:
            arrive = w_open
        if arrive > w_close:
            feasible = False
            break

        # 午餐/休息
        if (ctx.break_start and not took_break and ctx.break_minutes
                and arrive >= ctx.break_start):
            start_b = max(arrive, ctx.break_start)
            rows.append({"kind": "break", "name": "🍽️ 用餐 / 休息",
                         "arrive": start_b,
                         "end": start_b + timedelta(minutes=ctx.break_minutes),
                         "wait": 0.0, "walk": 0.0, "duration": float(ctx.break_minutes)})
            arrive = start_b + timedelta(minutes=ctx.break_minutes)
            took_break = True

        # 演出按固定场次，到达太早需要等到开演
        dur = float(r.duration)
        show_wait = 0.0
        shows = r.get("showtimes") or []
        if shows and ctx.day_shift:
            shows = [s + timedelta(days=ctx.day_shift) for s in shows]
        if shows:
            nxt = next((s for s in shows if s >= arrive), None)
            if nxt is None:
                feasible = False
                break
            show_wait = (nxt - arrive).total_seconds() / 60.0
            arrive = nxt

        wait = estimate_wait(ctx.snapshot, r, arrive, ctx.peak, ctx.wait_mode,
                             ctx.crowd_mult, ctx.day_hours)
        end = arrive + timedelta(minutes=wait + show_wait + dur)
        rows.append({
            "kind": "ride", "name": r["name"], "arrive": arrive, "end": end,
            "wait": wait, "walk": walk, "duration": dur, "show_wait": show_wait,
            "category": r.get("category"), "ll_class": r.get("ll_class"),
            "lat": r.lat, "lon": r.lon, "idx": idx,
        })
        t, loc = end, pos
    return {"rows": rows, "finish": t, "feasible": feasible}


def plan_route(snap: Snapshot, priorities: dict[int, float], start_dt: datetime,
               end_dt: datetime, *, strategy: str = "平衡", peak: float = 1.0,
               walk_speed: float = WALK_SPEED_M_PER_MIN,
               break_start: datetime | None = None, break_minutes: int = 0,
               gate: tuple | None = None, windows: dict | None = None) -> tuple[pd.DataFrame, dict]:
    """在时间预算内最大化“游玩价值”的启发式求解（定向越野问题的贪心 + 局部搜索）。

    priorities: {行号: 权重}，0 表示跳过，1 普通，3 必玩。
    """
    rides = snap.rides.reset_index(drop=True).copy()
    gate = gate or PARKS[snap.park]["gate"]
    windows = windows if windows is not None else snap.windows
    # 实时接口给的是“今天”的运营时段；若为明天做计划，整体平移到当天同一时刻
    day_shift = (start_dt.date() - snap.when.date()).days
    if day_shift and windows:
        windows = {k: (a + timedelta(days=day_shift), b + timedelta(days=day_shift))
                   for k, (a, b) in windows.items()}
    ctx = PlanContext(rides=rides, gate=gate, start_dt=start_dt, end_dt=end_dt,
                      snapshot=snap, peak=peak, day_shift=day_shift, walk_speed=walk_speed,
                      break_start=break_start, break_minutes=break_minutes,
                      windows=windows)
    ctx.wait_mode = anchor_mode(snap, start_dt)
    ctx.crowd_mult = _park_crowd_today(snap.park, start_dt.date()) / max(crowd_reference(snap.park), 1e-6)
    ctx.day_hours = (end_dt - start_dt).total_seconds() / 3600.0

    # ---------- 价值评分 ----------
    hist = rides.hist_avg.astype("float")
    pop = (hist - hist.min()) / max(hist.max() - hist.min(), 1e-6) if hist.notna().any() else pd.Series(0.5, index=rides.index)
    pop = pop.fillna(0.5)
    live_pop = rides.wait_now.astype("float")
    if live_pop.notna().any() and live_pop.max() > 0:
        live_pop = live_pop / live_pop.max()
    pop = 0.6 * pop + 0.4 * live_pop.fillna(pop)

    pw = {"平衡": 0.9, "热门优先": 2.2, "多刷项目": 0.25}.get(strategy, 0.9)
    has_win = (rides["has_window"].fillna(False).astype(bool)
               if "has_window" in rides.columns
               else pd.Series(False, index=rides.index))
    score = []
    for i in rides.index:
        pri = float(priorities.get(i, 1.0))
        s = pri * (1.0 + pw * float(pop[i]))
        if strategy == "热门优先" and rides.ll_class.iloc[i] in ("single_pass", "multi_pass"):
            s *= 1.25
        # 无固定运营场所的项目（街头快闪、巡游）只作为填充项
        if not has_win.iloc[i]:
            s *= 0.4
        score.append(s)
    score = np.array(score, dtype=float)

    has_geo = (rides["has_geo"].fillna(False).astype(bool)
               if "has_geo" in rides.columns
               else pd.Series(True, index=rides.index))
    wanted = [i for i in rides.index
              if priorities.get(i, 1.0) > 0 and not bool(rides.down.iloc[i])]
    usable = [i for i in wanted if has_geo.iloc[i]]
    no_geo = len(wanted) - len(usable)
    must = [i for i in usable if priorities.get(i, 1.0) >= 3]
    rest = [i for i in usable if i not in must]

    def feasible_with(order):
        """带记忆化的行程推演，避免局部搜索重复计算。"""
        key = tuple(order)
        if key not in _memo:
            _memo[key] = _simulate(list(key), ctx)
        return _memo[key]

    _memo: dict = {}

    # ---------- 贪心构造：先塞必玩，再按“价值/边际耗时”插入 ----------
    def greedy(pool, order):
        remaining = list(pool)
        base_finish = feasible_with(order)["finish"]
        while remaining:
            best_i, best_density, best_res = None, -1.0, None
            for i in remaining:
                res = feasible_with(order + [i])
                if not res["feasible"]:
                    continue
                marginal = (res["finish"] - base_finish).total_seconds() / 60.0
                density = score[i] / max(marginal, 1.0)
                if density > best_density:
                    best_i, best_density, best_res = i, density, res
            if best_i is None:
                break
            order.append(best_i)
            remaining.remove(best_i)
            base_finish = best_res["finish"]
        return order

    order = greedy(must, [])
    order = greedy(rest, order)

    # ---------- 局部搜索：2-opt + 单点迁移，先提价值再压缩时间 ----------
    def total_score(seq):
        return float(sum(score[i] for i in seq))

    def better(a_seq, b_seq):
        """a 是否优于 b。"""
        ra, rb = feasible_with(a_seq), feasible_with(b_seq)
        if not ra["feasible"]:
            return False
        if not rb["feasible"]:
            return True
        s_a, s_b = total_score(a_seq), total_score(b_seq)
        if abs(s_a - s_b) > 1e-9:
            return s_a > s_b
        return (ra["finish"], len(a_seq)) < (rb["finish"], len(b_seq))

    improved = True
    rounds = 0
    budget = [40000]  # 局部搜索的推演次数上限，保证界面响应速度

    def can_try() -> bool:
        budget[0] -= 1
        return budget[0] > 0

    while improved and rounds < 6 and budget[0] > 0:
        improved, rounds = False, rounds + 1
        for i in range(len(order) - 1):
            for j in range(i + 1, len(order)):
                if not can_try():
                    break
                cand = order[:i] + order[i:j + 1][::-1] + order[j + 1:]
                if better(cand, order):
                    order, improved = cand, True
        for i in range(len(order)):
            for j in range(len(order)):
                if i == j or not can_try():
                    continue
                cand = order[:]
                item = cand.pop(i)
                cand.insert(j, item)
                if better(cand, order):
                    order, improved = cand, True

    # ---------- 尝试补入被漏掉的项目 ----------
    placed = set(order)
    for i in sorted(usable, key=lambda k: -score[k]):
        if i in placed:
            continue
        for pos in range(len(order) + 1):
            cand = order[:pos] + [i] + order[pos:]
            if better(cand, order):
                order, placed = cand, placed | {i}
                break

    res = feasible_with(order)
    rows = res["rows"]
    table = pd.DataFrame(rows) if rows else pd.DataFrame()
    if not table.empty:
        table["序号"] = [f"{n}" if k == "ride" else "—"
                        for n, k in zip(range(1, len(table) + 1), table.kind)]
        table["到达"] = table.arrive.dt.strftime("%H:%M")
        table["离开"] = table.end.dt.strftime("%H:%M")
        table["预计排队"] = table.wait.round(0).astype(int)
        table["步行"] = table.walk.round(0).astype(int)
        table["体验时长"] = table.duration.round(0).astype(int)
        if "show_wait" not in table:
            table["show_wait"] = 0.0

    summary = {
        "strategy": strategy,
        "n_attractions": int((table.kind == "ride").sum()) if not table.empty else 0,
        "total_wait": float(table.loc[table.kind == "ride", "wait"].sum()) if not table.empty else 0.0,
        "total_walk": float(table.walk.sum()) if not table.empty else 0.0,
        "start": start_dt, "end": res["finish"],
        "feasible": res["feasible"],
        "available": len(usable),
        "no_geo": no_geo,
        "score": total_score(order),
    }
    return table, summary


# ================================================================ Lightning Lane 建议
def ll_advice(snap: Snapshot, start_dt: datetime, end_dt: datetime, peak: float = 1.0,
              top_n: int = 6) -> pd.DataFrame:
    """按“预计能省下的排队时间”给出 Lightning Lane 预约优先级。"""
    rides = snap.rides
    mode = anchor_mode(snap, start_dt)
    crowd_mult = _park_crowd_today(snap.park, start_dt.date()) / max(crowd_reference(snap.park), 1e-6)
    day_hours = (end_dt - start_dt).total_seconds() / 3600.0
    mid = start_dt + (end_dt - start_dt) / 2
    rows = []
    for _, r in rides.iterrows():
        if r.ll_class in ("none", "exclude") or bool(r.get("down")):
            continue
        est = estimate_wait(snap, r, mid, peak, mode, crowd_mult, day_hours)
        if est < 15:
            continue
        rows.append({
            "项目": r["name"],
            "类别": r.get("category"),
            "类型": {"single_pass": "单项尊享 (Single Pass)",
                     "multi_pass": "多项尊享 (Multi Pass)",
                     "unknown": "未收录分类"}.get(r.ll_class, r.ll_class),
            "预计排队": int(round(est)),
            "预计可省": int(round(est * LL_CUT_RATIO)),
            "当前等待": int(r.wait_now) if pd.notna(r.wait_now) else None,
            "ll_class": r.ll_class,
            "_sort": est * (1.2 if r.ll_class == "single_pass" else 1.0),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.sort_values("_sort", ascending=False).head(top_n).drop(columns="_sort")
    return df.reset_index(drop=True)


# ================================================================ 自检
if __name__ == "__main__":  # pragma: no cover
    for name in PARK_NAMES:
        s = get_snapshot(name)
        print(f"\n=== {name} | 来源={s.source} | 项目数={len(s.rides)} | "
              f"开园={s.hours.get('open')} 闭园={s.hours.get('close')}")
        if s.rides.empty:
            continue
        s = filter_snapshot(s, fixed_only=s.is_live)
        top = s.rides.nlargest(5, "wait_now")[["name", "wait_now", "status", "hist_avg", "ll_class"]]
        print(top.to_string(index=False))
        op, cl = s.hours.get("open"), s.hours.get("close")
        start = max(s.when, op) if (op and s.when < cl) else (op + timedelta(days=1))
        end = start + (cl - op)
        pri = {i: 1.0 for i in s.rides.index}
        tbl, sm = plan_route(s, pri, start, end)
        print(f"路线：{sm['n_attractions']} 个项目, 排队合计 {sm['total_wait']:.0f} 分钟, "
              f"步行合计 {sm['total_walk']:.0f} 分钟, 结束 {sm['end']:%m-%d %H:%M}")
        if not tbl.empty:
            print(tbl[["序号", "到达", "离开", "name", "预计排队", "步行", "体验时长"]].head(12).to_string(index=False))
