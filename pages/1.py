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
from pipelines.estimate_generator_ai import AIEstimateGenerator
from pipelines.export import EstimateExporter
from pipelines.cost_tracker import start_session, end_session
from pipelines.inquiry_extractor import InquiryExtractor


# カスタムCSS（シンプルデザイン）
st.markdown("""
<style>
    /* メインコンテンツのパディング調整 */
    .main .block-container {
        padding-top: 2rem;
        max-width: 1000px;
    }
    /* メトリクスカード */
    [data-testid="stMetricValue"] {
        font-size: 1.8rem;
        font-weight: 700;
        color: #1e40af;
    }
    [data-testid="stMetricLabel"] {
        font-size: 0.9rem;
        color: #64748b;
    }
    /* タブスタイル */
    .stTabs [data-baseweb="tab-list"] {
        gap: 0;
    }
    .stTabs [data-baseweb="tab"] {
        padding: 16px 32px;
        font-weight: 600;
        font-size: 1rem;
    }
    /* カード風ボックス */
    .result-card {
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        padding: 24px;
        margin: 16px 0;
    }
    /* 金額ハイライト */
    .amount-highlight {
        font-size: 2.5rem;
        font-weight: 700;
        color: #1e40af;
        text-align: center;
        padding: 20px;
    }
    /* テーブルヘッダー */
    .dataframe th {
        background: #f1f5f9 !important;
    }
</style>
""", unsafe_allow_html=True)


