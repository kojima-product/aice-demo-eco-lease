"""Streamlit UI for Ecolease PoC - 入札見積自動化システム"""

import streamlit as st
from pathlib import Path
import tempfile
import json
from datetime import datetime
from loguru import logger
import PyPDF2

from pipelines.schemas import DisciplineType, FacilityType, PriceReference
from pipelines.kb_builder import EnhancedEstimateExtractor, PriceKBBuilder
from pipelines.estimate_extractor import EstimateExtractor
from pipelines.export import EstimateExporter


# ページ設定
st.set_page_config(
    page_title="Ecolease 入札見積自動化システム",
    page_icon="📄",
    layout="wide"
)


def init_session_state():
    """セッション状態を初期化"""
    if 'fmt_doc' not in st.session_state:
        st.session_state.fmt_doc = None
    if 'processing_time' not in st.session_state:
        st.session_state.processing_time = None
    if 'price_kb' not in st.session_state:
        # 過去見積KBを読み込み
        kb_path = Path("kb/price_kb.json")
        if kb_path.exists():
            with open(kb_path, 'r', encoding='utf-8') as f:
                kb_data = json.load(f)
            st.session_state.price_kb = [PriceReference(**item) for item in kb_data]
        else:
            st.session_state.price_kb = []


def main():
    init_session_state()

    st.title("📄 Ecolease 入札見積自動化システム PoC")
    st.caption("Powered by Claude Sonnet 4.5")
    st.markdown("---")

    # サイドバー
    with st.sidebar:
        st.header("⚙️ 設定")

        use_rag = st.checkbox("過去見積RAG（単価検索）", value=True,
                             help="過去見積KBから類似価格を自動検索")

        show_confidence = st.checkbox("信頼度スコア表示", value=True,
                                     help="各項目の信頼度スコアを表示")

        show_source = st.checkbox("根拠情報表示", value=True,
                                 help="価格の出典（KB ID）を表示")

        # 工事区分選択
        st.markdown("---")
        st.header("🏗️ 工事区分")
        disciplines = st.multiselect(
            "抽出する工事区分を選択",
            options=[
                DisciplineType.GAS,
                DisciplineType.ELECTRICAL,
                DisciplineType.MECHANICAL,
                DisciplineType.HVAC,
                DisciplineType.PLUMBING
            ],
            default=[DisciplineType.GAS],
            format_func=lambda x: x.value
        )

        st.markdown("---")

        st.header("📊 システム情報")
        st.info(f"""
        **使用AI**
        - Claude Sonnet 4.5 (最新)

        **過去見積KB**
        - 登録項目数: {len(st.session_state.price_kb)}件
        - 総額: ¥{sum(ref.unit_price * ref.features.get('quantity', 1) for ref in st.session_state.price_kb):,.0f}

        **目標**
        - 処理時間: 5分以内
        - 信頼度スコア: ≥0.8

        **対応工事区分**
        - ガス・電気・機械
        - 空調・衛生・消防
        """)

    # メインコンテンツ
    tab1, tab2, tab3 = st.tabs(["📤 ファイルアップロード", "📋 見積生成", "📥 出力"])

    with tab1:
        st.header("入札書類のアップロード")

        uploaded_file = st.file_uploader(
            "入札仕様書PDFをアップロード",
            type=['pdf', 'docx', 'xlsx'],
            help="入札仕様書のPDFファイルを選択してください"
        )

        if uploaded_file:
            st.success(f"✅ ファイル: {uploaded_file.name} ({uploaded_file.size:,} bytes)")

            col1, col2 = st.columns([1, 4])
            with col1:
                if st.button("🚀 処理開始", type="primary"):
                    process_document(uploaded_file, disciplines, use_rag, show_confidence, show_source)

    with tab2:
        st.header("見積内容の確認・編集")

        if st.session_state.fmt_doc:
            fmt_doc = st.session_state.fmt_doc

            # 案件情報
            st.subheader("📌 案件情報")
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("案件名", fmt_doc.project_info.project_name)
            with col2:
                st.metric("施設区分", fmt_doc.facility_type.value)
            with col3:
                st.metric("工事区分", f"{len(fmt_doc.disciplines)}種類")

            st.markdown(f"**対象工事**: {', '.join([d.value for d in fmt_doc.disciplines])}")

            # 建物仕様
            if fmt_doc.building_specs:
                st.subheader("🏢 建物仕様")
                for building in fmt_doc.building_specs:
                    with st.expander(f"📐 {building.building_name}"):
                        col1, col2, col3 = st.columns(3)
                        with col1:
                            st.write(f"**延床面積**: {building.total_area}㎡" if building.total_area else "")
                        with col2:
                            st.write(f"**構造**: {building.structure}" if building.structure else "")
                        with col3:
                            st.write(f"**部屋数**: {len(building.rooms)}")

                        if building.rooms:
                            st.write("**部屋一覧**:")
                            room_data = []
                            for room in building.rooms[:10]:  # 最大10件表示
                                room_data.append({
                                    "部屋名": room.room_name,
                                    "面積": f"{room.area}㎡" if room.area else "",
                                    "設備数": len(room.equipment)
                                })
                            st.dataframe(room_data, use_container_width=True)

            # 見積明細
            st.subheader("💰 見積明細")

            if fmt_doc.estimate_items:
                # 統計情報
                col1, col2, col3 = st.columns(3)

                total = sum(item.amount or 0 for item in fmt_doc.estimate_items if item.level == 0)
                with col1:
                    st.metric("**合計金額（税別）**", f"¥{total:,.0f}")

                # 信頼度統計
                items_with_conf = [item for item in fmt_doc.estimate_items if item.confidence is not None]
                if items_with_conf:
                    avg_confidence = sum(item.confidence for item in items_with_conf) / len(items_with_conf)
                    with col2:
                        st.metric("**平均信頼度**", f"{avg_confidence:.2f}")

                    high_conf = sum(1 for item in items_with_conf if item.confidence >= 0.8)
                    with col3:
                        st.metric("**高信頼度項目**", f"{high_conf}/{len(items_with_conf)}")

                # テーブル表示
                estimate_data = []
                for item in fmt_doc.estimate_items:
                    indent = "　" * item.level

                    row = {
                        "No": item.item_no,
                        "名称": f"{indent}{item.name}",
                        "仕様": item.specification or "",
                        "数量": item.quantity if item.quantity else "",
                        "単位": item.unit or "",
                        "単価": f"¥{item.unit_price:,.0f}" if item.unit_price else "",
                        "金額": f"¥{item.amount:,.0f}" if item.amount else "",
                    }

                    # 信頼度スコア表示
                    if show_confidence and item.confidence is not None:
                        conf_indicator = "●" * int(item.confidence * 5)
                        row["信頼度"] = f"{item.confidence:.2f} {conf_indicator}"

                    # 根拠情報表示
                    if show_source and item.source_reference:
                        row["根拠"] = item.source_reference

                    row["摘要"] = item.remarks or ""

                    estimate_data.append(row)

                st.dataframe(estimate_data, use_container_width=True, height=400)

                # 処理時間表示
                if st.session_state.processing_time:
                    st.info(f"⏱️ 処理時間: {st.session_state.processing_time:.2f}秒")

            else:
                st.warning("見積明細が生成されていません")
        else:
            st.info("👈 左のタブから入札書類をアップロードして処理を開始してください")

    with tab3:
        st.header("見積書の出力")

        if st.session_state.fmt_doc:
            st.write("生成された見積書を以下の形式でダウンロードできます")

            col1, col2 = st.columns(2)

            with col1:
                if st.button("📊 Excelファイルを出力", type="primary"):
                    export_excel()

            with col2:
                if st.button("📄 PDFファイルを出力（分野別）"):
                    export_pdf_by_discipline()

            # FMTドキュメントをJSON出力
            with st.expander("🔧 FMTドキュメント（JSON）"):
                st.json(st.session_state.fmt_doc.model_dump(mode='json'))

        else:
            st.info("見積を生成してから出力してください")


