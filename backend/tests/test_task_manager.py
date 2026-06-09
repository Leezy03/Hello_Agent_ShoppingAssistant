from app.models.schemas import Product, ProductAnalysis, ShoppingReport
from app.services.task_manager import RedisTaskStore


class FakeRedisLock:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class FakeRedisClient:
    def __init__(self):
        self.values = {}
        self.expirations = {}

    def ping(self):
        return True

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, ex=None):
        self.values[key] = value
        self.expirations[key] = ex
        return True

    def scan_iter(self, match):
        prefix = match.rstrip("*")
        return [key for key in self.values if key.startswith(prefix)]

    def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)
            self.expirations.pop(key, None)
        return len(keys)

    def lock(self, name, timeout=None, blocking_timeout=None):
        return FakeRedisLock()


def _build_report():
    return ShoppingReport(
        query="手机",
        category="手机",
        products=[
            ProductAnalysis(
                product=Product(name="Apple iPhone 15", brand="Apple", model="iPhone 15", price_range="信息不足"),
                common_pros=["体验稳定"],
                common_cons=["价格偏高"],
                red_flags=["未检索到明确负面证据"],
                controversy_points=[],
                verdict="看需求",
                verdict_reason="测试返回",
            )
        ],
        comparison_summary="对比完成",
        final_recommendation="建议按需求选择",
        budget_advice=None,
        general_tips=["多平台交叉验证"],
    )


def test_redis_task_store_persists_task_status_and_trace():
    client = FakeRedisClient()
    store = RedisTaskStore(redis_url="redis://unused", ttl_seconds=60, client=client)

    task_id = store.create_task()
    event_id = store.start_step(task_id, "candidate", "候选产品抽取", 5, "候选产品抽取开始执行")
    store.finish_step(task_id, event_id, "success", "候选产品抽取完成", 20)
    store.complete_task(task_id, _build_report())

    task = store.get_task(task_id)

    assert task is not None
    assert task.status == "succeeded"
    assert task.progress == 100
    assert task.report.query == "手机"
    assert task.trace[0].step_key == "candidate"
    assert task.trace[0].status == "success"
    assert task.trace[0].duration_ms is not None
    assert client.expirations[f"shopping:task:{task_id}"] == 60
