"""Evaluation runner for the AI shopping assistant.

Usage from backend/:
    python -m evals.shopping_eval --validate-only
    python -m evals.shopping_eval --live --limit 3 --output evals/results/latest.json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents.shopping_advisor_agent import get_shopping_advisor  # noqa: E402
from app.models.schemas import ShoppingReport, ShoppingRequest  # noqa: E402

DEFAULT_CASES_PATH = Path(__file__).with_name("shopping_eval_cases.json")
VALID_VERDICT_TERMS = ("推荐", "不推荐", "看需求", "待定")
FORBIDDEN_PHRASES = (
    "作为AI",
    "作为一个AI",
    "我无法访问互联网",
    "建议你自行搜索",
    "无法直接访问",
    "基于我已有的知识",
)


@dataclass
class EvalCheck:
    name: str
    passed: bool
    score: float
    reason: str


def load_cases(path: Path = DEFAULT_CASES_PATH) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as file:
        cases = json.load(file)
    if not isinstance(cases, list):
        raise ValueError("评测集必须是JSON数组")
    for case in cases:
        validate_case(case)
    return cases


def validate_case(case: Dict[str, Any]):
    if not case.get("id"):
        raise ValueError("评测case缺少id")
    if not case.get("name"):
        raise ValueError(f"评测case缺少name: {case.get('id')}")
    ShoppingRequest(**case.get("request", {}))
    expected = case.get("expected", {})
    if not isinstance(expected.get("min_products", 1), int):
        raise ValueError(f"min_products必须是整数: {case['id']}")
    for key in ("category_keywords", "must_mention_terms", "risk_terms"):
        if not isinstance(expected.get(key, []), list):
            raise ValueError(f"{key}必须是数组: {case['id']}")


def evaluate_report(report: ShoppingReport, case: Dict[str, Any]) -> Dict[str, Any]:
    expected = case.get("expected", {})
    request = ShoppingRequest(**case["request"])
    text = _report_text(report)
    checks = [
        _check_product_count(report, expected.get("min_products", 1)),
        _check_category(report, expected.get("category_keywords", [])),
        _check_core_sections(report),
        _check_verdicts(report),
        _check_budget_awareness(report, request),
        _check_term_coverage("must_mention_terms", text, expected.get("must_mention_terms", []), min_ratio=0.6),
        _check_term_coverage("risk_terms", text, expected.get("risk_terms", []), min_ratio=0.5),
        _check_citation_integrity(report),
        _check_forbidden_phrases(text),
    ]
    score = round(sum(check.score for check in checks) / len(checks), 4)
    return {
        "case_id": case["id"],
        "case_name": case["name"],
        "score": score,
        "passed": score >= 0.75 and all(
            check.passed
            for check in checks
            if check.name in {"core_sections", "citation_integrity", "forbidden_phrases"}
        ),
        "checks": [check.__dict__ for check in checks],
    }


def run_live_eval(cases: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    advisor = get_shopping_advisor()
    results = []
    for case in cases:
        request = ShoppingRequest(**case["request"])
        report = advisor.analyze_product(request)
        result = evaluate_report(report, case)
        result["report"] = report.model_dump(mode="json")
        results.append(result)
    return summarize_results(results)


def summarize_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not results:
        return {
            "created_at": _now_iso(),
            "case_count": 0,
            "average_score": 0,
            "pass_rate": 0,
            "results": [],
        }
    passed = sum(1 for result in results if result["passed"])
    average_score = sum(result["score"] for result in results) / len(results)
    return {
        "created_at": _now_iso(),
        "case_count": len(results),
        "average_score": round(average_score, 4),
        "pass_rate": round(passed / len(results), 4),
        "results": results,
    }


def _check_product_count(report: ShoppingReport, min_products: int) -> EvalCheck:
    count = len(report.products)
    passed = count >= min_products
    score = 1.0 if passed else count / max(min_products, 1)
    return EvalCheck(
        name="product_count",
        passed=passed,
        score=round(score, 4),
        reason=f"products={count}, expected>={min_products}",
    )


def _check_category(report: ShoppingReport, keywords: List[str]) -> EvalCheck:
    if not keywords:
        return EvalCheck("category_keywords", True, 1.0, "未配置品类关键词")
    haystack = f"{report.category} {report.query}"
    matched = [keyword for keyword in keywords if keyword in haystack]
    passed = bool(matched)
    return EvalCheck(
        name="category_keywords",
        passed=passed,
        score=1.0 if passed else 0.0,
        reason=f"matched={matched or []}",
    )


def _check_core_sections(report: ShoppingReport) -> EvalCheck:
    missing = []
    if not report.products:
        missing.append("products")
    if not report.comparison_summary.strip():
        missing.append("comparison_summary")
    if not report.final_recommendation.strip():
        missing.append("final_recommendation")
    if not report.general_tips:
        missing.append("general_tips")
    for index, analysis in enumerate(report.products):
        if not analysis.common_pros:
            missing.append(f"products[{index}].common_pros")
        if not analysis.common_cons:
            missing.append(f"products[{index}].common_cons")
        if analysis.red_flags is None:
            missing.append(f"products[{index}].red_flags")
        if not analysis.verdict_reason.strip():
            missing.append(f"products[{index}].verdict_reason")
    passed = not missing
    score = max(0.0, 1.0 - len(missing) * 0.12)
    return EvalCheck(
        name="core_sections",
        passed=passed,
        score=round(score, 4),
        reason="完整" if passed else f"missing={missing}",
    )


def _check_verdicts(report: ShoppingReport) -> EvalCheck:
    invalid = [
        analysis.verdict
        for analysis in report.products
        if not any(term in analysis.verdict for term in VALID_VERDICT_TERMS)
    ]
    passed = not invalid
    return EvalCheck(
        name="verdicts",
        passed=passed,
        score=1.0 if passed else 0.0,
        reason="合法" if passed else f"invalid={invalid}",
    )


def _check_budget_awareness(report: ShoppingReport, request: ShoppingRequest) -> EvalCheck:
    if request.budget_min is None and request.budget_max is None:
        return EvalCheck("budget_awareness", True, 1.0, "无预算约束")
    text = f"{report.budget_advice or ''} {report.final_recommendation} {report.comparison_summary}"
    budget_terms = ["预算", "价格", "价位", str(request.budget_min or ""), str(request.budget_max or "")]
    matched = [term for term in budget_terms if term and term in text]
    passed = bool(matched)
    return EvalCheck(
        name="budget_awareness",
        passed=passed,
        score=1.0 if passed else 0.0,
        reason=f"matched={matched or []}",
    )


def _check_term_coverage(name: str, text: str, terms: List[str], min_ratio: float) -> EvalCheck:
    if not terms:
        return EvalCheck(name, True, 1.0, "未配置关键词")
    matched = [term for term in terms if term in text]
    ratio = len(matched) / len(terms)
    return EvalCheck(
        name=name,
        passed=ratio >= min_ratio,
        score=round(ratio, 4),
        reason=f"matched={matched}, total={terms}",
    )


def _check_forbidden_phrases(text: str) -> EvalCheck:
    matched = [phrase for phrase in FORBIDDEN_PHRASES if phrase in text]
    passed = not matched
    return EvalCheck(
        name="forbidden_phrases",
        passed=passed,
        score=1.0 if passed else 0.0,
        reason="未命中禁用表达" if passed else f"matched={matched}",
    )


def _check_citation_integrity(report: ShoppingReport) -> EvalCheck:
    evidence_ids = {item.evidence_id for item in report.evidence}
    referenced_ids = set(report.comparison_evidence_ids)
    referenced_ids.update(report.recommendation_evidence_ids)
    referenced_ids.update(report.budget_evidence_ids)
    for analysis in report.products:
        referenced_ids.update(analysis.product.price_evidence_ids)
        referenced_ids.update(analysis.product.spec_evidence_ids)
        referenced_ids.update(analysis.verdict_evidence_ids)
        for review in analysis.reviews:
            referenced_ids.update(review.evidence_ids)
        for citation_map in [
            analysis.pro_evidence_ids,
            analysis.con_evidence_ids,
            analysis.red_flag_evidence_ids,
            analysis.controversy_evidence_ids,
        ]:
            for ids in citation_map.values():
                referenced_ids.update(ids)

    missing_ids = sorted(referenced_ids - evidence_ids)
    passed = bool(evidence_ids) and bool(referenced_ids) and not missing_ids
    if missing_ids:
        score = max(0.0, 1.0 - len(missing_ids) / max(len(referenced_ids), 1))
    elif not evidence_ids or not referenced_ids:
        score = 0.0
    else:
        score = 1.0
    return EvalCheck(
        name="citation_integrity",
        passed=passed,
        score=round(score, 4),
        reason=(
            f"evidence={len(evidence_ids)}, referenced={len(referenced_ids)}"
            if passed
            else f"missing_ids={missing_ids}, evidence={len(evidence_ids)}, referenced={len(referenced_ids)}"
        ),
    )


def _report_text(report: ShoppingReport) -> str:
    parts = [
        report.query,
        report.category,
        report.comparison_summary,
        report.final_recommendation,
        report.budget_advice or "",
        " ".join(report.general_tips),
    ]
    for analysis in report.products:
        parts.extend(
            [
                analysis.product.name,
                analysis.product.brand,
                analysis.product.model,
                analysis.product.price_range,
                analysis.verdict,
                analysis.verdict_reason,
                " ".join(analysis.common_pros),
                " ".join(analysis.common_cons),
                " ".join(analysis.red_flags),
                " ".join(analysis.controversy_points),
            ]
        )
        for review in analysis.reviews:
            parts.extend([review.platform, review.author, review.title, review.stance, " ".join(review.key_points)])
    return " ".join(part for part in parts if part)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_output(summary: Dict[str, Any], output: Optional[Path]):
    payload = json.dumps(summary, ensure_ascii=False, indent=2)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload + "\n", encoding="utf-8")
    print(payload)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run shopping assistant evals")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH, help="Path to eval cases JSON")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of cases")
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON output path")
    parser.add_argument("--validate-only", action="store_true", help="Only validate eval cases")
    parser.add_argument("--live", action="store_true", help="Run live Agent workflow for each case")
    args = parser.parse_args(argv)

    cases = load_cases(args.cases)
    if args.limit is not None:
        cases = cases[: args.limit]

    if args.validate_only:
        summary = {
            "created_at": _now_iso(),
            "case_count": len(cases),
            "cases": [{"id": case["id"], "name": case["name"]} for case in cases],
            "status": "valid",
        }
        _write_output(summary, args.output)
        return 0

    if not args.live:
        parser.error("请指定 --validate-only 校验评测集，或指定 --live 运行真实Agent评测")

    summary = run_live_eval(cases)
    _write_output(summary, args.output)
    return 0 if summary["pass_rate"] >= 0.7 else 1


if __name__ == "__main__":
    raise SystemExit(main())
