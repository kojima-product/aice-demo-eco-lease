"""
見積書作成

仕様書PDFから見積書を自動生成するAIシステム
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
import time

sys.path.insert(0, '.')

from pipelines.logging_config import setup_logging
setup_logging()

from pipelines.schemas import DisciplineType
from pipelines.estimate_generator_with_legal import EstimateGeneratorWithLegal
from pipelines.estimate_validator import EstimateValidator
from pipelines.estimate_from_reference import EstimateFromReference
from pipelines.estimate_generator_ai import AIEstimateGenerator
from pipelines.export import EstimateExporter
from pipelines.cost_tracker import start_session, end_session, get_tracker
from pipelines.inquiry_extractor import InquiryExtractor


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
    /* セクションヘッダー */
    .sidebar-section-header {
        font-size: 0.9rem;
        font-weight: 600;
        margin-bottom: 0.5rem;
        padding-bottom: 0.3rem;
        border-bottom: 1px solid rgba(128, 128, 128, 0.2);
    }
    /* サイドバー要素の横幅統一 */
    [data-testid="stSidebar"] [data-testid="stAlert"],
    [data-testid="stSidebar"] [data-testid="stMetric"],
    [data-testid="stSidebar"] .stProgress {
        width: 100% !important;
        box-sizing: border-box;
    }
    [data-testid="stSidebar"] [data-testid="stAlert"] > div {
        width: 100%;
    }
    /* ステータスカードを全幅に */
    [data-testid="stMarkdownContainer"] > div {
        width: 100%;
    }
</style>
""", unsafe_allow_html=True)


