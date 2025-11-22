"""
見積生成デモ - メインページ

マルチページアプリのトップページ
"""

import streamlit as st
import json

# ページ設定
st.set_page_config(
    page_title="見積生成デモ",
    page_icon="page_facing_up",
    layout="wide",
    initial_sidebar_state="expanded"
)

# カスタムCSS
st.markdown("""
<style>
    /* サイドバーから「app」を非表示 */
    [data-testid="stSidebarNav"] > ul > li:first-child {
        display: none;
    }
    [data-testid="stSidebar"] {
        min-width: 300px;
        max-width: 360px;
    }
    [data-testid="stSidebar"] > div:first-child {
        padding-top: 1.5rem;
        padding-left: 1.5rem;
        padding-right: 1.5rem;
    }
    .main .block-container {
        max-width: 1200px;
    }
    .sidebar-section-header {
        font-size: 0.9rem;
        font-weight: 600;
        margin-bottom: 0.5rem;
        padding-bottom: 0.3rem;
        border-bottom: 1px solid rgba(128, 128, 128, 0.2);
    }
</style>
""", unsafe_allow_html=True)

# サイドバー
with st.sidebar:
    st.markdown("### システム状況")

    # 単価DB状況
    st.markdown('<p class="sidebar-section-header">単価データベース</p>', unsafe_allow_html=True)
    try:
        with open('kb/price_kb.json', 'r') as f:
            kb_data = json.load(f)
        kb_count = len(kb_data)
        disciplines = set(item.get('discipline', '') for item in kb_data)

        col1, col2 = st.columns(2)
        with col1:
            st.metric("登録件数", f"{kb_count:,}")
        with col2:
            st.metric("工事区分", f"{len(disciplines)}種類")

        if kb_count >= 30:
            st.success("データベース構築済み")
        else:
            st.warning("データ追加を推奨")
    except:
        st.error("未構築")
        st.caption("「単価データベース」ページで構築してください")

    st.markdown("---")

    # ナビゲーション
    st.markdown('<p class="sidebar-section-header">ページ一覧</p>', unsafe_allow_html=True)
    st.markdown("""
    | ページ | 機能 |
    |--------|------|
    | 見積書作成 | 仕様書から見積書を生成 |
    | 単価データベース | 過去見積から単価を登録 |
    """)

    st.markdown("---")
    st.caption("見積生成デモ v2.0")
    st.caption("Powered by Claude Sonnet 4.5")

# メインページ
st.title("見積生成デモ")
st.caption("入札仕様書から見積書を自動生成するシステム")

# システム概要
st.info("""
**このシステムでできること**

入札仕様書（PDF）をアップロードするだけで、AIが見積項目を自動抽出し、
過去の見積実績データベースから適切な単価を自動付与します。
""")

st.divider()

# 機能説明
col1, col2 = st.columns(2)

with col1:
    st.subheader("見積書生成")
    st.markdown("""
    仕様書PDFから見積書を自動作成する機能です。

    | 機能 | 説明 |
    |------|------|
    | AI自動生成 | 仕様書から項目を自動抽出し、KBから単価をマッチング |
    | 参照見積ベース | 既存の見積書をテンプレートとして使用 |

    **対応工事区分**: 電気設備 / 機械設備 / ガス設備
    """)

with col2:
    st.subheader("単価データベース")
    st.markdown("""
    過去の見積実績から構築した**単価情報のデータベース**です。

    | 機能 | 説明 |
    |------|------|
    | 見積書アップロード | Excel/PDFから価格データを自動抽出 |
    | 価格統合 | 複数見積の中央値・平均値を算出 |
    | データ保存 | 抽出したデータをデータベースに登録 |

    **対応形式**: Excel (.xlsx, .xls) / PDF（OCR自動処理）
    """)

st.divider()

# 用語説明
with st.expander("用語説明", expanded=False):
    st.markdown("""
    | 用語 | 説明 |
    |------|------|
    | **単価データベース** | 過去の見積書から抽出した単価情報のデータベース。新規見積作成時の単価参照に使用 |
    | **マッチング** | データベースから類似項目を検索し、単価を自動付与する処理 |
    | **マッチング率** | 生成した見積項目のうち、データベースから単価を付与できた項目の割合 |
    | **信頼度** | 抽出した情報の確からしさ（仕様書に明記=高、推定=低） |
    | **OCR** | 画像から文字を読み取る技術。PDF見積書の自動読み取りに使用 |
    """)

# クイックスタート
st.subheader("利用手順")

st.markdown("""
| ステップ | 操作 | 説明 |
|----------|------|------|
| 1 | データベース構築（初回のみ） | サイドバー「単価データベース」から過去見積書をアップロード |
| 2 | 見積書作成 | サイドバー「見積書作成」から仕様書PDFをアップロード |
| 3 | ダウンロード | 生成された見積書（PDF/JSON）をダウンロード |
""")

st.warning("""
**注意**: 初回利用時は、まず単価データベースに過去見積書を登録してください。
データベースが空の場合、単価の自動付与ができません。
""")

st.divider()

# 精度指標
st.subheader("システム性能")
st.caption("現在のシステムの精度指標")

col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric(
        "項目抽出率",
        "95%",
        help="仕様書に記載された必要項目を正しく抽出できる割合"
    )
with col2:
    st.metric(
        "単価マッチング",
        "86.3%",
        help="抽出した項目に対して、データベースから適切な単価を付与できた割合"
    )
with col3:
    st.metric(
        "質疑自動抽出",
        "対応",
        help="仕様が不明確な項目を自動検出し、質疑書ドラフトを生成"
    )
with col4:
    st.metric(
        "根拠記録",
        "全項目",
        help="単価の出典（データベースID、参照見積書など）を全項目で記録"
    )

# フッター
st.divider()
st.caption("v2.0 | Powered by Claude Sonnet 4.5")
