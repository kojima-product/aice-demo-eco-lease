"""PDF Generator - Ecolease形式の見積書PDF生成"""

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
from reportlab.platypus import Table, TableStyle
from reportlab.lib import colors

from pipelines.schemas import FMTDocument


class EcoleasePDFGenerator:
    """Ecolease形式のPDF生成"""

    def __init__(self):
        self.font_name = self._register_japanese_font()

    def _register_japanese_font(self) -> str:
        """日本語フォントを登録"""

        # フォント候補リスト
        font_paths = [
            # macOS
            '/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc',
            '/System/Library/Fonts/ヒラギノ明朝 ProN.ttc',
            '/Library/Fonts/Arial Unicode.ttf',
            # Linux
            '/usr/share/fonts/truetype/takao-gothic/TakaoGothic.ttf',
            '/usr/share/fonts/opentype/ipaexfont-gothic/ipaexg.ttf',
            '/usr/share/fonts/truetype/fonts-japanese-gothic.ttf',
        ]

        for font_path in font_paths:
            if os.path.exists(font_path):
                try:
                    pdfmetrics.registerFont(TTFont('Japanese', font_path))
                    logger.info(f"Registered Japanese font: {font_path}")
                    return 'Japanese'
                except Exception as e:
                    logger.warning(f"Failed to register {font_path}: {e}")
                    continue

        # フォールバック: Courier (日本語は□になる)
        logger.warning("No Japanese font found, using Courier")
        return 'Courier'

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

        # 二重線の大外枠
        outer_margin = 12*mm
        inner_margin = 3*mm
        c.setLineWidth(2.5)
        c.rect(outer_margin, outer_margin, width - 2*outer_margin, height - 2*outer_margin, stroke=1, fill=0)
        c.setLineWidth(0.8)
        c.rect(outer_margin + inner_margin, outer_margin + inner_margin,
               width - 2*outer_margin - 2*inner_margin, height - 2*outer_margin - 2*inner_margin, stroke=1, fill=0)

        # コンテンツエリア
        content_left = outer_margin + inner_margin + 8*mm
        content_right = width - outer_margin - inner_margin - 8*mm
        content_top = height - outer_margin - inner_margin - 8*mm

        # 見積No（左上）
        c.setFont(self.font_name, 10)
        quote_no = fmt_doc.metadata.get('quote_no', 'XXXXXXX-00')
        c.drawString(content_left, content_top, f"見積No　{quote_no}")

        # 日付（右上）
        c.drawRightString(content_right, content_top, datetime.now().strftime("%Y年　%m月　%d日"))

        # タイトル「御　見　積　書」（中央上部）
        c.setFont(self.font_name, 22)
        c.drawCentredString(width / 2, content_top - 22*mm, "御　見　積　書")

        # 宛先（左上、タイトルの下）
        y = content_top - 38*mm
        c.setFont(self.font_name, 11)
        client_name = fmt_doc.project_info.client_name or ""
        client_text = f"{client_name}　御中"
        c.drawString(content_left, y, client_text)

        # 宛先の下線
        text_width = c.stringWidth(client_text, self.font_name, 11)
        c.line(content_left, y - 2*mm, content_left + text_width, y - 2*mm)

        # 御見積金額
        y -= 18*mm
        total_amount = sum(item.amount or 0 for item in fmt_doc.estimate_items if item.level == 0)
        c.setFont(self.font_name, 10)
        c.drawString(content_left, y, "御見積金額")
        c.setFont(self.font_name, 18)
        c.drawString(content_left + 28*mm, y, f"￥{int(total_amount):,}*")

        # NET金額注釈
        c.setFont(self.font_name, 7)
        c.drawString(content_left + 28*mm, y - 4*mm, "上記NET金額の為値引き不可となります")

        # 「上記の通り御見積申し上げます。」
        y -= 22*mm
        c.setFont(self.font_name, 10)
        c.drawString(content_left, y, "上記の通り御見積申し上げます。")

        # 工事情報（左側）
        y -= 12*mm
        c.drawString(content_left, y, "工　事　名")
        c.drawString(content_left + 25*mm, y, fmt_doc.project_info.project_name)

        y -= 8*mm
        c.drawString(content_left, y, "工事場所")
        location = fmt_doc.project_info.location or ""
        c.drawString(content_left + 25*mm, y, location)

        y -= 8*mm
        c.drawString(content_left, y, "リース期間")
        period = fmt_doc.project_info.contract_period or ""
        c.drawString(content_left + 25*mm, y, period)

        y -= 8*mm
        c.drawString(content_left, y, "決済条件")
        c.drawString(content_left + 25*mm, y, "本紙記載内容のみ有効とする。")

        y -= 8*mm
        c.drawString(content_left, y, "備　　　考")
        c.drawString(content_left + 25*mm, y, "法定福利費を含む。")

        # 検印欄（右側中央）
        stamp_width = 50*mm
        stamp_height = 18*mm
        stamp_x = content_right - stamp_width
        stamp_y = content_top - 52*mm

        c.rect(stamp_x, stamp_y, stamp_width, stamp_height)

        # 縦線で3分割
        col_width = stamp_width / 3
        c.line(stamp_x + col_width, stamp_y, stamp_x + col_width, stamp_y + stamp_height)
        c.line(stamp_x + col_width * 2, stamp_y, stamp_x + col_width * 2, stamp_y + stamp_height)

        # ラベル
        c.setFont(self.font_name, 8)
        label_y = stamp_y + stamp_height - 4*mm
        c.drawCentredString(stamp_x + col_width / 2, label_y, "検印")
        c.drawCentredString(stamp_x + col_width * 1.5, label_y, "検印")
        c.drawCentredString(stamp_x + col_width * 2.5, label_y, "作成者")

        # 会社情報（右下）
        company_y = outer_margin + inner_margin + 38*mm
        c.setFont(self.font_name, 13)
        c.drawRightString(content_right, company_y, "株式会社　エコリース")
        company_y -= 6*mm
        c.setFont(self.font_name, 9)
        c.drawRightString(content_right, company_y, "代表取締役　　赤澤　健一")
        company_y -= 5*mm
        c.setFont(self.font_name, 8)
        c.drawRightString(content_right, company_y, "徳島県板野郡板野町川端字鶴ヶ須47-10")
        company_y -= 4*mm
        c.drawRightString(content_right, company_y, "TEL　(088)　672-0441(代)")
        company_y -= 4*mm
        c.drawRightString(content_right, company_y, "FAX　(088)　672-3623")

    def _create_detail_pages(self, c, fmt_doc: FMTDocument):
        """見積内訳明細書ページ（2ページ目以降、横向き）"""

        lwidth, lheight = landscape(A4)

        # タイトル
        c.setFont(self.font_name, 13)
        title_y = lheight - 15*mm
        c.drawCentredString(lwidth / 2, title_y, "見　積　内　訳　明　細　書")

        # タイトル下線
        line_start = 80*mm
        line_end = lwidth - 80*mm
        c.line(line_start, title_y - 2*mm, line_end, title_y - 2*mm)

        # 見積番号
        c.setFont(self.font_name, 9)
        quote_no = fmt_doc.metadata.get('quote_no', 'XXXXXXX-00')
        c.drawString(25*mm, lheight - 25*mm, f"({quote_no})")

        # テーブルデータ準備
        table_data = []

        # ヘッダー
        table_data.append(['No', '名　　　称', '仕　　　様', '数　量', '単位', '単　　価', '金　　額', '摘　　要'])

        # プロジェクトタイトル行（結合セル）
        project_name = fmt_doc.project_info.project_name
        table_data.append(['', project_name, '', '', '', '', '', ''])

        # 空行
        table_data.append(['', '', '', '', '', '', '', ''])

        # 大項目のみ（level=0）を表示
        for item in fmt_doc.estimate_items:
            if item.level == 0:
                row = [
                    item.item_no,
                    item.name,
                    '',
                    '1',
                    '式',
                    '',
                    f"{int(item.amount):,}",
                    ''
                ]
                table_data.append(row)

        # 空行を追加（合計20行程度にする）
        while len(table_data) < 20:
            table_data.append(['', '', '', '', '', '', '', ''])

        # 総計行
        total_amount = sum(item.amount or 0 for item in fmt_doc.estimate_items if item.level == 0)
        table_data.append(['', '総　　　計', '', '', '', '', f"{int(total_amount):,}", ''])

        # テーブル描画
        col_widths = [18*mm, 60*mm, 50*mm, 20*mm, 15*mm, 25*mm, 28*mm, 42*mm]

        table = Table(table_data, colWidths=col_widths, rowHeights=7*mm)
        table.setStyle(TableStyle([
            # フォント
            ('FONTNAME', (0, 0), (-1, -1), self.font_name),
            ('FONTSIZE', (0, 0), (-1, -1), 9),

            # ヘッダー
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),

            # プロジェクトタイトル行（2行目）を結合
            ('SPAN', (1, 1), (3, 1)),
            ('FONTSIZE', (1, 1), (1, 1), 9),

            # 数値列右寄せ
            ('ALIGN', (3, 1), (3, -1), 'RIGHT'),
            ('ALIGN', (6, 1), (6, -1), 'RIGHT'),

            # 罫線
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
            ('LINEABOVE', (0, 0), (-1, 0), 1, colors.black),
            ('LINEBELOW', (0, 0), (-1, 0), 1, colors.black),

            # 最終行（総計）
            ('LINEABOVE', (0, -1), (-1, -1), 1, colors.black),
            ('FONTSIZE', (0, -1), (-1, -1), 9),
            ('ALIGN', (1, -1), (1, -1), 'CENTER'),
        ]))

        # テーブル配置
        table_start_y = lheight - 35*mm
        table.wrapOn(c, lwidth, lheight)
        table.drawOn(c, 25*mm, table_start_y - len(table_data) * 7*mm)

        # フッター
        c.setFont(self.font_name, 9)
        c.drawString(25*mm, 12*mm, "株式会社　　エコリース")
        c.drawRightString(lwidth - 25*mm, 12*mm, "No　1")
