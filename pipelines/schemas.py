"""Data schemas for FMT (社内統一フォーマット) standardized format."""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import date
from enum import Enum


class DisciplineType(str, Enum):
    """工事区分"""
    ELECTRICAL = "電気設備工事"
    MECHANICAL = "機械設備工事"
    HVAC = "空調設備工事"
    PLUMBING = "衛生設備工事"
    GAS = "ガス設備工事"
    FIRE_PROTECTION = "消防設備工事"
    CONSTRUCTION = "建築工事"


class CostType(str, Enum):
    """費用区分（file_logic.md分析より）"""
    MATERIAL = "材料費"  # 材料単価 × 数量
    LABOR = "労務費"  # 作業員単価 × 人数 × 日数
    CONSTRUCTION = "施工費"  # 工事範囲に応じた一式計上
    OVERHEAD = "諸経費"  # 法定福利費、現場管理費など
    LUMP_SUM = "一式"  # 工種別一式金額
    EQUIPMENT = "機器費"  # キュービクル等の機器
    DEMOLITION = "解体費"  # 既存撤去・切断
    EXCAVATION = "掘削・埋戻し"  # 土工事
    RESTORATION = "復旧費"  # 舗装復旧等


class FacilityType(str, Enum):
    """施設区分"""
    SCHOOL = "学校"
    OFFICE = "オフィス"
    HOSPITAL = "病院"
    FACTORY = "工場"
    COMMERCIAL = "商業施設"
    OTHER = "その他"


class RoomSpec(BaseModel):
    """部屋仕様"""
    room_name: str = Field(description="部屋名")
    room_number: Optional[str] = Field(default=None, description="部屋番号")
    area: Optional[float] = Field(default=None, description="面積(㎡)")
    floor: Optional[str] = Field(default=None, description="階数")
    equipment: List[str] = Field(default_factory=list, description="設備リスト")
    specifications: Dict[str, Any] = Field(default_factory=dict, description="仕様詳細")


class BuildingSpec(BaseModel):
    """建物仕様"""
    building_name: str = Field(description="建物名称")
    building_type: str = Field(description="建物種別")
    structure: Optional[str] = Field(default=None, description="構造")
    total_area: Optional[float] = Field(default=None, description="延床面積(㎡)")
    floors: Optional[int] = Field(default=None, description="階数")
    height: Optional[float] = Field(default=None, description="高さ(m)")
    rooms: List[RoomSpec] = Field(default_factory=list, description="部屋リスト")


class ProjectInfo(BaseModel):
    """案件情報"""
    project_id: Optional[str] = Field(default=None, description="案件ID")
    project_name: str = Field(description="案件名")
    client_name: Optional[str] = Field(default=None, description="顧客名")
    location: Optional[str] = Field(default=None, description="所在地")
    location_pref: Optional[str] = Field(default=None, description="都道府県")
    floor_area_m2: Optional[float] = Field(default=None, description="延床面積(㎡)")
    num_rooms: Optional[int] = Field(default=None, description="部屋数")
    building_age: Optional[int] = Field(default=None, description="築年数")
    bid_date: Optional[date] = Field(default=None, description="入札日")
    delivery_date: Optional[date] = Field(default=None, description="納期")
    deadline: Optional[date] = Field(default=None, description="締切日")
    contract_period: Optional[str] = Field(default=None, description="契約期間")
    remarks: Optional[str] = Field(default=None, description="備考・特記事項")


class EstimateItem(BaseModel):
    """見積明細項目（file_logic.md分析に基づく拡張版）"""
    item_no: str = Field(description="項番")
    name: str = Field(description="名称")
    specification: Optional[str] = Field(default=None, description="仕様")
    quantity: Optional[float] = Field(default=None, description="数量")
    unit: Optional[str] = Field(default=None, description="単位")
    unit_price: Optional[float] = Field(default=None, description="単価")
    amount: Optional[float] = Field(default=None, description="金額")
    remarks: Optional[str] = Field(default=None, description="摘要")
    parent_item_no: Optional[str] = Field(default=None, description="親項番")
    level: int = Field(default=0, description="階層レベル（0-3）")
    discipline: Optional[DisciplineType] = Field(default=None, description="工事区分")

    # 費用区分（file_logic.md分析より）
    cost_type: Optional[CostType] = Field(default=None, description="費用区分")

    # 計算ロジック
    calculation_formula: Optional[str] = Field(default=None, description="計算式（例: 単価×数量、工事費×16.07%）")
    calculation_basis: Optional[Dict[str, Any]] = Field(default_factory=dict, description="計算根拠（単価、人数、日数等）")

    # 労務費の場合
    labor_unit_price: Optional[float] = Field(default=None, description="労務単価（円/人日）")
    labor_days: Optional[float] = Field(default=None, description="人工数（人日）")

    # 諸経費の場合
    overhead_rate: Optional[float] = Field(default=None, description="諸経費率（例: 16.07%）")
    overhead_base_amount: Optional[float] = Field(default=None, description="諸経費の基礎額")

    # RAG/根拠情報
    source_type: Optional[str] = Field(default=None, description="出典タイプ(rag|rule|manual)")
    source_reference: Optional[str] = Field(default=None, description="出典参照(KB ID/式/条文)")
    confidence: Optional[float] = Field(default=None, description="信頼度スコア(0-1)")
    price_references: List[str] = Field(default_factory=list, description="価格参照ID一覧")


