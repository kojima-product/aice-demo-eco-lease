"""
利用状況・コスト管理

LLM API の利用状況とコストを表示・管理します。
"""

import streamlit as st
from pathlib import Path
import json
from datetime import datetime, timedelta
import sys

sys.path.insert(0, '.')

from pipelines.cost_tracker import CostTracker, get_tracker


# ページ設定
st.set_page_config(
    page_title="利用状況",
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
    /* サイドバー幅を拡大 */
    [data-testid="stSidebar"] {
        min-width: 300px;
        max-width: 360px;
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

    /* メトリクスカード */
    [data-testid="stMetricValue"] {
        font-size: 1.4rem;
        font-weight: 600;
    }
    [data-testid="stMetricLabel"] {
        font-size: 0.85rem;
    }

    /* タブスタイル */
    .stTabs [data-baseweb="tab"] {
        padding: 12px 24px;
        font-weight: 500;
        font-size: 0.95rem;
    }

    /* セクションヘッダー */
    .sidebar-section-header {
        font-size: 0.9rem;
        font-weight: 600;
        margin-bottom: 0.5rem;
        padding-bottom: 0.3rem;
        border-bottom: 1px solid rgba(128, 128, 128, 0.2);
    }
</style>
""", unsafe_allow_html=True)


def main():
    tracker = get_tracker()

    # ヘッダー
    st.title("利用状況")
    st.caption("LLM API の利用状況とコストを表示")

    # サイドバー
    with st.sidebar:
        st.markdown("### コスト概要")

        summary = tracker.get_summary()

        st.markdown('<p class="sidebar-section-header">累計利用</p>', unsafe_allow_html=True)

        col1, col2 = st.columns(2)
        with col1:
            st.metric(
                "API呼出",
                f"{summary['total_records']}回",
                help="API呼び出しの総回数"
            )
        with col2:
            st.metric(
                "累計コスト",
                f"¥{summary['total_cost_jpy']:.0f}",
                help="累計のAPI利用料金（日本円）"
            )

        st.markdown('<p class="sidebar-section-header">トークン使用量</p>', unsafe_allow_html=True)
        st.text(f"入力: {summary['total_input_tokens']:,}")
        st.text(f"出力: {summary['total_output_tokens']:,}")
        st.text(f"合計: {summary['total_tokens']:,}")

        st.markdown("---")

        # 料金表
        st.markdown('<p class="sidebar-section-header">API料金（参考）</p>', unsafe_allow_html=True)
        st.markdown("""
        | 項目 | 料金 |
        |------|------|
        | 入力 | $3/1Mトークン |
        | 出力 | $15/1Mトークン |
        | レート | ¥150/$1 |
        """)

        st.markdown("---")
        st.caption("見積生成デモ v2.0")

    # タブ
    tab1, tab2, tab3 = st.tabs(["サマリー", "履歴詳細", "設定"])

    # タブ1: サマリー
    with tab1:
        # 期間選択
        period = st.selectbox(
            "集計期間",
            ["全期間", "今日", "過去7日間", "過去30日間"],
            index=0
        )

        days_map = {
            "全期間": None,
            "今日": 1,
            "過去7日間": 7,
            "過去30日間": 30
        }
        summary = tracker.get_summary(days=days_map[period])

        # メイン指標
        st.markdown("### 利用状況サマリー")

        col1, col2, col3, col4 = st.columns(4)

        with col1:
            st.metric(
                "API呼出回数",
                f"{summary['total_records']}回",
                help="選択期間内のAPI呼び出し回数"
            )

        with col2:
            st.metric(
                "総トークン数",
                f"{summary['total_tokens']:,}",
                help="入力＋出力の合計トークン数"
            )

        with col3:
            st.metric(
                "コスト（USD）",
                f"${summary['total_cost_usd']:.4f}",
                help="米ドル換算のAPI利用料金"
            )

        with col4:
            st.metric(
                "コスト（JPY）",
                f"¥{summary['total_cost_jpy']:.2f}",
                help="日本円換算のAPI利用料金（1USD=150円）"
            )

        st.divider()

        # 操作別集計
        st.markdown("### 操作別コスト")

        if summary['by_operation']:
            # テーブル形式で表示
            op_data = []
            for op, stats in summary['by_operation'].items():
                op_data.append({
                    "操作": op,
                    "回数": stats['count'],
                    "トークン": f"{stats['tokens']:,}",
                    "コスト（USD）": f"${stats['cost_usd']:.4f}",
                    "コスト（JPY）": f"¥{stats['cost_jpy']:.2f}"
                })

            st.dataframe(op_data, use_container_width=True, hide_index=True)

            # 円グラフ風の表示
            st.markdown("**コスト内訳**")
            total_jpy = summary['total_cost_jpy']
            if total_jpy > 0:
                for op, stats in summary['by_operation'].items():
                    pct = (stats['cost_jpy'] / total_jpy) * 100
                    st.progress(pct / 100, text=f"{op}: ¥{stats['cost_jpy']:.2f} ({pct:.1f}%)")
        else:
            st.info("まだ利用履歴がありません")

        st.divider()

        # 日別集計
        st.markdown("### 日別利用状況")

        if summary['by_date']:
            date_data = []
            for date, stats in list(summary['by_date'].items())[:14]:  # 最新14日間
                date_data.append({
                    "日付": date,
                    "回数": stats['count'],
                    "トークン": f"{stats['tokens']:,}",
                    "コスト": f"¥{stats['cost_jpy']:.2f}"
                })

            st.dataframe(date_data, use_container_width=True, hide_index=True)
        else:
            st.info("まだ利用履歴がありません")

    # タブ2: 履歴詳細
    with tab2:
        st.markdown("### API呼び出し履歴")

        records = tracker.get_recent_records(limit=100)

        if records:
            # フィルタ
            col1, col2 = st.columns(2)
            with col1:
                operations = list(set(r['operation'] for r in records))
                filter_op = st.selectbox(
                    "操作でフィルタ",
                    ["すべて"] + operations
                )
            with col2:
                display_limit = st.number_input(
                    "表示件数",
                    min_value=10,
                    max_value=100,
                    value=50
                )

            # フィルタ適用
            filtered = records
            if filter_op != "すべて":
                filtered = [r for r in records if r['operation'] == filter_op]

            st.info(f"{len(filtered)}件の履歴（最新{display_limit}件を表示）")

            # 履歴表示
            for idx, record in enumerate(filtered[:display_limit], 1):
                timestamp = record['timestamp'][:19].replace('T', ' ')
                op = record['operation']
                tokens = record['total_tokens']
                cost_jpy = record['cost_jpy']

                with st.expander(
                    f"{idx}. [{timestamp}] {op} - {tokens:,}トークン / ¥{cost_jpy:.2f}"
                ):
                    col1, col2 = st.columns(2)

                    with col1:
                        st.markdown(f"**操作**: {record['operation']}")
                        st.markdown(f"**モデル**: {record['model']}")
                        st.markdown(f"**日時**: {timestamp}")

                    with col2:
                        st.markdown(f"**入力トークン**: {record['input_tokens']:,}")
                        st.markdown(f"**出力トークン**: {record['output_tokens']:,}")
                        st.markdown(f"**コスト**: ${record['cost_usd']:.4f} (¥{record['cost_jpy']:.2f})")

                    # メタデータ
                    if record.get('metadata'):
                        st.markdown("**詳細情報**:")
                        for key, value in record['metadata'].items():
                            st.text(f"  {key}: {value}")

            # エクスポート
            st.markdown("---")
            if st.button("履歴をJSONエクスポート", use_container_width=True):
                export_data = json.dumps(records, ensure_ascii=False, indent=2)
                st.download_button(
                    label="ダウンロード",
                    data=export_data,
                    file_name=f"api_costs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                    mime="application/json"
                )

        else:
            st.info("まだ利用履歴がありません。見積生成やKB抽出を実行すると、ここに履歴が表示されます。")

    # タブ3: 設定
    with tab3:
        st.markdown("### 設定")

        st.markdown("#### 為替レート")
        current_rate = CostTracker.USD_JPY_RATE
        st.info(f"現在のレート: 1 USD = ¥{current_rate}")
        st.caption("為替レートはコード内で固定されています。変更する場合は `pipelines/cost_tracker.py` を編集してください。")

        st.markdown("---")

        st.markdown("#### データ管理")

        col1, col2 = st.columns(2)

        with col1:
            st.markdown("**ログファイル**")
            log_path = tracker.log_path
            st.text(f"保存先: {log_path}")

            if log_path.exists():
                file_size = log_path.stat().st_size / 1024
                st.text(f"ファイルサイズ: {file_size:.1f} KB")
                st.text(f"レコード数: {len(tracker.records)}")

        with col2:
            st.markdown("**履歴クリア**")
            if st.button("履歴をクリア", type="secondary"):
                if st.checkbox("本当にクリアしますか？全ての履歴が削除されます。", key="confirm_clear"):
                    tracker.clear_records()
                    st.success("履歴をクリアしました")
                    st.rerun()

        st.markdown("---")

        st.markdown("#### 料金体系")
        st.markdown("""
        **Claude API 料金（2024年時点）**

        | モデル | 入力 | 出力 |
        |--------|------|------|
        | Claude Sonnet 4 | $3.00/1Mトークン | $15.00/1Mトークン |
        | Claude 3.5 Sonnet | $3.00/1Mトークン | $15.00/1Mトークン |
        | Claude 3 Opus | $15.00/1Mトークン | $75.00/1Mトークン |
        | Claude 3 Haiku | $0.25/1Mトークン | $1.25/1Mトークン |

        **参考: 1回あたりの目安コスト**

        | 操作 | トークン目安 | コスト目安 |
        |------|-------------|-----------|
        | 見積生成（AI自動） | 10,000-30,000 | ¥5-20 |
        | KB抽出（PDF） | 5,000-20,000 | ¥3-12 |
        | 法令抽出 | 3,000-10,000 | ¥2-6 |
        """)


if __name__ == "__main__":
    main()
