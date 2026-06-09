from evals.shopping_eval import evaluate_report, load_cases
from tests.helpers import build_evidence_json, build_report_json

from app.models.schemas import EvidenceCollectionResult, ShoppingReport


def _report_from_json(response: str) -> ShoppingReport:
    json_text = response.split("```json\n", 1)[1].rsplit("\n```", 1)[0]
    report = ShoppingReport.model_validate_json(json_text)
    for evidence_type in ["review", "price", "risk"]:
        evidence_response = build_evidence_json(evidence_type)
        evidence_json = evidence_response.split("```json\n", 1)[1].rsplit("\n```", 1)[0]
        report.evidence.extend(EvidenceCollectionResult.model_validate_json(evidence_json).evidence)
    return report


def test_eval_cases_are_valid():
    cases = load_cases()

    assert len(cases) >= 10
    assert all(case["id"] for case in cases)


def test_evaluate_report_scores_valid_report():
    case = {
        "id": "phone_case",
        "name": "手机评测",
        "request": {
            "product_name": "手机",
            "budget_min": 3000,
            "budget_max": 6000,
            "brand_preferences": [],
            "usage_scenario": "",
            "concerns": ["拍照", "续航"],
            "free_text_input": "",
        },
        "expected": {
            "category_keywords": ["手机"],
            "min_products": 1,
            "must_mention_terms": ["拍照", "续航"],
            "risk_terms": ["发热", "售后"],
        },
    }
    report = _report_from_json(
        build_report_json(
            comparison_summary="手机对比完成，拍照和续航表现有差异，需注意发热。",
            final_recommendation="预算3000-6000元内建议按需求选择，售后也要考虑。",
        )
    )

    result = evaluate_report(report, case)

    assert result["passed"] is True
    assert result["score"] >= 0.75


def test_evaluate_report_flags_missing_sections():
    case = {
        "id": "bad_case",
        "name": "缺失报告",
        "request": {
            "product_name": "手机",
            "brand_preferences": [],
            "usage_scenario": "",
            "concerns": [],
            "free_text_input": "",
        },
        "expected": {
            "category_keywords": ["手机"],
            "min_products": 2,
            "must_mention_terms": [],
            "risk_terms": [],
        },
    }
    report = ShoppingReport(
        query="手机",
        category="手机",
        products=[],
        comparison_summary="",
        final_recommendation="",
        budget_advice=None,
        general_tips=[],
    )

    result = evaluate_report(report, case)

    assert result["passed"] is False
    assert any(check["name"] == "core_sections" and not check["passed"] for check in result["checks"])
