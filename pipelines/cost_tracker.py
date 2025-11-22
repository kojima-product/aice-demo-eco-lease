"""
LLM API コスト追跡モジュール

API呼び出しごとのトークン使用量と料金を記録・集計します。
"""

import os
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List
from loguru import logger


class CostTracker:
    """
    LLM API コスト追跡クラス

    機能:
    - API呼び出しごとのトークン使用量を記録
    - 料金を自動計算
    - ローカルファイルに保存
    - 集計情報の提供
    """

    # Claude API 料金（2024年時点、USD/1Mトークン）
    PRICING = {
        "claude-sonnet-4-20250514": {
            "input": 3.00,   # $3.00 / 1M input tokens
            "output": 15.00  # $15.00 / 1M output tokens
        },
        "claude-3-5-sonnet-20241022": {
            "input": 3.00,
            "output": 15.00
        },
        "claude-3-opus-20240229": {
            "input": 15.00,
            "output": 75.00
        },
        "claude-3-haiku-20240307": {
            "input": 0.25,
            "output": 1.25
        },
        # デフォルト（不明なモデル用）
        "default": {
            "input": 3.00,
            "output": 15.00
        }
    }

    # USD/JPY レート（概算）
    USD_JPY_RATE = 150.0

    def __init__(self, log_path: str = "logs/api_costs.json"):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.records: List[Dict[str, Any]] = []

        # 既存ログを読み込み
        if self.log_path.exists():
            try:
                with open(self.log_path, 'r', encoding='utf-8') as f:
                    self.records = json.load(f)
                logger.info(f"Loaded {len(self.records)} cost records")
            except Exception as e:
                logger.warning(f"Failed to load cost log: {e}")
                self.records = []

    def get_pricing(self, model_name: str) -> Dict[str, float]:
        """モデル名から料金を取得"""
        # モデル名の正規化（バージョン番号を除去）
        for key in self.PRICING:
            if key in model_name or model_name in key:
                return self.PRICING[key]
        return self.PRICING["default"]

    def calculate_cost(
        self,
        model_name: str,
        input_tokens: int,
        output_tokens: int
    ) -> Dict[str, float]:
        """コストを計算"""
        pricing = self.get_pricing(model_name)

        input_cost_usd = (input_tokens / 1_000_000) * pricing["input"]
        output_cost_usd = (output_tokens / 1_000_000) * pricing["output"]
        total_cost_usd = input_cost_usd + output_cost_usd
        total_cost_jpy = total_cost_usd * self.USD_JPY_RATE

        return {
            "input_cost_usd": input_cost_usd,
            "output_cost_usd": output_cost_usd,
            "total_cost_usd": total_cost_usd,
            "total_cost_jpy": total_cost_jpy
        }

    def record(
        self,
        operation: str,
        model_name: str,
        input_tokens: int,
        output_tokens: int,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        API呼び出しを記録

        Args:
            operation: 操作種別（"見積生成", "KB抽出", "法令抽出" など）
            model_name: 使用モデル名
            input_tokens: 入力トークン数
            output_tokens: 出力トークン数
            metadata: 追加情報（ファイル名など）

        Returns:
            記録されたレコード
        """
        cost = self.calculate_cost(model_name, input_tokens, output_tokens)

        record = {
            "timestamp": datetime.now().isoformat(),
            "operation": operation,
            "model": model_name,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cost_usd": cost["total_cost_usd"],
            "cost_jpy": cost["total_cost_jpy"],
            "metadata": metadata or {}
        }

        self.records.append(record)
        self._save()

        logger.info(
            f"Cost recorded: {operation} - "
            f"{input_tokens:,} in / {output_tokens:,} out = "
            f"${cost['total_cost_usd']:.4f} (¥{cost['total_cost_jpy']:.2f})"
        )

        return record

    def _save(self):
        """ログをファイルに保存"""
        try:
            with open(self.log_path, 'w', encoding='utf-8') as f:
                json.dump(self.records, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save cost log: {e}")

    def get_summary(
        self,
        days: Optional[int] = None,
        operation: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        集計情報を取得

        Args:
            days: 過去N日間のみ集計（Noneは全期間）
            operation: 特定の操作のみ集計

        Returns:
            集計情報
        """
        records = self.records

        # 日数フィルタ
        if days:
            cutoff = datetime.now().timestamp() - (days * 24 * 60 * 60)
            records = [
                r for r in records
                if datetime.fromisoformat(r["timestamp"]).timestamp() > cutoff
            ]

        # 操作フィルタ
        if operation:
            records = [r for r in records if r["operation"] == operation]

        if not records:
            return {
                "total_records": 0,
                "total_tokens": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_cost_usd": 0,
                "total_cost_jpy": 0,
                "by_operation": {},
                "by_date": {}
            }

        # 操作別集計
        by_operation = {}
        for r in records:
            op = r["operation"]
            if op not in by_operation:
                by_operation[op] = {
                    "count": 0,
                    "tokens": 0,
                    "cost_usd": 0,
                    "cost_jpy": 0
                }
            by_operation[op]["count"] += 1
            by_operation[op]["tokens"] += r["total_tokens"]
            by_operation[op]["cost_usd"] += r["cost_usd"]
            by_operation[op]["cost_jpy"] += r["cost_jpy"]

        # 日別集計
        by_date = {}
        for r in records:
            date = r["timestamp"][:10]  # YYYY-MM-DD
            if date not in by_date:
                by_date[date] = {
                    "count": 0,
                    "tokens": 0,
                    "cost_usd": 0,
                    "cost_jpy": 0
                }
            by_date[date]["count"] += 1
            by_date[date]["tokens"] += r["total_tokens"]
            by_date[date]["cost_usd"] += r["cost_usd"]
            by_date[date]["cost_jpy"] += r["cost_jpy"]

        return {
            "total_records": len(records),
            "total_tokens": sum(r["total_tokens"] for r in records),
            "total_input_tokens": sum(r["input_tokens"] for r in records),
            "total_output_tokens": sum(r["output_tokens"] for r in records),
            "total_cost_usd": sum(r["cost_usd"] for r in records),
            "total_cost_jpy": sum(r["cost_jpy"] for r in records),
            "by_operation": by_operation,
            "by_date": dict(sorted(by_date.items(), reverse=True))
        }

    def get_recent_records(self, limit: int = 50) -> List[Dict[str, Any]]:
        """最近のレコードを取得"""
        return list(reversed(self.records[-limit:]))

    def clear_records(self):
        """全レコードをクリア"""
        self.records = []
        self._save()
        logger.info("Cost records cleared")


# グローバルインスタンス（シングルトン的に使用）
_tracker_instance: Optional[CostTracker] = None


def get_tracker() -> CostTracker:
    """グローバルトラッカーを取得"""
    global _tracker_instance
    if _tracker_instance is None:
        _tracker_instance = CostTracker()
    return _tracker_instance


def record_cost(
    operation: str,
    model_name: str,
    input_tokens: int,
    output_tokens: int,
    metadata: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """コストを記録（簡易関数）"""
    return get_tracker().record(operation, model_name, input_tokens, output_tokens, metadata)


if __name__ == "__main__":
    # テスト
    tracker = CostTracker()

    # テスト記録
    tracker.record(
        operation="見積生成",
        model_name="claude-sonnet-4-20250514",
        input_tokens=5000,
        output_tokens=2000,
        metadata={"file": "test.pdf"}
    )

    tracker.record(
        operation="KB抽出",
        model_name="claude-sonnet-4-20250514",
        input_tokens=10000,
        output_tokens=3000,
        metadata={"file": "estimate.pdf"}
    )

    # 集計表示
    summary = tracker.get_summary()
    print(f"\n=== コスト集計 ===")
    print(f"総レコード数: {summary['total_records']}")
    print(f"総トークン数: {summary['total_tokens']:,}")
    print(f"総コスト: ${summary['total_cost_usd']:.4f} (¥{summary['total_cost_jpy']:.2f})")

    print(f"\n操作別:")
    for op, stats in summary['by_operation'].items():
        print(f"  {op}: {stats['count']}回, {stats['tokens']:,}トークン, ¥{stats['cost_jpy']:.2f}")
