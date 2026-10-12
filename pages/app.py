import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import os

st.set_page_config(page_title="Disney Parks Explorer", page_icon="🎢", layout="wide")

# 自动处理文件路径（兼容本地与 pages 子目录运行）
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
    # 将年份统一转为字符串以防匹配失效
    rides_df["year"] = rides_df["year"].astype(str)
    calendar_df["year"] = calendar_df["year"].astype(str)
    return rides_df, calendar_df

try:
    rides_df, calendar_df = load_disney_data()
except Exception as e:
    st.error(f"读取数据失败，请检查 CSV 文件是否在仓库根目录下: {e}")
    st.stop()

# 侧边栏：筛选控制
st.sidebar.header("Filter Options")

# 1. 园区选择
all_parks = sorted(rides_df["park_name"].dropna().unique().tolist())
selected_park = st.sidebar.selectbox("Select Park:", all_parks)

# 2. 年份选择（排除 all_time，按数字年份倒序排）
park_specific_rides = rides_df[rides_df["park_name"] == selected_park]
available_years = sorted([y for y in park_specific_rides["year"].unique().tolist() if y != "all_time"], reverse=True)
selected_year = st.sidebar.selectbox("Select Year:", available_years, index=0)

# 主页面标题
st.title(f"🏰 {selected_park} Analytics ({selected_year})")

# ----------------- 模块 1：热门设施平均等待时间 -----------------
st.subheader("⏱️ Top Rides by Average Wait Time")

# 数据集中实际指标为 'ride_avg_wait'
park_rides = rides_df[
    (rides_df["park_name"] == selected_park) & 
    (rides_df["year"] == selected_year) &
    (rides_df["metric"] == "ride_avg_wait")
].sort_values(by="minutes", ascending=False)

if not park_rides.empty:
    top_10_rides = park_rides.head(10)

    # 绘制水平柱状图（使用英文标签防止服务器字体乱码）
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.barh(top_10_rides["ride_name"], top_10_rides["minutes"], color="#4A90E2")
    ax.set_xlabel("Average Wait Time (Minutes)")
    ax.set_ylabel("Attraction / Ride")
    ax.set_title(f"Top 10 Average Wait Times - {selected_park} ({selected_year})")
    ax.invert_yaxis()
    st.pyplot(fig)
else:
    st.info("No ride data available for the selected year and park.")

# ----------------- 模块 2：客流拥挤度与日历分析 -----------------
st.subheader("📅 Crowd Level Trend by Month")

park_calendar = calendar_df[
    (calendar_df["park_name"] == selected_park) & 
    (calendar_df["year"] == selected_year)
]

if not park_calendar.empty:
    month_crowd = park_calendar.groupby("month_name", as_index=False)["crowd_level_pct"].mean()
    month_order = [
        'January', 'February', 'March', 'April', 'May', 'June', 
        'July', 'August', 'September', 'October', 'November', 'December'
    ]
    month_crowd['month_name'] = pd.Categorical(month_crowd['month_name'], categories=month_order, ordered=True)
    month_crowd = month_crowd.sort_values('month_name')

    fig2, ax2 = plt.subplots(figsize=(10, 3.5))
    ax2.plot(month_crowd['month_name'], month_crowd['crowd_level_pct'], marker='o', color='#E94E77', linewidth=2)
    ax2.set_xlabel("Month")
    ax2.set_ylabel("Average Crowd Level (%)")
    ax2.set_title(f"Monthly Crowd Level Trend - {selected_park} ({selected_year})")
    plt.xticks(rotation=45)
    st.pyplot(fig2)
else:
    st.info("No calendar crowd data available for the selected year.")

# ----------------- 模块 3：数据明细表 -----------------
st.subheader("📋 Raw Data Details")
st.dataframe(park_rides[["ride_name", "minutes", "is_archived"]], use_container_width=True)
