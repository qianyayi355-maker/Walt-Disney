"""pages/app.py —— 主应用页：实时排队看板 / 今日最佳路线 / 客流分析 / 方法说明。

由 Home.py 通过 st.switch_page("pages/app.py") 进入，也可以直接
    streamlit run pages/app.py
运行。
"""

from __future__ import annotations

import time
from datetime import datetime, time as dtime, timedelta

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import data_layer as dl

st.set_page_config(
    page_title="实时看板与今日路线 · Disney World",
    page_icon="🎢",
    layout="wide",
)

ACCENT = "#1f6feb"
PLOT_TEMPLATE = "plotly_white"

st.markdown(
    """
    <style>
        .block-container { padding-top: 2.2rem; }
        .badge {
            display: inline-block; padding: 3px 10px; border-radius: 999px;
            font-size: 12px; font-weight: 600; margin-right: 6px;
        }
        .badge-live { background: #dafbe1; color: #1a7f37; }
        .badge-sim  { background: #fff8c5; color: #7d4e00; }
        .badge-info { background: #ddf4ff; color: #0969da; }
        .kpi {
            border: 1px solid #e3e7eb; border-radius: 12px; padding: 12px 14px;
            background: #fbfcfd;
        }
        .kpi .label { font-size: 12px; color: #666; }
        .kpi .value { font-size: 24px; font-weight: 700; color: #16191d; }
        .kpi .hint  { font-size: 11px; color: #888; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ================================================================ 侧边栏
with st.sidebar:
    st.markdown("### 🏰 园区")
    park = st.selectbox("选择园区", dl.PARK_NAMES,
                        format_func=lambda p: f"{dl.PARKS[p]['icon']} {p}",
                        label_visibility="collapsed")

    st.markdown("### 🔄 数据来源与刷新")
    source_mode = st.radio("数据来源", ["自动（优先实时）", "仅实时接口", "仅离线模拟"],
                           index=0,
                           help="离线模拟基于历史平均排队 + 当日客流水平生成，"
                                "用于没有外网时演示页面效果。")
    refresh_label = st.selectbox("自动刷新间隔", ["关闭", "30 秒", "60 秒", "5 分钟"], index=1)
    interval = {"关闭": None, "30 秒": 30, "60 秒": 60, "5 分钟": 300}[refresh_label]

    col_a, col_b = st.columns(2)
    with col_a:
        manual = st.button("🔄 立即刷新")
    with col_b:
        if st.button("🧹 清空趋势"):
            st.session_state["trend"] = {}
    if manual:
        st.session_state["bust"] = st.session_state.get("bust", 0) + 1

    st.divider()
    st.markdown("### 🧭 路线参数")
    strategy = st.select_slider("规划策略", options=["多刷项目", "平衡", "热门优先"], value="平衡",
                                help="热门优先＝把排队最长的招牌项目尽量排在前面；"
                                     "多刷项目＝在有限时间里尽可能多玩项目。")
    depart = st.radio("出发时间", ["尽快出发", "开园冲刺 (Rope Drop)", "自定义"], index=1)
    custom_start = None
    if depart == "自定义":
        d = st.date_input("日期", value=datetime.now(dl.ET).date())
        t = st.time_input("时间", value=dtime(9, 30))
        custom_start = datetime.combine(d, t, tzinfo=dl.ET)

    peak = st.slider("客流峰值系数", 0.6, 1.6, 1.0, 0.1,
                     help="调节“午间高峰比开园时拥挤多少”。1.0 为默认日内曲线。")
    walk_speed = st.slider("步行速度 (米/分钟)", 45, 95, 70, 5,
                           help="带小朋友或推车可以调到 50–60。")
    with_break = st.checkbox("安排午餐休息", value=True)
    break_start, break_minutes = None, 0
    if with_break:
        bt = st.time_input("午餐时间", value=dtime(12, 30))
        break_minutes = st.number_input("休息时长（分钟）", 20, 90, 45, 5)
        break_start = bt

    st.divider()
    st.markdown("### 🎫 项目范围")
    all_cats = ["刺激项目", "亲子项目", "演出", "观光", "见面会"]
    cats = st.multiselect("纳入的项目类型", all_cats, default=all_cats)
    fixed_only = st.checkbox("只包含固定场馆项目", value=True,
                             help="排除街头快闪、巡游等没有固定运营时段的项目。"
                                  "离线模拟模式没有场馆数据，该项自动失效。")
    recorded_only = st.checkbox("只保留有排队记录的项目", value=True,
                                help="排除城堡、喷泉广场、商店等无需排队的小型体验点。")
    with_event = st.checkbox("纳入特别活动专场项目", value=False,
                             help="例如万圣节派对的专属项目，需要另外购票。")

    st.divider()
    st.caption("数据来源：ThemeParks.wiki 公开实时接口 + 项目自带的历史客流数据集。"
               "本应用仅用于教学演示。")


# ================================================================ 数据获取
def make_stamp() -> str:
    """缓存键：随刷新周期和手动刷新次数变化。"""
    bucket = int(time.time() // max(interval or 300, 15))
    return f"{bucket}-{st.session_state.get('bust', 0)}"


def fetch(park_name: str, allow_live: bool = True, stamp: str | None = None) -> dl.Snapshot:
    return dl.get_snapshot(park_name, force_live=allow_live, ttl_bucket=stamp or make_stamp())


use_live = source_mode != "仅离线模拟"
force_only_live = source_mode == "仅实时接口"

snap = fetch(park, allow_live=use_live)

if force_only_live and not snap.is_live:
    st.error("已选择“仅实时接口”，但当前无法连接 ThemeParks.wiki。请检查网络后点击“立即刷新”。")

if snap.rides.empty:
    st.error(
        "当前拿不到任何项目数据。若为离线环境，请确认 `data/attraction_meta.json` 存在"
        "（可运行 `python3 tools/build_attraction_meta.py` 生成）。"
    )
    st.stop()

filtered = dl.filter_snapshot(
    snap,
    include_event_only=with_event,
    categories=cats or None,
    fixed_only=fixed_only and snap.is_live,
    recorded_only=recorded_only,
)
if filtered.rides.empty:
    st.warning("当前的筛选条件下没有可规划的项目，请放宽侧边栏的“项目范围”设置。")
    filtered = dl.filter_snapshot(snap, include_event_only=with_event, categories=cats or None)


# ================================================================ 图表工具
def fig_live_board(rides: pd.DataFrame, park_name: str, note: str = "") -> go.Figure:
    top = (rides.dropna(subset=["wait_now"]).nlargest(15, "wait_now")
           .sort_values("wait_now"))
    title = f"Current Standby Wait · {park_name}"
    if note:
        title += f"<br><sup>{note}</sup>"
    fig = px.bar(
        top, x="wait_now", y="name", orientation="h",
        color="wait_now", color_continuous_scale=["#2da44e", "#d29922", "#cf222e"],
        labels={"wait_now": "Standby wait (min)", "name": ""},
        title=title,
        hover_data={"category": True, "status": True, "wait_now": ":.0f"},
    )
    fig.update_layout(template=PLOT_TEMPLATE, height=max(360, 26 * len(top) + 90),
                      coloraxis_showscale=False, margin=dict(l=10, r=20, t=76, b=10),
                      title_font_size=16)
    return fig


def fig_trend(records: list[dict]) -> go.Figure | None:
    if len(records) < 2:
        return None
    d = pd.DataFrame(records)
    d["t"] = pd.to_datetime(d.t).dt.tz_localize(None)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=d.t, y=d.avg, mode="lines+markers", name="Average wait",
                             line=dict(color=ACCENT, width=2)))
    fig.add_trace(go.Scatter(x=d.t, y=d.top, mode="lines", name="Longest wait",
                             line=dict(color="#cf222e", width=1.5, dash="dot")))
    fig.update_layout(template=PLOT_TEMPLATE, height=280,
                      title="Wait Trend (this session)",
                      xaxis_title="Time", yaxis_title="Wait (min)",
                      margin=dict(l=10, r=20, t=56, b=10), legend=dict(orientation="h", y=-0.25),
                      title_font_size=16)
    return fig


def fig_gantt(route: pd.DataFrame) -> go.Figure:
    g = route[route.kind == "ride"].copy()
    # 去掉时区信息，让横轴直接显示园区当地时间
    g["arrive"] = g.arrive.dt.tz_localize(None)
    g["end"] = g.end.dt.tz_localize(None)
    fig = px.timeline(
        g, x_start="arrive", x_end="end", y="name", color="category",
        hover_data={"预计排队": True, "步行": True, "体验时长": True},
        title="Route Timeline (planned)",
    )
    fig.update_yaxes(autorange="reversed", title="")
    fig.update_xaxes(title="Time of day")
    fig.update_layout(template=PLOT_TEMPLATE,
                      height=max(320, 28 * len(g) + 120),
                      margin=dict(l=10, r=20, t=56, b=10), title_font_size=16,
                      legend=dict(orientation="h", y=-0.12))
    return fig


def fig_crowd_month(cal: pd.DataFrame, park_name: str) -> go.Figure:
    d = (cal[cal.park_name == park_name].groupby("month_name", as_index=False)
         .crowd_level_pct.mean())
    order = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    d["month_name"] = pd.Categorical(d.month_name, order, ordered=True)
    d = d.sort_values("month_name")
    fig = px.bar(d, x="month_name", y="crowd_level_pct",
                 labels={"month_name": "", "crowd_level_pct": "Crowd level (0-100)"},
                 title="Average Crowd Level by Month", color="crowd_level_pct",
                 color_continuous_scale=["#54aeff", "#bf8700", "#cf222e"])
    fig.update_layout(template=PLOT_TEMPLATE, height=320, coloraxis_showscale=False,
                      margin=dict(l=10, r=20, t=56, b=10), title_font_size=16)
    return fig


def fig_crowd_dow(cal: pd.DataFrame, park_name: str) -> go.Figure:
    order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    d = (cal[cal.park_name == park_name].groupby("day_of_week", as_index=False)
         .crowd_level_pct.mean())
    d["day_of_week"] = pd.Categorical(d.day_of_week, order, ordered=True)
    d = d.sort_values("day_of_week")
    fig = px.bar(d, x="day_of_week", y="crowd_level_pct",
                 labels={"day_of_week": "", "crowd_level_pct": "Crowd level (0-100)"},
                 title="Average Crowd Level by Weekday", color="crowd_level_pct",
                 color_continuous_scale=["#54aeff", "#bf8700", "#cf222e"])
    fig.update_layout(template=PLOT_TEMPLATE, height=320, coloraxis_showscale=False,
                      margin=dict(l=10, r=20, t=56, b=10), title_font_size=16)
    return fig


def fig_year_trend(chained: pd.DataFrame, park_name: str) -> go.Figure:
    d = chained[chained.park_name == park_name].sort_values("year")
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=d.year, y=d.chained_index, mode="lines+markers",
                             name="Chained crowd index", line=dict(color=ACCENT, width=2)))
    fig.add_trace(go.Scatter(x=d.year, y=d.mean_wait_all_tracked, mode="lines+markers",
                             name="Mean tracked wait (min)", yaxis="y2",
                             line=dict(color="#cf222e", width=2)))
    fig.update_layout(
        template=PLOT_TEMPLATE, height=360, title="Crowd Index vs Wait Time by Year",
        xaxis=dict(title="Year", automargin=True),
        yaxis=dict(title="Chained crowd index", automargin=True),
        yaxis2=dict(title="Mean wait (min)", overlaying="y", side="right", automargin=True),
        legend=dict(orientation="h", y=-0.25), margin=dict(l=70, r=70, t=56, b=50),
        title_font_size=16,
    )
    return fig


def fig_ride_scatter(rs: pd.DataFrame, park_name: str) -> go.Figure:
    d = rs[(rs.park_name == park_name) & (rs.year == "all_time")]
    w = (d.pivot_table(index="ride_name", columns="metric", values="minutes", aggfunc="max")
         .reset_index().rename(columns={"ride_avg_wait": "avg", "ride_max_wait": "max"}))
    # 只给排队最长的项目加文字标签，避免低值区域标签重叠
    top = w.nlargest(min(12, len(w)), "avg")
    w["label"] = np.where(w.ride_name.isin(top.ride_name), w.ride_name, "")
    fig = px.scatter(w, x="avg", y="max", text="label", hover_name="ride_name",
                     labels={"avg": "Avg wait (min)", "max": "Longest wait (min)"},
                     title="Historical Average vs Longest Wait",
                     color="max", color_continuous_scale=["#54aeff", "#bf8700", "#cf222e"])
    fig.update_traces(textposition="top center", textfont_size=9, marker=dict(size=10))
    fig.update_layout(template=PLOT_TEMPLATE, height=560, coloraxis_showscale=False,
                      margin=dict(l=70, r=30, t=56, b=50), title_font_size=16,
                      xaxis=dict(automargin=True), yaxis=dict(automargin=True))
    return fig


def kpi(col, label: str, value: str, hint: str = "") -> None:
    with col:
        st.markdown(
            f"<div class='kpi'><div class='label'>{label}</div>"
            f"<div class='value'>{value}</div><div class='hint'>{hint}</div></div>",
            unsafe_allow_html=True,
        )


# ================================================================ 页面标题
park_info = dl.PARKS[park]
st.title(f"{park_info['icon']} {park} · 实时游玩助手")
st.caption("实时排队来自 ThemeParks.wiki 公开接口；历史规律来自项目自带数据集。"
           "侧边栏可以调整园区、刷新频率与规划参数。")

tab_live, tab_route, tab_analysis, tab_about = st.tabs(
    ["🎢 实时排队看板", "🧭 今日最佳路线", "📊 客流与项目分析", "📖 方法说明"]
)


# ================================================================ Tab 1 实时看板
@st.fragment(run_every=interval)
def live_board() -> None:
    s = fetch(park, allow_live=use_live)

    badge = ("<span class='badge badge-live'>● 实时接口</span>" if s.is_live
             else "<span class='badge badge-sim'>● 离线模拟</span>")
    st.markdown(
        badge + f"<span class='badge badge-info'>更新于 {s.when:%H:%M:%S}</span>"
        + (f"<span class='badge badge-info'>{s.message}</span>" if s.message and not s.is_live else ""),
        unsafe_allow_html=True,
    )

    rides = s.rides
    open_cnt = int(rides.open_now.sum())
    open_waits = rides.loc[rides.open_now, "wait_now"].dropna()
    # 园区闭园时没有实时排队，退回历史均值做参考，并明确标注
    closed_now = open_cnt == 0
    waits = open_waits if len(open_waits) else rides.wait_now.dropna()
    if closed_now and len(waits):
        st.warning("园区此刻不在运营时段，下方数值为**历史参考排队**，不是实时数据。"
                   "可切换到“今日最佳路线”查看次日安排。", icon="🌙")
    hours = s.hours
    hours_txt = (f"{hours['open']:%H:%M} – {hours['close']:%H:%M}"
                 if hours.get("open") and hours.get("close") else "—")

    c1, c2, c3, c4, c5 = st.columns(5)
    kpi(c1, "开放项目", f"{open_cnt}", f"共收录 {len(rides)} 个")
    kpi(c2, "平均排队", f"{waits.mean():.0f} 分" if len(waits) else "—",
        ("参考值" if closed_now else f"中位数 {waits.median():.0f} 分") if len(waits) else "")
    kpi(c3, "最长排队", f"{waits.max():.0f} 分" if len(waits) else "—",
        rides.loc[waits.idxmax(), "name"][:18] if len(waits) else "")
    kpi(c4, "今日客流水平", f"{dl._park_crowd_today(park, s.when.date()):.0f}/100",
        f"历史均值 {dl.crowd_reference(park):.0f}")
    kpi(c5, "运营时段", hours_txt,
        ("含早享 " + f"{hours['early_entry'][0]:%H:%M}" if hours.get("early_entry") else ""))

    st.plotly_chart(fig_live_board(
        rides, park,
        "Park is closed — showing historical reference waits" if closed_now else ""))

    # 本次会话的等待趋势
    key = f"{park}"
    trend = st.session_state.setdefault("trend", {})
    hist = trend.setdefault(key, [])
    if len(waits):
        now_pt = {"t": s.when, "avg": float(waits.mean()), "top": float(waits.max())}
        if not hist or (now_pt["t"] - hist[-1]["t"]).total_seconds() >= 20:
            hist.append(now_pt)
            del hist[:-120]
    fig = fig_trend(hist)
    if fig is not None:
        st.plotly_chart(fig)
    else:
        st.info("等待趋势会在本页停留期间自动累积，约 1 分钟后出现曲线。")

    with st.expander(f"📋 全部项目明细（{len(rides)} 个）", expanded=False):
        show = rides[["name", "category", "status", "wait_now", "hist_avg", "hist_max",
                      "ll_class", "has_window"]].copy()
        show.columns = ["项目", "类型", "状态", "当前排队", "历史均值", "历史峰值",
                        "LL 分类", "固定场馆"]
        show = show.sort_values("当前排队", ascending=False, na_position="last")
        st.dataframe(
            show, hide_index=True, height=460,
            column_config={
                "当前排队": st.column_config.ProgressColumn(
                    "当前排队", min_value=0, max_value=180, format="%d 分"),
                "历史均值": st.column_config.NumberColumn("历史均值", format="%.0f 分"),
                "历史峰值": st.column_config.NumberColumn("历史峰值", format="%.0f 分"),
            },
        )
        st.download_button("⬇️ 下载当前排队数据 (CSV)",
                           show.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"live_waits_{park}_{s.when:%Y%m%d_%H%M}.csv",
                           mime="text/csv")


with tab_live:
    live_board()


# ================================================================ Tab 2 今日路线
def plan_window(s: dl.Snapshot, choice: str, custom: datetime | None) -> tuple[datetime, datetime, str]:
    """根据出发方式计算规划时间窗，返回 (开始, 结束, 日期说明)。"""
    op, cl = s.hours.get("open"), s.hours.get("close")
    if op is None or cl is None:
        base = s.when
        return base, base + timedelta(hours=10), "按 10 小时游玩窗口估算"
    if choice == "自定义" and custom is not None:
        start = custom
        note = "自定义时间"
    elif choice == "开园冲刺 (Rope Drop)":
        start = op if s.when < op else op + timedelta(days=1)
        note = "开园时刻出发"
    else:  # 尽快出发
        start = s.when if s.when < cl else op + timedelta(days=1)
        note = "现在出发" if s.when < cl else "园区已闭园，改为次日开园"
    shift = (start.date() - op.date()).days
    return start, cl + timedelta(days=shift), note


with tab_route:
    start_dt, end_dt, note = plan_window(snap, depart, custom_start)
    day_desc = start_dt.strftime("%Y-%m-%d")

    st.markdown(f"**规划对象：{park} · {day_desc} · {start_dt:%H:%M} – {end_dt:%H:%M}**"
                f"　<span class='badge badge-info'>{note}</span>", unsafe_allow_html=True)

    if start_dt.date() != snap.when.date():
        st.info(
            "园区当前不在运营时段，已按**次日**的运营时段做计划。"
            "排队估计以历史平均值为基准，再按目标日的客流水平与日内曲线折算"
            "（实时看板仍然显示此刻的真实数据）。"
        )

    # ---- 项目优先级 ----
    st.markdown("#### ① 设置项目优先级")
    st.caption("把想玩的标成“必玩”，不想玩的标成“跳过”，其余保持“普通”。规划器会在时间预算内优先安排。")
    base_rides = filtered.rides.reset_index(drop=True)
    editor_df = pd.DataFrame({
        "项目": base_rides.name,
        "类型": base_rides.category,
        "当前排队": base_rides.wait_now.round(0),
        "历史均值": base_rides.hist_avg.round(0),
        "LL 分类": base_rides.ll_class,
        "优先级": "普通",
    })
    edited = st.data_editor(
        editor_df, hide_index=True, height=360,
        column_config={
            "优先级": st.column_config.SelectboxColumn(
                "优先级", options=["必玩", "普通", "跳过"], default="普通", required=True),
            "当前排队": st.column_config.NumberColumn("当前排队", format="%.0f 分"),
            "历史均值": st.column_config.NumberColumn("历史均值", format="%.0f 分"),
        },
        key=f"prio_{park}",
        disabled=["项目", "类型", "当前排队", "历史均值", "LL 分类"],
    )
    pri_map = {"必玩": 3.0, "普通": 1.0, "跳过": 0.0}
    priorities = {i: pri_map.get(v, 1.0) for i, v in zip(edited.index, edited["优先级"])}

    colx, coly = st.columns([1, 3])
    with colx:
        run_plan = st.button("🧭 生成今日最佳路线", type="primary", width="stretch")
    with coly:
        st.caption(f"候选项目 {int((pd.Series(list(priorities.values())) > 0).sum())} 个"
                   f"（其中必玩 {int((pd.Series(list(priorities.values())) >= 3).sum())} 个）· "
                   f"策略：{strategy} · 峰值系数 {peak:.1f}")

    if run_plan:
        with st.spinner("正在按实时排队、步行距离与开放时段求解路线…"):
            route, summary = dl.plan_route(
                filtered, priorities, start_dt, end_dt,
                strategy=strategy, peak=peak, walk_speed=float(walk_speed),
                break_start=(datetime.combine(start_dt.date(), break_start, tzinfo=dl.ET)
                             if (with_break and break_start) else None),
                break_minutes=int(break_minutes),
                gate=dl.PARKS[park]["gate"],
            )
        st.session_state["route"] = route
        st.session_state["route_summary"] = summary
        st.session_state["route_park"] = park
        st.session_state["route_day"] = day_desc

    route = st.session_state.get("route")
    summary = st.session_state.get("route_summary")
    if route is None or summary is None or st.session_state.get("route_park") != park:
        st.info("点击上方按钮生成路线。规划器会综合当前排队、项目之间真实步行距离、"
                "每个项目的开放时段和你的优先级，给出全天顺序。")
    elif route.empty:
        st.warning("在给定时间窗口内没有排入任何项目，请放宽筛选条件或调整时间。")
    else:
        st.markdown("#### ② 推荐路线")
        total_span = summary["end"] - start_dt
        span_min = int(total_span.total_seconds() // 60)
        r1, r2, r3, r4 = st.columns(4)
        kpi(r1, "安排项目", f"{summary['n_attractions']} 个", f"候选 {summary['available']} 个")
        kpi(r2, "预计排队合计", f"{summary['total_wait']:.0f} 分")
        kpi(r3, "步行合计", f"{summary['total_walk']:.0f} 分")
        kpi(r4, "预计结束", f"{summary['end']:%H:%M}",
            f"全程 {span_min // 60} 小时 {span_min % 60} 分")

        if summary["n_attractions"] < summary["available"]:
            st.caption(f"受时间预算限制，{summary['available'] - summary['n_attractions']} 个"
                       "候选项目没有排入；把它们标为“必玩”或延长游玩时间可以改变结果。")

        st.plotly_chart(fig_gantt(route))

        disp = route[route.kind == "ride"][
            ["序号", "到达", "离开", "name", "category", "预计排队", "步行", "体验时长"]].copy()
        disp.columns = ["序号", "到达", "离开", "项目", "类型", "预计排队", "步行", "体验时长"]
        st.dataframe(
            disp, hide_index=True,
            column_config={
                "预计排队": st.column_config.NumberColumn("预计排队", format="%d 分"),
                "步行": st.column_config.NumberColumn("步行", format="%d 分"),
                "体验时长": st.column_config.NumberColumn("体验时长", format="%d 分"),
            },
        )
        dl_col1, dl_col2 = st.columns([1, 3])
        with dl_col1:
            st.download_button("⬇️ 下载路线 (CSV)",
                               disp.to_csv(index=False).encode("utf-8-sig"),
                               file_name=f"route_{park}_{day_desc}.csv", mime="text/csv")
        with dl_col2:
            st.caption("“步行”是按项目经纬度计算的直线距离 ×1.35 绕行系数后，"
                       f"以 {walk_speed} 米/分钟折算的时间。")

        st.markdown("#### ③ Lightning Lane 预约建议")
        if snap.rides.ll_class.eq("unknown").all():
            st.info("当前园区没有逐项目的 Lightning Lane 分类数据，下表按排队时长排序给出参考。")
        ll = dl.ll_advice(filtered, start_dt, end_dt, peak=peak, top_n=8)
        if ll.empty:
            st.info("暂无可推荐的 Lightning Lane 项目。")
        else:
            st.dataframe(
                ll.drop(columns=["ll_class"]), hide_index=True,
                column_config={
                    "预计排队": st.column_config.NumberColumn("预计排队", format="%d 分"),
                    "预计可省": st.column_config.NumberColumn("预计可省", format="%d 分"),
                    "当前等待": st.column_config.NumberColumn("当前等待", format="%d 分"),
                },
            )
            st.caption("“预计可省”＝该项目预计排队 × 65% 的缩减比例（行业经验值）。"
                       "单项尊享 (Single Pass) 已在排序中加权。")


# ================================================================ Tab 3 客流分析
with tab_analysis:
    cal = dl.load_calendar()
    chained = dl.load_chained()
    ride_stats = dl.load_ride_stats()
    park_stats = dl.load_park_stats()
    ll_split = dl.load_ll_split()

    st.markdown(f"**{park} 的客流规律**（基于 2015 年以来的逐日记录，"
                f"共 {len(cal[cal.park_name == park]):,} 天）")

    a1, a2 = st.columns(2)
    with a1:
        st.plotly_chart(fig_crowd_month(cal, park))
    with a2:
        st.plotly_chart(fig_crowd_dow(cal, park))

    st.plotly_chart(fig_year_trend(chained, park))

    cal_park = cal[cal.park_name == park]
    st.markdown("#### 什么时候最挤")
    worst_month = (cal_park.groupby("month_name").crowd_level_pct.mean().idxmax())
    best_month = (cal_park.groupby("month_name").crowd_level_pct.mean().idxmin())
    worst_dow = (cal_park.groupby("day_of_week").crowd_level_pct.mean().idxmax())
    best_dow = (cal_park.groupby("day_of_week").crowd_level_pct.mean().idxmin())
    ev = (park_stats[(park_stats.park_name == park) & (park_stats.dimension == "crowd_by_event")]
          .groupby("key").crowd_level_pct.mean().sort_values(ascending=False))

    lines = [
        f"- 全年最忙的月份是 **{worst_month}**，最清闲的是 **{best_month}**",
        f"- 一周里 **{worst_dow}** 最挤，**{best_dow}** 相对最松",
    ]
    if len(ev):
        lines.append("- 特殊活动日的平均客流：" +
                     "；".join(f"{k} {v:.0f}" for k, v in ev.items()))
    st.markdown("\n".join(lines))

    st.markdown("#### 项目排队画像")
    st.plotly_chart(fig_ride_scatter(ride_stats, park))

    if park == "Magic Kingdom" and not ll_split.empty:
        st.markdown("#### Lightning Lane 对排队的分流效果（魔法王国）")
        d = ll_split.sort_values("year")
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=d.year, y=d.index_ll_eligible, mode="lines+markers",
                                 name="LL-eligible attractions", line=dict(color=ACCENT, width=2)))
        fig.add_trace(go.Scatter(x=d.year, y=d.index_no_ll, mode="lines+markers",
                                 name="Non-LL attractions", line=dict(color="#bf8700", width=2)))
        fig.add_trace(go.Bar(x=d.year, y=d.gap, name="Gap", marker_color="#d0d7de", yaxis="y2"))
        fig.update_layout(
            template=PLOT_TEMPLATE, height=360,
            title="Crowd Index: LL-eligible vs Non-LL Attractions",
            xaxis=dict(title="Year"), yaxis=dict(title="Crowd index"),
            yaxis2=dict(title="Gap", overlaying="y", side="right", showgrid=False),
            legend=dict(orientation="h", y=-0.22), margin=dict(l=10, r=20, t=56, b=10),
            title_font_size=16,
        )
        st.plotly_chart(fig)
        st.caption("LL-eligible 项目的客流指数与未纳入 LL 的项目相比，缺口(gap)为正表示"
                   "LL 项目承担了更多客流。")
    elif not ll_split.empty:
        st.caption("Lightning Lane 分流指数只有魔法王国的数据，其他园区暂缺。")


# ================================================================ Tab 4 方法说明
with tab_about:
    st.markdown(
        """
        ### 数据来源

        | 数据 | 来源 | 用途 |
        | --- | --- | --- |
        | 项目当前排队、开放状态、当天运营时段 | ThemeParks.wiki 公开接口 `/v1/entity/{id}/live` | 实时看板、路线排期 |
        | 项目经纬度 | ThemeParks.wiki `/v1/entity/{id}`（缓存在 `data/attraction_meta.json`） | 估算步行时间 |
        | 逐日客流、项目平均/最大排队、连锁客流指数、LL 分类 | 随项目打包的历史数据集 (`data/*.csv`) | 客流分析、排队外推、LL 建议 |

        ### 排队时间是怎么估算的

        1. **当天剩余时段**：以刚取到的实时排队为基准，按日内曲线折算——
           $$w(t) = w_{\\text{now}} \\times \\frac{f(t)}{f(t_{\\text{now}})}$$
        2. **次日或闭园之后**：以历史平均排队为基准，再乘以目标日客流倍率与日内曲线——
           $$w(t) = \\overline{w}_{\\text{hist}} \\times \\frac{\\text{crowd}_{\\text{target}}}{\\overline{\\text{crowd}}} \\times \\frac{f(t)}{\\overline{f}}$$
        3. 日内曲线 $$f(t)$$ 以“开园后小时数”为横轴，开园最低(0.62)、
           午后最高(1.20)、闭园前回落(0.45)，属于行业通行的经验规律，可用侧边栏的
           **客流峰值系数**调节。

        ### 路线是怎么求解的

        把“在闭园前尽可能玩到高价值项目”建模为**带时间窗的定向越野问题**：
        每个项目有开放时段、当前/预计排队、体验时长，项目之间有真实步行距离。
        在时间预算内最大化
        $$\\sum_{i \\in \\text{route}} \\text{priority}_i \\times (1 + \\lambda \\cdot \\text{popularity}_i)$$

        求解用**贪心构造 + 局部搜索**：先按“价值 / 边际耗时”逐个插入（必玩项目优先），
        再用 2-opt 与单点迁移反复调整顺序，最后尝试补入被漏掉的项目。
        这是启发式解，不保证全局最优。

        ### 已知限制

        - Lightning Lane 分项目分类只有魔法王国的数据，其他园区按“未收录分类”处理。
        - 步行距离是直线距离乘 1.35 绕行系数，没有考虑园区实际道路与排队队伍内部动线。
        - 演出按 live 接口返回的场次安排；没有场次数据时按固定体验时长处理。
        - 离线模拟模式的排队由历史均值 + 当天客流 + 日内曲线 + 确定性噪声生成，
          仅用于演示页面效果，不代表真实情况。

        ### 运行方式

        ```bash
        pip install -r requirements.txt
        streamlit run Home.py
        ```
        """
    )
    st.caption("ClawsGO Science Agent · 教学演示项目")
