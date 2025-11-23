"""
法令要件抽出機能（関係法令一覧_追加１.pdfに基づく実装）
"""

import os
import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
from dotenv import load_dotenv
from anthropic import Anthropic
from loguru import logger

from pipelines.schemas import (
    DisciplineType, LegalReference, Requirement, EstimateItem
)
from pipelines.cost_tracker import record_cost


class LegalRequirementExtractor:
    """
    法令要件抽出器

    関係法令一覧_追加１.pdfに基づき、仕様書から法令要件を抽出し、
    見積項目に反映すべき法令遵守事項を特定する。
    """

    # 重要法令リスト（赤字部分）
    CRITICAL_LAWS = {
        "common": [
            "内線規程（JEAC 8001）",
            "学校施設設備基準",
            "学校環境衛生管理マニュアル",
            "学校保健安全法",
            "学校環境衛生基準",
            "消防法",
            "消防法施行規則",
            "消防法施行令",
            "東京都工事標準仕様書"
        ],
        "electrical": [
            "電気設備技術基準の解釈（経済産業省告示）",
            "公共建築工事標準仕様書(電気設備工事編)"
        ],
        "mechanical": [
            "建築設備設計基準（国交省・官庁営繕部）",
            "公共建築設備工事標準図（機械設備編）",
            "標準仕様書（国交省）"
        ],
        "gas": [
            "ガス事業法",
            "LPガス法",
            "都市ガス事業法",
            "液化石油ガス法"
        ]
    }

    # 法令コードマッピング
    LAW_CODE_MAP = {
        "内線規程（JEAC 8001）": "JEAC8001",
        "電気設備技術基準の解釈": "DENSETSU",
        "建築設備設計基準": "KENSETSUBI",
        "公共建築工事標準仕様書(電気設備工事編)": "KOHKYO_DENKI",
        "公共建築設備工事標準図（機械設備編）": "KOHKYO_KIKAI",
        "学校施設設備基準": "GAKKO_SHISETSU",
        "消防法": "SHOBO",
        "ガス事業法": "GAS_JIGYOU",
        "都市ガス事業法": "TOSHI_GAS"
    }

    def __init__(self):
        load_dotenv()
        self.client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.model_name = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

    def extract_legal_requirements(
        self,
        spec_text: str,
        discipline: DisciplineType
    ) -> List[Dict[str, Any]]:
        """
        仕様書から法令要件を抽出

        Args:
            spec_text: 仕様書テキスト
            discipline: 工事区分

        Returns:
            法令要件のリスト
        """
        logger.info(f"Extracting legal requirements for discipline: {discipline}")

        # 工事区分別の重要法令を取得
        critical_laws = self.CRITICAL_LAWS.get("common", []).copy()

        if discipline == DisciplineType.ELECTRICAL:
            critical_laws.extend(self.CRITICAL_LAWS.get("electrical", []))
        elif discipline == DisciplineType.MECHANICAL:
            critical_laws.extend(self.CRITICAL_LAWS.get("mechanical", []))
        elif discipline == DisciplineType.GAS:
            critical_laws.extend(self.CRITICAL_LAWS.get("gas", []))

        critical_laws_str = "\n".join([f"- {law}" for law in critical_laws])

        prompt = f"""あなたは建設業の法令遵守の専門家です。以下の仕様書から、法令に基づく要求事項を抽出してください。

# 重要な関係法令（特に注意が必要）

{critical_laws_str}

# 仕様書テキスト

{spec_text[:60000]}

# 抽出指示

以下の観点で法令要件を抽出してください：

## 1. 電気設備の法令要件
- **JEAC 8001（内線規程）**: 電圧区分、ケーブルサイズ、保護装置
- **電気設備技術基準**: 接地、絶縁、保護
- **公共建築工事標準仕様書**: 施工基準、材料規格

## 2. 学校施設の法令要件
- **学校施設設備基準**: 照度、換気、衛生
- **学校環境衛生基準**: 室内環境、空気質
- **消防法**: 消防設備、避難設備

## 3. ガス設備の法令要件
- **ガス事業法**: ガス配管基準、安全装置
- **都市ガス事業法**: 供給基準、検査

## 4. 建築設備の法令要件
- **建築設備設計基準**: 設計手法、性能基準
- **東京都工事標準仕様書**: 地域特有の基準

# 出力形式

各法令要件について、以下の情報を抽出してください：

```json
[
  {{
    "law_code": "JEAC8001",
    "law_name": "内線規程（JEAC 8001）",
    "requirement_type": "技術基準",
    "topic": "低圧屋内配線の保護",
    "description": "低圧屋内配線には適切な過電流保護装置を設置すること",
    "target_value": "定格電流の1.25倍以下",
    "applicable_items": ["分電盤", "配線用遮断器", "幹線"],
    "source_page": 12,
    "confidence": 0.9
  }},
  {{
    "law_code": "GAKKO_SHISETSU",
    "law_name": "学校施設設備基準",
    "requirement_type": "性能基準",
    "topic": "教室の照度",
    "description": "普通教室の机上面照度は300ルクス以上を確保すること",
    "target_value": "300lx以上",
    "applicable_items": ["LED照明", "照明器具"],
    "source_page": 9,
    "confidence": 0.95
  }},
  {{
    "law_code": "SHOBO",
    "law_name": "消防法",
    "requirement_type": "安全基準",
    "topic": "消防設備の設置",
    "description": "学校施設には自動火災報知設備の設置が義務付けられている",
    "target_value": "自動火災報知設備一式",
    "applicable_items": ["火災報知設備", "感知器", "受信機"],
    "source_page": 15,
    "confidence": 1.0
  }}
]
```

必ずJSON形式で回答してください。仕様書に記載がない場合でも、工事種別から一般的に適用される法令要件を推測して含めてください。"""

        try:
            response = self.client.messages.create(
                model=self.model_name,
                max_tokens=16000,
                temperature=0,
                messages=[{"role": "user", "content": prompt}]
            )

            # コスト記録
            record_cost(
                operation="法令要件抽出",
                model_name=self.model_name,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                metadata={"source": "extract_legal_requirements", "discipline": discipline.value}
            )

            response_text = response.content[0].text
            logger.debug(f"LLM Response: {response_text[:500]}...")

            # JSONを抽出
            json_start = response_text.find('[')
            json_end = response_text.rfind(']') + 1

            if json_start == -1 or json_end == 0:
                logger.error("No JSON found in response")
                return []

            json_str = response_text[json_start:json_end]
            requirements_data = json.loads(json_str)

            logger.info(f"Extracted {len(requirements_data)} legal requirements")
            return requirements_data

        except Exception as e:
            logger.error(f"Error extracting legal requirements: {e}")
            return []

    def convert_to_legal_references(
        self,
        requirements_data: List[Dict[str, Any]]
    ) -> List[LegalReference]:
        """
        抽出データをLegalReferenceオブジェクトに変換

        Args:
            requirements_data: 抽出された法令要件データ

        Returns:
            LegalReferenceオブジェクトのリスト
        """
        legal_refs = []

        for req in requirements_data:
            legal_ref = LegalReference(
                law_code=req.get("law_code", ""),
                title=req.get("law_name", ""),
                article=req.get("topic", ""),
                year=2024,  # デフォルト年版
                clause_text=req.get("description", ""),
                norm_value={"target": req.get("target_value", "")},
                citation={
                    "requirement_type": req.get("requirement_type", ""),
                    "source_page": str(req.get("source_page", ""))
                },
                relevance_score=req.get("confidence", 0.0)
            )
            legal_refs.append(legal_ref)

        return legal_refs

    def validate_estimate_against_laws(
        self,
        estimate_items: List[EstimateItem],
        legal_refs: List[LegalReference]
    ) -> List[Dict[str, Any]]:
        """
        見積項目が法令要件を満たしているか検証

        Args:
            estimate_items: 見積項目リスト
            legal_refs: 法令参照リスト

        Returns:
            検証結果（不適合項目のリスト）
        """
        logger.info("Validating estimate items against legal requirements")

        violations = []

        # 法令要件ごとに該当する見積項目が存在するかチェック
        for legal_ref in legal_refs:
            applicable_items = []

            # 法令に該当する見積項目を検索
            for item in estimate_items:
                # 簡易マッチング（項目名で判定）
                # TODO: より高度なマッチングロジックを実装
                if any(keyword in item.name for keyword in ["照明", "分電盤", "配線", "火災報知", "消防"]):
                    applicable_items.append(item)

            # 該当項目が見つからない場合は違反として記録
            if not applicable_items and legal_ref.relevance_score >= 0.8:
                violations.append({
                    "law_code": legal_ref.law_code,
                    "law_name": legal_ref.title,
                    "requirement": legal_ref.clause_text,
                    "severity": "high" if legal_ref.relevance_score >= 0.9 else "medium",
                    "message": f"法令要件に該当する見積項目が見つかりません: {legal_ref.title}",
                    "recommendation": f"以下の項目を追加することを検討してください: {legal_ref.norm_value}"
                })

        logger.info(f"Found {len(violations)} potential violations")
        return violations


