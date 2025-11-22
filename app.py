"""
見積生成デモ - エントリーポイント

Streamlitマルチページアプリのエントリーポイント
サイドバーから「ホーム」を選択してください
"""

import streamlit as st

# ページ設定
st.set_page_config(
    page_title="見積生成デモ",
    page_icon="page_facing_up",
    layout="wide",
    initial_sidebar_state="expanded"
)

# サイドバーから「app」を非表示にするCSS
st.markdown("""
<style>
    [data-testid="stSidebarNav"] > ul > li:first-child {
        display: none;
    }
</style>
""", unsafe_allow_html=True)

# 自動リダイレクト（Streamlit 1.30以降）
try:
    st.switch_page("pages/0_ホーム.py")
except Exception:
    # フォールバック: 手動でナビゲーションを案内
    st.title("見積生成デモ")
    st.info("サイドバーから「ホーム」を選択してください。")

    st.markdown("""
    ## ページ一覧

    | ページ | 説明 |
    |--------|------|
    | **ホーム** | システム概要・利用手順 |
    | **見積書作成** | 仕様書から見積書を自動生成 |
    | **単価データベース** | 過去見積から単価を登録・管理 |
    """)
