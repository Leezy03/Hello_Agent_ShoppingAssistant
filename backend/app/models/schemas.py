"""数据模型定义 - 避雷购物助手"""

from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Literal
from pydantic import BaseModel, Field


# ============ 请求模型 ============

class ShoppingRequest(BaseModel):
    """购物避雷请求"""
    product_name: str = Field(..., description="要查询的产品名称", example="洗地机")
    budget_min: Optional[int] = Field(default=None, description="预算下限(元)", example=1000)
    budget_max: Optional[int] = Field(default=None, description="预算上限(元)", example=3000)
    brand_preferences: List[str] = Field(default=[], description="品牌偏好", example=["追觅", "石头"])
    usage_scenario: str = Field(default="", description="使用场景", example="家用，120平米")
    concerns: List[str] = Field(default=[], description="关注要点", example=["续航", "噪音", "售后"])
    free_text_input: Optional[str] = Field(default="", description="额外要求", example="家里有宠物，毛发多")

    class Config:
        json_schema_extra = {
            "example": {
                "product_name": "洗地机",
                "budget_min": 1000,
                "budget_max": 3000,
                "brand_preferences": ["追觅", "石头"],
                "usage_scenario": "家用，120平米",
                "concerns": ["续航", "噪音", "售后"],
                "free_text_input": "家里有宠物，毛发多"
            }
        }


class ProductSearchRequest(BaseModel):
    """产品搜索请求"""
    keywords: str = Field(..., description="搜索关键词", example="洗地机")
    platform: str = Field(default="all", description="搜索平台: all/bilibili/xiaohongshu/zhihu")


# ============ 响应模型 ============

class EvidenceItem(BaseModel):
    """检索节点产出的结构化证据"""
    evidence_id: str = Field(..., description="证据唯一ID")
    product_name: str = Field(..., description="证据对应的候选产品标准名称")
    evidence_type: Literal["review", "price", "risk"] = Field(..., description="证据类型")
    source_url: Optional[str] = Field(default=None, description="来源URL")
    source_title: str = Field(default="", description="来源标题")
    platform: str = Field(default="", description="来源平台或站点")
    author: Optional[str] = Field(default=None, description="作者或发布主体")
    snippet: str = Field(default="", description="检索结果中的原始摘要或关键片段")
    claims: List[str] = Field(default_factory=list, description="该来源直接支持的事实或观点")
    search_query: Optional[str] = Field(default=None, description="产生该证据的搜索关键词")
    retrieved_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="证据检索时间",
    )


class EvidenceCollectionResult(BaseModel):
    """单个检索节点的结构化证据集合"""
    evidence: List[EvidenceItem] = Field(default_factory=list, description="证据列表")
    coverage_notes: List[str] = Field(default_factory=list, description="来源覆盖或信息不足说明")


class ReviewSource(BaseModel):
    """测评来源"""
    platform: str = Field(..., description="平台: B站/小红书/知乎")
    author: str = Field(..., description="作者/博主名")
    title: str = Field(..., description="测评标题")
    url: Optional[str] = Field(default=None, description="原文链接")
    stance: str = Field(..., description="立场: 推荐/不推荐/中立")
    is_sponsored: bool = Field(default=False, description="是否疑似恰饭(广告)")
    key_points: List[str] = Field(default=[], description="核心观点")
    credibility_score: float = Field(default=5.0, description="可信度评分(1-10)")
    evidence_ids: List[str] = Field(default_factory=list, description="支持该测评来源分析的证据ID")


class Product(BaseModel):
    """产品信息"""
    name: str = Field(..., description="产品名称")
    brand: str = Field(default="", description="品牌")
    model: str = Field(default="", description="型号")
    price_range: str = Field(default="", description="价格区间")
    rating: Optional[float] = Field(default=None, description="综合评分")
    image_url: Optional[str] = Field(default=None, description="产品图片URL")
    specs: Optional[Dict[str, Any]] = Field(default=None, description="关键参数")
    price_evidence_ids: List[str] = Field(default_factory=list, description="支持价格区间的证据ID")
    spec_evidence_ids: List[str] = Field(default_factory=list, description="支持规格参数的证据ID")


class CandidateProduct(BaseModel):
    """候选产品,用于统一后续证据归档目标"""
    name: str = Field(..., description="候选产品标准名称")
    brand: str = Field(default="", description="品牌")
    model: str = Field(default="", description="型号")
    reason: str = Field(default="", description="入选候选列表的原因")


class CandidateExtractionResult(BaseModel):
    """候选产品抽取结果"""
    category: str = Field(default="", description="产品品类")
    candidates: List[CandidateProduct] = Field(default=[], description="候选产品列表")


class ProductAnalysis(BaseModel):
    """单个产品的深度分析"""
    product: Product = Field(..., description="产品信息")
    reviews: List[ReviewSource] = Field(default=[], description="测评来源列表")
    common_pros: List[str] = Field(default=[], description="公认优点")
    common_cons: List[str] = Field(default=[], description="公认缺点")
    red_flags: List[str] = Field(default=[], description="避雷点")
    controversy_points: List[str] = Field(default=[], description="争议点(博主意见不一致)")
    verdict: str = Field(default="待定", description="结论: 推荐/不推荐/看需求")
    verdict_reason: str = Field(default="", description="结论理由")
    pro_evidence_ids: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="优点文本到证据ID列表的映射",
    )
    con_evidence_ids: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="缺点文本到证据ID列表的映射",
    )
    red_flag_evidence_ids: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="避雷点文本到证据ID列表的映射",
    )
    controversy_evidence_ids: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="争议点文本到证据ID列表的映射",
    )
    verdict_evidence_ids: List[str] = Field(default_factory=list, description="支持产品结论的证据ID")


