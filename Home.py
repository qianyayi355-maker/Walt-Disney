import streamlit as st

st.set_page_config(
    page_title="World Cities Project",
    page_icon="🌍",
    layout="wide"
)

st.title("Welcome to World Cities Data App")
st.markdown("""
### 项目介绍
这是一个基于 Streamlit 构建的多页面交互式城市数据分析应用。

* **数据来源**：World Cities Dataset
* **功能**：地图可视化分布、人口与国家筛选、数据明细查看

👈 **请在左侧侧边栏点击进入 `app` 页面查看完整功能！**
""")


