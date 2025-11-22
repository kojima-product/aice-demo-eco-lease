"""
Streamlit App - 見積書KB化システム

過去の見積書（Excel/PDF）をアップロードして、価格KBを構築・管理します。
"""

import streamlit as st
from pathlib import Path
import tempfile
import json
from datetime import datetime
from loguru import logger
import sys
import os

sys.path.insert(0, '.')

from pipelines.kb_builder import PriceKBBuilder
from pipelines.schemas import PriceReference


# ページ設定
st.set_page_config(
    page_title="見積書KB化システム",
    page_icon="📦",
    layout="wide"
)


def init_session_state():
    """セッション状態を初期化"""
    if 'kb_builder' not in st.session_state:
        st.session_state.kb_builder = PriceKBBuilder(kb_path="kb/price_kb.json")
    if 'extracted_items' not in st.session_state:
        st.session_state.extracted_items = []
    if 'kb_stats' not in st.session_state:
        st.session_state.kb_stats = None


def display_kb_stats():
    """現在のKB統計情報を表示"""
    kb_items = st.session_state.kb_builder.kb_items

    if not kb_items:
        st.info("📊 現在のKBは空です")
        return

    # 工事区分別の統計
    discipline_stats = {}
    for item in kb_items:
        discipline = item.get('discipline', '不明')
        if discipline not in discipline_stats:
            discipline_stats[discipline] = {
                'count': 0,
                'total_price': 0,
                'min_price': float('inf'),
                'max_price': 0
            }

        discipline_stats[discipline]['count'] += 1
        unit_price = item.get('unit_price', 0)
        discipline_stats[discipline]['total_price'] += unit_price
        discipline_stats[discipline]['min_price'] = min(
            discipline_stats[discipline]['min_price'],
            unit_price
        )
        discipline_stats[discipline]['max_price'] = max(
            discipline_stats[discipline]['max_price'],
            unit_price
        )

    # メトリクス表示
    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric("総項目数", f"{len(kb_items)}項目")

    with col2:
        st.metric("工事区分数", f"{len(discipline_stats)}区分")

    with col3:
        avg_price = sum(item.get('unit_price', 0) for item in kb_items) / len(kb_items)
        st.metric("平均単価", f"¥{avg_price:,.0f}")

    # 工事区分別の詳細
    st.subheader("📊 工事区分別統計")

    for discipline, stats in sorted(discipline_stats.items()):
        with st.expander(f"{discipline} ({stats['count']}項目)"):
            col1, col2, col3 = st.columns(3)

            with col1:
                st.metric("項目数", f"{stats['count']}項目")

            with col2:
                avg = stats['total_price'] / stats['count']
                st.metric("平均単価", f"¥{avg:,.0f}")

            with col3:
                st.metric(
                    "価格レンジ",
                    f"¥{stats['min_price']:,.0f}",
                    f"〜 ¥{stats['max_price']:,.0f}"
                )


def extract_from_files(uploaded_files, project_name_prefix="uploaded"):
    """アップロードされたファイルからKBを抽出"""
    kb_builder = st.session_state.kb_builder
    all_extracted = []

    progress_bar = st.progress(0)
    status_text = st.empty()

    for idx, uploaded_file in enumerate(uploaded_files):
        progress = (idx + 1) / len(uploaded_files)
        progress_bar.progress(progress)
        status_text.text(f"処理中: {uploaded_file.name} ({idx + 1}/{len(uploaded_files)})")

        # 一時ファイルに保存
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(uploaded_file.name).suffix) as tmp_file:
            tmp_file.write(uploaded_file.getbuffer())
            tmp_path = tmp_file.name

        try:
            # プロジェクト名の生成
            project_name = f"{project_name_prefix}_{Path(uploaded_file.name).stem}"

            # ファイル形式に応じて処理
            if uploaded_file.name.endswith(('.xlsx', '.xls')):
                with st.spinner(f"📊 Excelから抽出中: {uploaded_file.name}"):
                    price_refs = kb_builder.extract_estimate_from_excel(
                        tmp_path,
                        project_name=project_name
                    )
            elif uploaded_file.name.endswith('.pdf'):
                with st.spinner(f"📄 PDFから抽出中（OCR処理）: {uploaded_file.name}"):
                    price_refs = kb_builder.extract_estimate_from_pdf(tmp_path)
            else:
                st.warning(f"⚠️ サポートされていないファイル形式: {uploaded_file.name}")
                continue

            if price_refs:
                all_extracted.extend(price_refs)
                st.success(f"✅ {uploaded_file.name}: {len(price_refs)}項目抽出")
            else:
                st.error(f"❌ {uploaded_file.name}: 抽出失敗")

        except Exception as e:
            st.error(f"❌ エラー ({uploaded_file.name}): {str(e)}")
            logger.error(f"Error processing {uploaded_file.name}: {e}")

        finally:
            # 一時ファイルを削除
            os.unlink(tmp_path)

    progress_bar.progress(1.0)
    status_text.text("✅ 処理完了")

    return all_extracted


