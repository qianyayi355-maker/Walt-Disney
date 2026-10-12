import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import os

st.set_page_config(page_title="Disney Parks Explorer", page_icon="🎢", layout="wide")

# 自动处理文件路径（支持根目录或当前目录）
def get_file_path(filename):
    if os.path.exists(filename):
        return filename
    parent_path = os.path.join("..", filename)
    if os.path.exists(parent_path):
        return parent_path
    return filename

@st.cache_data
def load_disney_data():
    rides_df = pd.read_csv(get_file_path("ride_stats.csv"))
    calendar_df = pd.read_csv(get_file_path("calendar_enriched.csv"))
    return rides_df, calendar_df

try:
    rides_df, calendar_df = load_disney_data()
except Exception as e:
    st.error(f"读取数据失败，请检查 CSV 文件是否在仓库根目录下: {e}")
    st.stop()

# 侧边栏：筛选控制
st.sidebar.header("🔍 数据筛选 (Filters)")

# 园区选择
all_parks = sorted(rides_df["park_name"].dropna().unique().tolist())
selected_park = st.sidebar.selectbox("选择园区 (Park):", all_parks)

# 年份选择
available_years = sorted(rides_df[rides_df["park_name"] == selected_park]["year"].unique().tolist())
selected_year = st.sidebar.selectbox("选择年份 (Year):", available_years, index=len(available_years)-1)

# 主页面标题
st.title(f"🏰 {selected_park} - 数据分析 ({selected_year})")

# 1. 热门设施排队时间分析
st.subheader("⏱️ 热门游乐设施等待时间 (Top Rides Wait Times)")

# 过滤当前园区与年份的设施数据（通常看平均等待时间）
park_rides = rides_df[(rides_df["park_name"] == selected_park) & (rides_df["year"] == selected_year)]

if not park_rides.empty:
    # 筛选平均等待时间
    avg_rides = park_rides[park_rides["metric"] == "average"].sort_values(by="minutes", ascending=False)
    top_10_rides = avg_rides.head(10)

    # 绘图：柱状图
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.barh(top_10_rides["ride_name"], top_10_rides["minutes"], color="#4A90E2")
    ax.set_xlabel("等待时间 (分钟)")
    ax.set_ylabel("游乐设施")
    ax.set_title(f"{selected_year} 年 {selected_park} 热门设施平均排队时长 Top 10")
    ax.invert_yaxis()
    st.pyplot(fig)
else:
    st.info("当前筛选条件下无排队数据。")

# 2. 客流拥挤度与日历分析
st.subheader("📅 月份客流拥挤度指数 (Crowd Level by Month)")

park_calendar = calendar_df[(calendar_df["park_name"] == selected_park) & (calendar_df["year"] == selected_year)]

if not park_calendar.empty:
    month_crowd = park_calendar.groupby("month_name", as_index=False)["crowd_level_pct"].mean()
    # 按月份排序
    month_order = ['January', 'February', 'March', 'April', 'May', 'June', 
                   'July', 'August', 'September', 'October', 'November', 'December']
    month_crowd['month_name'] = pd.Categorical(month_crowd['month_name'], categories=month_order, ordered=True)
    month_crowd = month_crowd.sort_values('month_name')

    fig2, ax2 = plt.subplots(figsize=(10, 3.5))
    ax2.plot(month_crowd['month_name'], month_crowd['crowd_level_pct'], marker='o', color='#E94E77', linewidth=2)
    ax2.set_xlabel("月份")
    ax2.set_ylabel("平均拥挤度等级 (%)")
    ax2.set_title(f"{selected_year} 年各月份游客拥挤度趋势")
    plt.xticks(rotation=45)
    st.pyplot(fig2)

# 3. 数据明细表
st.subheader("📋 详细数据列表")
st.dataframe(park_rides[["ride_name", "metric", "minutes", "is_archived"]], use_container_width=True)