class ShoppingReport(BaseModel):
    """购物避雷报告"""
    query: str = Field(..., description="用户查询")
    category: str = Field(default="", description="产品品类")
    products: List[ProductAnalysis] = Field(default=[], description="产品分析列表")
    comparison_summary: str = Field(default="", description="横向对比总结")
    final_recommendation: str = Field(default="", description="最终购买建议")
    budget_advice: Optional[str] = Field(default=None, description="预算建议")
    general_tips: List[str] = Field(default=[], description="品类选购通用建议")
    comparison_evidence_ids: List[str] = Field(default_factory=list, description="支持横向对比的证据ID")
    recommendation_evidence_ids: List[str] = Field(default_factory=list, description="支持最终建议的证据ID")
    budget_evidence_ids: List[str] = Field(default_factory=list, description="支持预算建议的证据ID")
    evidence: List[EvidenceItem] = Field(default_factory=list, description="本报告使用的完整证据目录")
    citation_warnings: List[str] = Field(default_factory=list, description="引用一致性校验警告")


class ShoppingReportResponse(BaseModel):
    """购物报告响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: Optional[ShoppingReport] = Field(default=None, description="购物报告数据")


# ============ 任务状态与 Trace ============

class SearchCallTrace(BaseModel):
    """单次搜索工具调用的执行明细"""
    query: str = Field(..., description="搜索关键词")
    status: str = Field(..., description="状态: pending/running/success/empty/failed/cancelled")
    duration_ms: Optional[int] = Field(default=None, description="搜索耗时毫秒")
    result_chars: int = Field(default=0, description="返回结果字符数")
    error_type: Optional[str] = Field(default=None, description="错误类型")
    error_message: Optional[str] = Field(default=None, description="错误信息")


class StepAttemptTrace(BaseModel):
    """工作流节点单次尝试的执行明细"""
    attempt: int = Field(..., description="尝试序号,从1开始")
    status: str = Field(..., description="状态: success/failed")
    duration_ms: int = Field(..., description="本次尝试耗时毫秒")
    tool_call_count: int = Field(default=0, description="本次尝试的工具调用数")
    model_duration_ms: Optional[int] = Field(default=None, description="结构化模型调用耗时毫秒")
    search_calls: List[SearchCallTrace] = Field(default_factory=list, description="搜索调用明细")
    error_type: Optional[str] = Field(default=None, description="错误类型")
    error_message: Optional[str] = Field(default=None, description="错误信息")


class TaskTraceEvent(BaseModel):
    """单个工作流节点的 trace 事件"""
    event_id: str = Field(..., description="Trace事件ID")
    step_key: str = Field(..., description="步骤Key")
    step_name: str = Field(..., description="步骤名称")
    status: str = Field(..., description="状态: running/success/failed/partial")
    message: str = Field(default="", description="事件说明")
    started_at: datetime = Field(..., description="开始时间")
    ended_at: Optional[datetime] = Field(default=None, description="结束时间")
    duration_ms: Optional[int] = Field(default=None, description="耗时毫秒")
    attempt_count: int = Field(default=0, description="节点实际尝试次数")
    tool_call_count: int = Field(default=0, description="节点累计工具调用数")
    attempts: List[StepAttemptTrace] = Field(default_factory=list, description="每次尝试及搜索调用明细")
    error_type: Optional[str] = Field(default=None, description="错误类型")
    error_message: Optional[str] = Field(default=None, description="错误信息")


class ShoppingAnalysisTaskStatus(BaseModel):
    """购物分析任务状态"""
    task_id: str = Field(..., description="任务ID")
    status: str = Field(..., description="任务状态: pending/running/succeeded/partial/failed")
    current_step: Optional[str] = Field(default=None, description="当前步骤")
    progress: int = Field(default=0, description="进度百分比")
    message: str = Field(default="", description="状态说明")
    created_at: datetime = Field(..., description="创建时间")
    updated_at: datetime = Field(..., description="更新时间")
    completed_at: Optional[datetime] = Field(default=None, description="完成时间")
    report: Optional[ShoppingReport] = Field(default=None, description="分析报告")
    error: Optional[str] = Field(default=None, description="失败原因")
    trace: List[TaskTraceEvent] = Field(default_factory=list, description="节点级Trace事件")


class ShoppingTaskCreateResponse(BaseModel):
    """创建购物分析任务响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    task_id: str = Field(..., description="任务ID")


class ShoppingTaskTraceResponse(BaseModel):
    """购物分析任务Trace响应"""
    success: bool = Field(..., description="是否成功")
    task_id: str = Field(..., description="任务ID")
    trace: List[TaskTraceEvent] = Field(default_factory=list, description="Trace事件列表")


# ============ 错误响应 ============

class ErrorResponse(BaseModel):
    """错误响应"""
    success: bool = Field(default=False, description="是否成功")
    message: str = Field(..., description="错误消息")
    error_code: Optional[str] = Field(default=None, description="错误代码")