def init_session_state():
    """セッション状態を初期化"""
    defaults = {
        'fmt_doc': None,
        'processing_time': None,
        'generated_files': [],
        'email_info': None,
        'is_processing': False,
        'generation_completed': False,
        'pending_files': None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def main():
    init_session_state()

    # ===== サイドバー =====
    with st.sidebar:
        st.markdown("### 単価KB状況")

        # KB情報を読み込み
        try:
            import json
            kb_path = Path("kb/price_kb.json")
            if kb_path.exists():
                with open(kb_path, 'r', encoding='utf-8') as f:
                    kb_items = json.load(f)

                col1, col2 = st.columns(2)
                with col1:
                    st.metric("登録項目数", f"{len(kb_items):,}")
                with col2:
                    disciplines = set(item.get('discipline', '') for item in kb_items)
                    st.metric("工事区分", f"{len(disciplines)}種類")

                # 工事区分別内訳
                st.markdown('<p style="font-size: 0.85rem; font-weight: 600; margin-top: 1rem; margin-bottom: 0.5rem; border-bottom: 1px solid rgba(128,128,128,0.2); padding-bottom: 0.3rem;">工事区分別</p>', unsafe_allow_html=True)
                discipline_counts = {}
                for item in kb_items:
                    d = item.get('discipline', '不明')
                    discipline_counts[d] = discipline_counts.get(d, 0) + 1

                for discipline, count in sorted(discipline_counts.items(), key=lambda x: -x[1])[:5]:
                    st.text(f"• {discipline}: {count}件")
            else:
                st.warning("KBが空です")
        except Exception as e:
            st.error(f"KB読込エラー: {e}")

        st.markdown("---")

        # 生成オプション
        st.markdown('<p style="font-size: 0.85rem; font-weight: 600; margin-bottom: 0.5rem;">生成オプション</p>', unsafe_allow_html=True)

        st.checkbox(
            "カテゴリ階層を適用",
            value=True,
            key="use_category_hierarchy",
            help="見積項目をカテゴリ別に整理します"
        )

        st.checkbox(
            "類似案件と比較",
            value=True,
            key="compare_similar",
            help="過去の類似プロジェクトと比較します"
        )

        st.markdown("---")

        # 処理状況
        st.markdown('<p style="font-size: 0.85rem; font-weight: 600; margin-bottom: 0.5rem;">処理状況</p>', unsafe_allow_html=True)

        if st.session_state.fmt_doc:
            items = st.session_state.fmt_doc.estimate_items
            total = sum((i.amount or 0) for i in items if i.level == 0)
            st.metric("生成項目数", len(items))
            st.metric("推定総額", f"¥{total:,.0f}")
        else:
            st.caption("まだ見積書は生成されていません")

        st.markdown("---")
        st.caption("v2.0 - AI見積システム (Opus 4.5)")

    # ヘッダー
    st.title("見積書作成")
    st.caption("仕様書PDFをアップロードすると、AIが自動で見積書を作成します")

    # タブ構成（3つにシンプル化）
    tab1, tab2, tab3 = st.tabs(["📤 アップロード", "📊 見積結果", "📥 ダウンロード"])

    # ===== タブ1: アップロード =====
    with tab1:
        st.markdown("### 仕様書をアップロード")

        uploaded_files = st.file_uploader(
            "仕様書PDF",
            type=['pdf'],
            accept_multiple_files=True,
            label_visibility="collapsed",
            key="spec_upload",
            help="複数ファイルのアップロードが可能です"
        )

        if uploaded_files:
            st.success(f"✓ {len(uploaded_files)}件のファイルを選択しました")
            for f in uploaded_files:
                st.caption(f"　📄 {f.name}")

        st.markdown("---")

        # メール情報セクション（オプション）
        with st.expander("メール本文から顧客情報を抽出（任意）", expanded=False):
            uploaded_email = st.file_uploader(
                "メール本文PDF",
                type=['pdf'],
                help="顧客名・工期を自動抽出します",
                label_visibility="collapsed",
                key="email_upload"
            )

            if uploaded_email and st.session_state.email_info is None:
                with st.spinner("解析中..."):
                    try:
                        from pipelines.email_extractor import EmailExtractor
                        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                            tmp.write(uploaded_email.read())
                            tmp_path = tmp.name
                        extractor = EmailExtractor()
                        st.session_state.email_info = extractor.extract_email_info(tmp_path)
                        st.rerun()
                    except Exception as e:
                        st.error(f"解析エラー: {e}")

            if st.session_state.email_info:
                email = st.session_state.email_info
                st.success("✓ メール情報を取得しました")
                st.text(f"顧客: {email.client_company or '-'}")
                st.text(f"工期: {email.construction_start or '-'} ～ {email.construction_end or '-'}")

                if st.button("クリア", key="clear_email"):
                    st.session_state.email_info = None
                    st.rerun()

        st.markdown("---")

        # 生成ボタン / 完了ステータス
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            generate_clicked = False

            # 生成完了後は完了ステータスを表示
            if st.session_state.generation_completed and st.session_state.generated_files:
                st.markdown("""
                <div style="text-align: center; padding: 20px; background: linear-gradient(135deg, #22c55e 0%, #16a34a 100%); border-radius: 12px;">
                    <p style="color: white; font-size: 1.2rem; font-weight: 600; margin: 0;">✓ 見積書の生成が完了しました</p>
                    <p style="color: rgba(255,255,255,0.8); font-size: 0.9rem; margin-top: 8px;">「見積結果」「ダウンロード」タブで確認できます</p>
                </div>
                """, unsafe_allow_html=True)

                # 新規作成ボタン
                if st.button("新しい見積書を作成", use_container_width=True):
                    st.session_state.generation_completed = False
                    st.session_state.fmt_doc = None
                    st.session_state.generated_files = []
                    st.rerun()

            elif uploaded_files:
                generate_clicked = st.button(
                    "見積書を生成",
                    type="primary",
                    disabled=st.session_state.is_processing,
                    use_container_width=True
                )
            else:
                st.button("見積書を生成", type="primary", disabled=True, use_container_width=True)
                st.caption("↑ 仕様書をアップロードしてください")

        # ステータス表示
        status_placeholder = st.empty()

        # 生成処理
        if generate_clicked and not st.session_state.is_processing:
            st.session_state.pending_files = [(f.name, f.read()) for f in uploaded_files]
            st.session_state.is_processing = True
            st.rerun()

        if st.session_state.is_processing and st.session_state.pending_files:
            generate_estimate(st.session_state.pending_files, status_placeholder)

    # ===== タブ2: 見積結果 =====
    with tab2:
        if st.session_state.fmt_doc and st.session_state.generated_files:
            fmt_doc = st.session_state.fmt_doc
            items = fmt_doc.estimates if hasattr(fmt_doc, 'estimates') else fmt_doc.estimate_items

            # Level 0の合計
            total_amount = sum(item.amount or 0 for item in items if item.level == 0)

            # 推定総額（大きく表示）
            st.markdown(f"""
            <div style="text-align: center; padding: 30px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border-radius: 16px; margin-bottom: 24px;">
                <p style="color: rgba(255,255,255,0.8); font-size: 1rem; margin-bottom: 8px;">推定総額</p>
                <p style="color: white; font-size: 3rem; font-weight: 700; margin: 0;">¥{total_amount:,.0f}</p>
            </div>
            """, unsafe_allow_html=True)

            # 工事区分別内訳
            st.markdown("### 工事区分別内訳")

            disc_stats = {}
            for item in items:
                disc = item.discipline.value if item.discipline else "その他"
                if disc not in disc_stats:
                    disc_stats[disc] = {'count': 0, 'amount': 0}
                disc_stats[disc]['count'] += 1
                if item.level == 0:
                    disc_stats[disc]['amount'] += item.amount or 0

            if disc_stats:
                cols = st.columns(len(disc_stats))
                for col, (disc, stats) in zip(cols, sorted(disc_stats.items())):
                    with col:
                        # 短い工事区分名に変換
                        short_name = disc.replace("設備工事", "")
                        st.metric(short_name, f"¥{stats['amount']:,.0f}")

            st.markdown("---")

            # 項目一覧（シンプル版）
            st.markdown("### 見積項目一覧")

            display_data = []
            for item in items:
                if item.level <= 1:  # 大項目・中項目のみ表示
                    indent = "　　" if item.level == 1 else ""
                    display_data.append({
                        "項目": f"{indent}{item.name}",
                        "金額": f"¥{item.amount:,.0f}" if item.amount else "-",
                    })

            if display_data:
                st.dataframe(
                    display_data,
                    use_container_width=True,
                    hide_index=True,
                    height=min(400, len(display_data) * 40 + 40)
                )

            # 詳細表示（折りたたみ）
            with st.expander("詳細項目を表示", expanded=False):
                detail_data = []
                for item in items:
                    indent = "　" * item.level
                    detail_data.append({
                        "項目名": f"{indent}{item.name}",
                        "仕様": item.specification or "",
                        "数量": f"{item.quantity:,.0f}" if item.quantity else "",
                        "単位": item.unit or "",
                        "単価": f"¥{item.unit_price:,.0f}" if item.unit_price else "",
                        "金額": f"¥{item.amount:,.0f}" if item.amount else "",
                    })
                st.dataframe(detail_data, use_container_width=True, hide_index=True, height=400)

            # ===== 📊 見積の作り方（役員・営業向けサマリー）=====
            st.markdown("---")
            st.markdown("### 📊 この見積はどのように作られたか")

            # 処理フローの説明
            st.markdown("""
            <div style="background: linear-gradient(135deg, #f0f9ff 0%, #e0f2fe 100%); border-radius: 12px; padding: 20px; margin: 16px 0;">
                <h4 style="color: #0369a1; margin-bottom: 16px;">🔄 AIの見積作成プロセス</h4>
                <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
                    <div style="text-align: center; flex: 1; min-width: 120px;">
                        <div style="background: white; border-radius: 8px; padding: 12px; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
                            <div style="font-size: 1.5rem;">📄</div>
                            <div style="font-size: 0.85rem; font-weight: 600;">仕様書</div>
                            <div style="font-size: 0.75rem; color: #64748b;">PDFを解析</div>
                        </div>
                    </div>
                    <div style="font-size: 1.5rem; color: #0284c7;">→</div>
                    <div style="text-align: center; flex: 1; min-width: 120px;">
                        <div style="background: white; border-radius: 8px; padding: 12px; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
                            <div style="font-size: 1.5rem;">🏗️</div>
                            <div style="font-size: 0.85rem; font-weight: 600;">建物情報抽出</div>
                            <div style="font-size: 0.75rem; color: #64748b;">面積・用途を特定</div>
                        </div>
                    </div>
                    <div style="font-size: 1.5rem; color: #0284c7;">→</div>
                    <div style="text-align: center; flex: 1; min-width: 120px;">
                        <div style="background: white; border-radius: 8px; padding: 12px; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
                            <div style="font-size: 1.5rem;">📋</div>
                            <div style="font-size: 0.85rem; font-weight: 600;">項目生成</div>
                            <div style="font-size: 0.75rem; color: #64748b;">テンプレート適用</div>
                        </div>
                    </div>
                    <div style="font-size: 1.5rem; color: #0284c7;">→</div>
                    <div style="text-align: center; flex: 1; min-width: 120px;">
                        <div style="background: white; border-radius: 8px; padding: 12px; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
                            <div style="font-size: 1.5rem;">💰</div>
                            <div style="font-size: 0.85rem; font-weight: 600;">単価マッチング</div>
                            <div style="font-size: 0.75rem; color: #64748b;">過去実績から検索</div>
                        </div>
                    </div>
                    <div style="font-size: 1.5rem; color: #0284c7;">→</div>
                    <div style="text-align: center; flex: 1; min-width: 120px;">
                        <div style="background: white; border-radius: 8px; padding: 12px; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
                            <div style="font-size: 1.5rem;">✅</div>
                            <div style="font-size: 0.85rem; font-weight: 600;">見積完成</div>
                            <div style="font-size: 0.75rem; color: #64748b;">PDF/Excel出力</div>
                        </div>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)

            # データソースのサマリー
            st.markdown("#### 📁 使用したデータソース")

            # 統計を計算
            kb_matched = sum(1 for i in items if getattr(i, 'source_reference', None) and 'KB:' in str(getattr(i, 'source_reference', '')))
            template_items = sum(1 for i in items if getattr(i, 'source_type', '') == 'template')
            total_detail_items = sum(1 for i in items if i.level >= 2)
            match_rate = (kb_matched / total_detail_items * 100) if total_detail_items > 0 else 0

            col1, col2, col3 = st.columns(3)
            with col1:
                st.markdown(f"""
                <div style="background: #f0fdf4; border-radius: 8px; padding: 16px; text-align: center;">
                    <div style="font-size: 1.8rem; font-weight: 700; color: #16a34a;">{match_rate:.0f}%</div>
                    <div style="font-size: 0.9rem; color: #15803d;">単価マッチング率</div>
                    <div style="font-size: 0.75rem; color: #64748b; margin-top: 4px;">過去見積KBから取得</div>
                </div>
                """, unsafe_allow_html=True)
            with col2:
                st.markdown(f"""
                <div style="background: #eff6ff; border-radius: 8px; padding: 16px; text-align: center;">
                    <div style="font-size: 1.8rem; font-weight: 700; color: #2563eb;">{template_items}</div>
                    <div style="font-size: 0.9rem; color: #1d4ed8;">テンプレート項目</div>
                    <div style="font-size: 0.75rem; color: #64748b; margin-top: 4px;">建物タイプ別テンプレート</div>
                </div>
                """, unsafe_allow_html=True)
            with col3:
                st.markdown(f"""
                <div style="background: #faf5ff; border-radius: 8px; padding: 16px; text-align: center;">
                    <div style="font-size: 1.8rem; font-weight: 700; color: #7c3aed;">{len(items)}</div>
                    <div style="font-size: 0.9rem; color: #6d28d9;">総項目数</div>
                    <div style="font-size: 0.75rem; color: #64748b; margin-top: 4px;">親項目含む</div>
                </div>
                """, unsafe_allow_html=True)

            # 数量算出根拠の説明
            st.markdown("#### 📐 数量の算出方法")
            st.markdown("""
            <div style="background: #f8fafc; border-radius: 8px; padding: 16px;">
                <table style="width: 100%; border-collapse: collapse;">
                    <tr style="border-bottom: 1px solid #e2e8f0;">
                        <th style="text-align: left; padding: 8px; color: #64748b;">算出方法</th>
                        <th style="text-align: left; padding: 8px; color: #64748b;">説明</th>
                        <th style="text-align: left; padding: 8px; color: #64748b;">例</th>
                    </tr>
                    <tr style="border-bottom: 1px solid #e2e8f0;">
                        <td style="padding: 8px;"><strong>床面積ベース</strong></td>
                        <td style="padding: 8px;">建物の床面積から配管長等を推定</td>
                        <td style="padding: 8px; color: #64748b;">82㎡ × 0.15/㎡ = 12m</td>
                    </tr>
                    <tr style="border-bottom: 1px solid #e2e8f0;">
                        <td style="padding: 8px;"><strong>固定数量</strong></td>
                        <td style="padding: 8px;">設備として必要な最低数</td>
                        <td style="padding: 8px; color: #64748b;">給水バルブ = 3ヶ所</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px;"><strong>仕様書記載</strong></td>
                        <td style="padding: 8px;">仕様書に明記された数量</td>
                        <td style="padding: 8px; color: #64748b;">照明器具 = 10台</td>
                    </tr>
                </table>
            </div>
            """, unsafe_allow_html=True)

            # 単価取得元の説明
            st.markdown("#### 💴 単価の取得元")

            # KB内のユニークなプロジェクト
            source_projects = set()
            for item in items:
                ref = getattr(item, 'source_reference', '') or ''
                if 'KB:' in ref:
                    source_projects.add(ref.split('[')[0].replace('KB:', ''))

            st.markdown(f"""
            <div style="background: #f8fafc; border-radius: 8px; padding: 16px;">
                <p style="margin-bottom: 12px;"><strong>過去見積データベース（KB）に登録された実績単価を使用しています。</strong></p>
                <p style="color: #64748b; font-size: 0.9rem;">参照した過去案件（一部）:</p>
                <ul style="margin: 8px 0; color: #64748b;">
            """, unsafe_allow_html=True)

            for proj in list(source_projects)[:5]:
                st.markdown(f"<li>{proj}</li>", unsafe_allow_html=True)

            st.markdown("</ul></div>", unsafe_allow_html=True)

            # 詳細項目別の根拠（折りたたみ）
            with st.expander("🔍 項目別の詳細根拠を表示", expanded=False):
                detail_basis = []
                for item in items:
                    if item.level >= 2 and item.unit_price:
                        qty_basis = getattr(item, 'estimation_basis', None) or "仕様書から推定"
                        source_ref = getattr(item, 'source_reference', None) or "未マッチ"
                        detail_basis.append({
                            "項目名": item.name,
                            "仕様": item.specification or "",
                            "数量": f"{item.quantity:,.0f} {item.unit or ''}" if item.quantity else "-",
                            "数量根拠": qty_basis,
                            "単価": f"¥{item.unit_price:,.0f}" if item.unit_price else "-",
                            "単価元": source_ref.replace('KB:', '').split('[')[0] if source_ref else "-"
                        })
                if detail_basis:
                    st.dataframe(detail_basis, use_container_width=True, hide_index=True, height=300)

            # ===== 人間見積との比較 =====
            st.markdown("---")
            st.markdown("### 📈 人間見積との比較")

            # KBから同じプロジェクト名の人間見積を検索
            try:
                kb_path = Path("kb/price_kb.json")
                if kb_path.exists():
                    with open(kb_path, 'r', encoding='utf-8') as f:
                        kb_items = json.load(f)

                    # プロジェクト名で検索
                    project_name = fmt_doc.project_info.project_name if fmt_doc.project_info else ""
                    search_keywords = []
                    if project_name:
                        # キーワード抽出
                        for keyword in ["バイオ", "発電", "学校", "高校", "中学", "小学", "プラント"]:
                            if keyword in project_name:
                                search_keywords.append(keyword)

                    # マッチするKB項目を検索
                    human_items = []
                    for item in kb_items:
                        source = item.get('source_project', '')
                        if any(kw in source for kw in search_keywords) or any(kw in source for kw in project_name.split()[:3]):
                            human_items.append(item)

                    if human_items:
                        # 人間見積の集計
                        human_by_disc = {}
                        for item in human_items:
                            d = item.get('discipline', 'その他')
                            if d not in human_by_disc:
                                human_by_disc[d] = {'count': 0, 'total': 0}
                            human_by_disc[d]['count'] += 1
                            price = item.get('unit_price', 0) or 0
                            qty = item.get('features', {}).get('quantity', 0) or 0
                            human_by_disc[d]['total'] += price * qty

                        human_total = sum(v['total'] for v in human_by_disc.values())
                        human_count = sum(v['count'] for v in human_by_disc.values())

                        # AI見積との比較
                        ai_total = total_amount
                        ai_count = len(items)
                        diff_amount = ai_total - human_total
                        diff_pct = ((ai_total / human_total) - 1) * 100 if human_total > 0 else 0

                        # 比較表示
                        st.markdown(f"""
                        <div style="background: {'#fef2f2' if abs(diff_pct) > 20 else '#f0fdf4' if abs(diff_pct) < 10 else '#fffbeb'}; border-radius: 12px; padding: 20px; margin: 16px 0;">
                            <h4 style="color: {'#dc2626' if abs(diff_pct) > 20 else '#16a34a' if abs(diff_pct) < 10 else '#d97706'}; margin-bottom: 16px;">
                                {'⚠️ 乖離あり' if abs(diff_pct) > 20 else '✅ 良好' if abs(diff_pct) < 10 else '⚡ 要確認'}
                                （差異: {diff_pct:+.1f}%）
                            </h4>
                            <div style="display: flex; justify-content: space-around; gap: 20px; flex-wrap: wrap;">
                                <div style="text-align: center; flex: 1; min-width: 150px;">
                                    <div style="font-size: 0.9rem; color: #64748b; margin-bottom: 4px;">AI見積</div>
                                    <div style="font-size: 1.5rem; font-weight: 700; color: #1e40af;">¥{ai_total:,.0f}</div>
                                    <div style="font-size: 0.8rem; color: #64748b;">{ai_count}項目</div>
                                </div>
                                <div style="text-align: center; flex: 1; min-width: 150px;">
                                    <div style="font-size: 0.9rem; color: #64748b; margin-bottom: 4px;">人間見積</div>
                                    <div style="font-size: 1.5rem; font-weight: 700; color: #059669;">¥{human_total:,.0f}</div>
                                    <div style="font-size: 0.8rem; color: #64748b;">{human_count}項目</div>
                                </div>
                                <div style="text-align: center; flex: 1; min-width: 150px;">
                                    <div style="font-size: 0.9rem; color: #64748b; margin-bottom: 4px;">差額</div>
                                    <div style="font-size: 1.5rem; font-weight: 700; color: {'#dc2626' if diff_amount > 0 else '#059669'};">¥{diff_amount:+,.0f}</div>
                                    <div style="font-size: 0.8rem; color: #64748b;">({diff_pct:+.1f}%)</div>
                                </div>
                            </div>
                        </div>
                        """, unsafe_allow_html=True)

                        # 工事区分別比較
                        with st.expander("工事区分別の比較を表示", expanded=False):
                            comparison_data = []
                            for disc in set(list(human_by_disc.keys()) + list(disc_stats.keys())):
                                h_data = human_by_disc.get(disc, {'count': 0, 'total': 0})
                                a_data = disc_stats.get(disc, {'count': 0, 'amount': 0})
                                diff = a_data['amount'] - h_data['total']
                                diff_p = ((a_data['amount'] / h_data['total']) - 1) * 100 if h_data['total'] > 0 else 0
                                comparison_data.append({
                                    "工事区分": disc,
                                    "人間見積": f"¥{h_data['total']:,.0f}",
                                    "AI見積": f"¥{a_data['amount']:,.0f}",
                                    "差額": f"¥{diff:+,.0f}",
                                    "差異率": f"{diff_p:+.1f}%"
                                })
                            st.dataframe(comparison_data, use_container_width=True, hide_index=True)

                    else:
                        st.info("📊 KBに同じプロジェクトの人間見積データがありません。比較できませんが、参考として他の類似案件と比較しています。")

            except Exception as e:
                st.warning(f"人間見積との比較でエラー: {e}")

            # 類似案件比較セクション
            if fmt_doc.metadata and fmt_doc.metadata.get("similar_projects"):
                similar_info = fmt_doc.metadata["similar_projects"]
                with st.expander("🔍 類似案件との比較", expanded=False):
                    similar_projects = similar_info.get("similar_projects", [])
                    if similar_projects:
                        st.markdown("#### 類似プロジェクト")
                        for idx, proj in enumerate(similar_projects[:3], 1):
                            score = proj.get("similarity_score", 0)
                            score_color = "#22c55e" if score > 0.5 else "#f59e0b" if score > 0.3 else "#ef4444"
                            st.markdown(f"""
                            <div style="background: #f8fafc; border-left: 3px solid {score_color}; padding: 10px; margin: 5px 0; border-radius: 4px;">
                                <strong>{idx}. {proj.get('project_name', '不明')}</strong><br/>
                                <span style="color: {score_color};">類似度: {score*100:.0f}%</span> |
                                項目数: {proj.get('item_count', 0)} |
                                {', '.join(proj.get('match_reasons', [])[:2])}
                            </div>
                            """, unsafe_allow_html=True)

                    # 比較結果
                    comparison = similar_info.get("comparison", {})
                    if comparison and not comparison.get("error"):
                        st.markdown("#### 見積比較")
                        col1, col2, col3 = st.columns(3)
                        with col1:
                            st.metric("現在の項目数", comparison.get("current_item_count", 0))
                        with col2:
                            st.metric("参照の項目数", comparison.get("reference_item_count", 0))
                        with col3:
                            diff_pct = comparison.get("total_diff_percent", 0)
                            st.metric("金額差", f"{diff_pct:+.1f}%", delta_color="inverse" if diff_pct > 10 else "normal")

                        # 不足項目
                        missing = comparison.get("missing_from_current", [])
                        if missing:
                            st.markdown("**参照見積にあるが現在の見積にない項目:**")
                            st.code(", ".join(missing[:5]) + ("..." if len(missing) > 5 else ""))

            # 処理時間
            if st.session_state.processing_time:
                st.caption(f"処理時間: {st.session_state.processing_time:.1f}秒")

        else:
            st.info("「アップロード」タブで仕様書をアップロードし、「見積書を生成」ボタンを押してください。")

    # ===== タブ3: ダウンロード =====
    with tab3:
        if st.session_state.generated_files:
            st.markdown("### 生成されたファイル")

            # 一括ダウンロード
            zip_buffer = BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                for file_info in st.session_state.generated_files:
                    spec_name = file_info['spec_name']

                    for key in ['fmt_json', 'excel', 'inquiry', 'summary']:
                        path = file_info.get(key)
                        if path and Path(path).exists():
                            zf.write(path, f"{spec_name}/{Path(path).name}")

                    for pdf_path in file_info.get('pdfs', []):
                        if Path(pdf_path).exists():
                            zf.write(pdf_path, f"{spec_name}/{Path(pdf_path).name}")

            zip_buffer.seek(0)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            col1, col2, col3 = st.columns([1, 2, 1])
            with col2:
                st.download_button(
                    "📦 すべてダウンロード（ZIP）",
                    data=zip_buffer,
                    file_name=f"見積書_{timestamp}.zip",
                    mime="application/zip",
                    type="primary",
                    use_container_width=True
                )

            st.markdown("---")

            # 個別ダウンロード
            st.markdown("### 個別ダウンロード")

            for file_info in st.session_state.generated_files:
                st.markdown(f"**{file_info['spec_name']}**")

                col1, col2, col3 = st.columns(3)

                with col1:
                    for i, pdf_path in enumerate(file_info.get('pdfs', [])):
                        if Path(pdf_path).exists():
                            with open(pdf_path, 'rb') as f:
                                st.download_button(
                                    "📄 見積書PDF",
                                    data=f,
                                    file_name=Path(pdf_path).name,
                                    mime="application/pdf",
                                    use_container_width=True,
                                    key=f"pdf_{file_info['spec_name']}_{i}"
                                )

                with col2:
                    excel_path = file_info.get('excel')
                    if excel_path and Path(excel_path).exists():
                        with open(excel_path, 'rb') as f:
                            st.download_button(
                                "📊 Excel",
                                data=f,
                                file_name=Path(excel_path).name,
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                use_container_width=True,
                                key=f"excel_{file_info['spec_name']}"
                            )

                with col3:
                    inquiry_path = file_info.get('inquiry')
                    if inquiry_path and Path(inquiry_path).exists():
                        with open(inquiry_path, 'rb') as f:
                            st.download_button(
                                "❓ 質疑ドラフト",
                                data=f,
                                file_name=Path(inquiry_path).name,
                                mime="text/plain",
                                use_container_width=True,
                                key=f"inquiry_{file_info['spec_name']}"
                            )

                st.markdown("")

        else:
            st.info("見積書を生成すると、ここからダウンロードできます。")


def generate_estimate(file_data_list: list, status_card):
    """見積生成処理"""

    st.session_state.generated_files = []
    start_time = datetime.now()

    session_id = start_session("見積作成")

    def show_status(step: int, total: int, message: str, status: str = "processing"):
        """シンプルなステータス表示"""
        if status == "processing":
            icon = "⏳"
            color = "#3b82f6"
        elif status == "success":
            icon = "✓"
            color = "#22c55e"
        else:
            icon = "✕"
            color = "#ef4444"

        progress = (step / total) * 100
        status_card.markdown(f"""
        <div style="border: 1px solid #e5e7eb; border-left: 4px solid {color};
                    padding: 20px; border-radius: 8px; margin: 16px 0;">
            <div style="display: flex; justify-content: space-between; margin-bottom: 12px;">
                <span style="font-size: 16px; font-weight: 600;">{icon} {message}</span>
                <span style="color: #6b7280;">ステップ {step}/{total}</span>
            </div>
            <div style="background: #e5e7eb; border-radius: 4px; height: 6px;">
                <div style="background: {color}; height: 100%; width: {progress}%; border-radius: 4px;"></div>
            </div>
        </div>
        """, unsafe_allow_html=True)

    try:
        for file_idx, (file_name, file_bytes) in enumerate(file_data_list):
            # 一時ファイル作成
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
                tmp_file.write(file_bytes)
                tmp_path = tmp_file.name

            # ステップ1: 仕様書解析
            show_status(1, 4, "仕様書を解析しています...", "processing")
            ai_generator = AIEstimateGenerator(kb_path="kb/price_kb.json")

            # ステップ2: 見積生成
            show_status(2, 4, "見積項目を生成しています...", "processing")
            fmt_doc = ai_generator.generate_estimate_unified(tmp_path, legal_standards=[])
            items = fmt_doc.estimates if hasattr(fmt_doc, 'estimates') else fmt_doc.estimate_items

            # メール情報統合
            if st.session_state.email_info:
                email = st.session_state.email_info
                if email.client_company:
                    fmt_doc.project_info.client_name = email.client_company
                if email.construction_start and email.construction_end:
                    fmt_doc.project_info.contract_period = f"工期: {email.construction_start} ～ {email.construction_end}"

            # ステップ3: ファイル生成
            show_status(3, 4, "ファイルを作成しています...", "processing")

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

            # サマリー
            total_items = len(items)
            total_amount = sum(item.amount or 0 for item in items if item.level == 0)
            summary_path = output_dir / f"サマリー_{spec_name}_{timestamp}.txt"
            with open(summary_path, 'w', encoding='utf-8') as f:
                f.write(f"見積サマリー\n{'='*40}\n\n")
                f.write(f"仕様書: {file_name}\n")
                f.write(f"生成日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                f.write(f"生成項目数: {total_items}件\n")
                f.write(f"推定総額: ¥{total_amount:,.0f}\n")

            # 結果保存
            st.session_state.generated_files.append({
                'spec_name': spec_name,
                'fmt_json': str(fmt_json_path),
                'pdfs': [str(pdf_path)] if pdf_path else [],
                'excel': str(excel_path),
                'inquiry': str(inquiry_path),
                'summary': str(summary_path),
            })

            st.session_state.fmt_doc = fmt_doc

        # ステップ4: 完了
        elapsed = (datetime.now() - start_time).total_seconds()
        st.session_state.processing_time = elapsed

        total_amount = sum(item.amount or 0 for item in items if item.level == 0)
        show_status(4, 4, f"完了しました（推定総額: ¥{total_amount:,.0f}）", "success")

        end_session()
        st.toast("見積書の生成が完了しました", icon="✅")
        st.session_state.generation_completed = True

    except Exception as e:
        logger.error(f"Generation error: {e}")
        show_status(0, 4, f"エラー: {str(e)[:50]}", "error")
        import traceback
        traceback.print_exc()

    finally:
        st.session_state.is_processing = False
        st.session_state.pending_files = None
        st.rerun()


if __name__ == "__main__":
    main()
else:
    main()