def process_document(uploaded_file, disciplines: list, use_rag: bool, show_confidence: bool, show_source: bool):
    """ドキュメントを処理して見積を生成（RAG統合版）"""

    start_time = datetime.now()

    with st.spinner("処理中..."):
        try:
            # 一時ファイルに保存
            with tempfile.NamedTemporaryFile(delete=False, suffix=Path(uploaded_file.name).suffix) as tmp_file:
                tmp_file.write(uploaded_file.read())
                tmp_path = tmp_file.name

            # ステップ1: PDFからテキストを抽出
            st.info("📥 ステップ1: PDFからテキストを抽出中...")
            with open(tmp_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                spec_text = ""
                page_count = len(pdf_reader.pages)
                for page_num in range(min(page_count, 50)):
                    spec_text += pdf_reader.pages[page_num].extract_text() + "\n"

            st.success(f"✅ {page_count}ページから{len(spec_text):,}文字を抽出")

            # ステップ2: プロジェクト情報を抽出
            st.info("🔄 ステップ2: プロジェクト情報をLLMで抽出中...")
            extractor_basic = EstimateExtractor()
            project_info_dict = extractor_basic.extract_project_info(spec_text)

            st.success(f"✅ 工事名: {project_info_dict.get('project_name', 'N/A')}")

            # ステップ3: 見積項目を抽出（信頼度スコア付き）
            all_items = []

            if not use_rag:
                # RAG なしの場合は基本抽出のみ
                st.info("📋 ステップ3: 見積項目をLLMで抽出中...")
                for discipline in disciplines:
                    items = extractor_basic.extract_estimate_items(spec_text, discipline)
                    all_items.extend(items)

                st.success(f"✅ {len(all_items)}項目を抽出")
            else:
                # RAG ありの場合は拡張版を使用
                st.info("📋 ステップ3: 信頼度スコア付きで項目を抽出中...")

                # KB読み込み
                price_kb = st.session_state.price_kb
                extractor_enhanced = EnhancedEstimateExtractor(price_kb)

                for discipline in disciplines:
                    items = extractor_enhanced.extract_with_confidence(spec_text, discipline)
                    all_items.extend(items)

                # 信頼度統計
                if all_items:
                    avg_conf = sum(item.confidence or 0 for item in all_items) / len(all_items)
                    high_conf = sum(1 for item in all_items if item.confidence and item.confidence >= 0.8)
                    st.success(f"✅ {len(all_items)}項目を抽出 (平均信頼度: {avg_conf:.2f}, 高信頼度: {high_conf}項目)")

                # ステップ4: KB単価検索（RAG）
                st.info("🔍 ステップ4: 過去見積KBから単価を検索中...")
                all_items = extractor_enhanced.enrich_with_price_rag(all_items)

                matched = sum(1 for item in all_items if item.unit_price is not None)
                st.success(f"✅ {matched}/{len(all_items)}項目の単価をマッチング")

            # FMTDocumentを作成
            from pipelines.schemas import FMTDocument, ProjectInfo

            project_info = ProjectInfo(
                project_name=project_info_dict.get("project_name", ""),
                client_name=project_info_dict.get("client_name", ""),
                location=project_info_dict.get("location", ""),
                contract_period=project_info_dict.get("contract_period", "")
            )

            fmt_doc = FMTDocument(
                created_at=datetime.now().isoformat(),
                project_info=project_info,
                facility_type=FacilityType.SCHOOL,  # デフォルト
                disciplines=disciplines,
                estimate_items=all_items,
                metadata={
                    "payment_terms": project_info_dict.get("payment_terms", "本紙記載内容のみ有効とする。"),
                    "remarks": project_info_dict.get("remarks", "法定福利費を含む。"),
                    "source": "RAG自動生成" if use_rag else "LLM基本抽出"
                }
            )

            # セッションに保存
            st.session_state.fmt_doc = fmt_doc

            # 処理時間を記録
            end_time = datetime.now()
            processing_time = (end_time - start_time).total_seconds()
            st.session_state.processing_time = processing_time

            # 統計情報
            total = sum(item.amount or 0 for item in all_items if item.amount)

            # 完了メッセージ
            st.success(f"🎉 処理完了！")

            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("処理時間", f"{processing_time:.1f}秒")
            with col2:
                st.metric("抽出項目数", len(all_items))
            with col3:
                st.metric("推定総額", f"¥{total:,.0f}" if total > 0 else "要確認")

            # 目標達成チェック
            if processing_time <= 300:  # 5分
                st.balloons()
                st.success("✅ 目標処理時間（5分以内）を達成！")

        except Exception as e:
            st.error(f"❌ エラーが発生しました: {str(e)}")
            logger.exception("Processing error")
            import traceback
            st.code(traceback.format_exc())


def export_excel():
    """Excelファイルを出力"""

    with st.spinner("Excelファイルを生成中..."):
        try:
            exporter = EstimateExporter()
            output_path = exporter.export_to_excel(st.session_state.fmt_doc)

            st.success(f"✅ Excelファイルを生成しました: {output_path}")

            # ダウンロードボタン
            with open(output_path, 'rb') as f:
                st.download_button(
                    label="📥 Excelファイルをダウンロード",
                    data=f,
                    file_name=Path(output_path).name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )

        except Exception as e:
            st.error(f"❌ Excel出力エラー: {str(e)}")
            logger.exception("Export error")


def export_pdf_by_discipline():
    """PDFファイルを分野別に出力"""

    with st.spinner("PDFファイルを生成中（分野別）..."):
        try:
            exporter = EstimateExporter(output_dir="./output")
            output_paths = exporter.export_to_pdfs_by_discipline(st.session_state.fmt_doc)

            st.success(f"✅ {len(output_paths)}件のPDFファイルを生成しました")

            # 各ファイルのダウンロードボタン
            for output_path in output_paths:
                with open(output_path, 'rb') as f:
                    st.download_button(
                        label=f"📥 {Path(output_path).name}",
                        data=f,
                        file_name=Path(output_path).name,
                        mime="application/pdf",
                        key=output_path
                    )

        except Exception as e:
            st.error(f"❌ PDF出力エラー: {str(e)}")
            logger.exception("PDF export error")
            import traceback
            st.code(traceback.format_exc())


if __name__ == "__main__":
    main()