def main():
    init_session_state()

    # ヘッダー
    st.title("📦 見積書KB化システム")
    st.caption("過去の見積書から価格ナレッジベース（KB）を構築")
    st.markdown("---")

    # タブで機能を分割
    tab1, tab2, tab3 = st.tabs(["📤 アップロード", "📊 KB管理", "📖 使い方"])

    # ===== タブ1: アップロード =====
    with tab1:
        st.header("📤 見積書のアップロード")

        col1, col2 = st.columns([2, 1])

        with col1:
            # ファイルアップロード
            uploaded_files = st.file_uploader(
                "見積書ファイルを選択（複数可）",
                type=['xlsx', 'xls', 'pdf'],
                accept_multiple_files=True,
                help="Excel形式またはPDF形式の見積書をアップロードしてください"
            )

        with col2:
            # プロジェクト名のプレフィックス
            project_prefix = st.text_input(
                "プロジェクト名（オプション）",
                value="project",
                help="KB項目のIDに使用されます"
            )

            # 統合方法
            aggregation_method = st.selectbox(
                "価格統合方法",
                ["median", "average", "time_weighted"],
                index=0,
                help="複数見積の価格をどう統合するか"
            )

            # マージ戦略
            merge_strategy = st.selectbox(
                "既存KBとのマージ",
                ["keep_new", "keep_old", "average"],
                index=0,
                help="既存項目と重複した場合の処理"
            )

        st.markdown("---")

        # 処理ボタン
        if uploaded_files:
            st.info(f"📁 {len(uploaded_files)}ファイル選択済み")

            # ファイルリスト表示
            with st.expander("📋 選択ファイル一覧"):
                for file in uploaded_files:
                    file_size = len(file.getbuffer()) / 1024  # KB
                    st.text(f"• {file.name} ({file_size:.1f} KB)")

            col1, col2, col3 = st.columns(3)

            with col1:
                if st.button("🚀 KB抽出開始", type="primary", use_container_width=True):
                    st.markdown("---")
                    st.subheader("📊 処理状況")

                    # ファイルから抽出
                    extracted_items = extract_from_files(uploaded_files, project_prefix)

                    if extracted_items:
                        st.session_state.extracted_items = extracted_items
                        st.success(f"🎉 合計 {len(extracted_items)}項目を抽出しました")

                        # サンプル表示
                        with st.expander("📋 抽出サンプル（最初の10項目）"):
                            for idx, item in enumerate(extracted_items[:10], 1):
                                spec = item.features.get('specification', '')
                                spec_str = f" {spec}" if spec else ""
                                st.text(
                                    f"{idx}. {item.description}{spec_str}: "
                                    f"¥{item.unit_price:,}/{item.unit}"
                                )
                    else:
                        st.error("❌ 抽出に失敗しました")

            with col2:
                if st.button("📊 価格統合", use_container_width=True,
                            disabled=not st.session_state.extracted_items):
                    st.markdown("---")
                    st.subheader("📊 価格統合処理")

                    # 一時ファイルに保存してaggregate機能を使う
                    with st.spinner("統合中..."):
                        # 簡易実装: 抽出済みアイテムを直接グループ化
                        from collections import defaultdict
                        import statistics

                        grouped = defaultdict(list)
                        for item in st.session_state.extracted_items:
                            key = (
                                item.description,
                                item.features.get('specification', ''),
                                item.unit
                            )
                            grouped[key].append(item)

                        aggregated = []
                        for key, items in grouped.items():
                            if len(items) == 1:
                                aggregated.append(items[0])
                            else:
                                prices = [item.unit_price for item in items]

                                if aggregation_method == "median":
                                    agg_price = statistics.median(prices)
                                elif aggregation_method == "average":
                                    agg_price = statistics.mean(prices)
                                else:  # time_weighted
                                    weights = list(range(1, len(items) + 1))
                                    agg_price = sum(p * w for p, w in zip(prices, weights)) / sum(weights)

                                # 最初のアイテムをベースに価格を更新
                                agg_item = items[0].model_copy(update={"unit_price": agg_price})
                                agg_item.features['aggregated_from'] = len(items)
                                agg_item.features['price_range'] = f"¥{min(prices):,.0f} - ¥{max(prices):,.0f}"
                                aggregated.append(agg_item)

                        st.session_state.extracted_items = aggregated
                        st.success(f"✅ {len(aggregated)}項目に統合しました")

                        # 統合された項目の表示
                        multi_items = [item for item in aggregated
                                      if item.features.get('aggregated_from', 1) > 1]
                        if multi_items:
                            with st.expander(f"📊 複数見積から統合された項目 ({len(multi_items)}件)"):
                                for item in multi_items[:10]:
                                    spec = item.features.get('specification', '')
                                    st.text(
                                        f"{item.description} {spec}: ¥{item.unit_price:,}/{item.unit} "
                                        f"({item.features.get('aggregated_from')}件統合)"
                                    )

            with col3:
                if st.button("💾 KBに保存", use_container_width=True,
                            disabled=not st.session_state.extracted_items):
                    st.markdown("---")
                    st.subheader("💾 KB保存処理")

                    with st.spinner("マージ中..."):
                        kb_builder = st.session_state.kb_builder

                        # 既存KBとマージ
                        merged = kb_builder.merge_with_existing_kb(
                            st.session_state.extracted_items,
                            merge_strategy=merge_strategy
                        )

                        # 保存
                        kb_builder.save_kb_to_json(merged, kb_builder.kb_path)

                        # セッション状態を更新
                        kb_builder.kb_items = [ref.model_dump(mode='json') for ref in merged]

                        st.success(f"✅ KBを保存しました: {len(merged)}項目")
                        st.info(f"📁 保存先: {kb_builder.kb_path}")

                        # 抽出アイテムをクリア
                        st.session_state.extracted_items = []

                        st.rerun()
        else:
            st.info("👆 見積書ファイルをアップロードしてください")

    # ===== タブ2: KB管理 =====
    with tab2:
        st.header("📊 KB管理")

        # 統計情報表示
        display_kb_stats()

        st.markdown("---")

        # KB詳細表示
        if st.session_state.kb_builder.kb_items:
            st.subheader("📋 KB詳細")

            # フィルタリング
            col1, col2, col3 = st.columns(3)

            with col1:
                # 工事区分でフィルタ
                disciplines = list(set(item.get('discipline', '不明')
                                      for item in st.session_state.kb_builder.kb_items))
                selected_discipline = st.selectbox(
                    "工事区分フィルタ",
                    ["すべて"] + sorted(disciplines)
                )

            with col2:
                # 検索
                search_query = st.text_input("項目名で検索", "")

            with col3:
                # 表示件数
                display_limit = st.number_input("表示件数", min_value=10, max_value=500, value=50)

            # フィルタリング適用
            filtered_items = st.session_state.kb_builder.kb_items

            if selected_discipline != "すべて":
                filtered_items = [item for item in filtered_items
                                 if item.get('discipline') == selected_discipline]

            if search_query:
                filtered_items = [item for item in filtered_items
                                 if search_query.lower() in item.get('description', '').lower()]

            st.info(f"📊 {len(filtered_items)}項目（全{len(st.session_state.kb_builder.kb_items)}項目中）")

            # テーブル表示
            if filtered_items:
                for idx, item in enumerate(filtered_items[:display_limit], 1):
                    with st.expander(
                        f"{idx}. {item.get('description', '')} - "
                        f"¥{item.get('unit_price', 0):,}/{item.get('unit', '')}"
                    ):
                        col1, col2 = st.columns(2)

                        with col1:
                            st.text(f"ID: {item.get('item_id', '')}")
                            st.text(f"工事区分: {item.get('discipline', '')}")
                            st.text(f"単位: {item.get('unit', '')}")
                            st.text(f"単価: ¥{item.get('unit_price', 0):,}")

                        with col2:
                            features = item.get('features', {})
                            if features:
                                st.text("仕様・特徴:")
                                for key, value in features.items():
                                    st.text(f"  {key}: {value}")

                            tags = item.get('context_tags', [])
                            if tags:
                                st.text(f"タグ: {', '.join(tags)}")

            # エクスポート
            st.markdown("---")
            col1, col2 = st.columns(2)

            with col1:
                if st.button("📥 JSON出力", use_container_width=True):
                    kb_json = json.dumps(
                        st.session_state.kb_builder.kb_items,
                        ensure_ascii=False,
                        indent=2
                    )
                    st.download_button(
                        label="💾 KBをダウンロード",
                        data=kb_json,
                        file_name=f"price_kb_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                        mime="application/json"
                    )

            with col2:
                if st.button("🗑️ KBをクリア", use_container_width=True, type="secondary"):
                    if st.checkbox("本当にクリアしますか？"):
                        st.session_state.kb_builder.kb_items = []
                        st.session_state.kb_builder.save_kb_to_json([], st.session_state.kb_builder.kb_path)
                        st.success("✅ KBをクリアしました")
                        st.rerun()

    # ===== タブ3: 使い方 =====
    with tab3:
        st.header("📖 使い方")

        st.markdown("""
        ## 📦 見積書KB化システムとは

        過去の見積書（Excel/PDF）から価格情報を抽出し、ナレッジベース（KB）として保存・管理するシステムです。
        構築したKBは、新規見積作成時の単価マッチング（RAG）に使用されます。

        ## 🚀 基本的な使い方

        ### 1. 見積書のアップロード

        1. **📤 アップロード**タブを開く
        2. 見積書ファイル（Excel/PDF）をアップロード
           - 複数ファイルを同時に選択可能
           - Excel: .xlsx, .xls形式
           - PDF: OCR自動処理（処理時間：約1分/ファイル）
        3. プロジェクト名を入力（オプション）
        4. **🚀 KB抽出開始**をクリック

        ### 2. 価格統合（複数見積がある場合）

        - **median**: 中央値（推奨）
        - **average**: 平均値
        - **time_weighted**: 新しい見積ほど重み付け

        ### 3. KBへの保存

        既存KBとのマージ方法を選択：
        - **keep_new**: 新しいデータを優先（推奨）
        - **keep_old**: 既存データを優先
        - **average**: 価格を平均化

        ## 📊 KB管理

        **📊 KB管理**タブでは以下の操作が可能です：

        - 📈 統計情報の確認（総項目数、工事区分別統計等）
        - 🔍 項目の検索・フィルタリング
        - 📋 詳細情報の閲覧
        - 📥 JSONエクスポート
        - 🗑️ KBのクリア

        ## ⚙️ 技術仕様

        ### 対応ファイル形式

        - **Excel**: ヘッダー行を自動検出、列マッピング
        - **PDF**: テキスト抽出 → 失敗時はOCR（Claude Vision API）

        ### 抽出項目

        - 項目名（description）
        - 仕様（specification）
        - 数量（quantity）
        - 単位（unit）
        - 単価（unit_price）
        - 工事区分（discipline）- 自動推定

        ### 保存先

        ```
        kb/price_kb.json
        ```

        ## 💡 ヒント

        - 📄 **PDF処理は時間がかかります**：8ページのPDFで約1分30秒
        - 📊 **10-20案件のKB推奨**：統計的に安定した価格が得られます
        - 🔄 **定期的な更新**：新しい見積書を追加してKBを更新してください
        - 🎯 **工事区分の精度**：キーワードベースの自動推定のため、手動修正も検討してください

        ## 🔗 関連機能

        - [AI見積書生成システム](http://localhost:8501) - 構築したKBを使って見積を自動生成
        """)


if __name__ == "__main__":
    main()
