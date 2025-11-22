"""
単価データベース管理

過去の見積書（Excel/PDF）をアップロードして、単価データベースを構築・管理します。
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
    page_title="単価データベース",
    page_icon="page_facing_up",
    layout="wide",
    initial_sidebar_state="expanded"
)

# カスタムCSS - サイドバー幅拡大・フォーマルデザイン
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
        st.info("KBにデータがありません。「アップロード」タブから見積書をアップロードしてKBを構築してください。")
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

    avg_price = sum(item.get('unit_price', 0) for item in kb_items) / len(kb_items)

    # メトリクス表示
    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric(
            "登録項目数",
            f"{len(kb_items):,}件",
            help="KBに登録されている単価データの総数"
        )

    with col2:
        st.metric(
            "工事区分数",
            f"{len(discipline_stats)}種類",
            help="登録されている工事区分の種類（電気・機械・ガス等）"
        )

    with col3:
        st.metric(
            "平均単価",
            f"¥{avg_price:,.0f}",
            help="全項目の平均単価"
        )

    # 工事区分別の詳細
    st.divider()
    st.markdown("**工事区分別統計**")

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


def extract_from_files(uploaded_files, project_name_prefix="uploaded", discipline_override=None):
    """アップロードされたファイルからKBを抽出"""
    from pipelines.estimate_from_reference import EstimateFromReference
    from pipelines.schemas import DisciplineType, PriceReference
    from datetime import date

    kb_builder = st.session_state.kb_builder
    all_extracted = []

    progress_bar = st.progress(0)
    status_text = st.empty()
    detail_text = st.empty()

    # 工事区分のマッピング
    discipline_map = {
        "電気設備工事": DisciplineType.ELECTRICAL,
        "機械設備工事": DisciplineType.MECHANICAL,
        "ガス設備工事": DisciplineType.GAS,
        "空調設備工事": DisciplineType.HVAC,
        "衛生設備工事": DisciplineType.PLUMBING,
    }

    for idx, uploaded_file in enumerate(uploaded_files):
        progress = idx / len(uploaded_files)
        progress_bar.progress(progress)

        file_type = "Excel" if uploaded_file.name.endswith(('.xlsx', '.xls')) else "PDF"
        status_text.markdown(f"### 処理中: {uploaded_file.name}")
        detail_text.text(f"進捗: {idx + 1}/{len(uploaded_files)} ({int(progress * 100)}%)")

        # 一時ファイルに保存
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(uploaded_file.name).suffix) as tmp_file:
            tmp_file.write(uploaded_file.getbuffer())
            tmp_path = tmp_file.name

        try:
            # プロジェクト名の生成
            project_name = f"{project_name_prefix}_{Path(uploaded_file.name).stem}"

            # ファイル形式に応じて処理
            if uploaded_file.name.endswith(('.xlsx', '.xls')):
                detail_text.text(f"Excelファイルを解析中... ({idx + 1}/{len(uploaded_files)})")
                price_refs = kb_builder.extract_estimate_from_excel(
                    tmp_path,
                    project_name=project_name
                )
            elif uploaded_file.name.endswith('.pdf'):
                detail_text.text(f"PDFを画像に変換中... ({idx + 1}/{len(uploaded_files)})")
                detail_text.text(f"OCR処理中（1-2分かかります）... ({idx + 1}/{len(uploaded_files)})")

                # 工事区分を指定してPDFから抽出
                if discipline_override and discipline_override != "自動判定":
                    discipline = discipline_map.get(discipline_override, DisciplineType.GAS)
                    extractor = EstimateFromReference()
                    estimate_items = extractor.extract_estimate_from_pdf(
                        pdf_path=tmp_path,
                        discipline=discipline
                    )

                    # EstimateItemをPriceReferenceに変換
                    price_refs = []
                    for i, item in enumerate(estimate_items):
                        if item.unit_price and item.unit_price > 0:
                            context_tags = ["学校"] if "学校" in project_name or "高校" in project_name else []
                            price_ref = PriceReference(
                                item_id=f"{project_name}_{i+1:03d}",
                                description=item.name,
                                discipline=discipline,
                                unit=item.unit or "式",
                                unit_price=float(item.unit_price),
                                vendor=None,
                                valid_from=date.today(),
                                valid_to=None,
                                source_project=project_name,
                                context_tags=context_tags,
                                features={
                                    "specification": item.specification or "",
                                    "quantity": item.quantity,
                                },
                                similarity_score=0.0
                            )
                            price_refs.append(price_ref)
                else:
                    # 自動判定モード（既存の処理）
                    price_refs = kb_builder.extract_estimate_from_pdf(tmp_path)
            else:
                st.warning(f"サポートされていないファイル形式: {uploaded_file.name}")
                continue

            if price_refs:
                all_extracted.extend(price_refs)
                st.success(f"{uploaded_file.name}: {len(price_refs)}項目抽出完了")
            else:
                st.error(f"{uploaded_file.name}: 抽出失敗（項目が見つかりませんでした）")

        except Exception as e:
            st.error(f"エラー ({uploaded_file.name}): {str(e)}")
            logger.error(f"Error processing {uploaded_file.name}: {e}")
            import traceback
            logger.error(traceback.format_exc())

        finally:
            # 一時ファイルを削除
            try:
                os.unlink(tmp_path)
            except:
                pass

    progress_bar.progress(1.0)
    status_text.markdown("### 全ファイルの処理完了")
    detail_text.text(f"合計: {len(all_extracted)}項目を抽出しました")

    return all_extracted


def main():
    init_session_state()

    # ヘッダー
    st.title("単価データベース")
    st.caption("過去の見積書から単価情報を抽出・管理")

    # サイドバー
    with st.sidebar:
        st.markdown("### データベース概要")

        # 現在のKB状況
        kb_items = st.session_state.kb_builder.kb_items

        st.markdown('<p class="sidebar-section-header">登録状況</p>', unsafe_allow_html=True)

        if kb_items:
            col1, col2 = st.columns(2)
            with col1:
                st.metric(
                    "総登録数",
                    f"{len(kb_items):,}",
                    help="データベースに登録されている単価項目の総数"
                )
            with col2:
                disciplines = set(item.get('discipline', '') for item in kb_items)
                st.metric(
                    "工事区分",
                    f"{len(disciplines)}種類",
                    help="登録されている工事区分の種類"
                )

            # 工事区分別内訳
            st.markdown('<p class="sidebar-section-header">工事区分別内訳</p>', unsafe_allow_html=True)
            discipline_counts = {}
            for item in kb_items:
                d = item.get('discipline', '不明')
                discipline_counts[d] = discipline_counts.get(d, 0) + 1

            for discipline, count in sorted(discipline_counts.items()):
                st.text(f"{discipline}: {count}件")

        else:
            st.warning("データベースは空です")
            st.caption("「見積書アップロード」タブから過去の見積書をアップロードしてください")

        st.markdown("---")

        # ヘルプ情報
        st.markdown('<p class="sidebar-section-header">対応ファイル形式</p>', unsafe_allow_html=True)
        st.markdown("""
        | 形式 | 拡張子 |
        |------|--------|
        | Excel | .xlsx, .xls |
        | PDF | .pdf（OCR対応） |
        """)

        st.markdown("---")

        st.caption("見積生成デモ v2.0")

    # タブで機能を分割
    tab1, tab2, tab3 = st.tabs(["見積書アップロード", "データ管理", "操作ガイド"])

    # ===== タブ1: アップロード =====
    with tab1:
        st.info("""