def init_session_state():
    """セッション状態を初期化"""
    defaults = {
        'fmt_doc': None,
        'validation_results': None,
        'processing_time': None,
        'legal_refs': [],
        'generated_files': [],
        'email_info': None,
        'is_processing': False,
        'generation_completed': False,  # 生成完了フラグ
        'pending_files': None,  # 処理待ちファイル
        'pending_include_legal': None,
        'pending_legal_standards': None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def extract_email_info_auto(uploaded_email):
    """メール情報を自動抽出"""
    from pipelines.email_extractor import EmailExtractor

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_email:
        tmp_email.write(uploaded_email.read())
        tmp_email_path = tmp_email.name

    extractor = EmailExtractor()
    email_info = extractor.extract_email_info(tmp_email_path)
    return email_info


def main():
    init_session_state()

    # ヘッダー
    st.title("見積書作成")
    st.caption("仕様書PDFから見積書を自動生成")

    # サイドバー
    with st.sidebar:
        # 単価DB状態
        st.markdown('<p class="sidebar-section-header">単価データベース</p>', unsafe_allow_html=True)
        try:
            with open('kb/price_kb.json', 'r') as f:
                kb_data = json.load(f)
            kb_count = len(kb_data)
            st.caption(f"登録項目: {kb_count:,}件")
        except:
            st.caption("未構築")

        st.markdown("---")

        # 法令設定
        st.markdown('<p class="sidebar-section-header">法令参照設定</p>', unsafe_allow_html=True)
        include_legal = st.checkbox("法令情報を含める", value=True)
        if include_legal:
            legal_standards = st.multiselect(
                "参照法令",
                ["建築基準法", "電気設備技術基準", "ガス事業法", "消防法", "JEAC8001"],
                default=["建築基準法", "電気設備技術基準", "ガス事業法", "消防法", "JEAC8001"],
                label_visibility="collapsed"
            )
        else:
            legal_standards = []

        st.markdown("---")

        # 処理状況
        if st.session_state.is_processing:
            st.info("⏳ 処理中...")
            st.progress(0.5)  # 不定のプログレス表示
        elif st.session_state.generation_completed and st.session_state.fmt_doc:
            st.success("✅ 生成完了")
            items = st.session_state.fmt_doc.estimate_items
            total_amount = sum(item.amount or 0 for item in items if item.level == 0)
            st.metric("推定総額", f"¥{total_amount:,.0f}")
            st.caption("👆「生成結果」タブで詳細を確認できます")
            # フラグをリセット（次回のために）
            st.session_state.generation_completed = False
        elif st.session_state.fmt_doc:
            st.success("✅ 生成完了")
            st.caption("「生成結果」タブで確認")

    # タブで機能を分割
    tab1, tab2, tab3, tab4 = st.tabs(["仕様書アップロード", "生成結果", "要点シート", "ダウンロード"])

    # ===== タブ1: アップロード =====
    with tab1:
        # 仕様書アップロードセクション
        st.markdown("**仕様書PDF**")
        uploaded_files = st.file_uploader(
            "仕様書PDF",
            type=['pdf'],
            accept_multiple_files=True,
            label_visibility="collapsed",
            key="spec_upload",
            help="複数ファイルのアップロードが可能です"
        )

        # アップロード済みファイル表示
        if uploaded_files:
            file_names = ", ".join([f.name for f in uploaded_files])
            st.caption(f"📄 {len(uploaded_files)}ファイル選択済み: {file_names}")

        st.divider()

        # メール情報セクション（折りたたみ）
        with st.expander("メール本文から情報を抽出（任意）", expanded=False):
            uploaded_email = st.file_uploader(
                "メール本文PDF",
                type=['pdf'],
                help="顧客名・工期を自動抽出",
                label_visibility="collapsed",
                key="email_upload"
            )

            # メールPDFがアップロードされたら自動で解析
            if uploaded_email and st.session_state.email_info is None:
                with st.spinner("解析中..."):
                    try:
                        email_info = extract_email_info_auto(uploaded_email)
                        st.session_state.email_info = email_info
                        st.rerun()
                    except Exception as e:
                        st.error(f"解析エラー: {e}")

            # メール情報表示
            if st.session_state.email_info:
                email = st.session_state.email_info
                st.success("メール情報を抽出しました")

                col1, col2 = st.columns(2)
                with col1:
                    st.text(f"顧客: {email.client_company or '-'} {email.client_branch or ''}")
                    st.text(f"担当: {email.client_contact or '-'}")
                    st.text(f"期限: {email.quote_deadline or '-'}")

                with col2:
                    st.text(f"工期: {email.construction_start or '-'} ～ {email.construction_end or '-'}")
                    st.text(f"レンタル: {email.rental_start or '-'} ～ {email.rental_end or '-'}")
                    st.text(f"面積: {email.building_area_tsubo or '-'}坪")

                if st.button("クリア", type="secondary", key="clear_email"):
                    st.session_state.email_info = None
                    st.rerun()

        st.divider()

        # 生成ボタン
        col1, col2, col3 = st.columns([1, 1, 1])
        with col2:
            generate_clicked = False
            if uploaded_files:
                generate_clicked = st.button("見積書を生成", type="primary", disabled=st.session_state.is_processing, use_container_width=True)
            else:
                st.button("見積書を生成", type="primary", disabled=True, use_container_width=True)
                st.caption("仕様書をアップロードしてください")

        # ステータス表示エリア（ボタンの下、全幅で表示）
        status_placeholder = st.empty()

        # ボタンクリック時：ファイルを保存して処理開始フラグを立てて再実行
        if generate_clicked and not st.session_state.is_processing:
            # ファイルをバイトデータとして保存（再実行後も使えるように）
            st.session_state.pending_files = [(f.name, f.read()) for f in uploaded_files]
            st.session_state.pending_include_legal = include_legal
            st.session_state.pending_legal_standards = legal_standards
            st.session_state.is_processing = True
            st.rerun()

        # 処理中の場合：実際の処理を実行
        if st.session_state.is_processing and st.session_state.pending_files:
            generate_estimate_unified(
                st.session_state.pending_files,
                st.session_state.pending_include_legal,
                st.session_state.pending_legal_standards,
                status_placeholder
            )

    # ===== タブ2: 生成結果 =====
    with tab2:
        if st.session_state.fmt_doc and st.session_state.generated_files:
            fmt_doc = st.session_state.fmt_doc
            items = fmt_doc.estimates if hasattr(fmt_doc, 'estimates') else fmt_doc.estimate_items
            total_items = len(items)
            with_price = sum(1 for item in items if item.unit_price and item.unit_price > 0)
            # Level 0（工事区分の親項目）の合計のみを使用（PDFと一致させる）
            total_amount = sum(item.amount or 0 for item in items if item.level == 0)

            # メトリクス（3カラムに変更）
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("生成項目数", f"{total_items}件")
            with col2:
                rate = with_price/total_items*100 if total_items > 0 else 0
                st.metric("単価マッチング率", f"{rate:.0f}%", f"{with_price}/{total_items}件")
            with col3:
                st.metric("推定総額", f"¥{total_amount:,.0f}")

            st.divider()

            # 工事区分別内訳
            st.markdown("**工事区分別内訳**")

            disc_stats = {}
            for item in items:
                disc = item.discipline.value if item.discipline else "その他"
                if disc not in disc_stats:
                    disc_stats[disc] = {'count': 0, 'amount': 0}
                disc_stats[disc]['count'] += 1
                # Level 0（工事区分の親項目）の金額のみを合計（重複計算を防止）
                # Level 1以上は親項目の金額に含まれているため加算しない
                if item.level == 0:
                    disc_stats[disc]['amount'] += item.amount or 0

            # 横並びで表示
            cols = st.columns(len(disc_stats)) if disc_stats else []
            for col, (disc, stats) in zip(cols, sorted(disc_stats.items())):
                with col:
                    st.metric(disc, f"¥{stats['amount']:,.0f}", f"{stats['count']}項目")

            st.divider()

            # 項目一覧
            st.markdown("**生成項目一覧**")

            # データフレーム用にデータ整形
            display_data = []
            for item in items[:100]:  # 最大100件表示
                # 階層に応じたインデント
                indent = "　" * item.level
                display_data.append({
                    "No.": item.item_no if item.item_no else "",
                    "項目名": f"{indent}{item.name}",
                    "仕様": item.specification or "",
                    "数量": item.quantity if item.quantity else "",
                    "単位": item.unit or "",
                    "単価": f"¥{item.unit_price:,.0f}" if item.unit_price else "",
                    "金額": f"¥{item.amount:,.0f}" if item.amount else "",
                })

            st.dataframe(display_data, use_container_width=True, hide_index=True, height=400)

            if len(items) > 100:
                st.caption(f"※ 全{len(items)}件中、100件を表示")

            # 処理時間
            if st.session_state.processing_time:
                st.caption(f"処理時間: {st.session_state.processing_time:.1f}秒")

            # 整合性チェック
            st.divider()
            st.markdown("**整合性チェック**")
            try:
                from pipelines.estimate_validator import EstimateValidator
                validator = EstimateValidator()
                validation_results = validator.validate_estimate(fmt_doc)

                # サマリー表示
                summary = validation_results.get("summary", {})
                col1, col2 = st.columns(2)
                with col1:
                    st.metric("単価/㎡", f"¥{summary.get('amount_per_sqm', 0):,.0f}")
                with col2:
                    status = "✓ 妥当" if validation_results.get("is_valid") else "⚠ 要確認"
                    st.metric("判定", status)

                # 工事区分別チェック
                with st.expander("工事区分別チェック結果", expanded=False):
                    for disc_name, check in validation_results.get("discipline_checks", {}).items():
                        if check["status"] == "ok":
                            st.success(check["message"])
                        elif check["status"] == "warning":
                            st.warning(check["message"])
                        else:
                            st.error(check["message"])

                # 異常項目
                anomalies = validation_results.get("anomaly_items", [])
                if anomalies:
                    with st.expander(f"⚠ 異常項目 ({len(anomalies)}件)", expanded=True):
                        for anomaly in anomalies:
                            st.warning(f"{anomaly['item']}: {anomaly['message']}")

            except Exception as e:
                st.warning(f"整合性チェックエラー: {e}")

            # チェックリスト網羅性
            st.divider()
            st.markdown("**チェックリスト網羅性**")
            checklist_coverage = fmt_doc.metadata.get("checklist_coverage", {})
            if checklist_coverage:
                if isinstance(checklist_coverage, dict):
                    # 複数工事区分の場合
                    if "coverage_rate" in checklist_coverage:
                        # 単一工事区分
                        rate = checklist_coverage.get("coverage_rate", 0) * 100
                        covered = checklist_coverage.get("covered_count", 0)
                        total = checklist_coverage.get("total_check_items", 0)
                        st.metric("カバー率", f"{rate:.0f}%", f"{covered}/{total}項目")
                        missing = checklist_coverage.get("missing_items", [])
                        if missing:
                            with st.expander(f"不足項目 ({len(missing)}件)", expanded=False):
                                for item in missing[:20]:
                                    st.caption(f"・{item}")
                    else:
                        # 複数工事区分
                        cols = st.columns(len(checklist_coverage))
                        for col, (disc, cov) in zip(cols, checklist_coverage.items()):
                            with col:
                                rate = cov.get("coverage_rate", 0) * 100
                                st.metric(disc, f"{rate:.0f}%")

            # ㎡単価検証
            unit_price_checks = fmt_doc.metadata.get("unit_price_checks", {}) or fmt_doc.metadata.get("unit_price_check", {})
            if unit_price_checks:
                st.divider()
                st.markdown("**㎡単価検証**")
                if "is_valid" in unit_price_checks:
                    # 単一
                    msg = unit_price_checks.get("message", "")
                    if unit_price_checks.get("is_valid"):
                        st.success(msg)
                    else:
                        st.warning(msg)
                else:
                    # 複数工事区分
                    for disc, check in unit_price_checks.items():
                        msg = check.get("message", "")
                        if check.get("is_valid"):
                            st.success(f"{disc}: {msg}")
                        else:
                            st.warning(f"{disc}: {msg}")

        else:
            st.info("見積書を生成すると、ここに結果が表示されます。")

    # ===== タブ3: 要点シート（抽出データプレビュー） =====
    with tab3:
        if st.session_state.fmt_doc:
            fmt_doc = st.session_state.fmt_doc

            # プロジェクト情報
            st.markdown("**📋 プロジェクト情報**")
            proj = fmt_doc.project_info

            col1, col2 = st.columns(2)
            with col1:
                st.text(f"工事名: {proj.project_name or '-'}")
                st.text(f"場所: {proj.location or '-'}")
                st.text(f"顧客: {proj.client_name or '-'}")
            with col2:
                st.text(f"期間: {proj.contract_period or '-'}")
                st.text(f"面積: {proj.floor_area_m2 or '-'}㎡")
                st.text(f"決済: {proj.payment_terms or '-'}")

            st.divider()

            # 信頼度サマリー
            st.markdown("**📊 信頼度サマリー**")
            items = fmt_doc.estimates if hasattr(fmt_doc, 'estimates') else fmt_doc.estimate_items

            high_conf = sum(1 for item in items if (item.confidence or 0) >= 0.8)
            mid_conf = sum(1 for item in items if 0.5 <= (item.confidence or 0) < 0.8)
            low_conf = sum(1 for item in items if (item.confidence or 0) < 0.5 and item.confidence is not None)
            no_conf = sum(1 for item in items if item.confidence is None)

            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("高信頼度 (≥0.8)", f"{high_conf}件", help="仕様書に明記されている項目")
            with col2:
                st.metric("中信頼度 (0.5-0.8)", f"{mid_conf}件", delta="要確認", delta_color="off", help="推定値を含む項目")
            with col3:
                st.metric("低信頼度 (<0.5)", f"{low_conf}件", delta="要注意" if low_conf > 0 else None, delta_color="inverse" if low_conf > 0 else "off", help="確認が必要な項目")
            with col4:
                st.metric("未評価", f"{no_conf}件")

            st.divider()

            # 要点シート（項目詳細）
            st.markdown("**📝 抽出項目詳細（信頼度・根拠情報付き）**")

            # フィルタ
            filter_option = st.selectbox(
                "表示フィルタ",
                ["すべて表示", "低信頼度のみ (< 0.8)", "根拠情報あり"],
                label_visibility="collapsed"
            )

            # データ整形
            display_items = []
            for item in items:
                conf = item.confidence or 0

                # フィルタ適用
                if filter_option == "低信頼度のみ (< 0.8)" and conf >= 0.8:
                    continue
                if filter_option == "根拠情報あり" and not item.source_reference:
                    continue

                # 信頼度に応じた表示
                if conf >= 0.8:
                    conf_display = f"✅ {conf:.0%}"
                elif conf >= 0.5:
                    conf_display = f"⚠️ {conf:.0%}"
                elif conf > 0:
                    conf_display = f"🔴 {conf:.0%}"
                else:
                    conf_display = "-"

                # 根拠情報
                source = item.source_reference or ""
                if item.price_references:
                    source += f" [KB: {', '.join(item.price_references[:3])}]"

                indent = "　" * item.level
                display_items.append({
                    "項目名": f"{indent}{item.name}",
                    "仕様": item.specification or "",
                    "数量": item.quantity if item.quantity else "",
                    "単位": item.unit or "",
                    "信頼度": conf_display,
                    "根拠/出典": source[:50] + "..." if len(source) > 50 else source,
                })

            if display_items:
                st.dataframe(display_items, use_container_width=True, hide_index=True, height=400)
                st.caption(f"表示: {len(display_items)}件 / 全{len(items)}件")
            else:
                st.info("該当する項目がありません")

            # RAGヒット詳細（折りたたみ）
            st.divider()
            st.markdown("**🔍 RAGヒット根拠詳細**")

            with st.expander("単価マッチング詳細を表示", expanded=False):
                rag_items = [item for item in items if item.price_references or item.source_reference]

                if rag_items:
                    for item in rag_items[:20]:  # 最大20件表示
                        st.markdown(f"**{item.name}** ({item.specification or '-'})")

                        if item.source_reference:
                            st.caption(f"📌 根拠: {item.source_reference}")

                        if item.price_references:
                            st.caption(f"💰 KB参照: {', '.join(item.price_references)}")

                        if item.unit_price:
                            st.caption(f"単価: ¥{item.unit_price:,.0f}")

                        st.markdown("---")
                else:
                    st.info("RAGヒット情報がありません")

            # JSON表示（折りたたみ）
            with st.expander("生データ（JSON）を表示", expanded=False):
                st.json(fmt_doc.model_dump(mode='json'), expanded=False)
        else:
            st.info("見積書を生成すると、ここに抽出データが表示されます。")

    # ===== タブ4: ダウンロード =====
    with tab4:
        if st.session_state.generated_files:
            all_files = st.session_state.generated_files

            # ZIP一括ダウンロード
            zip_buffer = BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                for file_info in all_files:
                    spec_name = file_info['spec_name']

                    # JSON
                    if file_info.get('fmt_json') and Path(file_info['fmt_json']).exists():
                        zf.write(file_info['fmt_json'], f"{spec_name}/{Path(file_info['fmt_json']).name}")

                    # PDF
                    for pdf_path in file_info.get('pdfs', []):
                        if Path(pdf_path).exists():
                            zf.write(pdf_path, f"{spec_name}/{Path(pdf_path).name}")

                    # Excel
                    if file_info.get('excel') and Path(file_info['excel']).exists():
                        zf.write(file_info['excel'], f"{spec_name}/{Path(file_info['excel']).name}")

                    # 質疑ドラフト
                    if file_info.get('inquiry') and Path(file_info['inquiry']).exists():
                        zf.write(file_info['inquiry'], f"{spec_name}/{Path(file_info['inquiry']).name}")

                    # 法令根拠リスト
                    if file_info.get('legal') and Path(file_info['legal']).exists():
                        zf.write(file_info['legal'], f"{spec_name}/{Path(file_info['legal']).name}")

                    # Summary
                    if file_info.get('summary') and Path(file_info['summary']).exists():
                        zf.write(file_info['summary'], f"{spec_name}/{Path(file_info['summary']).name}")

            zip_buffer.seek(0)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            total_file_count = sum(
                1 + len(f.get('pdfs', [])) + (1 if f.get('excel') else 0) +
                (1 if f.get('inquiry') else 0) + (1 if f.get('legal') else 0) +
                (1 if f.get('summary') else 0)
                for f in all_files
            )

            col1, col2, col3 = st.columns([1, 1, 1])
            with col2:
                st.download_button(
                    f"全ファイルをZIPでダウンロード（{total_file_count}件）",
                    data=zip_buffer,
                    file_name=f"見積書_{timestamp}.zip",
                    mime="application/zip",
                    type="primary",
                    use_container_width=True
                )

            st.divider()

            # 個別ダウンロード
            st.markdown("**個別ダウンロード**")

            for file_info in all_files:
                st.markdown(f"**{file_info['spec_name']}**")

                # 見積関連（1行目）
                st.caption("📋 見積書")
                col1, col2, col3 = st.columns(3)

                with col1:
                    for i, pdf_path in enumerate(file_info.get('pdfs', [])):
                        if Path(pdf_path).exists():
                            with open(pdf_path, 'rb') as f:
                                st.download_button(
                                    "📄 PDF見積書",
                                    data=f,
                                    file_name=Path(pdf_path).name,
                                    mime="application/pdf",
                                    use_container_width=True,
                                    key=f"pdf_{file_info['spec_name']}_{i}"
                                )

                with col2:
                    if file_info.get('excel') and Path(file_info['excel']).exists():
                        with open(file_info['excel'], 'rb') as f:
                            st.download_button(
                                "📊 Excel見積書",
                                data=f,
                                file_name=Path(file_info['excel']).name,
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                use_container_width=True,
                                key=f"excel_{file_info['spec_name']}"
                            )

                with col3:
                    if file_info.get('fmt_json') and Path(file_info['fmt_json']).exists():
                        with open(file_info['fmt_json'], 'rb') as f:
                            st.download_button(
                                "💾 JSONデータ",
                                data=f,
                                file_name=Path(file_info['fmt_json']).name,
                                mime="application/json",
                                use_container_width=True,
                                key=f"json_{file_info['spec_name']}"
                            )

                # 補足資料（2行目）
                st.caption("📑 補足資料")
                col4, col5, col6 = st.columns(3)

                with col4:
                    if file_info.get('inquiry') and Path(file_info['inquiry']).exists():
                        with open(file_info['inquiry'], 'rb') as f:
                            st.download_button(
                                "❓ 質疑ドラフト",
                                data=f,
                                file_name=Path(file_info['inquiry']).name,
                                mime="text/plain",
                                use_container_width=True,
                                key=f"inquiry_{file_info['spec_name']}"
                            )

                with col5:
                    if file_info.get('legal') and Path(file_info['legal']).exists():
                        with open(file_info['legal'], 'rb') as f:
                            st.download_button(
                                "⚖️ 法令根拠リスト",
                                data=f,
                                file_name=Path(file_info['legal']).name,
                                mime="text/plain",
                                use_container_width=True,
                                key=f"legal_{file_info['spec_name']}"
                            )

                with col6:
                    if file_info.get('summary') and Path(file_info['summary']).exists():
                        with open(file_info['summary'], 'rb') as f:
                            st.download_button(
                                "📝 サマリー",
                                data=f,
                                file_name=Path(file_info['summary']).name,
                                mime="text/plain",
                                use_container_width=True,
                                key=f"summary_{file_info['spec_name']}"
                            )

                if file_info != all_files[-1]:
                    st.divider()

        else:
            st.info("見積書を生成すると、ここからダウンロードできます。")


def generate_estimate_unified(
    file_data_list: list,  # [(filename, bytes), ...]
    include_legal: bool,
    legal_standards: list,
    status_card
):
    """統合見積生成"""

    st.session_state.generated_files = []
    start_time = datetime.now()

    # コスト追跡
    session_id = start_session("見積作成（AI統合生成）")

    def show_status(step: int, total: int, title: str, detail: str, status: str = "processing"):
        """ステータスカードを更新表示"""
        if status == "processing":
            border_color = "#2563eb"
            bg_color = "#f8fafc"
            icon = "⏳"
            status_text = "処理中"
        elif status == "success":
            border_color = "#16a34a"
            bg_color = "#f0fdf4"
            icon = "✓"
            status_text = "完了"
        elif status == "error":
            border_color = "#dc2626"
            bg_color = "#fef2f2"
            icon = "✕"
            status_text = "エラー"
        else:
            border_color = "#6b7280"
            bg_color = "#f9fafb"
            icon = "○"
            status_text = ""

        # プログレスバーの幅を計算
        progress_percent = (step / total) * 100

        status_card.markdown(f"""
        <div style="border: 1px solid #e5e7eb; border-left: 4px solid {border_color};
                    background: {bg_color}; padding: 16px 20px; border-radius: 4px; margin: 8px 0;
                    width: 100%; max-width: 100%; box-sizing: border-box;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                <span style="font-size: 15px; font-weight: 600; color: #1f2937;">{icon} {title}</span>
                <span style="font-size: 13px; color: #6b7280;">ステップ {step}/{total}</span>
            </div>
            <div style="font-size: 13px; color: #4b5563; margin-bottom: 12px;">{detail}</div>
            <div style="background: #e5e7eb; border-radius: 2px; height: 4px; overflow: hidden;">
                <div style="background: {border_color}; height: 100%; width: {progress_percent}%; transition: width 0.3s;"></div>
            </div>
        </div>
        """, unsafe_allow_html=True)

    try:
        total_files = len(file_data_list)

        for file_idx, (file_name, file_bytes) in enumerate(file_data_list):
            file_start_time = datetime.now()

            # 一時ファイル保存
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
                tmp_file.write(file_bytes)
                tmp_path = tmp_file.name

            # ===== ステップ1: 仕様書解析 =====
            show_status(1, 6, "仕様書を解析中", f"ファイル: {file_name}", "processing")

            ai_generator = AIEstimateGenerator(kb_path="kb/price_kb.json")

            # ===== ステップ2: 建物情報抽出 =====
            show_status(2, 6, "建物情報を抽出中", "面積・階数・部屋数を特定しています", "processing")
            time.sleep(0.3)

            # ===== ステップ3: 設備項目生成 =====
            show_status(3, 6, "設備項目を生成中", "AIが見積項目を分析・生成しています", "processing")

            fmt_doc = ai_generator.generate_estimate_unified(
                tmp_path,
                legal_standards=legal_standards if include_legal else []
            )

            items = fmt_doc.estimates if hasattr(fmt_doc, 'estimates') else fmt_doc.estimate_items

            # ===== ステップ4: 単価マッチング =====
            show_status(4, 6, "単価をマッチング中", f"{len(items)}項目の単価を検索しています", "processing")

            # メール情報統合
            if st.session_state.email_info:
                email_info = st.session_state.email_info

                if email_info.client_company:
                    fmt_doc.project_info.client_name = f"{email_info.client_company}"
                    if email_info.client_branch:
                        fmt_doc.project_info.client_name += f" {email_info.client_branch}"

                if email_info.construction_start and email_info.construction_end:
                    fmt_doc.project_info.contract_period = f"工期: {email_info.construction_start} ～ {email_info.construction_end}"

                if email_info.rental_start and email_info.rental_end:
                    rental_info = f"レンタル期間: {email_info.rental_start} ～ {email_info.rental_end}"
                    if email_info.rental_months:
                        rental_info += f" ({email_info.rental_months}ヶ月)"
                    if fmt_doc.project_info.contract_period:
                        fmt_doc.project_info.contract_period += f" / {rental_info}"
                    else:
                        fmt_doc.project_info.contract_period = rental_info

                if email_info.quote_deadline:
                    if fmt_doc.project_info.remarks:
                        fmt_doc.project_info.remarks += f"\n見積提出期限: {email_info.quote_deadline}"
                    else:
                        fmt_doc.project_info.remarks = f"見積提出期限: {email_info.quote_deadline}"

            with_price = sum(1 for item in items if item.unit_price and item.unit_price > 0)

            # ===== ステップ5: ファイル生成 =====
            show_status(5, 6, "ファイルを生成中", "PDF・Excel・質疑ドラフトを作成しています", "processing")

            output_dir = Path("output")
            output_dir.mkdir(exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            spec_name = Path(file_name).stem

            # JSON保存
            fmt_json_path = output_dir / f"見積データ_{spec_name}_{timestamp}.json"
            with open(fmt_json_path, 'w', encoding='utf-8') as f:
                json.dump(fmt_doc.model_dump(mode='json'), f, ensure_ascii=False, indent=2)

            # PDF生成
            exporter = EstimateExporter(output_dir=str(output_dir))
            pdf_filename = f"見積書_{spec_name}_{timestamp}.pdf"
            pdf_path = exporter.export_to_pdf(fmt_doc, pdf_filename)

            # Excel生成
            excel_filename = f"見積書_{spec_name}_{timestamp}.xlsx"
            excel_path = exporter.export_to_excel(fmt_doc, excel_filename)

            # 質疑ドラフト生成
            inquiry_extractor = InquiryExtractor(confidence_threshold=0.8)
            inquiries = inquiry_extractor.extract_inquiries(fmt_doc)
            inquiry_draft = inquiry_extractor.generate_inquiry_draft(
                inquiries,
                project_name=fmt_doc.project_info.project_name
            )
            inquiry_path = output_dir / f"質疑ドラフト_{spec_name}_{timestamp}.txt"
            with open(inquiry_path, 'w', encoding='utf-8') as f:
                f.write(inquiry_draft)

            # ===== ステップ6: 法令・サマリー生成 =====
            show_status(6, 6, "最終処理中", "法令根拠リスト・サマリーを生成しています", "processing")

            # 法令根拠リスト生成
            legal_refs = fmt_doc.metadata.get('legal_references', []) or []
            legal_path = None
            if legal_refs or include_legal:
                legal_path = output_dir / f"法令根拠リスト_{spec_name}_{timestamp}.txt"
                with open(legal_path, 'w', encoding='utf-8') as f:
                    f.write("=" * 80 + "\n")
                    f.write("法令根拠リスト\n")
                    f.write("=" * 80 + "\n\n")
                    f.write(f"工事名: {fmt_doc.project_info.project_name}\n")
                    f.write(f"生成日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                    f.write("-" * 80 + "\n")
                    if legal_refs:
                        for i, ref in enumerate(legal_refs, 1):
                            if isinstance(ref, dict):
                                f.write(f"\n【{i}】{ref.get('law_name', '不明')}\n")
                                f.write(f"  条項: {ref.get('article', '-')}\n")
                                f.write(f"  内容: {ref.get('description', '-')}\n")
                            else:
                                f.write(f"\n【{i}】{ref}\n")
                    else:
                        f.write("\n適用法令（参照設定）:\n")
                        for std in legal_standards:
                            f.write(f"  ・{std}\n")
                    f.write("\n" + "-" * 80 + "\n")
                    f.write("※ 本リストはAIにより自動生成されました。\n")

            # サマリー生成
            total_items = len(items)
            # Level 0（工事区分の親項目）の合計のみを使用（PDFと一致させる）
            total_amount = sum(item.amount or 0 for item in items if item.level == 0)

            summary_path = output_dir / f"サマリー_{spec_name}_{timestamp}.txt"
            with open(summary_path, 'w', encoding='utf-8') as f:
                f.write(f"見積生成サマリー\n")
                f.write(f"=" * 50 + "\n\n")
                f.write(f"仕様書: {file_name}\n")
                f.write(f"生成日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                f.write(f"生成項目数: {total_items}件\n")
                f.write(f"単価付与数: {with_price}件\n")
                f.write(f"マッチング率: {with_price/total_items*100:.1f}%\n" if total_items > 0 else "")
                f.write(f"推定総額: ¥{total_amount:,.0f}\n")

            # 結果保存
            st.session_state.generated_files.append({
                'spec_name': spec_name,
                'fmt_json': fmt_json_path,
                'pdfs': [pdf_path] if pdf_path else [],
                'excel': excel_path,
                'inquiry': inquiry_path,
                'legal': legal_path,
                'summary': summary_path,
            })

            st.session_state.fmt_doc = fmt_doc

        # ===== 完了表示 =====
        elapsed = (datetime.now() - start_time).total_seconds()
        st.session_state.processing_time = elapsed

        # 完了ステータス
        total_amount = sum(item.amount or 0 for item in items if item.level == 0)
        show_status(6, 6, "生成完了", f"推定総額: ¥{total_amount:,.0f}　処理時間: {elapsed:.1f}秒", "success")

        # コスト追跡終了
        session_cost = end_session()

        # 完了通知
        st.toast("見積書の生成が完了しました", icon="✅")

        # 完了フラグを設定
        st.session_state.generation_completed = True

    except Exception as e:
        logger.error(f"Generation error: {e}")
        show_status(0, 6, "エラー", str(e), "error")
        import traceback
        traceback.print_exc()

    finally:
        st.session_state.is_processing = False
        st.session_state.pending_files = None
        st.session_state.pending_include_legal = None
        st.session_state.pending_legal_standards = None
        st.rerun()


if __name__ == "__main__":
    main()
else:
    main()
