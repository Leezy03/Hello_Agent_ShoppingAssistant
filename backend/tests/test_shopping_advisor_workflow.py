import time

from app.models.schemas import ShoppingReport

from tests.helpers import build_report_json


def test_analyze_product_success_path(advisor_factory, shopping_request):
    advisor = advisor_factory()

    report = advisor.analyze_product(shopping_request)

    assert isinstance(report, ShoppingReport)
    assert report.query == "手机"
    assert report.products[0].product.name == "Apple iPhone 15"
    assert report.final_recommendation == "建议按需求选择"
    assert {item.evidence_type for item in report.evidence} == {"review", "price", "risk"}
    assert report.products[0].verdict_evidence_ids == ["review_001", "price_001", "risk_001"]


def test_analyze_product_runs_retrieval_steps_in_parallel(advisor_factory, shopping_request):
    advisor = advisor_factory(
        delays={"review": 0.5, "price": 0.5, "red_flag": 0.5},
        report_responses=[build_report_json(comparison_summary="并行成功")],
    )

    start = time.perf_counter()
    report = advisor.analyze_product(shopping_request)
    elapsed = time.perf_counter() - start

    assert report.comparison_summary == "并行成功"
    assert elapsed < 1.2


def test_candidate_failure_falls_back_to_default_candidates(advisor_factory, shopping_request):
    advisor = advisor_factory(
        candidate_responses=[RuntimeError("mcp tool unavailable")],
        report_responses=[RuntimeError("llm unavailable"), RuntimeError("llm unavailable")],
    )

    report = advisor.analyze_product(shopping_request)

    assert report.products[0].product.name == "手机"
    assert advisor.candidate_agent.calls == 2