**単価データベースとは**

過去の見積書から抽出した「項目名・仕様・単価」のデータベースです。

1. 見積書（Excel/PDF）をアップロード
2. AIが自動で項目・単価を抽出
3. データベースに登録

登録したデータは、新規見積書作成時に単価の自動マッチングに使用されます。
        """)

        col1, col2 = st.columns([2, 1])

        with col1:
            st.markdown("**見積書ファイル**")

            uploaded_files = st.file_uploader(
                "見積書ファイルを選択（複数可）",
                type=['xlsx', 'xls', 'pdf'],
                accept_multiple_files=True,
                help="Excel形式またはPDF形式の見積書をアップロードしてください",
                label_visibility="collapsed"
            )

        with col2:
            st.markdown("**設定**")

            project_prefix = st.text_input(
                "プロジェクト名",
                value="project",
                help="KB項目のIDに使用されます"
            )

            discipline_options = [
                "自動判定",
                "電気設備工事",
                "機械設備工事",
                "ガス設備工事",
                "空調設備工事",
                "衛生設備工事"
            ]
            selected_discipline = st.selectbox(
                "工事区分",
                discipline_options,
                index=0,
                help="自動判定: 項目名・ファイル名から電気/機械/ガス等を自動分類します"
            )

            with st.expander("詳細設定", expanded=False):
                aggregation_options = {
                    "中央値（推奨）": "median",
                    "平均値": "average",
                    "新しい見積を重視": "time_weighted"
                }
                aggregation_label = st.selectbox(
                    "価格統合方法",
                    list(aggregation_options.keys()),
                    index=0,
                    help="同じ項目が複数の見積書にある場合、どの価格を採用するか"
                )
                aggregation_method = aggregation_options[aggregation_label]

                merge_options = {
                    "新データで上書き（推奨）": "keep_new",
                    "既存データを保持": "keep_old",
                    "価格を平均化": "average"
                }
                merge_label = st.selectbox(
                    "重複時の処理",
                    list(merge_options.keys()),
                    index=0,
                    help="データベースに同じ項目が既にある場合の処理方法"
                )
                merge_strategy = merge_options[merge_label]

        st.divider()

        # 処理ボタン
        if uploaded_files:
            st.success(f"選択済み: {len(uploaded_files)}ファイル")

            # ファイルリスト表示
            with st.expander("選択ファイル一覧", expanded=False):
                for file in uploaded_files:
                    file_size = len(file.getbuffer()) / 1024  # KB
                    file_type = "Excel" if file.name.endswith(('.xlsx', '.xls')) else "PDF"
                    st.text(f"[{file_type}] {file.name} ({file_size:.1f} KB)")

            col1, col2, col3 = st.columns(3)

            with col1:
                if st.button("KB抽出開始", type="primary", use_container_width=True):
                    st.markdown("---")
                    st.subheader("処理状況")

                    # 処理時間の見積もり
                    pdf_count = sum(1 for f in uploaded_files if f.name.endswith('.pdf'))
                    if pdf_count > 0:
                        st.warning(f"PDF {pdf_count}ファイルの処理には時間がかかります（1ファイルあたり約1-2分）")

                    st.info("処理を開始します。しばらくお待ちください...")

                    # ファイルから抽出（工事区分を指定）
                    extracted_items = extract_from_files(uploaded_files, project_prefix, selected_discipline)

                    if extracted_items:
                        st.session_state.extracted_items = extracted_items
                        st.success(f"合計 {len(extracted_items)}項目を抽出しました")

                        # 工事区分別の分布を表示
                        discipline_dist = {}
                        for item in extracted_items:
                            d = item.discipline.value if hasattr(item.discipline, 'value') else str(item.discipline)
                            discipline_dist[d] = discipline_dist.get(d, 0) + 1

                        st.markdown("**工事区分別の自動分類結果**")
                        cols = st.columns(len(discipline_dist) if len(discipline_dist) <= 4 else 4)
                        for idx, (discipline, count) in enumerate(sorted(discipline_dist.items())):
                            with cols[idx % len(cols)]:
                                st.metric(discipline, f"{count}件")

                        # サンプル表示（工事区分ごとにグループ化）
                        with st.expander("抽出結果（工事区分別）"):
                            for discipline in sorted(discipline_dist.keys()):
                                items_in_discipline = [
                                    item for item in extracted_items
                                    if (item.discipline.value if hasattr(item.discipline, 'value') else str(item.discipline)) == discipline
                                ]
                                st.markdown(f"**{discipline}** ({len(items_in_discipline)}件)")
                                for item in items_in_discipline[:5]:
                                    spec = item.features.get('specification', '')
                                    spec_str = f" {spec}" if spec else ""
                                    st.text(
                                        f"  - {item.description}{spec_str}: "
                                        f"¥{item.unit_price:,}/{item.unit}"
                                    )
                                if len(items_in_discipline) > 5:
                                    st.caption(f"  ... 他 {len(items_in_discipline) - 5}件")
                    else:
                        st.error("抽出に失敗しました")

            with col2:
                if st.button("価格統合", use_container_width=True,
                            disabled=not st.session_state.extracted_items):
                    st.markdown("---")
                    st.subheader("価格統合処理")

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
                        st.success(f"{len(aggregated)}項目に統合しました")

                        # 統合された項目の表示
                        multi_items = [item for item in aggregated
                                      if item.features.get('aggregated_from', 1) > 1]
                        if multi_items:
                            with st.expander(f"複数見積から統合された項目 ({len(multi_items)}件)"):
                                for item in multi_items[:10]:
                                    spec = item.features.get('specification', '')
                                    st.text(
                                        f"{item.description} {spec}: ¥{item.unit_price:,}/{item.unit} "
                                        f"({item.features.get('aggregated_from')}件統合)"
                                    )

            with col3:
                if st.button("KBに保存", use_container_width=True,
                            disabled=not st.session_state.extracted_items):
                    st.markdown("---")
                    st.subheader("KB保存処理")

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

                        st.success(f"KBを保存しました: {len(merged)}項目")
                        st.info(f"保存先: {kb_builder.kb_path}")

                        # 抽出アイテムをクリア
                        st.session_state.extracted_items = []

                        st.rerun()
        else:
            st.info("Excel (.xlsx, .xls) または PDF 形式のファイルをアップロードしてください")

    # ===== タブ2: KB管理 =====
    with tab2:
        # 統計情報表示
        display_kb_stats()

        st.divider()

        # KB詳細表示
        if st.session_state.kb_builder.kb_items:
            st.markdown("**KB詳細**")

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

            st.info(f"{len(filtered_items)}項目（全{len(st.session_state.kb_builder.kb_items)}項目中）")

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
                if st.button("JSON出力", use_container_width=True):
                    kb_json = json.dumps(
                        st.session_state.kb_builder.kb_items,
                        ensure_ascii=False,
                        indent=2
                    )
                    st.download_button(
                        label="KBをダウンロード",
                        data=kb_json,
                        file_name=f"price_kb_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                        mime="application/json"
                    )

            with col2:
                if st.button("KBをクリア", use_container_width=True, type="secondary"):
                    if st.checkbox("本当にクリアしますか？"):
                        st.session_state.kb_builder.kb_items = []
                        st.session_state.kb_builder.save_kb_to_json([], st.session_state.kb_builder.kb_path)
                        st.success("KBをクリアしました")
                        st.rerun()

    # ===== タブ3: 使い方 =====
    with tab3:
        st.markdown("""
        ## 見積書KB化システムとは

        過去の見積書（Excel/PDF）から価格情報を抽出し、ナレッジベース（KB）として保存・管理するシステムです。
        構築したKBは、新規見積作成時の単価マッチング（RAG）に使用されます。

        ## 基本的な使い方

        ### 1. 見積書のアップロード

        1. 「アップロード」タブを開く
        2. 見積書ファイル（Excel/PDF）をアップロード
           - 複数ファイルを同時に選択可能
           - Excel: .xlsx, .xls形式
           - PDF: OCR自動処理（処理時間：約1分/ファイル）
        3. プロジェクト名を入力（オプション）
        4. 「KB抽出開始」をクリック

        ### 2. 価格統合（複数見積がある場合）

        - **median**: 中央値（推奨）
        - **average**: 平均値
        - **time_weighted**: 新しい見積ほど重み付け

        ### 3. KBへの保存

        既存KBとのマージ方法を選択：
        - **keep_new**: 新しいデータを優先（推奨）
        - **keep_old**: 既存データを優先
        - **average**: 価格を平均化

        ## KB管理機能

        「KB管理」タブでは以下の操作が可能です：

        - 統計情報の確認（総項目数、工事区分別統計等）
        - 項目の検索・フィルタリング
        - 詳細情報の閲覧
        - JSONエクスポート
        - KBのクリア

        ## 技術仕様

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

        ## ヒント

        - PDF処理は時間がかかります：8ページのPDFで約1分30秒
        - 10-20案件のKBを構築すると、統計的に安定した価格が得られます
        - 新しい見積書を定期的に追加してKBを更新してください
        """)


if __name__ == "__main__":
    main()
