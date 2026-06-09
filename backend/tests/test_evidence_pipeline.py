import pytest
import json

from app.agents.shopping_advisor_agent import CitationValidationError
from app.models.schemas import (
    CandidateProduct,
    EvidenceItem,
    Product,
    ProductAnalysis,
    ShoppingReport,
)
from tests.helpers import build_evidence_json, build_report_json


def _candidates():
    return [
        CandidateProduct(
            name="Apple iPhone 15",
            brand="Apple",
            model="iPhone 15",
            reason="主流候选",
        )
    ]


def _build_evidence_catalog(advisor):
    evidence = []
    for evidence_type, agent in [
        ("review", advisor.review_agent),
        ("price", advisor.price_agent),
        ("risk", advisor.red_flag_agent),
    ]:
        evidence.extend(
            advisor._parse_evidence_response(
                response=build_evidence_json(evidence_type),
                repair_agent=agent,
                repair_label=f"{evidence_type}证据JSON",
                evidence_type=evidence_type,
                candidates=_candidates(),
            )
        )
    return evidence


def test_retrieval_response_is_parsed_into_structured_evidence(advisor_factory):
    advisor = advisor_factory()

    evidence = advisor._parse_evidence_response(
        response=build_evidence_json("review"),
        repair_agent=advisor.review_agent,
        repair_label="测评证据JSON",
        evidence_type="review",
        candidates=_candidates(),
    )

    assert len(evidence) == 1
    assert evidence[0].evidence_id == "review_001"
    assert evidence[0].product_name == "Apple iPhone 15"
    assert evidence[0].source_url == "https://example.com/review"
    assert evidence[0].claims == ["手感稳定", "价格偏高"]


def test_evidence_product_alignment_prefers_exact_model_over_shorter_name(advisor_factory):
    advisor = advisor_factory()
    candidates = [
        CandidateProduct(
            name="Apple iPhone 15 标准版",
            brand="Apple",
            model="iPhone 15",
        ),
        CandidateProduct(
            name="Apple iPhone 15 Pro",
            brand="Apple",
            model="iPhone 15 Pro",
        ),
        CandidateProduct(
            name="Apple iPhone 15 Pro Max",
            brand="Apple",
            model="iPhone 15 Pro Max",
        ),
    ]

    assert advisor._resolve_candidate_name("Apple iPhone 15", candidates) == "Apple iPhone 15 标准版"
    assert advisor._resolve_candidate_name("Apple iPhone 15 Pro", candidates) == "Apple iPhone 15 Pro"
    assert advisor._resolve_candidate_name("iPhone 15 Pro Max", candidates) == "Apple iPhone 15 Pro Max"


def test_report_product_alias_is_normalized_to_candidate_name(advisor_factory):
    advisor = advisor_factory()
    candidates = [
        CandidateProduct(
            name="戴尔 G5 5500",
            brand="戴尔",
            model="G5 5500",
        )
    ]
    report = ShoppingReport(
        query="游戏本",
        products=[
            ProductAnalysis(
                product=Product(
                    name="戴尔 G5 5500（2020款）",
                    brand="戴尔",
                    model="G5 5500",
                )
            )
        ],
    )

    scope_partial = advisor._normalize_report_products(report, candidates)

    assert report.products[0].product.name == "戴尔 G5 5500"
    assert "报告产品名称已归一化" in report.citation_warnings[0]
    assert scope_partial is False


def test_grouped_report_product_is_removed_without_degrading_valid_products(advisor_factory):
    advisor = advisor_factory()
    candidates = [
        CandidateProduct(name="戴尔 G5 5500", brand="戴尔", model="G5 5500"),
        CandidateProduct(name="戴尔 G5 15 SE", brand="戴尔", model="G5 15 SE"),
        CandidateProduct(name="联想拯救者 Y7000", brand="联想", model="Y7000"),
    ]
    report = ShoppingReport(
        query="游戏本",
        evidence=[
            EvidenceItem(
                evidence_id="review_001",
                product_name="戴尔 G5 5500",
                evidence_type="review",
            ),
            EvidenceItem(
                evidence_id="review_002",
                product_name="戴尔 G5 15 SE",
                evidence_type="review",
            ),
            EvidenceItem(
                evidence_id="review_003",
                product_name="联想拯救者 Y7000",
                evidence_type="review",
            ),
        ],
        products=[
            ProductAnalysis(
                product=Product(
                    name="戴尔 G5（含 5500/15 SE 等衍生款）",
                    brand="戴尔",
                    model="G5 系列",
                ),
                verdict_evidence_ids=["review_001", "review_002"],
            ),
            ProductAnalysis(
                product=Product(
                    name="联想拯救者 Y7000",
                    brand="联想",
                    model="Y7000",
                ),
                verdict_evidence_ids=["review_003"],
            ),
        ],
    )

    scope_partial = advisor._normalize_report_products(report, candidates)

    assert [item.product.name for item in report.products] == ["联想拯救者 Y7000"]
    assert any("已移除无法唯一对齐" in warning for warning in report.citation_warnings)
    assert scope_partial is True


