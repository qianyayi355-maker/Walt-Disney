import streamlit as st

st.set_page_config(
    page_title="Walt Disney Analytics",
    page_icon="🏰",
    layout="wide"
)

st.title("🏰 Walt Disney Theme Parks Analytics")

st.markdown("""
### 欢迎来到迪士尼乐园数据分析平台

本项目利用历史游园数据，分析迪士尼乐园的排队等待时间、园区拥挤度以及各游乐设施的表现。

#### 核心模块：
* **园区概览**：查看不同园区（Magic Kingdom, Epcot 等）的历年拥挤度趋势
* **设施排队分析**：探索热门游乐设施的等待时间（排队时长）分布
* **日历与时间趋势**：查看周末与平日、不同月份的游客客流差异

👈 **请在左侧侧边栏点击 `app` 页面开始交互式数据探索！**
""")
