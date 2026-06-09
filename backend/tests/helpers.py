import json
import time

from app.agents.shopping_advisor_agent import MultiAgentShoppingAdvisor


class StubAgent:
    def __init__(self, responses, delay=0.0):
        self.responses = list(responses)
        self.delay = delay
        self.calls = 0

    def run(self, query):
        if self.delay:
            time.sleep(self.delay)
        index = self.calls if self.calls < len(self.responses) else len(self.responses) - 1
        self.calls += 1
        response = self.responses[index]
        if isinstance(response, Exception):
            raise response
        return response


def build_candidate_json(name="Apple iPhone 15"):
    return (
        "```json\n"
        + json.dumps(
            {
                "category": "手机",
                "candidates": [
                    {
                        "name": name,
                        "brand": "Apple",
                        "model": "iPhone 15",
                        "reason": "主流候选",
                    }
                ],
            },
            ensure_ascii=False,
        )
        + "\n```"
    )


def build_evidence_json(evidence_type, name="Apple iPhone 15"):
    evidence_defaults = {
        "review": {
            "source_title": "iPhone 15 长期体验",
            "platform": "知乎",
            "author": "测试作者",
            "snippet": "手感稳定，但价格偏高。",
            "claims": ["手感稳定", "价格偏高"],
        },
        "price": {
            "source_title": "iPhone 15 商品页",
            "platform": "京东",
            "author": "测试店铺",
            "snippet": "当前价格信息不足。",
            "claims": ["价格信息不足"],
        },
        "risk": {
            "source_title": "iPhone 15 风险讨论",
            "platform": "论坛",
            "author": "测试用户",
            "snippet": "未检索到明确负面证据。",
            "claims": ["未检索到明确负面证据"],
        },
    }
    item = evidence_defaults[evidence_type]
    return (
        "```json\n"
        + json.dumps(
            {
                "evidence": [
                    {
                        "evidence_id": f"{evidence_type}_001",
                        "product_name": name,
                        "evidence_type": evidence_type,
                        "source_url": f"https://example.com/{evidence_type}",
                        "source_title": item["source_title"],
                        "platform": item["platform"],
                        "author": item["author"],
                        "snippet": item["snippet"],
                        "claims": item["claims"],
                        "search_query": f"{name} {evidence_type}",
                    }
                ],
                "coverage_notes": [],
            },
            ensure_ascii=False,
        )
        + "\n```"
    )


def build_report_json(name="Apple iPhone 15", comparison_summary="对比完成", final_recommendation="建议按需求选择"):
    return (
        "```json\n"
        + json.dumps(
            {
                "query": "手机",
                "category": "手机",
                "products": [
                    {
                        "product": {
                            "name": name,
                            "brand": "Apple",
                            "model": "iPhone 15",
                            "price_range": "信息不足",
                            "rating": None,
                            "image_url": None,
                            "specs": None,
                            "price_evidence_ids": ["price_001"],
                            "spec_evidence_ids": [],
                        },
                        "reviews": [
                            {
                                "platform": "知乎",
                                "author": "测试作者",
                                "title": "iPhone 15 长期体验",
                                "url": "https://example.com/review",
                                "stance": "中立",
                                "is_sponsored": False,
                                "key_points": ["手感稳定", "价格偏高"],
                                "credibility_score": 7.0,
                                "evidence_ids": ["review_001"],
                            }
                        ],
                        "common_pros": ["手感稳定"],
                        "common_cons": ["价格偏高"],
                        "red_flags": ["未检索到明确负面证据"],
                        "controversy_points": [],
                        "verdict": "看需求",
                        "verdict_reason": "测试用例",
                        "pro_evidence_ids": {"手感稳定": ["review_001"]},
                        "con_evidence_ids": {"价格偏高": ["review_001"]},
                        "red_flag_evidence_ids": {"未检索到明确负面证据": ["risk_001"]},
                        "controversy_evidence_ids": {},
                        "verdict_evidence_ids": ["review_001", "price_001", "risk_001"],
                    }
                ],
                "comparison_summary": comparison_summary,
                "final_recommendation": final_recommendation,
                "budget_advice": None,
                "general_tips": ["多平台交叉验证"],
                "comparison_evidence_ids": ["review_001", "price_001", "risk_001"],
                "recommendation_evidence_ids": ["review_001", "price_001", "risk_001"],
                "budget_evidence_ids": [],
            },
            ensure_ascii=False,
        )
        + "\n```"
    )


def build_test_advisor(candidate_responses=None, review_responses=None, price_responses=None, red_flag_responses=None, report_responses=None, delays=None):
    delays = delays or {}
    advisor = MultiAgentShoppingAdvisor.__new__(MultiAgentShoppingAdvisor)
    advisor.candidate_agent = StubAgent(candidate_responses or [build_candidate_json()], delay=delays.get("candidate", 0.0))
    advisor.review_agent = StubAgent(review_responses or [build_evidence_json("review")], delay=delays.get("review", 0.0))
    advisor.price_agent = StubAgent(price_responses or [build_evidence_json("price")], delay=delays.get("price", 0.0))
    advisor.red_flag_agent = StubAgent(red_flag_responses or [build_evidence_json("risk")], delay=delays.get("red_flag", 0.0))
    advisor.report_agent = StubAgent(report_responses or [build_report_json()], delay=delays.get("report", 0.0))
    return advisor