@pytest.mark.parametrize(
    ("evidence_type", "limit"),
    [("review", 3), ("price", 2), ("risk", 3)],
)
def test_evidence_is_limited_per_product(advisor_factory, evidence_type, limit):
    advisor = advisor_factory()
    raw_items = []
    for index in range(5):
        raw_items.append(
            {
                "evidence_id": f"ignored_{index}",
                "product_name": "Apple iPhone 15",
                "evidence_type": evidence_type,
                "source_url": f"https://example.com/{evidence_type}/{index}",
                "source_title": f"source {index}",
                "platform": "测试平台",
                "snippet": f"snippet {index}",
                "claims": [f"claim {index}"],
            }
        )

    evidence = advisor._parse_evidence_response(
        response=json.dumps({"evidence": raw_items, "coverage_notes": []}),
        repair_agent=advisor.report_agent,
        repair_label=f"{evidence_type}证据JSON",
        evidence_type=evidence_type,
        candidates=_candidates(),
    )

    assert len(evidence) == limit
    assert [item.evidence_id for item in evidence] == [
        f"{evidence_type}_{index:03d}"
        for index in range(1, limit + 1)
    ]


def test_citation_validator_accepts_consistent_report(advisor_factory, shopping_request):
    advisor = advisor_factory()
    report = advisor._parse_response(build_report_json(), shopping_request)
    report.evidence = _build_evidence_catalog(advisor)

    advisor._validate_report_citations(report, _candidates())

    assert report.citation_warnings == []


def test_citation_validator_accepts_price_evidence_for_price_pros_and_cons(
    advisor_factory,
    shopping_request,
):
    advisor = advisor_factory()
    report = advisor._parse_response(build_report_json(), shopping_request)
    report.evidence = _build_evidence_catalog(advisor)
    analysis = report.products[0]
    analysis.common_pros = ["当前价格有竞争力"]
    analysis.common_cons = ["不同平台价格差异较大"]
    analysis.pro_evidence_ids = {"当前价格有竞争力": ["price_001"]}
    analysis.con_evidence_ids = {"不同平台价格差异较大": ["price_001"]}

    advisor._validate_report_citations(report, _candidates())

    assert report.citation_warnings == []


def test_citation_validator_rejects_price_evidence_for_red_flags(
    advisor_factory,
    shopping_request,
):
    advisor = advisor_factory()
    report = advisor._parse_response(build_report_json(), shopping_request)
    report.evidence = _build_evidence_catalog(advisor)
    analysis = report.products[0]
    analysis.red_flags = ["存在严重质量问题"]
    analysis.red_flag_evidence_ids = {"存在严重质量问题": ["price_001"]}

    with pytest.raises(CitationValidationError, match="错误类型证据"):
        advisor._validate_report_citations(report, _candidates())


def test_citation_validator_rejects_unknown_evidence_id(advisor_factory, shopping_request):
    advisor = advisor_factory()
    report = advisor._parse_response(build_report_json(), shopping_request)
    report.evidence = _build_evidence_catalog(advisor)
    report.products[0].verdict_evidence_ids = ["missing_001"]

    with pytest.raises(CitationValidationError, match="不存在的证据ID"):
        advisor._validate_report_citations(report, _candidates())


def test_citation_validator_rejects_cross_product_reference(advisor_factory, shopping_request):
    advisor = advisor_factory()
    report = advisor._parse_response(build_report_json(), shopping_request)
    report.evidence = _build_evidence_catalog(advisor)
    report.evidence[0].product_name = "其他品牌 其他型号"

    with pytest.raises(CitationValidationError, match="跨产品引用"):
        advisor._validate_report_citations(report, _candidates())
