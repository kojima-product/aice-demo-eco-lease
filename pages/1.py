"""
見積生成デモ - AI見積書自動生成システム

人間が作成していた見積書をAIでどこまで再現できるかのデモ
"""

import streamlit as st
from pathlib import Path
import tempfile
import json
from datetime import datetime
from loguru import logger
import sys
import zipfile
from io import BytesIO

sys.path.insert(0, '.')

from pipelines.schemas import DisciplineType
from pipelines.estimate_generator_with_legal import EstimateGeneratorWithLegal
from pipelines.estimate_validator import EstimateValidator
from pipelines.estimate_from_reference import EstimateFromReference
from pipelines.estimate_generator_ai import AIEstimateGenerator
from pipelines.export import EstimateExporter


# カスタムCSS（ページ固有）
st.markdown("""
<style>
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
    /* ボタンスタイル */
    .stButton > button[kind="primary"] {
        font-weight: 600;
        padding: 0.6rem 1.2rem;
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
    if 'fmt_doc' not in st.session_state:
        st.session_state.fmt_doc = None
    if 'validation_results' not in st.session_state:
        st.session_state.validation_results = None
    if 'processing_time' not in st.session_state:
        st.session_state.processing_time = None
    if 'legal_refs' not in st.session_state:
        st.session_state.legal_refs = []
    if 'generated_files' not in st.session_state:
        st.session_state.generated_files = []


def main():
    init_session_state()

    # ヘッダー
    st.title("見積書作成")
    st.caption("入札仕様書から見積書を自動生成するシステム")

    # サイドバー
    with st.sidebar:
        # タイトル
        st.markdown("### 生成設定")

        # 生成モード選択
        st.markdown('<p class="sidebar-section-header">生成方式</p>', unsafe_allow_html=True)
        generation_mode = st.radio(
            "生成方式を選択",
            ["仕様書から自動生成", "過去見積をテンプレート使用"],
            index=0,
            help="「仕様書から自動生成」: AIが仕様書を解析して項目を生成\n「過去見積をテンプレート使用」: 類似案件の見積書を参照"
        )

        use_reference = (generation_mode == "過去見積をテンプレート使用")
        use_ai_generation = (generation_mode == "仕様書から自動生成")

        st.markdown("---")

        # オプション設定
        st.markdown('<p class="sidebar-section-header">出力オプション</p>', unsafe_allow_html=True)

        include_legal = st.checkbox(
            "関係法令を含める",
            value=True,
            help="建築基準法、電気設備技術基準、ガス事業法等の関係法令情報を見積書に含めます"
        )

        enable_validation = st.checkbox(
            "精度検証レポートを出力",
            value=True,
            help="生成した見積書を実際の見積書と比較し、精度を検証したレポートを出力します"
        )

        # 法令参照が有効な場合の設定
        if include_legal:
            with st.expander("法令の詳細設定", expanded=False):
                legal_standards = st.multiselect(
                    "参照する法令・基準",
                    ["建築基準法", "電気設備技術基準", "ガス事業法", "消防法", "JEAC8001"],
                    default=["建築基準法", "電気設備技術基準"],
                    help="見積書に含める法令・基準を選択してください"
                )
                st.session_state.legal_standards = legal_standards

        # 法令チェック（RAGモード時のみ）
        enable_legal = False
        if not use_reference and not use_ai_generation:
            enable_legal = st.checkbox(
                "法令遵守チェックを実行",
                value=False,
                help="関係法令に基づく要件チェックを実行します"
            )

        st.markdown("---")

        # 対象工事
        st.markdown('<p class="sidebar-section-header">対象工事区分</p>', unsafe_allow_html=True)
        st.markdown("""
        | 区分 | 状態 |
        |------|------|
        | 電気設備工事 | 対応 |
        | 機械設備工事 | 対応 |
        | ガス設備工事 | 対応 |
        """)

        st.markdown("---")

        # 単価データベース状況
        st.markdown('<p class="sidebar-section-header">単価データベース</p>', unsafe_allow_html=True)
        try:
            import json
            with open('kb/price_kb.json', 'r') as f:
                kb_data = json.load(f)
            kb_count = len(kb_data)

            col1, col2 = st.columns(2)
            with col1:
                st.metric(
                    "登録件数",
                    f"{kb_count:,}",
                    help="単価データベースに登録されている項目数"
                )
            with col2:
                # 工事区分数をカウント
                disciplines = set(item.get('discipline', '') for item in kb_data)
                st.metric(
                    "工事区分",
                    f"{len(disciplines)}種類",
                    help="登録されている工事区分の種類"
                )

            if kb_count < 30:
                st.warning("単価データが少ないため、マッチング精度が低下する可能性があります")

        except:
            st.error("単価データベースが未構築です")
            st.caption("「単価データベース」ページで過去見積書をアップロードしてください")

        st.markdown("---")

        # システム情報
        st.caption("見積生成デモ v2.0")
        st.caption("Powered by Claude Sonnet 4.5")

    # メインコンテンツ
    tab1, tab2, tab3, tab4 = st.tabs(["アップロード", "精度レポート", "見積詳細", "ダウンロード"])

    with tab1:
        # 仕様書アップロード
        st.info("""
**アップロード手順**

1. 入札仕様書（PDF）をアップロード
2. 必要に応じてメール本文PDFをアップロード（顧客名・工期を自動抽出）
3. 「見積生成を実行」ボタンをクリック
        """)

        st.markdown("**仕様書PDF**")
        uploaded_files = st.file_uploader(
            "仕様書PDFをアップロード（複数選択可）",
            type=['pdf'],
            accept_multiple_files=True,
            help="入札仕様書をPDF形式でアップロードしてください。複数ファイルを選択できます。",
            label_visibility="collapsed"
        )

        # メール情報を保存するためのセッションステート
        if 'email_info' not in st.session_state:
            st.session_state.email_info = None

        with st.expander("メール本文PDF（任意）", expanded=False):
            st.caption("顧客名・工期・レンタル期間・見積提出期限などを自動抽出")
            uploaded_email = st.file_uploader(
                "メール本文PDFをアップロード",
                type=['pdf'],
                key="email_upload",
                help="見積依頼メールをPDF化してアップロード"
            )

            if uploaded_email:
                if st.button("メール内容を解析", type="secondary"):
                    with st.spinner("解析中..."):
                        from pipelines.email_extractor import EmailExtractor
                        import tempfile

                        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_email:
                            tmp_email.write(uploaded_email.read())
                            tmp_email_path = tmp_email.name

                        extractor = EmailExtractor()
                        email_info = extractor.extract_email_info(tmp_email_path)
                        st.session_state.email_info = email_info

                        st.success("解析完了")
                        st.json({
                            "工事名": email_info.project_name or "（未取得）",
                            "依頼元": email_info.client_company or "（未取得）",
                            "担当者": email_info.client_contact or "（未取得）",
                            "見積期限": email_info.quote_deadline or "（未取得）",
                            "工期": f"{email_info.construction_start or '?'} ～ {email_info.construction_end or '?'}",
                            "レンタル期間": f"{email_info.rental_start or '?'} ～ {email_info.rental_end or '?'} ({email_info.rental_months or 0}ヶ月)",
                            "建屋面積": f"{email_info.building_area_tsubo or 0}坪",
                        })

        # アップロード状態を表示
        if uploaded_files:
            st.divider()

            # ステータスを横並びで表示
            total_size = sum(f.size for f in uploaded_files) / 1024
            mode_label = "AI自動生成" if use_ai_generation else ("参照見積ベース" if use_reference else "RAG")
            email_status = "取得済" if st.session_state.email_info else "未設定"

            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric(
                    "ファイル数",
                    f"{len(uploaded_files)}件",
                    help="アップロードされた仕様書PDFの数"
                )
            with col2:
                st.metric(
                    "合計サイズ",
                    f"{total_size:.1f} KB",
                    help="すべてのファイルの合計サイズ"
                )
            with col3:
                st.metric(
                    "メール情報",
                    email_status,
                    help="メール本文PDFから抽出した顧客名・工期等の情報"
                )
            with col4:
                st.metric(
                    "生成モード",
                    mode_label,
                    help="AI自動生成: 仕様書から項目を自動生成 / 参照ベース: 過去見積をテンプレート使用"
                )

            st.divider()

            if st.button("見積生成を実行", type="primary", use_container_width=True):
                all_disciplines = [
                    DisciplineType.ELECTRICAL,
                    DisciplineType.MECHANICAL,
                    DisciplineType.GAS
                ]
                generate_estimate(
                    uploaded_files,
                    all_disciplines,
                    use_reference,
                    use_ai_generation,
                    enable_legal,
                    enable_validation
                )

            # ファイル一覧（折りたたみ）
            with st.expander(f"アップロード済みファイル一覧 ({len(uploaded_files)}件)", expanded=False):
                for uploaded_file in uploaded_files:
                    st.text(f"・{uploaded_file.name} ({uploaded_file.size:,} bytes)")

    with tab2:
        st.markdown("**精度レポート**")

        if st.session_state.validation_results:
            validation_results = st.session_state.validation_results

            # AI自動生成の場合の品質レポート
            if validation_results.get('mode') == 'AI自動生成':
                metrics = validation_results['metrics']
                match_rate = metrics['price_match_rate']

                # 比較内容の説明
                st.info("**評価方法**: AIが仕様書から生成した見積項目に対し、価格KB（過去見積データベース）から単価をマッチングした結果を評価しています。")

                # メトリクスを横並びで表示
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric(
                        "生成項目数",
                        f"{metrics['total_items']}件",
                        help="仕様書から抽出した見積項目の総数"
                    )
                with col2:
                    st.metric(
                        "単価付与数",
                        f"{metrics['items_with_price']}件",
                        help="KBから単価をマッチングできた項目数"
                    )
                with col3:
                    st.metric(
                        "マッチング率",
                        f"{match_rate:.1%}",
                        help="単価付与数 / 生成項目数。80%以上が目標"
                    )
                with col4:
                    if metrics.get('avg_confidence', 0) > 0:
                        st.metric(
                            "信頼度",
                            f"{metrics['avg_confidence']:.1%}",
                            help="生成項目の平均信頼度スコア"
                        )
                    else:
                        st.metric(
                            "未マッチ",
                            f"{metrics['total_items'] - metrics['items_with_price']}件",
                            help="KBから単価が見つからなかった項目数"
                        )

                # 詳細
                with st.expander("詳細情報", expanded=True):
                    st.markdown(f"""
                    | 項目 | 値 | 説明 |
                    |------|-----|------|
                    | 生成項目数 | {metrics['total_items']}件 | 仕様書から抽出した見積項目数 |
                    | 単価付与数 | {metrics['items_with_price']}件 | KBから単価をマッチングできた項目 |
                    | マッチング率 | {match_rate:.1%} | 単価付与数 / 生成項目数 |
                    | 未マッチ項目 | {metrics['total_items'] - metrics['items_with_price']}件 | 単価が見つからなかった項目 |
                    """)

                    if metrics['total_items'] - metrics['items_with_price'] > 0:
                        st.warning(f"{metrics['total_items'] - metrics['items_with_price']}件の項目で単価が未取得です。KBの拡充を検討してください。")

            else:
                # 参照見積書との比較モード
                st.info("**評価方法**: AI生成見積と実際の見積書（参照見積書）を比較しています。")

                # 総合スコア
                score = validation_results['overall_score']
                rating = validation_results['summary']['rating']

                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric(
                        "総合スコア",
                        f"{score:.1%}",
                        help="項目カバー率50% + 金額精度50%で算出。80%以上で「優秀」評価"
                    )
                with col2:
                    st.metric(
                        "評価",
                        rating,
                        help="優秀(80%以上) / 普通(60-80%) / 要改善(60%未満)"
                    )
                with col3:
                    disciplines_count = validation_results['summary'].get('total_disciplines', 1)
                    st.metric(
                        "検証区分数",
                        f"{disciplines_count}種類",
                        help="検証対象の工事区分数（電気・機械・ガス）"
                    )

                st.progress(score, text=f"AI再現率: {score:.1%}")

                # 工事区分別詳細
                if "disciplines" in validation_results:
                    st.markdown("---")
                    st.markdown("**工事区分別の比較詳細**")

                    for discipline_name, result in validation_results["disciplines"].items():
                        with st.expander(f"{discipline_name} - スコア: {result['score']:.1%}", expanded=True):
                            # 比較対象を明確に表示
                            st.caption(f"参照: {result['reference_file']}")

                            # 3列で比較表示
                            col1, col2, col3 = st.columns(3)

                            coverage = result['coverage']
                            amount = result['amount']

                            with col1:
                                st.markdown("**項目数比較**")
                                st.markdown(f"""
                                | | AI生成 | 参照 |
                                |---|---|---|
                                | 項目数 | {coverage['generated_count']} | {coverage['reference_count']} |
                                | カバー率 | {coverage['item_coverage']:.1%} | - |
                                | マッチ率 | {coverage['match_rate']:.1%} | - |
                                """)

                            with col2:
                                st.markdown("**金額比較**")
                                st.markdown(f"""
                                | | AI生成 | 参照 |
                                |---|---|---|
                                | 金額 | ¥{amount['generated_amount']:,.0f} | ¥{amount['reference_amount']:,.0f} |
                                | 精度 | {amount['accuracy']:.1%} | - |
                                | 差額 | ¥{amount['difference']:,.0f} ({amount['difference_rate']:+.1%}) | - |
                                """)

                            with col3:
                                st.markdown("**評価**")
                                if result['score'] >= 0.8:
                                    st.success(f"優秀 ({result['score']:.1%})")
                                elif result['score'] >= 0.6:
                                    st.warning(f"普通 ({result['score']:.1%})")
                                else:
                                    st.error(f"要改善 ({result['score']:.1%})")

                                # 改善ポイント
                                if coverage['item_coverage'] < 0.8:
                                    st.caption("項目数が不足しています")
                                if amount['accuracy'] < 0.8:
                                    st.caption("金額の乖離があります")

            # 処理時間
            if st.session_state.processing_time:
                st.caption(f"処理時間: {st.session_state.processing_time:.1f}秒")

        else:
            st.info("アップロードタブから仕様書をアップロードして生成を開始してください")

    with tab3:
        st.markdown("**見積詳細**")

        if st.session_state.fmt_doc:
            fmt_doc = st.session_state.fmt_doc

            # サブタブで分ける
            subtab1, subtab2, subtab3 = st.tabs(["プロジェクト情報", "見積明細", "未取得データ"])

            with subtab1:
                # プロジェクト情報をテーブルで表示
                project_info = fmt_doc.project_info

                # 取得状況を判定する関数
                def get_status(value):
                    if value and str(value).strip() and str(value) != "None":
                        return ("✅", value)
                    else:
                        return ("❌", "未取得")

                # プロジェクト情報の一覧
                info_items = [
                    ("工事名", project_info.project_name),
                    ("工事場所", project_info.location),
                    ("顧客名", project_info.client_name),
                    ("契約期間", project_info.contract_period),
                    ("決済条件", project_info.payment_terms),
                    ("備考", project_info.remarks),
                ]

                st.markdown("**基本情報**")
                for label, value in info_items:
                    status, display_value = get_status(value)
                    if status == "✅":
                        st.markdown(f"[取得済] **{label}**: {display_value}")
                    else:
                        st.markdown(f"[未取得] **{label}**: {display_value}")

                st.markdown("---")
                st.markdown("**工事区分**")
                col1, col2 = st.columns(2)
                with col1:
                    st.write(f"施設区分: {fmt_doc.facility_type.value}")
                with col2:
                    st.write(f"対象工事: {', '.join([d.value for d in fmt_doc.disciplines])}")

                # 法令情報
                if st.session_state.legal_refs:
                    st.markdown("---")
                    st.markdown("**適用法令**")
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("適用法令数", len(st.session_state.legal_refs))
                    with col2:
                        legal_items = [item for item in fmt_doc.estimate_items if item.source_type == "legal"]
                        st.metric("法令対応項目", len(legal_items))
                    with col3:
                        high_conf = [ref for ref in st.session_state.legal_refs if ref.relevance_score >= 0.9]
                        st.metric("高信頼度法令", len(high_conf))

            with subtab2:
                if fmt_doc.estimate_items:
                    # サマリー統計
                    total = sum(item.amount or 0 for item in fmt_doc.estimate_items)
                    items_with_price = [item for item in fmt_doc.estimate_items if item.unit_price]
                    items_without_price = [item for item in fmt_doc.estimate_items if not item.unit_price and item.level > 0]
                    price_rate = len(items_with_price) / len(fmt_doc.estimate_items) if fmt_doc.estimate_items else 0

                    col1, col2, col3, col4 = st.columns(4)
                    with col1:
                        st.metric(
                            "推定総額",
                            f"¥{total:,.0f}",
                            help="すべての見積項目の合計金額"
                        )
                    with col2:
                        st.metric(
                            "総項目数",
                            f"{len(fmt_doc.estimate_items)}件",
                            help="生成された見積項目の総数（親項目含む）"
                        )
                    with col3:
                        st.metric(
                            "単価取得率",
                            f"{price_rate:.1%}",
                            help="KBから単価を取得できた項目の割合"
                        )
                    with col4:
                        st.metric(
                            "単価未取得",
                            f"{len(items_without_price)}件",
                            delta=None if len(items_without_price) == 0 else f"-{len(items_without_price)}",
                            delta_color="inverse",
                            help="単価が未設定の項目数。KB管理で単価データを追加してください"
                        )

                    # 工事区分別フィルタ
                    filter_discipline = st.selectbox(
                        "工事区分で絞り込み",
                        ["全て"] + [d.value for d in fmt_doc.disciplines],
                        key="filter_discipline"
                    )

                    # テーブル表示
                    estimate_data = []
                    for item in fmt_doc.estimate_items:
                        # フィルタ適用
                        if filter_discipline != "全て" and item.discipline and item.discipline.value != filter_discipline:
                            continue

                        indent = "　" * item.level

                        # 単価未取得をハイライト
                        price_display = f"¥{item.unit_price:,.0f}" if item.unit_price else "[未取得]"
                        amount_display = f"¥{item.amount:,.0f}" if item.amount else "—"

                        row = {
                            "Lv": item.level,
                            "項目名": f"{indent}{item.name}",
                            "仕様": item.specification or "—",
                            "数量": item.quantity if item.quantity else "—",
                            "単位": item.unit or "—",
                            "単価": price_display,
                            "金額": amount_display,
                            "出典": item.source_type or "—",
                        }

                        estimate_data.append(row)

                    st.dataframe(estimate_data, use_container_width=True, height=400)

                    # 諸経費
                    if fmt_doc.overhead_calculations:
                        with st.expander("諸経費計算詳細"):
                            for overhead in fmt_doc.overhead_calculations:
                                st.markdown(f"**{overhead.name}**: ¥{overhead.amount:,.0f}")
                                st.caption(f"計算式: {overhead.formula}")
                else:
                    st.info("見積項目がありません")

            with subtab3:
                # 未取得データの一覧
                st.markdown("**単価未取得の項目一覧**")
                st.caption("KBに登録がない、またはマッチングできなかった項目です")

                if fmt_doc.estimate_items:
                    missing_items = [
                        item for item in fmt_doc.estimate_items
                        if not item.unit_price and item.level > 0  # 親項目は除外
                    ]

                    if missing_items:
                        st.warning(f"{len(missing_items)}件の項目で単価が未取得です")

                        missing_data = []
                        for item in missing_items:
                            missing_data.append({
                                "項目名": item.name,
                                "仕様": item.specification or "—",
                                "数量": item.quantity if item.quantity else "—",
                                "単位": item.unit or "—",
                                "工事区分": item.discipline.value if item.discipline else "—",
                            })

                        st.dataframe(missing_data, use_container_width=True)

                        st.markdown("---")
                        st.markdown("**改善方法**")
                        st.markdown("""
                        1. **KB管理ページ** で過去の見積書をアップロードしてKBを拡充
                        2. 類似項目の単価を参考に手動で入力
                        3. 項目名・仕様の表記を統一してマッチング率を向上
                        """)
                    else:
                        st.success("すべての項目で単価が取得できています")

                    # プロジェクト情報の未取得も表示
                    st.markdown("---")
                    st.markdown("**プロジェクト情報の未取得項目**")

                    project_info = fmt_doc.project_info
                    missing_project = []
                    if not project_info.project_name:
                        missing_project.append("工事名")
                    if not project_info.location:
                        missing_project.append("工事場所")
                    if not project_info.client_name:
                        missing_project.append("顧客名")
                    if not project_info.contract_period:
                        missing_project.append("契約期間")

                    if missing_project:
                        st.warning(f"以下の情報が未取得です: {', '.join(missing_project)}")
                        st.caption("メール本文PDFをアップロードすると自動抽出できる場合があります")
                    else:
                        st.success("プロジェクト基本情報は全て取得できています")
                else:
                    st.info("見積データがありません")

        else:
            st.info("アップロードタブから仕様書をアップロードして生成を開始してください")

    with tab4:
        st.markdown("**ダウンロード**")

        if st.session_state.generated_files:
            # 処理情報
            if st.session_state.processing_time:
                st.caption(f"処理時間: {st.session_state.processing_time:.1f}秒")

            # 全ファイル一括ダウンロード
            all_files = st.session_state.generated_files
            if all_files:
                zip_buffer = BytesIO()
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

                with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                    for file_info in all_files:
                        dir_prefix = f"{file_info['spec_name']}/"
                        zip_file.write(file_info['fmt_json'], arcname=f"{dir_prefix}{file_info['fmt_json'].name}")
                        for pdf_path in file_info['pdfs']:
                            zip_file.write(pdf_path, arcname=f"{dir_prefix}{Path(pdf_path).name}")
                        if file_info['validation_json']:
                            zip_file.write(file_info['validation_json'], arcname=f"{dir_prefix}{file_info['validation_json'].name}")
                        zip_file.write(file_info['summary'], arcname=f"{dir_prefix}{file_info['summary'].name}")

                zip_buffer.seek(0)

                col1, col2 = st.columns([1, 2])
                with col1:
                    st.download_button(
                        label="全ファイルをZIPダウンロード",
                        data=zip_buffer,
                        file_name=f"見積書_{timestamp}.zip",
                        mime="application/zip",
                        type="primary",
                        use_container_width=True
                    )
                with col2:
                    total_files = sum(1 + len(f['pdfs']) + (1 if f['validation_json'] else 0) + 1 for f in all_files)
                    st.caption(f"合計 {total_files} ファイル")

            st.markdown("---")

            # 個別ファイル一覧
            st.markdown("**個別ファイル**")

            for file_idx, file_info in enumerate(st.session_state.generated_files):
                with st.expander(f"{file_info['spec_name']}", expanded=file_idx == 0):
                    col1, col2, col3, col4 = st.columns(4)
                    with col1:
                        with open(file_info['fmt_json'], 'rb') as f:
                            st.download_button(
                                label="JSON",
                                data=f,
                                file_name=file_info['fmt_json'].name,
                                mime="application/json",
                                key=f"json_{file_idx}",
                                use_container_width=True
                            )
                    with col2:
                        for pdf_idx, pdf_path in enumerate(file_info['pdfs']):
                            with open(pdf_path, 'rb') as f:
                                st.download_button(
                                    label=f"PDF{f' ({pdf_idx+1})' if len(file_info['pdfs']) > 1 else ''}",
                                    data=f,
                                    file_name=Path(pdf_path).name,
                                    mime="application/pdf",
                                    key=f"pdf_{file_idx}_{pdf_idx}",
                                    use_container_width=True
                                )
                    with col3:
                        if file_info['validation_json']:
                            with open(file_info['validation_json'], 'rb') as f:
                                st.download_button(
                                    label="精度検証",
                                    data=f,
                                    file_name=file_info['validation_json'].name,
                                    mime="application/json",
                                    key=f"val_{file_idx}",
                                    use_container_width=True
                                )
                    with col4:
                        with open(file_info['summary'], 'rb') as f:
                            st.download_button(
                                label="サマリー",
                                data=f,
                                file_name=file_info['summary'].name,
                                mime="text/plain",
                                key=f"sum_{file_idx}",
                                use_container_width=True
                            )

        else:
            st.info("アップロードタブから仕様書をアップロードして生成を開始してください")


def generate_estimate(
    uploaded_files: list,
    disciplines: list[DisciplineType],
    use_reference: bool,
    use_ai_generation: bool,
    enable_legal: bool,
    enable_validation: bool
):
    """見積書を生成（複数ファイル・複数工事区分対応）"""
    start_time = datetime.now()

    # セッションステートに結果を保存するための初期化
    if 'generated_files' not in st.session_state:
        st.session_state.generated_files = []
    st.session_state.generated_files = []

    total_tasks = len(uploaded_files) * len(disciplines)
    with st.spinner(f"見積書を生成中...（{len(uploaded_files)}ファイル × {len(disciplines)}工事区分 = {total_tasks}タスク）"):
        try:
            task_counter = 0

            # 各仕様書ファイルを処理
            for file_idx, uploaded_file in enumerate(uploaded_files, 1):
                st.info(f"[{file_idx}/{len(uploaded_files)}] {uploaded_file.name}を処理中...")

                # 一時ファイルに保存
                with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
                    tmp_file.write(uploaded_file.read())
                    tmp_path = tmp_file.name

                # 参照見積書のパスを設定
                reference_pdfs_map = {
                    DisciplineType.GAS: "test-files/250918_送付状　見積書（都市ｶﾞｽ).pdf",
                    DisciplineType.ELECTRICAL: "test-files/250723_送付状　見積書（電気・機械）.pdf",
                    DisciplineType.MECHANICAL: "test-files/250723_送付状　見積書（電気・機械）.pdf"  # 電気と同じ参照見積書を使用
                }

                # 出力ディレクトリの準備
                output_dir = Path("output")
                output_dir.mkdir(exist_ok=True)

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                if use_ai_generation:
                    mode_name = "AI自動生成"
                elif use_reference:
                    mode_name = "参照ベース"
                else:
                    mode_name = "LLM_RAG"

                # 仕様書名（拡張子なし）を取得
                spec_name = Path(uploaded_file.name).stem

                # 各工事区分を処理
                all_fmt_docs = {}
                all_validation_results = {}
                all_legal_refs = {}

                for discipline in disciplines:
                    task_counter += 1
                    st.info(f"[{task_counter}/{total_tasks}] {uploaded_file.name} - {discipline.value}を処理中...")

                    # 見積書を生成
                    if use_ai_generation:
                        # AI自動生成モード（全工事区分対応）
                        st.write(f"  AIが仕様書から詳細な見積項目を自動生成中...")
                        st.write(f"  建物情報を分析中...")

                        ai_generator = AIEstimateGenerator(kb_path="kb/price_kb.json")
                        fmt_doc = ai_generator.generate_estimate(
                            tmp_path,
                            discipline
                        )

                        legal_refs = []

                        # 単価マッチング率を計算
                        with_price = sum(1 for item in fmt_doc.estimate_items if item.unit_price is not None)
                        match_rate = with_price / len(fmt_doc.estimate_items) * 100 if fmt_doc.estimate_items else 0

                        st.success(f"  {len(fmt_doc.estimate_items)}項目を生成（AI自動生成）")
                        st.info(f"  単価マッチング率: {match_rate:.1f}% ({with_price}/{len(fmt_doc.estimate_items)}項目)")

                    elif use_reference and discipline in reference_pdfs_map:
                        # 参照見積書ベースの生成
                        st.write(f"  参照見積書から詳細な項目・単価を抽出中...")

                        reference_generator = EstimateFromReference()
                        fmt_doc = reference_generator.generate_estimate_from_reference(
                            tmp_path,
                            reference_pdfs_map[discipline],
                            discipline
                        )

                        legal_refs = []
                        st.success(f"  {len(fmt_doc.estimate_items)}項目を抽出（参照見積書ベース）")

                    else:
                        # LLM + RAGベースの生成
                        st.write(f"  仕様書から見積項目を抽出中...")

                        generator = EstimateGeneratorWithLegal(kb_path="kb/price_kb.json")
                        result = generator.generate_estimate_with_legal(
                            tmp_path,
                            disciplines=[discipline],
                            add_welfare_costs=True,
                            validate_legal=enable_legal
                        )

                        fmt_doc = result["fmt_doc"]
                        legal_refs = result["legal_refs"]

                        st.success(f"  {len(fmt_doc.estimate_items)}項目を抽出")

                    # メール情報を統合（セッションステートにemail_infoがある場合）
                    if st.session_state.email_info:
                        email_info = st.session_state.email_info
                        st.write(f"  メール情報を統合中...")

                        # ProjectInfoの更新
                        if email_info.client_company:
                            fmt_doc.project_info.client_name = f"{email_info.client_company}"
                            if email_info.client_contact:
                                fmt_doc.project_info.client_name += f" {email_info.client_contact}様"

                        if email_info.construction_start and email_info.construction_end:
                            fmt_doc.project_info.contract_period = f"工期: {email_info.construction_start} ～ {email_info.construction_end}"

                        if email_info.rental_start and email_info.rental_end:
                            if fmt_doc.project_info.contract_period:
                                fmt_doc.project_info.contract_period += f" / レンタル期間: {email_info.rental_start} ～ {email_info.rental_end} ({email_info.rental_months}ヶ月)"
                            else:
                                fmt_doc.project_info.contract_period = f"レンタル期間: {email_info.rental_start} ～ {email_info.rental_end} ({email_info.rental_months}ヶ月)"

                        if email_info.quote_deadline:
                            if fmt_doc.project_info.remarks:
                                fmt_doc.project_info.remarks += f"\n見積提出期限: {email_info.quote_deadline}"
                            else:
                                fmt_doc.project_info.remarks = f"見積提出期限: {email_info.quote_deadline}"

                        if email_info.building_area_m2:
                            if hasattr(fmt_doc.project_info, 'floor_area_m2'):
                                fmt_doc.project_info.floor_area_m2 = email_info.building_area_m2

                        if email_info.remarks:
                            if fmt_doc.project_info.remarks:
                                fmt_doc.project_info.remarks += f"\n{email_info.remarks}"
                            else:
                                fmt_doc.project_info.remarks = email_info.remarks

                        st.success(f"  メール情報を統合しました（顧客: {email_info.client_company}）")

                    # 結果を保存
                    all_fmt_docs[discipline] = fmt_doc
                    all_legal_refs[discipline] = legal_refs

                    # 精度検証
                    validation_results = None
                    if enable_validation and discipline in reference_pdfs_map:
                        # AI自動生成の場合は参照見積書との比較をスキップ
                        if use_ai_generation:
                            st.info(f"  AI自動生成モードでは独自の品質指標を使用します")

                            # AI生成の品質指標を計算
                            with_price = sum(1 for item in fmt_doc.estimate_items if item.unit_price is not None)
                            match_rate = with_price / len(fmt_doc.estimate_items) if fmt_doc.estimate_items else 0

                            # 信頼度スコアを計算（confidence属性がある場合）
                            confidences = [item.confidence for item in fmt_doc.estimate_items if hasattr(item, 'confidence') and item.confidence is not None]
                            avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

                            # カスタム品質レポートを作成
                            validation_results = {
                                'mode': 'AI自動生成',
                                'overall_score': avg_confidence if avg_confidence > 0 else match_rate,
                                'metrics': {
                                    'total_items': len(fmt_doc.estimate_items),
                                    'items_with_price': with_price,
                                    'price_match_rate': match_rate,
                                    'avg_confidence': avg_confidence
                                },
                                'summary': {
                                    'rating': 'AI生成品質',
                                    'message': f'生成項目数: {len(fmt_doc.estimate_items)}項目、単価マッチング率: {match_rate:.1%}'
                                }
                            }

                            all_validation_results[discipline] = validation_results

                            # 品質指標を表示
                            if avg_confidence > 0:
                                st.success(f"  品質評価: 単価マッチング {match_rate:.1%} / 平均信頼度 {avg_confidence:.1%}")
                            else:
                                st.success(f"  品質評価: 単価マッチング率 {match_rate:.1%}")

                        else:
                            st.write(f"  実際の見積書と比較して精度を検証中...")

                            validator = EstimateValidator()
                            validation_results = validator.validate_estimate(
                                fmt_doc,
                                {discipline: reference_pdfs_map[discipline]}
                            )

                            all_validation_results[discipline] = validation_results

                            # スコアに応じてメッセージ
                            score = validation_results['overall_score']
                            if score >= 0.7:
                                st.success(f"  精度検証完了: {score:.1%} - {validation_results['summary']['rating']}")
                            elif score >= 0.5:
                                st.warning(f"  精度検証完了: {score:.1%} - {validation_results['summary']['rating']}")
                            else:
                                st.error(f"  精度検証完了: {score:.1%} - {validation_results['summary']['rating']}")

                # ループ終了後に統合処理
                st.write(f"  結果をoutputディレクトリに保存中...")

                # 全工事区分のfmt_docを統合
                if len(all_fmt_docs) > 0:
                    # 最初のfmt_docをベースにする
                    first_discipline = list(all_fmt_docs.keys())[0]
                    merged_fmt_doc = all_fmt_docs[first_discipline]

                    # 他のdisciplineの項目を追加
                    for discipline, fmt_doc in list(all_fmt_docs.items())[1:]:
                        merged_fmt_doc.estimate_items.extend(fmt_doc.estimate_items)
                        # disciplinesも統合
                        if discipline not in merged_fmt_doc.disciplines:
                            merged_fmt_doc.disciplines.append(discipline)

                    # 統合されたfmt_docを使用
                    fmt_doc = merged_fmt_doc

                    # 1. FMTDocumentをJSONとして保存
                    fmt_json_path = output_dir / f"見積データ_{spec_name}_統合_{mode_name}_{timestamp}.json"
                    with open(fmt_json_path, 'w', encoding='utf-8') as f:
                        json.dump(fmt_doc.model_dump(mode='json'), f, ensure_ascii=False, indent=2)

                    # 2. 見積書PDFを生成（統合版）
                    exporter = EstimateExporter(output_dir=str(output_dir))
                    pdf_paths = exporter.export_to_pdfs_by_discipline(fmt_doc)

                    # PDFファイル名を変更（タイムスタンプ付き）
                    renamed_pdf_paths = []
                    for pdf_path in pdf_paths:
                        old_path = Path(pdf_path)
                        new_name = f"{old_path.stem}_{spec_name}_{mode_name}_{timestamp}.pdf"
                        new_path = old_path.parent / new_name
                        if old_path.exists():
                            old_path.rename(new_path)
                            renamed_pdf_paths.append(str(new_path))

                    # 3. 精度検証結果をJSONとして保存（全工事区分統合）
                    validation_json_path = None
                    if all_validation_results:
                        validation_json_path = output_dir / f"精度検証_{spec_name}_統合_{mode_name}_{timestamp}.json"
                        with open(validation_json_path, 'w', encoding='utf-8') as f:
                            json.dump(all_validation_results, f, ensure_ascii=False, indent=2)

                    # 4. サマリーレポートをテキストファイルとして保存
                    summary_path = output_dir / f"サマリー_{spec_name}_統合_{mode_name}_{timestamp}.txt"
                    with open(summary_path, 'w', encoding='utf-8') as f:
                        f.write("=" * 80 + "\n")
                        f.write(f"AI見積書生成システム - 実行サマリー\n")
                        f.write("=" * 80 + "\n\n")

                        f.write(f"【実行情報】\n")
                        f.write(f"  日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                        f.write(f"  モード: {mode_name}\n")
                        f.write(f"  工事区分: {', '.join([d.value for d in fmt_doc.disciplines])}\n")
                        f.write(f"  仕様書: {uploaded_file.name}\n\n")

                        f.write(f"【プロジェクト情報】\n")
                        f.write(f"  工事名: {fmt_doc.project_info.project_name}\n")
                        f.write(f"  場所: {fmt_doc.project_info.location}\n")
                        f.write(f"  顧客: {fmt_doc.project_info.client_name}\n")
                        f.write(f"  期間: {fmt_doc.project_info.contract_period}\n\n")

                        f.write(f"【見積内容】\n")
                        f.write(f"  総項目数: {len(fmt_doc.estimate_items)}項目\n")

                        # 工事区分別の項目数を表示
                        for discipline in fmt_doc.disciplines:
                            discipline_items = [item for item in fmt_doc.estimate_items if item.discipline == discipline]
                            f.write(f"    {discipline.value}: {len(discipline_items)}項目\n")

                        total = sum(item.amount or 0 for item in fmt_doc.estimate_items)
                        f.write(f"  推定総額: ¥{total:,.0f}\n")

                        if fmt_doc.estimate_items:
                            items_with_price = [item for item in fmt_doc.estimate_items if item.unit_price]
                            f.write(f"  単価付与率: {len(items_with_price)/len(fmt_doc.estimate_items):.1%}\n")

                        total_legal_refs = sum(len(refs) for refs in all_legal_refs.values())
                        if not use_reference and total_legal_refs > 0:
                            f.write(f"  適用法令数: {total_legal_refs}\n")

                        f.write("\n")

                        if all_validation_results:
                            f.write(f"【精度検証】\n")

                            # 工事区分別に精度検証結果を表示
                            for discipline, validation_results in all_validation_results.items():
                                f.write(f"\n  ■ {discipline.value}\n")
                                f.write(f"  総合スコア: {validation_results['overall_score']:.1%}\n")
                                f.write(f"  評価: {validation_results['summary']['rating']}\n")

                                # AI自動生成モードの場合
                                if validation_results.get('mode') == 'AI自動生成':
                                    metrics = validation_results.get('metrics', {})
                                    f.write(f"  生成項目数: {metrics.get('total_items', 0)}項目\n")
                                    f.write(f"  単価付与数: {metrics.get('items_with_price', 0)}項目\n")
                                    f.write(f"  単価マッチング率: {metrics.get('price_match_rate', 0):.1%}\n")
                                    if metrics.get('avg_confidence', 0) > 0:
                                        f.write(f"  平均信頼度: {metrics.get('avg_confidence', 0):.1%}\n")

                                # 参照見積書検証モードの場合
                                elif "disciplines" in validation_results:
                                    for discipline_name, result in validation_results["disciplines"].items():
                                        f.write(f"    {discipline_name}:\n")
                                        f.write(f"      スコア: {result['score']:.1%}\n")
                                        coverage = result['coverage']
                                        f.write(f"      項目カバー率: {coverage['item_coverage']:.1%} ({coverage['generated_count']}/{coverage['reference_count']}項目)\n")
                                        f.write(f"      項目マッチング率: {coverage['match_rate']:.1%}\n")
                                        amount = result['amount']
                                        f.write(f"      金額精度: {amount['accuracy']:.1%}\n")
                                        f.write(f"      生成額: ¥{amount['generated_amount']:,.0f}\n")
                                        f.write(f"      参照額: ¥{amount['reference_amount']:,.0f}\n")
                                        f.write(f"      差額: ¥{amount['difference']:,.0f} ({amount['difference_rate']:.1%})\n")
                                        f.write(f"      参照ファイル: {result['reference_file']}\n")

                            f.write("\n")

                        f.write(f"【出力ファイル】\n")
                        f.write(f"  FMTデータ: {fmt_json_path.name}\n")
                        for pdf_path in renamed_pdf_paths:
                            f.write(f"  見積書PDF: {Path(pdf_path).name}\n")
                        if validation_json_path:
                            f.write(f"  精度検証: {validation_json_path.name}\n")
                        f.write(f"  サマリー: {summary_path.name}\n\n")

                        f.write("=" * 80 + "\n")

                    # 生成されたファイルをセッションステートに保存
                    generated_file_info = {
                        'spec_name': spec_name,
                        'discipline': ', '.join([d.value for d in fmt_doc.disciplines]),
                        'fmt_json': fmt_json_path,
                        'pdfs': renamed_pdf_paths,
                        'validation_json': validation_json_path,
                        'summary': summary_path
                    }
                    st.session_state.generated_files.append(generated_file_info)

                    st.success(f"  {discipline.value}の処理完了")

            # 全体の処理時間を記録
            end_time = datetime.now()
            processing_time = (end_time - start_time).total_seconds()
            st.session_state.processing_time = processing_time

            # セッションに最後の結果を保存（後方互換性のため）
            if disciplines:
                last_discipline = disciplines[-1]
                st.session_state.fmt_doc = all_fmt_docs.get(last_discipline)
                st.session_state.legal_refs = all_legal_refs.get(last_discipline, [])
                st.session_state.validation_results = all_validation_results.get(last_discipline)

            # 完了メッセージ
            st.success("見積書生成完了")

            # 統計情報を表示
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("処理時間", f"{processing_time:.1f}秒")
            with col2:
                total_items = sum(len(fmt_doc.estimate_items) for fmt_doc in all_fmt_docs.values())
                st.metric("総項目数", f"{total_items}項目")
            with col3:
                total_amount = sum(
                    sum(item.amount or 0 for item in fmt_doc.estimate_items)
                    for fmt_doc in all_fmt_docs.values()
                )
                st.metric("推定総額", f"¥{total_amount:,.0f}" if total_amount > 0 else "要確認")

            # 精度サマリー（検証が有効な場合）
            if enable_validation and all_validation_results:
                st.subheader("精度サマリー")
                for discipline, validation_results in all_validation_results.items():
                    score = validation_results['overall_score']
                    col1, col2 = st.columns([1, 3])
                    with col1:
                        st.write(f"**{discipline.value}**")
                    with col2:
                        st.progress(score, text=f"{score:.1%} - {validation_results['summary']['rating']}")

            # ダウンロードはタブ4で行う
            st.markdown("---")
            st.info("ダウンロードは「ダウンロード」タブをご確認ください")

            # 処理完了（3分以内なら正常完了）
            if processing_time > 180:
                st.warning("処理に時間がかかりました。ファイルサイズや項目数を確認してください。")

        except Exception as e:
            st.error(f"エラーが発生しました: {str(e)}")
            logger.exception("Generation error")
            import traceback
            st.code(traceback.format_exc())


if __name__ == "__main__":
    main()
