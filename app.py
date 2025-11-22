"""
見積生成デモ - エントリーポイント

Streamlit st.navigation APIを使用したマルチページアプリ
"""

import streamlit as st

# ページ設定（navigationより前に設定）
st.set_page_config(
    page_title="見積生成デモ",
    page_icon="page_facing_up",
    layout="wide",
    initial_sidebar_state="expanded"
)

# カスタムCSS
st.markdown("""
<style>
    /* サイドバー幅を拡大 */
    [data-testid="stSidebar"] {
        min-width: 320px;
        max-width: 380px;
    }
    [data-testid="stSidebar"] > div:first-child {
        padding-top: 1.5rem;
        padding-left: 1.5rem;
        padding-right: 1.5rem;
    }
    /* メインコンテナ */
    .main .block-container {
        padding-top: 1rem;
        padding-bottom: 2rem;
        max-width: 1400px;
    }
</style>
""", unsafe_allow_html=True)

# ページ定義（st.navigation API）
pages = [
    st.Page("pages/1.py", title="見積書作成", default=True),
    st.Page("pages/2.py", title="単価データベース"),
    st.Page("pages/3.py", title="法令データベース"),
    st.Page("pages/4.py", title="利用状況"),
]

# ナビゲーション実行
pg = st.navigation(pages)
pg.run()