if __name__ == "__main__":
    # テスト実行
    import sys
    sys.path.insert(0, '.')

    from pipelines.estimate_extractor_v2 import EstimateExtractorV2

    extractor = LegalRequirementExtractor()
    estimator = EstimateExtractorV2()

    spec_path = "test-files/仕様書【都立山崎高等学校仮設校舎等の借入れ】ord202403101060100130187c1e4d0.pdf"

    if Path(spec_path).exists():
        # 仕様書からテキストを抽出
        spec_text = estimator.extract_text_from_pdf(spec_path)

        # 法令要件を抽出
        requirements_data = extractor.extract_legal_requirements(
            spec_text,
            DisciplineType.ELECTRICAL
        )

        print(f"\n✅ 法令要件抽出完了")
        print(f"抽出された法令要件数: {len(requirements_data)}")

        # 最初の5件を表示
        print("\n【抽出された法令要件（最初の5件）】")
        for i, req in enumerate(requirements_data[:5]):
            print(f"\n{i+1}. {req.get('law_name', 'N/A')}")
            print(f"   要件: {req.get('topic', 'N/A')}")
            print(f"   内容: {req.get('description', 'N/A')}")
            print(f"   基準値: {req.get('target_value', 'N/A')}")
            print(f"   信頼度: {req.get('confidence', 0.0)}")

        # LegalReferenceに変換
        legal_refs = extractor.convert_to_legal_references(requirements_data)
        print(f"\n✅ LegalReferenceオブジェクト生成: {len(legal_refs)}件")

    else:
        print(f"❌ 仕様書が見つかりません: {spec_path}")
