"""PDF Generator - Ecolease形式の見積書PDF生成"""

import re
from pathlib import Path
from typing import Optional
from datetime import datetime
from loguru import logger
import os

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Table, TableStyle, Paragraph
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors

from pipelines.schemas import FMTDocument


class EcoleasePDFGenerator:
    """Ecolease形式のPDF生成"""

    def __init__(self):
        self.font_name = self._register_japanese_font()

    def _register_japanese_font(self) -> str:
        """日本語フォントを登録（明朝体優先、ライトウェイト）"""

        # 明朝体フォント候補（TTF/OTFのみ）
        mincho_fonts = [
            # IPA明朝（Streamlit Cloud: packages.txt でインストール）
            ('/usr/share/fonts/opentype/ipafont-mincho/ipam.ttf', None),
            ('/usr/share/fonts/truetype/fonts-japanese-mincho.ttf', None),
            # macOS ヒラギノ明朝（.ttc形式、subfontIndex指定）
            ('/System/Library/Fonts/ヒラギノ明朝 ProN.ttc', 0),
            ('/System/Library/Fonts/Hiragino Mincho ProN.ttc', 0),
            # IPA明朝（ローカル環境）
            ('/Library/Fonts/ipaexm.ttf', None),
            ('/usr/share/fonts/opentype/ipaexfont-mincho/ipaexm.ttf', None),
            ('/usr/share/fonts/truetype/takao-mincho/TakaoMincho.ttf', None),
            # MS明朝（Officeインストール時）
            ('/Library/Fonts/MS Mincho.ttf', None),
            ('/Library/Fonts/Microsoft/MS Mincho.ttf', None),
        ]

        # ゴシック体・Unicodeフォント候補（フォールバック、日本語対応）
        unicode_fonts = [
            # IPAゴシック（Streamlit Cloud: packages.txt でインストール）
            ('/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf', None),
            ('/usr/share/fonts/truetype/fonts-japanese-gothic.ttf', None),
            # macOS ヒラギノ角ゴシック（.ttc形式）
            ('/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc', 0),
            ('/System/Library/Fonts/Hiragino Sans GB.ttc', 0),
            # Arial Unicode（日本語を含む）
            ('/Library/Fonts/Arial Unicode.ttf', None),
            # Noto Sans JP
            ('/Library/Fonts/NotoSansJP-Light.otf', None),
            ('/Library/Fonts/NotoSansJP-Regular.otf', None),
        ]

        all_fonts = mincho_fonts + unicode_fonts

        for font_path, subfont_index in all_fonts:
            if os.path.exists(font_path):
                try:
                    if subfont_index is not None:
                        pdfmetrics.registerFont(TTFont('Japanese', font_path, subfontIndex=subfont_index))
                    else:
                        pdfmetrics.registerFont(TTFont('Japanese', font_path))
                    logger.info(f"✅ Registered Japanese font: {font_path}")
                    return 'Japanese'
                except Exception as e:
                    logger.warning(f"❌ Failed to register {font_path}: {e}")
                    continue

        # 最後のフォールバック: Helvetica（日本語は□になる）
        logger.error("⚠️  No Japanese font found, using Helvetica (Japanese text will appear as boxes)")
        return 'Helvetica'

    def _draw_text_with_weight(self, c, x, y, text, weight, align='left'):
        """文字の太さを考慮してテキストを描画

        Args:
            c: Canvas オブジェクト
            x, y: 描画位置
            text: 描画するテキスト
            weight: 文字の太さ (-2.0: extra light, 0.0: 通常, 2.0: 太字)
            align: 'left', 'center', 'right'
        """
        if weight < 0:
            # 細字効果（透明度を下げて細く見せる）
            opacity = max(0.4, 1.0 + (weight * 0.2))  # -2.0なら0.6, -1.5なら0.7
            c.setFillAlpha(opacity)
            if align == 'center':
                c.drawCentredString(x, y, text)
            elif align == 'right':
                c.drawRightString(x, y, text)
            else:
                c.drawString(x, y, text)
            c.setFillAlpha(1.0)  # 透明度を戻す
        elif weight == 0:
            # 太さ0の場合は通常描画
            if align == 'center':
                c.drawCentredString(x, y, text)
            elif align == 'right':
                c.drawRightString(x, y, text)
            else:
                c.drawString(x, y, text)
        else:
            # 太字効果のため、微妙にずらして複数回描画
            offsets = [
                (0, 0),
                (weight * 0.3, 0),
                (0, weight * 0.3),
                (weight * 0.3, weight * 0.3),
            ]
            for dx, dy in offsets:
                if align == 'center':
                    c.drawCentredString(x + dx, y + dy, text)
                elif align == 'right':
                    c.drawRightString(x + dx, y + dy, text)
                else:
                    c.drawString(x + dx, y + dy, text)

    def _wrap_text(self, c, text, max_width):
        """テキストを指定幅で折り返す

        Args:
            c: Canvas オブジェクト
            text: 折り返すテキスト
            max_width: 最大幅（ポイント単位）

        Returns:
            list: 折り返されたテキストの行リスト
        """
        if not text:
            return ['']

        words = []
        current_word = ""

        # 文字列を単語とデリミタに分割
        for char in text:
            if char in ['、', '。', '（', '）', '　', ' ', '/', '～', '~', ',', '.']:
                if current_word:
                    words.append(current_word)
                    current_word = ""
                words.append(char)
            else:
                current_word += char

        if current_word:
            words.append(current_word)

        lines = []
        current_line = ""

        for word in words:
            test_line = current_line + word
            # 現在のフォント設定でテキスト幅を測定
            font_name = c._fontname
            font_size = c._fontsize

            if c.stringWidth(test_line, font_name, font_size) <= max_width:
                current_line = test_line
            else:
                if current_line:
                    lines.append(current_line)
                    current_line = word
                else:
                    # 単語自体が長すぎる場合、強制的に折り返す
                    lines.append(word)
                    current_line = ""

        if current_line:
            lines.append(current_line)

        return lines if lines else ['']

    def generate(self, fmt_doc: FMTDocument, output_path: str):
        """PDF生成メイン処理"""

        # 全ページ横向き
        c = canvas.Canvas(output_path, pagesize=landscape(A4))

        # 1ページ目: 御見積書（枠付き、横向き）
        self._create_quotation_page(c, fmt_doc)
        c.showPage()

        # 2ページ目以降: 見積内訳明細書（横向き）
        self._create_detail_pages(c, fmt_doc)

        c.save()
        logger.info(f"PDF saved: {output_path}")

    def _create_cover_letter(self, c, fmt_doc: FMTDocument):
        """送付状ページ（1ページ目）"""

        width, height = A4

        # ヘッダー: 堀江ひとみ エコリース
        c.setFont(self.font_name, 10)
        c.drawString(30*mm, height - 20*mm, "堀江ひとみ エコリース")
        c.line(30*mm, height - 21*mm, width - 30*mm, height - 21*mm)

        # メール情報
        y = height - 30*mm
        c.setFont(self.font_name, 9)

        c.drawString(30*mm, y, "差出人:")
        c.drawString(60*mm, y, "積算　メールボックス <sekisan@ecolease.co.jp>")
        y -= 5*mm

        c.drawString(30*mm, y, "送信日時:")
        c.drawString(60*mm, y, datetime.now().strftime("%Y年%m月%d日 %H:%M"))
        y -= 5*mm

        c.drawString(30*mm, y, "宛先:")
        client_name = fmt_doc.project_info.client_name or "御中"
        c.drawString(60*mm, y, client_name)
        y -= 5*mm

        c.drawString(30*mm, y, "件名:")
        c.drawString(60*mm, y, f"{fmt_doc.project_info.project_name}　見積書送付")
        y -= 5*mm

        c.drawString(30*mm, y, "添付ファイル:")
        c.drawString(60*mm, y, "見積表紙.pdf; 見積明細.xls")
        y -= 10*mm

        # 本文
        c.setFont(self.font_name, 10)
        c.drawString(30*mm, y, f"{client_name}")
        y -= 5*mm
        c.drawString(30*mm, y, "御担当者様")
        y -= 10*mm

        c.drawString(30*mm, y, "御世話になっております。")
        y -= 5*mm
        c.drawString(30*mm, y, "件名物件の見積書を送付させて頂きます。")
        y -= 5*mm
        c.drawString(30*mm, y, "尚、NET見積となっております。")
        y -= 10*mm

        # 見積番号
        c.setFont(self.font_name, 10)
        quote_no = fmt_doc.metadata.get('quote_no', 'XXXXXXX-00')
        c.drawString(35*mm, y, f"見積No.{quote_no}　{fmt_doc.disciplines[0].value if fmt_doc.disciplines else ''}設備")
        y -= 10*mm

        c.drawString(30*mm, y, "見積条件および別途工事につきましては、")
        y -= 5*mm
        c.drawString(30*mm, y, "見積表紙及び明細内に記載させて頂いておりますので、")
        y -= 5*mm
        c.drawString(30*mm, y, "御確認頂ければと思います。")
        y -= 5*mm
        c.drawString(30*mm, y, "宜しくお願い致します。")
        y -= 15*mm

        # 署名
        c.drawString(30*mm, y, "*" * 45)
        y -= 5*mm
        c.drawString(30*mm, y, "〒779-0102")
        y -= 5*mm
        c.drawString(30*mm, y, "徳島県板野郡板野町川端字鶴ヶ須47-10")
        y -= 5*mm
        c.drawString(30*mm, y, "(株)エコリース徳島　送信：堀江ひとみ")
        y -= 5*mm
        c.drawString(30*mm, y, "TEL(088)672-0446(積算・設計)")
        y -= 5*mm
        c.drawString(30*mm, y, "FAX(088)672-3713(積算・設計)")
        y -= 5*mm
        c.drawString(30*mm, y, "*" * 45)

    def _create_quotation_page(self, c, fmt_doc: FMTDocument):
        """御見積書ページ（1ページ目、枠付き、横向き）"""

        width, height = landscape(A4)

        # 外枠とマージン
        outer_margin = 15.0*mm
        inner_margin = 2.0*mm
        c.setLineWidth(0.5)  # 外枠線
        c.rect(outer_margin, outer_margin, width - 2*outer_margin, height - 2*outer_margin, stroke=1, fill=0)
        c.setLineWidth(0.5)  # 内枠線
        c.rect(outer_margin + inner_margin, outer_margin + inner_margin,
               width - 2*outer_margin - 2*inner_margin, height - 2*outer_margin - 2*inner_margin, stroke=1, fill=0)

        # コンテンツエリア
        content_left = outer_margin + inner_margin + 15.0*mm
        content_right = width - outer_margin - inner_margin - 15.0*mm
        content_top = height - outer_margin - inner_margin - 15.0*mm

        # 見積No・日付
        header_font_size = 12.0
        header_font_weight = -2.0
        header_y = content_top - 20.0*mm

        c.setFont(self.font_name, header_font_size)
        quote_no = fmt_doc.metadata.get('quote_no', 'XXXXXXX-00')
        self._draw_text_with_weight(c, content_left, header_y, f"見積No　{quote_no}",
                                     header_font_weight, align='left')

        # 日付（右上）
        c.setFont(self.font_name, header_font_size)
        self._draw_text_with_weight(c, content_right, header_y,
                                     datetime.now().strftime("%Y年　%m月　%d日"),
                                     header_font_weight, align='right')

        # タイトル
        title_font_size = 28.0
        title_font_weight = 0.0
        title_y = content_top - 13.0*mm

        c.setFont(self.font_name, title_font_size)
        self._draw_text_with_weight(c, width / 2, title_y, "御　見　積　書",
                                     title_font_weight, align='center')

        # 宛先
        client_font_size = 20.0
        client_font_weight = 0.0
        client_y = content_top - 40.0*mm
        client_underline_offset = 1.3*mm

        y = client_y
        c.setFont(self.font_name, client_font_size)
        client_name = fmt_doc.project_info.client_name or ""
        client_text = f"{client_name}　御中"
        self._draw_text_with_weight(c, content_left, y, client_text,
                                     client_font_weight, align='left')

        # 宛先の下線
        text_width = c.stringWidth(client_text, self.font_name, client_font_size)
        underline_y = y - client_underline_offset
        c.line(content_left, underline_y, content_left + text_width, underline_y)

        # 金額
        amount_label_font_size = 20.0
        amount_label_font_weight = 0.0
        amount_font_size = 30.0
        amount_font_weight = 0.0
        amount_offset_x = 40.0*mm
        amount_offset_y = 17.0*mm
        note_font_size = 10.0
        note_font_weight = -1.5
        note_offset_y = 7.0*mm

        y -= amount_offset_y
        total_amount = sum(item.amount or 0 for item in fmt_doc.estimate_items if item.level == 0)
        c.setFont(self.font_name, amount_label_font_size)
        self._draw_text_with_weight(c, content_left, y, "御見積金額",
                                     amount_label_font_weight, align='left')

        # 金額を大きく表示
        c.setFont(self.font_name, amount_font_size)
        amount_text = f"￥{int(total_amount):,}*"
        amount_x = content_left + amount_offset_x
        self._draw_text_with_weight(c, amount_x, y, amount_text,
                                     amount_font_weight, align='left')

        # 金額の下線
        amount_width = c.stringWidth(amount_text, self.font_name, amount_font_size)
        underline_y = y - 2.5*mm
        c.line(amount_x, underline_y, amount_x + amount_width, underline_y)

        # NET金額注釈（金額の下線の下に配置）
        c.setFont(self.font_name, note_font_size)
        note_y = underline_y - note_offset_y
        self._draw_text_with_weight(c, amount_x, note_y,
                                     "上記NET金額の為値引き不可となります",
                                     note_font_weight, align='left')

        # 上記の通り〜
        above_text_font_size = 12.0
        above_text_font_weight = -1.5
        above_text_offset_y = 30.0*mm

        y -= above_text_offset_y
        c.setFont(self.font_name, above_text_font_size)
        self._draw_text_with_weight(c, content_left, y, "上記の通り御見積申し上げます。",
                                     above_text_font_weight, align='left')

        # 工事情報
        work_info_font_size = 13.0
        work_info_font_weight = -1.0
        work_info_label_width = 30.0*mm
        work_info_line_spacing = 10.0*mm
        work_info_offset_y = 14.0*mm

        y -= work_info_offset_y
        c.setFont(self.font_name, work_info_font_size)
        label_width = work_info_label_width
        line_spacing = work_info_line_spacing
        max_text_width = content_right - (content_left + label_width)

        self._draw_text_with_weight(c, content_left, y, "工　事　名",
                                     work_info_font_weight, align='left')
        self._draw_text_with_weight(c, content_left + label_width, y, fmt_doc.project_info.project_name,
                                     work_info_font_weight, align='left')

        y -= line_spacing
        self._draw_text_with_weight(c, content_left, y, "工事場所",
                                     work_info_font_weight, align='left')
        self._draw_text_with_weight(c, content_left + label_width, y, fmt_doc.project_info.location or "",
                                     work_info_font_weight, align='left')

        y -= line_spacing
        # リース期間（項目ごとに改行）
        self._draw_text_with_weight(c, content_left, y, "リース期間",
                                     work_info_font_weight, align='left')
        contract_period = fmt_doc.project_info.contract_period or ""
        # 「、」「,」で分割して項目ごとに改行
        period_items = re.split(r'[、,]', contract_period)
        period_items = [item.strip() for item in period_items if item.strip()]
        for i, line in enumerate(period_items):
            self._draw_text_with_weight(c, content_left + label_width, y - (i * line_spacing * 0.85), line,
                                         work_info_font_weight, align='left')
        # 複数行の場合、追加分のスペースを確保
        if len(period_items) > 1:
            y -= line_spacing * 0.85 * (len(period_items) - 1)

        y -= line_spacing
        # 決済条件（折り返し対応）
        payment_terms = fmt_doc.metadata.get('payment_terms', '本紙記載内容のみ有効とする。')
        self._draw_text_with_weight(c, content_left, y, "決済条件",
                                     work_info_font_weight, align='left')
        wrapped_lines = self._wrap_text(c, payment_terms, max_text_width)
        for i, line in enumerate(wrapped_lines):
            self._draw_text_with_weight(c, content_left + label_width, y - (i * line_spacing * 0.85), line,
                                         work_info_font_weight, align='left')
        if len(wrapped_lines) > 1:
            y -= line_spacing * 0.85 * (len(wrapped_lines) - 1)

        y -= line_spacing
        # 備考（折り返し対応）
        remarks = fmt_doc.metadata.get('remarks', '法定福利費を含む。')
        self._draw_text_with_weight(c, content_left, y, "備　　　考",
                                     work_info_font_weight, align='left')
        wrapped_lines = self._wrap_text(c, remarks, max_text_width)
        for i, line in enumerate(wrapped_lines):
            self._draw_text_with_weight(c, content_left + label_width, y - (i * line_spacing * 0.85), line,
                                         work_info_font_weight, align='left')
        if len(wrapped_lines) > 1:
            y -= line_spacing * 0.85 * (len(wrapped_lines) - 1)

        # 会社情報（左寄せ）
        company_name_font_size = 18.0
        company_name_font_weight = 0.0
        company_president_font_size = 10.0
        company_president_font_weight = -1.5
        company_address_font_size = 10.0
        company_address_font_weight = -1.5
        company_offset_y_val = 27.0*mm
        company_line_spacing_val = 5.0*mm

        company_y = outer_margin + inner_margin + company_offset_y_val
        company_spacing = company_line_spacing_val
        company_x = content_right - 60.0*mm  # 検印欄の左端に合わせる

        c.setFont(self.font_name, company_name_font_size)
        self._draw_text_with_weight(c, company_x, company_y, "株式会社　エコリース",
                                     company_name_font_weight, align='left')
        company_y -= company_spacing

        c.setFont(self.font_name, company_president_font_size)
        self._draw_text_with_weight(c, company_x, company_y, "代表取締役　　赤澤　健一",
                                     company_president_font_weight, align='left')
        company_y -= company_spacing

        c.setFont(self.font_name, company_address_font_size)
        self._draw_text_with_weight(c, company_x, company_y, "徳島県板野郡板野町川端字鶴ヶ須47-10",
                                     company_address_font_weight, align='left')
        company_y -= company_spacing * 0.9
        self._draw_text_with_weight(c, company_x, company_y, "TEL　(088)　672-0441(代)",
                                     company_address_font_weight, align='left')
        company_y -= company_spacing * 0.9
        self._draw_text_with_weight(c, company_x, company_y, "FAX　(088)　672-3623",
                                     company_address_font_weight, align='left')

        # 検印欄（会社情報の上に配置）
        stamp_width_val = 60.0*mm
        stamp_height_val = 25.0*mm
        stamp_label_font_size = 12.0
        stamp_label_font_weight = -1.2
        stamp_label_offset_y = 5.5*mm

        # 会社名の上部から十分なスペースを確保して配置
        stamp_bottom = outer_margin + inner_margin + company_offset_y_val + 10*mm
        stamp_y = stamp_bottom
        stamp_x = content_right - stamp_width_val

        c.rect(stamp_x, stamp_y, stamp_width_val, stamp_height_val)

        # 縦線で3分割
        col_width = stamp_width_val / 3
        c.line(stamp_x + col_width, stamp_y, stamp_x + col_width, stamp_y + stamp_height_val)
        c.line(stamp_x + col_width * 2, stamp_y, stamp_x + col_width * 2, stamp_y + stamp_height_val)

        # ラベル（上部）
        c.setFont(self.font_name, stamp_label_font_size)
        label_y = stamp_y + stamp_height_val - stamp_label_offset_y
        self._draw_text_with_weight(c, stamp_x + col_width / 2, label_y, "検印",
                                     stamp_label_font_weight, align='center')
        self._draw_text_with_weight(c, stamp_x + col_width * 1.5, label_y, "検印",
                                     stamp_label_font_weight, align='center')
        self._draw_text_with_weight(c, stamp_x + col_width * 2.5, label_y, "作成者",
                                     stamp_label_font_weight, align='center')

        # ラベルの下にボーダー（横線）を追加
        border_y = stamp_y + stamp_height_val - stamp_label_offset_y - 3*mm
        c.line(stamp_x, border_y, stamp_x + stamp_width_val, border_y)

    def _create_detail_pages(self, c, fmt_doc: FMTDocument):
        """見積内訳明細書ページ（2ページ目以降、横向き）

        全項目を連続してリスト表示（無制限ページ対応）
        """

        # 1. サマリーページ（大項目のみ）
        self._create_summary_page(c, fmt_doc)

        # 2. 全項目を連続して明細表示（ページ数無制限）
        self._create_continuous_detail_pages(c, fmt_doc)

    def _create_summary_page(self, c, fmt_doc: FMTDocument):
        """サマリーページ（大項目のみ）"""
        lwidth, lheight = landscape(A4)

        # タイトル
        c.setFont(self.font_name, 14)
        title_y = lheight - 15*mm
        title_text = "見　積　内　訳　明　細　書"
        c.drawCentredString(lwidth / 2, title_y, title_text)

        # タイトル下線
        title_width = c.stringWidth(title_text, self.font_name, 14)
        line_start = (lwidth - title_width) / 2
        line_end = line_start + title_width
        c.line(line_start, title_y - 2.5*mm, line_end, title_y - 2.5*mm)

        # 見積番号
        c.setFont(self.font_name, 9)
        quote_no = fmt_doc.metadata.get('quote_no', 'XXXXXXX-00')
        c.drawString(25*mm, lheight - 25*mm, f"({quote_no})")

        # テーブルデータ準備
        table_data = []

        # ヘッダー
        table_data.append(['No', '名　　　称', '仕　　　様', '数　量', '単位', '単　　価', '金　　額', '摘　　要'])

        # プロジェクトタイトル行
        project_name = fmt_doc.project_info.project_name
        table_data.append(['', project_name, '', '', '', '', '', ''])

        # 空行
        table_data.append(['', '', '', '', '', '', '', ''])

        # 大項目のみ表示
        for item in fmt_doc.estimate_items:
            if item.level == 0:
                row = [
                    item.item_no if item.item_no else '',
                    item.name,
                    '',
                    '1',
                    '式',
                    '',
                    f"{int(item.amount):,}" if item.amount is not None else "",
                    ''
                ]
                table_data.append(row)

        # 空行を追加（20行程度まで）
        while len(table_data) < 20:
            table_data.append(['', '', '', '', '', '', '', ''])

        # 総計行
        total_amount = sum(item.amount or 0 for item in fmt_doc.estimate_items if item.level == 0)
        table_data.append(['', '総　　　計', '', '', '', '', f"{int(total_amount):,}", ''])

        # テーブル描画
        self._draw_table(c, table_data, lwidth, lheight)

    def _create_continuous_detail_pages(self, c, fmt_doc: FMTDocument):
        """全項目を連続して明細表示（参照PDF形式・ページ数無制限）"""
        lwidth, lheight = landscape(A4)

        # 利用可能なテーブル高さ
        available_height = lheight - 35*mm - 25*mm
        header_height = 7*mm
        row_base_height = 7*mm

        # 全項目を行高さを考慮してページ分割
        all_items = fmt_doc.estimate_items
        pages_data = []
        current_page_items = []
        current_height = header_height  # ヘッダー行

        for item in all_items:
            # 行高さ推定
            spec_text = item.specification if item.specification else ''
            name_text = item.name
            spec_lines = max(1, (len(spec_text) + 14) // 15) if spec_text else 1
            name_lines = max(1, (len(name_text) + 18) // 18) if name_text else 1
            max_lines = max(spec_lines, name_lines)
            row_height = row_base_height * max_lines

            # ページ分割判定
            if current_height + row_height > available_height and current_page_items:
                pages_data.append(current_page_items)
                current_page_items = []
                current_height = header_height

            current_page_items.append(item)
            current_height += row_height

        # 最後のページを追加
        if current_page_items:
            pages_data.append(current_page_items)

        # 各ページを描画
        for page_idx, page_items in enumerate(pages_data):
            c.showPage()
            page_no = page_idx + 2  # サマリーが1ページ目

            # ページヘッダー
            c.setFont(self.font_name, 14)
            title_y = lheight - 15*mm
            title_text = "見　積　内　訳　明　細　書"
            c.drawCentredString(lwidth / 2, title_y, title_text)

            title_width = c.stringWidth(title_text, self.font_name, 14)
            line_start = (lwidth - title_width) / 2
            c.line(line_start, title_y - 2.5*mm, line_start + title_width, title_y - 2.5*mm)

            c.setFont(self.font_name, 9)
            quote_no = fmt_doc.metadata.get('quote_no', 'XXXXXXX-00')
            c.drawString(25*mm, lheight - 25*mm, f"({quote_no})")

            # テーブルデータ作成
            table_data = []
            table_data.append(['No', '名　　　称', '仕　　　様', '数　量', '単位', '単　価', '金　額', '摘　要'])

            for item in page_items:
                # 親項目（level 0, 1）は名称と金額、子項目は詳細表示
                if item.level == 0:
                    # 大項目: 名称と金額（小計として表示）
                    row = [
                        '',
                        f"【{item.name}】",
                        '',
                        '1',
                        '式',
                        '',
                        f"{int(item.amount):,}" if item.amount is not None else "",
                        ''
                    ]
                elif item.level == 1:
                    # 中項目: 名称とインデント、金額表示
                    row = [
                        '',
                        f"　{item.name}",
                        '',
                        '1' if item.amount else '',
                        '式' if item.amount else '',
                        '',
                        f"{int(item.amount):,}" if item.amount is not None else "",
                        ''
                    ]
                else:
                    # 詳細項目: 全情報表示
                    indent = "　" * (item.level - 1)
                    row = [
                        item.item_no if item.item_no else '',
                        f"{indent}{item.name}",
                        item.specification if item.specification else '',
                        str(item.quantity) if item.quantity is not None else '',
                        item.unit if item.unit else '',
                        f"{int(item.unit_price):,}" if item.unit_price is not None else "",
                        f"{int(item.amount):,}" if item.amount is not None else "",
                        ''
                    ]
                table_data.append(row)

            # テーブル描画
            self._draw_table(c, table_data, lwidth, lheight, page_no)

    def _create_detail_page_for_item(self, c, fmt_doc: FMTDocument, main_item, start_page):
        """特定の大項目の詳細ページを作成（複数ページ対応・行高さ計算版）

        Returns:
            int: 作成したページ数
        """
        lwidth, lheight = landscape(A4)

        # その大項目とその子項目を収集
        start_idx = fmt_doc.estimate_items.index(main_item)
        detail_items = [main_item]

        # 次のlevel=0までの全項目を収集
        for i in range(start_idx + 1, len(fmt_doc.estimate_items)):
            if fmt_doc.estimate_items[i].level == 0:
                break
            detail_items.append(fmt_doc.estimate_items[i])

        # 利用可能なテーブル高さ（ヘッダー、フッター、マージン考慮）
        available_height = lheight - 35*mm - 25*mm  # 上35mm、下25mm

        # 全項目をページ単位に分割
        pages_data = []
        current_page_items = []
        current_height = 0
        header_height = 7*mm  # ヘッダー行の高さ
        row_base_height = 7*mm  # 基本行高さ
        is_first_page = True

        for item in detail_items:
            if item.level == 0:
                continue  # 大項目自体はスキップ

            # 行の高さを推定（仕様テキストの長さに基づく）
            spec_text = item.specification if item.specification else ''
            name_text = "　" * item.level + item.name
            # 1行あたり約15文字として折り返し行数を推定
            spec_lines = max(1, (len(spec_text) + 14) // 15) if spec_text else 1
            name_lines = max(1, (len(name_text) + 20) // 20) if name_text else 1
            max_lines = max(spec_lines, name_lines)
            row_height = row_base_height * max_lines

            # 新しいページが必要か判定
            needed_height = current_height + row_height
            if is_first_page and not current_page_items:
                # 最初のページはタイトル行追加
                needed_height += header_height * 3  # ヘッダー + タイトル行 + 空行

            if needed_height > available_height and current_page_items:
                pages_data.append(current_page_items)
                current_page_items = []
                current_height = header_height  # ヘッダー行の高さ
                is_first_page = False

            current_page_items.append(item)
            current_height += row_height

        # 最後のページを追加
        if current_page_items:
            pages_data.append(current_page_items)

        # ページなしの場合（大項目のみ）
        if not pages_data:
            pages_data = [[]]

        pages_created = 0

        for page_idx, page_items in enumerate(pages_data):
            if page_idx > 0:
                c.showPage()  # 2ページ目以降は改ページ

            # ページヘッダー
            self._draw_page_header(c, fmt_doc, lwidth, lheight, pages_created + 1, len(pages_data))

            # テーブルデータ
            table_data = []

            # ヘッダー
            table_data.append(['No', '名　　　称', '仕　　　様', '数　量', '単位', '単　　価', '金　　額', '摘　　要'])

            # 1ページ目のみ：大項目名を表示
            if page_idx == 0:
                table_data.append(['', main_item.name, '', '', '', '', '', ''])
                table_data.append(['', '', '', '', '', '', '', ''])

            # 詳細項目
            for item in page_items:
                indent = "　" * item.level
                indented_name = f"{indent}{item.name}"

                row = [
                    item.item_no if item.item_no else '',
                    indented_name,
                    item.specification if item.specification else '',
                    str(item.quantity) if item.quantity is not None else '',
                    item.unit if item.unit else '',
                    f"{int(item.unit_price):,}" if item.unit_price is not None else "",
                    f"{int(item.amount):,}" if item.amount is not None else "",
                    ''
                ]
                table_data.append(row)

            # 最終ページのみ：小計行
            if page_idx == len(pages_data) - 1:
                subtotal = main_item.amount if main_item.amount else 0
                table_data.append(['', '小　　　計', '', '', '', '', f"{int(subtotal):,}", ''])

            # テーブル描画
            current_page_no = start_page + pages_created
            self._draw_table(c, table_data, lwidth, lheight, current_page_no)

            pages_created += 1

        return pages_created

    def _draw_page_header(self, c, fmt_doc, lwidth, lheight, page_num, total_pages):
        """ページヘッダー描画"""
        c.setFont(self.font_name, 14)
        title_y = lheight - 15*mm
        title_text = "見　積　内　訳　明　細　書"
        c.drawCentredString(lwidth / 2, title_y, title_text)

        # タイトル下線
        title_width = c.stringWidth(title_text, self.font_name, 14)
        line_start = (lwidth - title_width) / 2
        line_end = line_start + title_width
        c.line(line_start, title_y - 2.5*mm, line_end, title_y - 2.5*mm)

        # 見積番号
        c.setFont(self.font_name, 9)
        quote_no = fmt_doc.metadata.get('quote_no', 'XXXXXXX-00')
        c.drawString(25*mm, lheight - 25*mm, f"({quote_no})")

    def _draw_table(self, c, table_data, lwidth, lheight, page_no=1):
        """テーブル描画（テキスト折り返し対応）"""
        col_widths = [18*mm, 60*mm, 50*mm, 20*mm, 15*mm, 25*mm, 28*mm, 42*mm]

        # 長いテキスト用のParagraphスタイル
        cell_style = ParagraphStyle(
            'CellStyle',
            fontName=self.font_name,
            fontSize=8,
            leading=10,  # 行間
            wordWrap='CJK',  # 日本語折り返し
        )

        # 名称(1)と仕様(2)列をParagraphに変換
        wrapped_data = []
        for row_idx, row in enumerate(table_data):
            new_row = list(row)
            if row_idx > 0:  # ヘッダー行以外
                # 名称列（インデックス1）
                if new_row[1]:
                    new_row[1] = Paragraph(str(new_row[1]), cell_style)
                # 仕様列（インデックス2）
                if new_row[2]:
                    new_row[2] = Paragraph(str(new_row[2]), cell_style)
            wrapped_data.append(new_row)

        table = Table(wrapped_data, colWidths=col_widths, rowHeights=None)  # 高さを自動調整
        table.setStyle(TableStyle([
            # フォント
            ('FONTNAME', (0, 0), (-1, -1), self.font_name),
            ('FONTSIZE', (0, 0), (-1, -1), 9),

            # ヘッダー
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),

            # 数値列右寄せ
            ('ALIGN', (0, 1), (0, -1), 'CENTER'),
            ('ALIGN', (3, 1), (3, -1), 'RIGHT'),
            ('ALIGN', (6, 1), (6, -1), 'RIGHT'),

            # 罫線
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
            ('LINEABOVE', (0, 0), (-1, 0), 1, colors.black),
            ('LINEBELOW', (0, 0), (-1, 0), 1, colors.black),

            # 最終行（総計/小計）
            ('LINEABOVE', (0, -1), (-1, -1), 1, colors.black),
            ('FONTSIZE', (0, -1), (-1, -1), 9),
            ('ALIGN', (1, -1), (1, -1), 'CENTER'),
        ]))

        # テーブル配置（可変高さに対応）
        table_start_y = lheight - 35*mm
        w, h = table.wrapOn(c, lwidth, lheight)  # 実際のテーブルサイズを取得
        table.drawOn(c, 25*mm, table_start_y - h)

        # フッター
        c.setFont(self.font_name, 8)
        c.drawString(25*mm, 12*mm, "株式会社　　エコリース")
        c.drawRightString(lwidth - 25*mm, 12*mm, f"No　{page_no}")