class Requirement(BaseModel):
    """要求事項（DEMO FMT仕様）"""
    discipline: DisciplineType = Field(description="工事区分")
    topic: str = Field(description="トピック（照度、コンセント、換気量等）")
    target_value: Optional[str] = Field(default=None, description="目標値")
    standard_ref: Optional[str] = Field(default=None, description="規格参照（{law_code}:{article}:{year}）")
    source_page: Optional[int] = Field(default=None, description="出典ページ")
    confidence: float = Field(default=0.0, description="信頼度スコア(0-1)")


class LegalReference(BaseModel):
    """法令参照（DEMO FMT仕様準拠）"""
    law_code: str = Field(description="法令コード（JEAC8001、建築基準法等）")
    title: str = Field(description="法令名称")
    article: Optional[str] = Field(default=None, description="条項")
    year: int = Field(description="年版")
    clause_text: Optional[str] = Field(default=None, description="条文テキスト")
    norm_value: Optional[Dict[str, Any]] = Field(default=None, description="規範値")
    citation: Optional[Dict[str, str]] = Field(default=None, description="引用情報(url, publisher)")
    relevance_score: float = Field(default=0.0, description="関連度スコア")


class QAItem(BaseModel):
    """質問事項"""
    question_no: str = Field(description="質問番号")
    category: str = Field(description="カテゴリ")
    question: str = Field(description="質問内容")
    background: Optional[str] = Field(default=None, description="背景・理由")
    priority: str = Field(default="中", description="優先度(高/中/低)")


class OverheadCalculation(BaseModel):
    """諸経費計算（file_logic.md分析より）"""
    name: str = Field(description="諸経費名称（例: 法定福利費）")
    rate: float = Field(description="率（例: 0.1607 = 16.07%）")
    base_amount: float = Field(description="基礎額（工事費等）")
    amount: float = Field(description="計算後金額")
    formula: str = Field(description="計算式（例: 基礎額 × 16.07%）")
    remarks: Optional[str] = Field(default=None, description="備考")


class FMTDocument(BaseModel):
    """FMT統一フォーマット - メインドキュメント"""

    # メタデータ
    fmt_version: str = Field(default="1.0", description="FMTバージョン")
    created_at: str = Field(description="作成日時")

    # 入札情報
    project_info: ProjectInfo = Field(description="案件情報")

    # 施設情報
    facility_type: FacilityType = Field(description="施設区分")
    building_specs: List[BuildingSpec] = Field(default_factory=list, description="建物仕様")

    # 工事区分
    disciplines: List[DisciplineType] = Field(default_factory=list, description="対象工事区分")

    # 抽出された要求事項
    requirements: List[Requirement] = Field(default_factory=list, description="要求事項")

    # 見積項目
    estimate_items: List[EstimateItem] = Field(default_factory=list, description="見積明細")

    # 諸経費計算（file_logic.md分析より）
    overhead_calculations: List[OverheadCalculation] = Field(default_factory=list, description="諸経費計算")

    # 法令参照
    legal_references: List[LegalReference] = Field(default_factory=list, description="法令参照")

    # 質問事項
    qa_items: List[QAItem] = Field(default_factory=list, description="質問事項")

    # その他
    raw_text: Optional[str] = Field(default=None, description="抽出された生テキスト")
    extracted_tables: List[Dict[str, Any]] = Field(default_factory=list, description="抽出されたテーブル")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="その他メタデータ")


class PriceReference(BaseModel):
    """過去価格参照（DEMO過去見積KB仕様）"""
    item_id: str = Field(description="項目ID")
    description: str = Field(description="項目説明")
    discipline: DisciplineType = Field(description="工事区分")
    unit: str = Field(description="単位")
    unit_price: float = Field(description="単価")
    vendor: Optional[str] = Field(default=None, description="業者")
    valid_from: date = Field(description="有効期間開始")
    valid_to: Optional[date] = Field(default=None, description="有効期間終了")
    source_project: str = Field(description="出典案件")
    context_tags: List[str] = Field(default_factory=list, description="コンテキストタグ")
    features: Dict[str, Any] = Field(default_factory=dict, description="特徴量")
    similarity_score: float = Field(default=0.0, description="類似度スコア")
