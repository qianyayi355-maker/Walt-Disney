"""Home.py —— 应用的入口页（首页）。

仿照课件 Streamlit Application 的多页应用结构：
Home.py 作为主入口，展示标题、欢迎语和跳转按钮；真正的功能页在 pages/app.py。

运行方式：
    streamlit run Home.py
"""

from datetime import datetime

import pandas as pd
import streamlit as st

import data_layer as dl

st.set_page_config(
    page_title="Disney World 实时游玩助手",
    page_icon="🏰",
    layout="centered",
)

# ---------------------------------------------------------------- 页面样式
st.markdown(
    """
    <style>
        .main-title {
            text-align: center;
            color: #1f6feb;
            margin-bottom: 6px;
            font-size: 2.6rem;
        }
        .welcome-text {
            text-align: center;
            font-size: 18px;
            color: #555;
            margin-bottom: 26px;
        }
        .park-card {
            border-radius: 12px;
            padding: 14px 16px;
            background: #f6f8fa;
            border: 1px solid #e3e7eb;
            text-align: center;
        }
        .park-card .name { font-weight: 600; font-size: 15px; }
        .park-card .value { font-size: 26px; font-weight: 700; color: #1f6feb; }
        .park-card .unit { font-size: 12px; color: #666; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown("<h1 class='main-title'>🏰 Disney World 实时游玩助手</h1>", unsafe_allow_html=True)
st.markdown(
    "<p class='welcome-text'>四个园区的实时排队、客流规律与当天最优游玩路线，"
    "都在这一个应用里。</p>",
    unsafe_allow_html=True,
)

st.write(
    """
    这是一个多页 Streamlit 应用：首页介绍全局概览，点击下方按钮进入主应用，
    即可查看**实时排队看板**、**今日最佳路线**和**客流数据分析**。
    侧边栏可以随时切换园区与规划参数。
    """
)

# ---------------------------------------------------------------- 跳转按钮
col1, col2, col3 = st.columns([1, 1, 1])
with col2:
    if st.button("🚀 进入实时看板与今日路线", key="go_to_app", width="stretch", type="primary"):
        st.switch_page("pages/app.py")

# ---------------------------------------------------------------- 四园概览
st.divider()
st.subheader("📊 四园概览")

cal = dl.load_calendar()
rides = dl.load_ride_stats()

cols = st.columns(4)
for col, park in zip(cols, dl.PARK_NAMES):
    info = dl.PARKS[park]
    cal_park = cal[cal.park_name == park]
    this_year = cal_park[cal_park.year == cal_park.year.max()]
    rides_park = rides[(rides.park_name == park) & (rides.year == "all_time")
                       & (rides.metric == "ride_avg_wait")]
    with col:
        st.markdown(
            f"""<div class='park-card'>
                    <div class='name'>{info['icon']} {park}</div>
                    <div class='value'>{this_year.crowd_level_pct.mean():.0f}<span class='unit'>/100</span></div>
                    <div class='unit'>{cal_park.year.max()} 年客流水平</div>
                    <div class='unit'>平均排队 {rides_park.minutes.mean():.0f} 分钟 · {len(rides_park)} 个项目</div>
                </div>""",
            unsafe_allow_html=True,
        )

st.caption(f"统计口径：逐日客流指数与项目平均排队时长，数据覆盖 2015–{cal.year.max()} 年、"
           f"共 {cal.shape[0]:,} 条园区日记录。")

# ---------------------------------------------------------------- 功能介绍
st.divider()
st.subheader("✨ 主应用有哪些功能")
st.write(
    """
    - 🎢 **实时排队看板**：项目当前排队分钟数、开放状态，自动定时刷新
    - 🧭 **今日最佳路线**：结合实时排队、步行距离与项目开放时段，给出游玩顺序
    - 🎫 **Lightning Lane 建议**：按"能省下多少排队时间"排序，告诉你优先预约哪个
    - 📈 **客流与项目分析**：月度、星期、年度的客流规律与项目排队画像
    - 📖 **方法说明**：模型假设、数据来源与计算公式，全部公开可查
    """
)

st.divider()
st.caption(
    f"上次进入时间：{datetime.now():%Y-%m-%d %H:%M} · "
    "实时数据来自 ThemeParks.wiki 公开接口，历史数据随项目打包"
)
