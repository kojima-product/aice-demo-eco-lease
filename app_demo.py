"""
Streamlit Demo App - AI見積書生成システム

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


# ページ設定
st.set_page_config(
    page_title="AI見積書生成システム DEMO",
    page_icon="🤖",
    layout="wide"
)


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
    st.title("🤖 AI見積書生成システム DEMO")
    st.caption("人間が作成していた見積書をAIでどこまで再現できるか")
    st.markdown("---")

    # サイドバー
    with st.sidebar:
        st.header("⚙️ 設定")

        # 工事区分は常に全て処理
        st.subheader("🏗️ 工事区分")
        st.info("""
        **処理対象**: 全工事区分を自動処理
        - ✅ 電気設備工事
        - ✅ 機械設備工事
        - ✅ ガス設備工事

        **出力グループ**:
        - 📦 電気・機械設備
        - 📦 都市ガス設備
        """)

        st.markdown("---")

        # 機能選択
        st.subheader("🔧 生成モード")

        generation_mode = st.radio(
            "見積生成方法を選択",
            ["🤖 AI自動生成（推奨）", "📋 参照見積書ベース", "🔍 LLM + RAGベース"],
            index=0,
            help="仕様書からの見積生成方法を選択してください"
        )

        # モードに応じた設定
        use_reference = (generation_mode == "📋 参照見積書ベース")
        use_ai_generation = (generation_mode == "🤖 AI自動生成（推奨）")

        if not use_reference and not use_ai_generation:
            enable_legal = st.checkbox("法令遵守チェック", value=False,
                                        help="関係法令に基づく要件チェックを実行")
        else:
            enable_legal = False

        enable_validation = st.checkbox("精度検証", value=True,
                                        help="実際の見積書と比較して精度を検証")

        st.markdown("---")

        st.header("📊 システム情報")

        if use_ai_generation:
            st.success(f"""
            **モード**: 🤖 AI自動生成（推奨）

            **特徴**:
            - ✅ 仕様書から直接、詳細な見積項目を自動生成
            - ✅ 参照見積書不要
            - ✅ AIが建築設備の専門知識で設計レベルの項目を推定
            - ✅ 過去見積KBから単価を自動取得
            - ✅ 生成項目数: 48項目 (ガス設備)
            - ✅ 単価マッチング率: 75%

            **使用AI**: Claude Sonnet 4.5
            """)
            st.warning(f"""
            **現在の対応状況**:
            - ✅ ガス設備工事: 完全対応（48項目生成）
            - ⚠️ 電気設備工事: 開発中（参照見積書ベースを使用）
            - ⚠️ 機械設備工事: 開発中（参照見積書ベースを使用）
            """)
        elif use_reference:
            st.success(f"""
            **モード**: 📋 参照見積書ベース

            **特徴**:
            - ✅ 実際の見積書の項目・単価をそのまま使用
            - ✅ 金額精度: ほぼ100%
            - ✅ 処理時間: 30秒以内

            **参照見積書**:
            - ガス: ¥13,401,093 (34項目)
            - 電気: ¥209,992,533
            """)
        else:
            st.info(f"""
            **モード**: 🔍 LLM + RAGベース

            **使用AI**: Claude Sonnet 4.5

            **機能**:
            - file_logic.md分析ベース
            - 関係法令統合
            - RAG単価検索
            - 法定福利費16.07%自動計算

            **注意**: 単価マッチング精度が低い可能性あり
            """)

    # メインコンテンツ
    tab1, tab2, tab3, tab4 = st.tabs(["📤 仕様書アップロード", "📊 精度レポート", "📋 見積詳細", "📥 ダウンロード"])

    with tab1:
        st.header("仕様書のアップロード")
        st.write("AIが仕様書を読み取り、見積書を自動生成します。")

        uploaded_files = st.file_uploader(
            "仕様書PDFをアップロード（複数選択可）",
            type=['pdf'],
            accept_multiple_files=True,
            help="複数の仕様書を選択できます"
        )

        if uploaded_files:
            st.success(f"✅ アップロード済み: {len(uploaded_files)}ファイル")
            for uploaded_file in uploaded_files:
                st.write(f"  - {uploaded_file.name} ({uploaded_file.size:,} bytes)")

            col1, col2 = st.columns([1, 4])
            with col1:
                if st.button("🚀 生成開始", type="primary"):
                    # 常に全工事区分を処理
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

    with tab2:
        # AI自動生成の場合はタイトルを変更
        if st.session_state.validation_results and st.session_state.validation_results.get('mode') == 'AI自動生成':
            st.header("📊 品質レポート")
            st.write("AI自動生成の品質指標を表示します。")
        else:
            st.header("📊 精度レポート")
            st.write("生成された見積書と実際の見積書（人間が作成）を比較します。")

        if st.session_state.validation_results:
            validation_results = st.session_state.validation_results

            # AI自動生成の場合の品質レポート
            if validation_results.get('mode') == 'AI自動生成':
                st.subheader("🤖 AI生成品質")

                col1, col2, col3 = st.columns(3)

                with col1:
                    metrics = validation_results['metrics']
                    st.metric(
                        "生成項目数",
                        f"{metrics['total_items']}項目",
                        help="仕様書から自動生成された見積項目数"
                    )

                with col2:
                    match_rate = metrics['price_match_rate']
                    st.metric(
                        "単価マッチング率",
                        f"{match_rate:.1%}",
                        help="KBから単価を取得できた項目の割合"
                    )

                with col3:
                    if metrics.get('avg_confidence', 0) > 0:
                        confidence = metrics['avg_confidence']
                        st.metric(
                            "平均信頼度",
                            f"{confidence:.1%}",
                            help="AI生成項目の平均信頼度スコア"
                        )
                    else:
                        st.metric(
                            "単価付与項目",
                            f"{metrics['items_with_price']}項目",
                            help="単価が設定された項目数"
                        )

                # プログレスバー
                st.progress(match_rate, text=f"単価マッチング率: {match_rate:.1%}")

                st.info(validation_results['summary']['message'])

            else:
                # 従来の精度レポート
                # 総合評価
                st.subheader("🎯 総合評価")

                col1, col2, col3 = st.columns(3)
                with col1:
                    score = validation_results['overall_score']
                    st.metric(
                        "総合スコア",
                        f"{score:.1%}",
                        delta=None,
                        help="項目カバー率50% + 金額精度50%"
                    )

                with col2:
                    rating = validation_results['summary']['rating']
                    st.metric("評価", rating)

                with col3:
                    disciplines_count = validation_results['summary']['total_disciplines']
                    st.metric("検証工事区分", f"{disciplines_count}種類")

                # プログレスバー
                st.progress(score, text=f"AI再現率: {score:.1%}")

            # 工事区分別詳細（従来モードのみ）
            if validation_results.get('mode') != 'AI自動生成' and "disciplines" in validation_results:
                st.subheader("🔍 工事区分別詳細")

                for discipline_name, result in validation_results["disciplines"].items():
                    with st.expander(f"📌 {discipline_name} - スコア: {result['score']:.1%}"):
                        col1, col2 = st.columns(2)

                        with col1:
                            st.markdown("**項目カバー率**")
                            coverage = result['coverage']
                            st.metric(
                                "生成項目数 / 参照項目数",
                                f"{coverage['generated_count']} / {coverage['reference_count']}"
                            )
                            st.progress(
                                coverage['item_coverage'],
                                text=f"{coverage['item_coverage']:.1%}"
                            )

                            st.markdown("**項目マッチング率**")
                            st.progress(
                                coverage['match_rate'],
                                text=f"{coverage['match_rate']:.1%}"
                            )

                        with col2:
                            st.markdown("**金額精度**")
                            amount = result['amount']
                            st.metric(
                                "生成額 / 参照額",
                                f"¥{amount['generated_amount']:,.0f} / ¥{amount['reference_amount']:,.0f}"
                            )
                            st.progress(
                                amount['accuracy'],
                                text=f"{amount['accuracy']:.1%}"
                            )

                            st.markdown("**差額**")
                            st.metric(
                                "金額差",
                                f"¥{amount['difference']:,.0f}",
                                delta=f"{amount['difference_rate']:.1%}",
                                delta_color="inverse"
                            )

                        st.markdown(f"**参照ファイル**: {result['reference_file']}")

            # 処理時間
            if st.session_state.processing_time:
                st.info(f"⏱️ 処理時間: {st.session_state.processing_time:.1f}秒")

        else:
            st.info("👈 左のタブから仕様書をアップロードして生成を開始してください")

    with tab3:
        st.header("📋 見積詳細")

        if st.session_state.fmt_doc:
            fmt_doc = st.session_state.fmt_doc

            # プロジェクト情報
            st.subheader("📌 プロジェクト情報")
            col1, col2 = st.columns(2)
            with col1:
                st.write(f"**工事名**: {fmt_doc.project_info.project_name}")
                st.write(f"**場所**: {fmt_doc.project_info.location}")
            with col2:
                st.write(f"**施設区分**: {fmt_doc.facility_type.value}")
                st.write(f"**工事区分**: {', '.join([d.value for d in fmt_doc.disciplines])}")

            # 法令遵守状況
            if st.session_state.legal_refs:
                st.subheader("⚖️ 法令遵守状況")
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("適用法令数", len(st.session_state.legal_refs))
                with col2:
                    legal_items = [
                        item for item in fmt_doc.estimate_items
                        if item.source_type == "legal"
                    ]
                    st.metric("法令対応項目", len(legal_items))
                with col3:
                    high_conf_legal = [
                        ref for ref in st.session_state.legal_refs
                        if ref.relevance_score >= 0.9
                    ]
                    st.metric("高信頼度法令", len(high_conf_legal))

            # 見積明細
            st.subheader("💰 見積明細")

            if fmt_doc.estimate_items:
                # 統計情報
                col1, col2, col3 = st.columns(3)

                total = sum(item.amount or 0 for item in fmt_doc.estimate_items)
                with col1:
                    st.metric("合計金額（税別）", f"¥{total:,.0f}")

                with col2:
                    st.metric("項目数", len(fmt_doc.estimate_items))

                with col3:
                    items_with_price = [
                        item for item in fmt_doc.estimate_items
                        if item.unit_price
                    ]
                    st.metric("単価付与率", f"{len(items_with_price)/len(fmt_doc.estimate_items):.1%}")

                # テーブル表示
                estimate_data = []
                for item in fmt_doc.estimate_items:
                    indent = "　" * item.level

                    row = {
                        "階層": item.level,
                        "項目名": f"{indent}{item.name}",
                        "仕様": item.specification or "",
                        "数量": item.quantity if item.quantity else "",
                        "単位": item.unit or "",
                        "単価": f"¥{item.unit_price:,.0f}" if item.unit_price else "",
                        "金額": f"¥{item.amount:,.0f}" if item.amount else "",
                        "費用区分": item.cost_type.value if item.cost_type else "",
                        "出典": item.source_type or "",
                    }

                    estimate_data.append(row)

                st.dataframe(estimate_data, use_container_width=True, height=500)

            # 諸経費計算
            if fmt_doc.overhead_calculations:
                st.subheader("💰 諸経費計算")
                for overhead in fmt_doc.overhead_calculations:
                    with st.expander(f"{overhead.name}: ¥{overhead.amount:,.0f}"):
                        st.write(f"**計算式**: {overhead.formula}")
                        st.write(f"**備考**: {overhead.remarks}")

        else:
            st.info("見積書がまだ生成されていません")

    with tab4:
        st.header("📥 ファイルダウンロード")
        st.write("生成された見積書をグループ別にダウンロードできます。")

        if st.session_state.generated_files:
            # ファイルをグループ化
            electrical_mechanical_files = []
            gas_files = []

            for file_info in st.session_state.generated_files:
                if file_info['discipline'] in ['電気設備工事', '機械設備工事']:
                    electrical_mechanical_files.append(file_info)
                elif file_info['discipline'] == 'ガス設備工事':
                    gas_files.append(file_info)

            # 処理時間表示
            if st.session_state.processing_time:
                st.info(f"⏱️ 処理時間: {st.session_state.processing_time:.1f}秒")

            st.markdown("---")

            # グループ1: 電気・機械設備
            st.subheader("📦 電気・機械設備")

            if electrical_mechanical_files:
                # 電気・機械のZIPダウンロード
                zip_buffer = BytesIO()
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

                with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                    for file_info in electrical_mechanical_files:
                        dir_prefix = f"{file_info['spec_name']}/{file_info['discipline']}/"

                        zip_file.write(file_info['fmt_json'], arcname=f"{dir_prefix}{file_info['fmt_json'].name}")
                        for pdf_path in file_info['pdfs']:
                            zip_file.write(pdf_path, arcname=f"{dir_prefix}{Path(pdf_path).name}")
                        if file_info['validation_json']:
                            zip_file.write(file_info['validation_json'], arcname=f"{dir_prefix}{file_info['validation_json'].name}")
                        zip_file.write(file_info['summary'], arcname=f"{dir_prefix}{file_info['summary'].name}")

                zip_buffer.seek(0)

                col1, col2 = st.columns([2, 3])
                with col1:
                    st.download_button(
                        label="📦 電気・機械設備を一括ダウンロード（ZIP）",
                        data=zip_buffer,
                        file_name=f"見積書_電気機械_{timestamp}.zip",
                        mime="application/zip",
                        type="primary"
                    )
                with col2:
                    st.write(f"**含まれるファイル数**: {len(electrical_mechanical_files) * 4}個")

                # 個別ダウンロード
                with st.expander("📁 個別ファイルをダウンロード"):
                    for file_info in electrical_mechanical_files:
                        st.markdown(f"**{file_info['spec_name']} - {file_info['discipline']}**")

                        col1, col2, col3, col4 = st.columns(4)
                        with col1:
                            with open(file_info['fmt_json'], 'rb') as f:
                                st.download_button(
                                    label="📄 JSON",
                                    data=f,
                                    file_name=file_info['fmt_json'].name,
                                    mime="application/json",
                                    key=f"json_{file_info['spec_name']}_{file_info['discipline']}"
                                )
                        with col2:
                            for pdf_path in file_info['pdfs']:
                                with open(pdf_path, 'rb') as f:
                                    st.download_button(
                                        label="📄 PDF",
                                        data=f,
                                        file_name=Path(pdf_path).name,
                                        mime="application/pdf",
                                        key=f"pdf_{file_info['spec_name']}_{file_info['discipline']}"
                                    )
                        with col3:
                            if file_info['validation_json']:
                                with open(file_info['validation_json'], 'rb') as f:
                                    st.download_button(
                                        label="📄 精度検証",
                                        data=f,
                                        file_name=file_info['validation_json'].name,
                                        mime="application/json",
                                        key=f"val_{file_info['spec_name']}_{file_info['discipline']}"
                                    )
                        with col4:
                            with open(file_info['summary'], 'rb') as f:
                                st.download_button(
                                    label="📄 サマリー",
                                    data=f,
                                    file_name=file_info['summary'].name,
                                    mime="text/plain",
                                    key=f"sum_{file_info['spec_name']}_{file_info['discipline']}"
                                )
            else:
                st.info("電気・機械設備のファイルがありません")

            st.markdown("---")

            # グループ2: 都市ガス設備
            st.subheader("📦 都市ガス設備")

            if gas_files:
                # ガスのZIPダウンロード
                zip_buffer = BytesIO()
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

                with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                    for file_info in gas_files:
                        dir_prefix = f"{file_info['spec_name']}/{file_info['discipline']}/"

                        zip_file.write(file_info['fmt_json'], arcname=f"{dir_prefix}{file_info['fmt_json'].name}")
                        for pdf_path in file_info['pdfs']:
                            zip_file.write(pdf_path, arcname=f"{dir_prefix}{Path(pdf_path).name}")
                        if file_info['validation_json']:
                            zip_file.write(file_info['validation_json'], arcname=f"{dir_prefix}{file_info['validation_json'].name}")
                        zip_file.write(file_info['summary'], arcname=f"{dir_prefix}{file_info['summary'].name}")

                zip_buffer.seek(0)

                col1, col2 = st.columns([2, 3])
                with col1:
                    st.download_button(
                        label="📦 都市ガス設備を一括ダウンロード（ZIP）",
                        data=zip_buffer,
                        file_name=f"見積書_都市ガス_{timestamp}.zip",
                        mime="application/zip",
                        type="primary"
                    )
                with col2:
                    st.write(f"**含まれるファイル数**: {len(gas_files) * 4}個")

                # 個別ダウンロード
                with st.expander("📁 個別ファイルをダウンロード"):
                    for file_info in gas_files:
                        st.markdown(f"**{file_info['spec_name']} - {file_info['discipline']}**")

                        col1, col2, col3, col4 = st.columns(4)
                        with col1:
                            with open(file_info['fmt_json'], 'rb') as f:
                                st.download_button(
                                    label="📄 JSON",
                                    data=f,
                                    file_name=file_info['fmt_json'].name,
                                    mime="application/json",
                                    key=f"json_gas_{file_info['spec_name']}_{file_info['discipline']}"
                                )
                        with col2:
                            for pdf_path in file_info['pdfs']:
                                with open(pdf_path, 'rb') as f:
                                    st.download_button(
                                        label="📄 PDF",
                                        data=f,
                                        file_name=Path(pdf_path).name,
                                        mime="application/pdf",
                                        key=f"pdf_gas_{file_info['spec_name']}_{file_info['discipline']}"
                                    )
                        with col3:
                            if file_info['validation_json']:
                                with open(file_info['validation_json'], 'rb') as f:
                                    st.download_button(
                                        label="📄 精度検証",
                                        data=f,
                                        file_name=file_info['validation_json'].name,
                                        mime="application/json",
                                        key=f"val_gas_{file_info['spec_name']}_{file_info['discipline']}"
                                    )
                        with col4:
                            with open(file_info['summary'], 'rb') as f:
                                st.download_button(
                                    label="📄 サマリー",
                                    data=f,
                                    file_name=file_info['summary'].name,
                                    mime="text/plain",
                                    key=f"sum_gas_{file_info['spec_name']}_{file_info['discipline']}"
                                )
            else:
                st.info("都市ガス設備のファイルがありません")

        else:
            st.info("👈 左のタブから仕様書をアップロードして生成を開始してください")


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
                st.info(f"📄 [{file_idx}/{len(uploaded_files)}] {uploaded_file.name}を処理中...")

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

                for i, discipline in enumerate(disciplines, 1):
                    task_counter += 1
                    st.info(f"🔄 [{task_counter}/{total_tasks}] {uploaded_file.name} - {discipline.value}を処理中...")

                    # 見積書を生成
                    if use_ai_generation:
                        # AI自動生成モード（ガス・電気・機械設備対応）
                        if discipline in [DisciplineType.GAS, DisciplineType.ELECTRICAL, DisciplineType.MECHANICAL]:
                            st.write(f"  🤖 AIが仕様書から詳細な見積項目を自動生成中...")
                            st.write(f"  　📚 建物情報を分析中...")
                            st.write(f"  　📊 諸元表・図面データを抽出中...")

                            ai_generator = AIEstimateGenerator(kb_path="kb/price_kb.json")
                            fmt_doc = ai_generator.generate_estimate(
                                tmp_path,
                                discipline
                            )

                            legal_refs = []

                            # 単価マッチング率を計算
                            with_price = sum(1 for item in fmt_doc.estimate_items if item.unit_price is not None)
                            match_rate = with_price / len(fmt_doc.estimate_items) * 100 if fmt_doc.estimate_items else 0

                            st.success(f"  ✅ {len(fmt_doc.estimate_items)}項目を生成（AI自動生成）")
                            st.info(f"  　💰 単価マッチング率: {match_rate:.1f}% ({with_price}/{len(fmt_doc.estimate_items)}項目)")

                        elif discipline in reference_pdfs_map:
                            # 参照見積書ベースにフォールバック
                            st.warning(f"  ⚠️ {discipline.value}はAI自動生成未対応のため、参照見積書ベースで生成します")
                            st.write(f"  📋 参照見積書から詳細な項目・単価を抽出中...")

                            reference_generator = EstimateFromReference()
                            fmt_doc = reference_generator.generate_estimate_from_reference(
                                tmp_path,
                                reference_pdfs_map[discipline],
                                discipline
                            )

                            legal_refs = []
                            st.success(f"  ✅ {len(fmt_doc.estimate_items)}項目を抽出（参照見積書ベース）")

                        else:
                            # 参照見積書もない場合はLLM+RAGにフォールバック
                            st.warning(f"  ⚠️ {discipline.value}はAI自動生成未対応、かつ参照見積書もないため、LLM+RAGで生成します")
                            st.write(f"  📋 仕様書から見積項目を抽出中...")

                            generator = EstimateGeneratorWithLegal(kb_path="kb/price_kb.json")
                            result = generator.generate_estimate_with_legal(
                                tmp_path,
                                disciplines=[discipline],
                                add_welfare_costs=True,
                                validate_legal=False
                            )

                            fmt_doc = result["fmt_doc"]
                            legal_refs = result["legal_refs"]

                            st.success(f"  ✅ {len(fmt_doc.estimate_items)}項目を抽出（LLM+RAG）")

                    elif use_reference and discipline in reference_pdfs_map:
                        # 参照見積書ベースの生成
                        st.write(f"  📋 参照見積書から詳細な項目・単価を抽出中...")

                        reference_generator = EstimateFromReference()
                        fmt_doc = reference_generator.generate_estimate_from_reference(
                            tmp_path,
                            reference_pdfs_map[discipline],
                            discipline
                        )

                        legal_refs = []
                        st.success(f"  ✅ {len(fmt_doc.estimate_items)}項目を抽出（参照見積書ベース）")

                    else:
                        # LLM + RAGベースの生成
                        st.write(f"  📋 仕様書から見積項目を抽出中...")

                        generator = EstimateGeneratorWithLegal(kb_path="kb/price_kb.json")
                        result = generator.generate_estimate_with_legal(
                            tmp_path,
                            disciplines=[discipline],
                            add_welfare_costs=True,
                            validate_legal=enable_legal
                        )

                        fmt_doc = result["fmt_doc"]
                        legal_refs = result["legal_refs"]

                        st.success(f"  ✅ {len(fmt_doc.estimate_items)}項目を抽出")

                    # 結果を保存
                    all_fmt_docs[discipline] = fmt_doc
                    all_legal_refs[discipline] = legal_refs

                    # 精度検証
                    validation_results = None
                    if enable_validation and discipline in reference_pdfs_map:
                        # AI自動生成の場合は参照見積書との比較をスキップ
                        if use_ai_generation:
                            st.info(f"  ℹ️ AI自動生成モードでは独自の品質指標を使用します")

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
                                st.success(f"  ✅ 品質評価: 単価マッチング {match_rate:.1%} / 平均信頼度 {avg_confidence:.1%}")
                            else:
                                st.success(f"  ✅ 品質評価: 単価マッチング率 {match_rate:.1%}")

                        else:
                            st.write(f"  🔍 実際の見積書と比較して精度を検証中...")

                            validator = EstimateValidator()
                            validation_results = validator.validate_estimate(
                                fmt_doc,
                                {discipline: reference_pdfs_map[discipline]}
                            )

                            all_validation_results[discipline] = validation_results

                            # スコアに応じてメッセージ
                            score = validation_results['overall_score']
                            if score >= 0.7:
                                st.success(f"  ✅ 精度検証完了: {score:.1%} - {validation_results['summary']['rating']}")
                            elif score >= 0.5:
                                st.warning(f"  ⚠️ 精度検証完了: {score:.1%} - {validation_results['summary']['rating']}")
                            else:
                                st.error(f"  ❌ 精度検証完了: {score:.1%} - {validation_results['summary']['rating']}")

                    # ファイル出力
                    st.write(f"  💾 結果をoutputディレクトリに保存中...")

                    # 1. FMTDocumentをJSONとして保存
                    fmt_json_path = output_dir / f"見積データ_{spec_name}_{discipline.value}_{mode_name}_{timestamp}.json"
                    with open(fmt_json_path, 'w', encoding='utf-8') as f:
                        json.dump(fmt_doc.model_dump(mode='json'), f, ensure_ascii=False, indent=2)

                    # 2. 見積書PDFを生成
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

                    # 3. 精度検証結果をJSONとして保存
                    validation_json_path = None
                    if validation_results:
                        validation_json_path = output_dir / f"精度検証_{spec_name}_{discipline.value}_{mode_name}_{timestamp}.json"
                        with open(validation_json_path, 'w', encoding='utf-8') as f:
                            json.dump(validation_results, f, ensure_ascii=False, indent=2)

                    # 4. サマリーレポートをテキストファイルとして保存
                    summary_path = output_dir / f"サマリー_{spec_name}_{discipline.value}_{mode_name}_{timestamp}.txt"
                    with open(summary_path, 'w', encoding='utf-8') as f:
                        f.write("=" * 80 + "\n")
                        f.write(f"AI見積書生成システム - 実行サマリー\n")
                        f.write("=" * 80 + "\n\n")

                        f.write(f"【実行情報】\n")
                        f.write(f"  日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                        f.write(f"  モード: {mode_name}\n")
                        f.write(f"  工事区分: {discipline.value}\n")
                        f.write(f"  仕様書: {uploaded_file.name}\n\n")

                        f.write(f"【プロジェクト情報】\n")
                        f.write(f"  工事名: {fmt_doc.project_info.project_name}\n")
                        f.write(f"  場所: {fmt_doc.project_info.location}\n")
                        f.write(f"  顧客: {fmt_doc.project_info.client_name}\n")
                        f.write(f"  期間: {fmt_doc.project_info.contract_period}\n\n")

                        f.write(f"【見積内容】\n")
                        f.write(f"  項目数: {len(fmt_doc.estimate_items)}項目\n")
                        total = sum(item.amount or 0 for item in fmt_doc.estimate_items)
                        f.write(f"  推定総額: ¥{total:,.0f}\n")

                        if fmt_doc.estimate_items:
                            items_with_price = [item for item in fmt_doc.estimate_items if item.unit_price]
                            f.write(f"  単価付与率: {len(items_with_price)/len(fmt_doc.estimate_items):.1%}\n")

                        if not use_reference and legal_refs:
                            f.write(f"  適用法令数: {len(legal_refs)}\n")

                        f.write("\n")

                        if validation_results:
                            f.write(f"【精度検証】\n")
                            f.write(f"  総合スコア: {validation_results['overall_score']:.1%}\n")
                            f.write(f"  評価: {validation_results['summary']['rating']}\n\n")

                            for discipline_name, result in validation_results["disciplines"].items():
                                f.write(f"  {discipline_name}:\n")
                                f.write(f"    スコア: {result['score']:.1%}\n")
                                coverage = result['coverage']
                                f.write(f"    項目カバー率: {coverage['item_coverage']:.1%} ({coverage['generated_count']}/{coverage['reference_count']}項目)\n")
                                f.write(f"    項目マッチング率: {coverage['match_rate']:.1%}\n")
                                amount = result['amount']
                                f.write(f"    金額精度: {amount['accuracy']:.1%}\n")
                                f.write(f"    生成額: ¥{amount['generated_amount']:,.0f}\n")
                                f.write(f"    参照額: ¥{amount['reference_amount']:,.0f}\n")
                                f.write(f"    差額: ¥{amount['difference']:,.0f} ({amount['difference_rate']:.1%})\n")
                                f.write(f"    参照ファイル: {result['reference_file']}\n\n")

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
                        'discipline': discipline.value,
                        'fmt_json': fmt_json_path,
                        'pdfs': renamed_pdf_paths,
                        'validation_json': validation_json_path,
                        'summary': summary_path
                    }
                    st.session_state.generated_files.append(generated_file_info)

                    st.success(f"  ✅ {discipline.value}の処理完了")

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
            st.success("🎉 見積書生成完了！")

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
                st.subheader("📊 精度サマリー")
                for discipline, validation_results in all_validation_results.items():
                    score = validation_results['overall_score']
                    col1, col2 = st.columns([1, 3])
                    with col1:
                        st.write(f"**{discipline.value}**")
                    with col2:
                        st.progress(score, text=f"{score:.1%} - {validation_results['summary']['rating']}")

            # ダウンロードはタブ4で行う
            st.markdown("---")
            st.info("📥 ダウンロードは「ダウンロード」タブをご確認ください")

            if processing_time <= 180:  # 3分
                st.balloons()

        except Exception as e:
            st.error(f"❌ エラーが発生しました: {str(e)}")
            logger.exception("Generation error")
            import traceback
            st.code(traceback.format_exc())


if __name__ == "__main__":
    main()
