<template>
  <div class="result-container">
    <!-- 页面头部 -->
    <div class="page-header">
      <a-button class="back-button" size="large" @click="goBack">
        ← 返回首页
      </a-button>
      <a-space size="middle">
        <a-dropdown>
          <template #overlay>
            <a-menu>
              <a-menu-item key="image" @click="exportAsImage">
                📷 导出为图片
              </a-menu-item>
              <a-menu-item key="pdf" @click="exportAsPDF">
                📄 导出为PDF
              </a-menu-item>
            </a-menu>
          </template>
          <a-button type="default">
            📥 导出报告 <DownOutlined />
          </a-button>
        </a-dropdown>
      </a-space>
    </div>

    <div v-if="report" class="content-wrapper">
      <!-- 侧边导航 -->
      <div class="side-nav">
        <a-affix :offset-top="80">
          <a-menu mode="inline" :selected-keys="[activeSection]" @click="scrollToSection">
            <a-menu-item key="overview">
              <span>📋 报告概览</span>
            </a-menu-item>
            <a-menu-item v-if="traceEvents.length > 0" key="trace">
              <span>🧭 执行Trace</span>
            </a-menu-item>
            <a-menu-item v-if="report.evidence && report.evidence.length > 0" key="evidence">
              <span>🔗 证据目录</span>
            </a-menu-item>
            <a-sub-menu key="products" title="📦 产品分析">
              <a-menu-item v-for="(p, i) in report.products" :key="`product-${i}`">
                {{ p.product.name }}
              </a-menu-item>
            </a-sub-menu>
            <a-menu-item key="comparison">
              <span>⚖️ 横向对比</span>
            </a-menu-item>
            <a-menu-item key="recommendation">
              <span>✅ 购买建议</span>
            </a-menu-item>
          </a-menu>
        </a-affix>
      </div>

      <!-- 主内容区 -->
      <div class="main-content">
        <!-- 报告概览 -->
        <a-card id="overview" :bordered="false" class="overview-card">
          <template #title>
            <span>🛡️ {{ report.query }} 避雷报告</span>
          </template>
          <div class="overview-content">
            <div class="info-item">
              <span class="info-label">📦 产品品类:</span>
              <span class="info-value">{{ report.category }}</span>
            </div>
            <div class="info-item" v-if="report.budget_advice">
              <span class="info-label">💰 预算建议:</span>
              <span class="info-value">{{ report.budget_advice }}</span>
            </div>
            <div class="info-item">
              <span class="info-label">📊 分析产品数:</span>
              <span class="info-value">{{ report.products.length }} 个</span>
            </div>
          </div>
        </a-card>

        <!-- 执行Trace -->
        <a-card id="trace" title="🧭 Agent执行Trace" :bordered="false" class="trace-card" v-if="traceEvents.length > 0">
          <div v-if="taskId" class="task-id-row">任务ID: {{ taskId }}</div>
          <a-table :data-source="traceEvents" :pagination="false" size="small" bordered row-key="event_id" :scroll="{ x: 980 }">
            <a-table-column title="状态" key="status" :width="90">
              <template #default="{ record }">
                <a-tag :color="getTraceStatusColor(record.status)">
                  {{ getTraceStatusText(record.status) }}
                </a-tag>
              </template>
            </a-table-column>
            <a-table-column title="节点" data-index="step_name" key="step_name" :width="140" />
            <a-table-column title="耗时" key="duration_ms" :width="90">
              <template #default="{ record }">
                {{ formatDuration(record.duration_ms) }}
              </template>
            </a-table-column>
            <a-table-column title="尝试" data-index="attempt_count" key="attempt_count" :width="70" />
            <a-table-column title="工具调用" data-index="tool_call_count" key="tool_call_count" :width="90" />
            <a-table-column title="说明" data-index="message" key="message" />
            <a-table-column title="错误" key="error" :width="180">
              <template #default="{ record }">
                <span v-if="record.error_type">{{ record.error_type }}: {{ record.error_message }}</span>
                <span v-else>-</span>
              </template>
            </a-table-column>
            <template #expandedRowRender="{ record }">
              <div v-if="record.attempts && record.attempts.length > 0" class="trace-attempts">
                <section
                  v-for="attempt in record.attempts"
                  :key="`${record.event_id}-${attempt.attempt}`"
                  class="trace-attempt"
                >
                  <div class="trace-attempt-summary">
                    <a-tag :color="getTraceStatusColor(attempt.status)">
                      第{{ attempt.attempt }}次 {{ getTraceStatusText(attempt.status) }}
                    </a-tag>
                    <span>总耗时 {{ formatDuration(attempt.duration_ms) }}</span>
                    <span>工具调用 {{ attempt.tool_call_count }}</span>
                    <span v-if="attempt.model_duration_ms !== undefined && attempt.model_duration_ms !== null">
                      模型耗时 {{ formatDuration(attempt.model_duration_ms) }}
                    </span>
                    <span v-if="attempt.error_type" class="trace-attempt-error">
                      {{ attempt.error_type }}: {{ attempt.error_message }}
                    </span>
                  </div>
                  <a-table
                    v-if="attempt.search_calls && attempt.search_calls.length > 0"
                    :data-source="attempt.search_calls"
                    :pagination="false"
                    size="small"
                    row-key="query"
                  >
                    <a-table-column title="搜索词" data-index="query" key="query" />
                    <a-table-column title="状态" key="status" :width="90">
                      <template #default="{ record: searchCall }">
                        <a-tag :color="getTraceStatusColor(searchCall.status)">
                          {{ getTraceStatusText(searchCall.status) }}
                        </a-tag>
                      </template>
                    </a-table-column>
                    <a-table-column title="耗时" key="duration" :width="90">
                      <template #default="{ record: searchCall }">
                        {{ formatDuration(searchCall.duration_ms) }}
                      </template>
                    </a-table-column>
                    <a-table-column title="结果字符" data-index="result_chars" key="result_chars" :width="90" />
                    <a-table-column title="错误" key="search_error" :width="220">
                      <template #default="{ record: searchCall }">
                        <span v-if="searchCall.error_type">
                          {{ searchCall.error_type }}: {{ searchCall.error_message }}
                        </span>
                        <span v-else>-</span>
                      </template>
                    </a-table-column>
                  </a-table>
                </section>
              </div>
            </template>
          </a-table>
        </a-card>

        <!-- 证据目录 -->
        <a-card
          id="evidence"
          title="🔗 检索证据目录"
          :bordered="false"
          class="evidence-card"
          v-if="report.evidence && report.evidence.length > 0"
        >
          <a-alert
            v-for="warning in report.citation_warnings || []"
            :key="warning"
            :message="warning"
            type="warning"
            show-icon
            class="citation-warning"
          />
          <a-table
            :data-source="report.evidence"
            :pagination="{ pageSize: 8 }"
            size="small"
            bordered
            row-key="evidence_id"
            :scroll="{ x: 980 }"
          >
            <a-table-column title="证据ID" data-index="evidence_id" key="evidence_id" :width="110" />
            <a-table-column title="类型" key="evidence_type" :width="80">
              <template #default="{ record }">
                <a-tag :color="getEvidenceTypeColor(record.evidence_type)">
                  {{ getEvidenceTypeText(record.evidence_type) }}
                </a-tag>
              </template>
            </a-table-column>
            <a-table-column title="产品" data-index="product_name" key="product_name" :width="170" />
            <a-table-column title="来源" key="source" :width="220">
              <template #default="{ record }">
                <a
                  v-if="record.source_url"
                  :href="record.source_url"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {{ record.source_title || record.platform || record.source_url }}
                </a>
                <span v-else>{{ record.source_title || record.platform || '来源链接缺失' }}</span>
              </template>
            </a-table-column>
            <a-table-column title="平台" data-index="platform" key="platform" :width="100" />
            <a-table-column title="证据摘要" key="snippet">
              <template #default="{ record }">
                <div class="evidence-snippet">{{ record.snippet || '信息不足' }}</div>
                <div v-for="claim in record.claims" :key="claim" class="evidence-claim">• {{ claim }}</div>
              </template>
            </a-table-column>
          </a-table>
        </a-card>

        <!-- 产品分析卡片 -->
        <div v-for="(analysis, index) in report.products" :key="index" :id="`product-${index}`" class="product-section">
          <a-card :bordered="false" class="product-card">
            <template #title>
              <div class="product-header">
                <span class="product-name">{{ analysis.product.name }}</span>
                <a-tag :color="getVerdictColor(analysis.verdict)" class="verdict-tag">
                  {{ getVerdictEmoji(analysis.verdict) }} {{ analysis.verdict }}
                </a-tag>
              </div>
            </template>

            <!-- 产品基本信息 -->
            <div class="product-info">
              <a-descriptions :column="3" size="small" bordered>
                <a-descriptions-item label="品牌">{{ analysis.product.brand }}</a-descriptions-item>
                <a-descriptions-item label="型号">{{ analysis.product.model }}</a-descriptions-item>
                <a-descriptions-item label="价格区间">{{ analysis.product.price_range }}</a-descriptions-item>
                <a-descriptions-item v-if="analysis.product.rating" label="综合评分">
                  {{ analysis.product.rating }} ⭐
                </a-descriptions-item>
              </a-descriptions>
            </div>

            <!-- 关键参数 -->
            <div v-if="analysis.product.specs && Object.keys(analysis.product.specs).length > 0" class="specs-section">
              <h4>📐 关键参数</h4>
              <a-descriptions :column="2" size="small" bordered>
                <a-descriptions-item v-for="(value, key) in analysis.product.specs" :key="key" :label="String(key)">
                  {{ value }}
                </a-descriptions-item>
              </a-descriptions>
            </div>

            <!-- 优缺点 -->
            <a-row :gutter="16" style="margin-top: 16px;">
              <a-col :span="12">
                <div class="pros-section">
                  <h4>✅ 公认优点</h4>
                  <div v-for="(pro, i) in analysis.common_pros" :key="i" class="point-item pro-item">
                    <span class="point-icon">👍</span> {{ pro }}
                  </div>
                </div>
              </a-col>
              <a-col :span="12">
                <div class="cons-section">
                  <h4>❌ 公认缺点</h4>
                  <div v-for="(con, i) in analysis.common_cons" :key="i" class="point-item con-item">
                    <span class="point-icon">👎</span> {{ con }}
                  </div>
                </div>
              </a-col>
            </a-row>

            <!-- 避雷点 -->
            <div v-if="analysis.red_flags && analysis.red_flags.length > 0" class="red-flags-section">
              <h4>🚩 避雷点</h4>
              <a-alert
                v-for="(flag, i) in analysis.red_flags"
                :key="i"
                :message="flag"
                type="error"
                show-icon
                style="margin-bottom: 8px;"
              />
            </div>

            <!-- 争议点 -->
            <div v-if="analysis.controversy_points && analysis.controversy_points.length > 0" class="controversy-section">
              <h4>⚠️ 争议点（博主意见不一致）</h4>
              <a-alert
                v-for="(point, i) in analysis.controversy_points"
                :key="i"
                :message="point"
                type="warning"
                show-icon
                style="margin-bottom: 8px;"
              />
            </div>

            <!-- 测评来源分析 -->
            <div v-if="analysis.reviews && analysis.reviews.length > 0" class="reviews-section">
              <h4>📋 测评来源分析</h4>
              <a-table :data-source="analysis.reviews" :pagination="false" size="small" bordered row-key="author">
                <a-table-column title="平台" data-index="platform" key="platform" :width="80" />
                <a-table-column title="博主" data-index="author" key="author" :width="120" />
                <a-table-column title="立场" key="stance" :width="80">
                  <template #default="{ record }">
                    <a-tag :color="getStanceColor(record.stance)">{{ record.stance }}</a-tag>
                  </template>
                </a-table-column>
                <a-table-column title="恰饭?" key="is_sponsored" :width="70">
                  <template #default="{ record }">
                    <a-tag :color="record.is_sponsored ? 'red' : 'green'">
                      {{ record.is_sponsored ? '疑似' : '否' }}
                    </a-tag>
                  </template>
                </a-table-column>
                <a-table-column title="可信度" key="credibility_score" :width="80">
                  <template #default="{ record }">
                    <span :style="{ color: record.credibility_score >= 7 ? '#52c41a' : record.credibility_score >= 4 ? '#faad14' : '#ff4d4f', fontWeight: 'bold' }">
                      {{ record.credibility_score }}/10
                    </span>
                  </template>
                </a-table-column>
                <a-table-column title="核心观点" key="key_points">
                  <template #default="{ record }">
                    <div v-for="(point, i) in record.key_points" :key="i" class="key-point">• {{ point }}</div>
                  </template>
                </a-table-column>
              </a-table>
            </div>

            <!-- 结论 -->
            <div class="verdict-section">
              <div class="verdict-badge" :class="getVerdictClass(analysis.verdict)">
                {{ getVerdictEmoji(analysis.verdict) }} {{ analysis.verdict }}
              </div>
              <p class="verdict-reason">{{ analysis.verdict_reason }}</p>
            </div>
          </a-card>
        </div>

        <!-- 横向对比 -->
        <a-card id="comparison" title="⚖️ 横向对比" :bordered="false" style="margin-top: 20px;" v-if="report.comparison_summary">
          <p style="font-size: 15px; line-height: 1.8;">{{ report.comparison_summary }}</p>
        </a-card>

        <!-- 最终购买建议 -->
        <a-card id="recommendation" title="✅ 最终购买建议" :bordered="false" style="margin-top: 20px;">
          <a-alert :message="report.final_recommendation" type="info" show-icon style="margin-bottom: 16px;" />
          <div v-if="report.general_tips && report.general_tips.length > 0">
            <h4>💡 选购小贴士</h4>
            <ul class="tips-list">
              <li v-for="(tip, i) in report.general_tips" :key="i">{{ tip }}</li>
            </ul>
          </div>
        </a-card>
      </div>
    </div>

    <a-empty v-else description="没有找到分析报告数据">
      <template #image>
        <div style="font-size: 80px;">🛡️</div>
      </template>
      <template #description>
        <span style="color: #999;">暂无分析数据，请先提交产品查询</span>
      </template>
      <a-button type="primary" @click="goBack">返回首页</a-button>
    </a-empty>

    <!-- 回到顶部按钮 -->
    <a-back-top :visibility-height="300">
      <div class="back-top-button">
        ↑
      </div>
    </a-back-top>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { DownOutlined } from '@ant-design/icons-vue'
import html2canvas from 'html2canvas'
import jsPDF from 'jspdf'
import type { ShoppingReport, TaskTraceEvent } from '@/types'

const router = useRouter()
const report = ref<ShoppingReport | null>(null)
const activeSection = ref('overview')
const taskId = ref('')
const traceEvents = ref<TaskTraceEvent[]>([])

onMounted(() => {
  const data = sessionStorage.getItem('shoppingReport')
  if (data) {
    report.value = JSON.parse(data)
  }

  const traceData = sessionStorage.getItem('shoppingTrace')
  if (traceData) {
    traceEvents.value = JSON.parse(traceData)
  }

  taskId.value = sessionStorage.getItem('shoppingTaskId') || ''
})

const goBack = () => {
  router.push('/')
}

// 滚动到指定区域
const scrollToSection = ({ key }: { key: string }) => {
  activeSection.value = key
  const element = document.getElementById(key)
  if (element) {
    element.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}

// 获取结论颜色
const getVerdictColor = (verdict: string): string => {
  if (verdict.includes('推荐') && !verdict.includes('不')) return 'green'
  if (verdict.includes('不推荐')) return 'red'
  return 'orange'
}

const getVerdictClass = (verdict: string): string => {
  if (verdict.includes('推荐') && !verdict.includes('不')) return 'verdict-positive'
  if (verdict.includes('不推荐')) return 'verdict-negative'
  return 'verdict-neutral'
}

const getVerdictEmoji = (verdict: string): string => {
  if (verdict.includes('推荐') && !verdict.includes('不')) return '✅'
  if (verdict.includes('不推荐')) return '❌'
  return '🤔'
}

// 获取立场颜色
const getStanceColor = (stance: string): string => {
  if (stance.includes('推荐') && !stance.includes('不')) return 'green'
  if (stance.includes('不推荐')) return 'red'
  return 'blue'
}

const getTraceStatusColor = (status: string): string => {
  if (status === 'success') return 'green'
  if (status === 'failed') return 'red'
  if (status === 'partial') return 'orange'
  if (status === 'empty') return 'orange'
  if (status === 'cancelled') return 'orange'
  return 'blue'
}

const getTraceStatusText = (status: string): string => {
  if (status === 'running') return '运行中'
  if (status === 'success') return '成功'
  if (status === 'partial') return '降级'
  if (status === 'failed') return '失败'
  if (status === 'empty') return '无结果'
  if (status === 'cancelled') return '已取消'
  if (status === 'pending') return '等待中'
  return status
}

const formatDuration = (durationMs?: number): string => {
  if (durationMs === undefined || durationMs === null) return '-'
  if (durationMs < 1000) return `${durationMs}ms`
  return `${(durationMs / 1000).toFixed(1)}s`
}

const getEvidenceTypeColor = (type: string): string => {
  if (type === 'review') return 'blue'
  if (type === 'price') return 'green'
  if (type === 'risk') return 'red'
  return 'default'
}

const getEvidenceTypeText = (type: string): string => {
  if (type === 'review') return '测评'
  if (type === 'price') return '价格'
  if (type === 'risk') return '风险'
  return type
}

// 导出为图片
const exportAsImage = async () => {
  try {
    message.loading({ content: '正在生成图片...', key: 'export', duration: 0 })

    const element = document.querySelector('.main-content') as HTMLElement
    if (!element) {
      throw new Error('未找到内容元素')
    }

    const canvas = await html2canvas(element, {
      backgroundColor: '#f5f7fa',
      scale: 2,
      logging: false,
      useCORS: true,
      allowTaint: true
    })

    const link = document.createElement('a')
    link.download = `避雷报告_${report.value?.query}_${new Date().getTime()}.png`
    link.href = canvas.toDataURL('image/png')
    link.click()

    message.success({ content: '图片导出成功!', key: 'export' })
  } catch (error: any) {
    console.error('导出图片失败:', error)
    message.error({ content: `导出图片失败: ${error.message}`, key: 'export' })
  }
}

// 导出为PDF
const exportAsPDF = async () => {
  try {
    message.loading({ content: '正在生成PDF...', key: 'export', duration: 0 })

    const element = document.querySelector('.main-content') as HTMLElement
    if (!element) {
      throw new Error('未找到内容元素')
    }

    const canvas = await html2canvas(element, {
      backgroundColor: '#f5f7fa',
      scale: 2,
      logging: false,
      useCORS: true,
      allowTaint: true
    })

    const imgData = canvas.toDataURL('image/png')
    const pdf = new jsPDF({
      orientation: 'portrait',
      unit: 'mm',
      format: 'a4'
    })

    const imgWidth = 210 // A4宽度(mm)
    const imgHeight = (canvas.height * imgWidth) / canvas.width

    let heightLeft = imgHeight
    let position = 0

    pdf.addImage(imgData, 'PNG', 0, position, imgWidth, imgHeight)
    heightLeft -= 297 // A4高度

    while (heightLeft > 0) {
      position = heightLeft - imgHeight
      pdf.addPage()
      pdf.addImage(imgData, 'PNG', 0, position, imgWidth, imgHeight)
      heightLeft -= 297
    }

    pdf.save(`避雷报告_${report.value?.query}_${new Date().getTime()}.pdf`)

    message.success({ content: 'PDF导出成功!', key: 'export' })
  } catch (error: any) {
    console.error('导出PDF失败:', error)
    message.error({ content: `导出PDF失败: ${error.message}`, key: 'export' })
  }
}
</script>

<style scoped>
.result-container {
  min-height: 100vh;
  background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
  padding: 40px 20px;
}

.page-header {
  max-width: 1200px;
  margin: 0 auto 30px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  animation: fadeInDown 0.6s ease-out;
}

.back-button {
  border-radius: 8px;
  font-weight: 500;
}

/* 内容布局 */
.content-wrapper {
  max-width: 1400px;
  margin: 0 auto;
  display: flex;
  gap: 24px;
}

.side-nav {
  width: 240px;
  flex-shrink: 0;
}

.side-nav :deep(.ant-menu) {
  border-radius: 12px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  background: white;
}

.side-nav :deep(.ant-menu-item) {
  margin: 4px 8px;
  border-radius: 8px;
  transition: all 0.3s ease;
}

.side-nav :deep(.ant-menu-item-selected) {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
}

.side-nav :deep(.ant-menu-item:hover) {
  background: rgba(102, 126, 234, 0.1);
}

.main-content {
  flex: 1;
  min-width: 0;
}

/* 概览卡片 */
.overview-card {
  margin-bottom: 20px;
}

.overview-content {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.info-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.info-label {
  font-size: 14px;
  font-weight: 600;
  color: #666;
}

.info-value {
  font-size: 15px;
  color: #333;
  line-height: 1.6;
}

.trace-card {
  margin-bottom: 20px;
}

.trace-attempts {
  display: grid;
  gap: 12px;
  padding: 8px 0;
}

.trace-attempt {
  border: 1px solid #e8e8e8;
  border-radius: 6px;
  overflow: hidden;
}

.trace-attempt-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 18px;
  align-items: center;
  padding: 10px 12px;
  background: #fafafa;
  color: #555;
  font-size: 13px;
}

.trace-attempt-error {
  color: #cf1322;
}

.evidence-card {
  margin-bottom: 20px;
}

.citation-warning {
  margin-bottom: 10px;
}

.evidence-snippet {
  color: #333;
  line-height: 1.6;
}

.evidence-claim {
  margin-top: 4px;
  color: #666;
  font-size: 13px;
  line-height: 1.5;
}

.task-id-row {
  margin-bottom: 12px;
  color: #666;
  font-size: 13px;
  word-break: break-all;
}

/* 产品分析卡片 */
.product-section {
  margin-top: 20px;
}

.product-card {
  border-radius: 12px;
  overflow: hidden;
}

.product-card :deep(.ant-card-head) {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}

.product-card :deep(.ant-card-head-title) {
  color: white;
}

.product-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
}

.product-name {
  font-size: 18px;
  font-weight: 600;
  color: white;
}

.verdict-tag {
  font-size: 14px;
  font-weight: 600;
  padding: 4px 12px;
  border-radius: 12px;
}

.product-info {
  margin-bottom: 16px;
}

.specs-section {
  margin-top: 16px;
}

.specs-section h4 {
  margin-bottom: 8px;
  color: #333;
}

/* 优缺点 */
.pros-section, .cons-section {
  padding: 16px;
  border-radius: 8px;
  min-height: 120px;
}

.pros-section {
  background: linear-gradient(135deg, #f0fff4 0%, #c6f6d5 100%);
  border: 1px solid #c6f6d5;
}

.cons-section {
  background: linear-gradient(135deg, #fff5f5 0%, #fed7d7 100%);
  border: 1px solid #fed7d7;
}

.pros-section h4, .cons-section h4 {
  margin-bottom: 12px;
  font-size: 15px;
}

.point-item {
  padding: 6px 0;
  font-size: 14px;
  line-height: 1.6;
}

.point-icon {
  margin-right: 6px;
}

.pro-item {
  color: #22543d;
}

.con-item {
  color: #742a2a;
}

/* 避雷点 */
.red-flags-section {
  margin-top: 20px;
  padding: 16px;
  background: linear-gradient(135deg, #fff5f5 0%, #ffe0e0 100%);
  border-radius: 8px;
  border: 2px solid #ff4d4f;
}

.red-flags-section h4 {
  margin-bottom: 12px;
  color: #cf1322;
  font-size: 16px;
}

/* 争议点 */
.controversy-section {
  margin-top: 16px;
  padding: 16px;
  background: linear-gradient(135deg, #fffbe6 0%, #fff1b8 100%);
  border-radius: 8px;
  border: 1px solid #faad14;
}

.controversy-section h4 {
  margin-bottom: 12px;
  color: #ad6800;
  font-size: 16px;
}

/* 测评来源 */
.reviews-section {
  margin-top: 20px;
}

.reviews-section h4 {
  margin-bottom: 12px;
  font-size: 16px;
  color: #333;
}

.key-point {
  font-size: 13px;
  color: #555;
  line-height: 1.6;
}

/* 结论 */
.verdict-section {
  margin-top: 20px;
  padding: 20px;
  background: #f5f7fa;
  border-radius: 12px;
  text-align: center;
}

.verdict-badge {
  display: inline-block;
  padding: 8px 24px;
  border-radius: 20px;
  font-size: 18px;
  font-weight: 700;
  margin-bottom: 12px;
}

.verdict-positive {
  background: linear-gradient(135deg, #52c41a 0%, #389e0d 100%);
  color: white;
}

.verdict-negative {
  background: linear-gradient(135deg, #ff4d4f 0%, #cf1322 100%);
  color: white;
}

.verdict-neutral {
  background: linear-gradient(135deg, #faad14 0%, #d48806 100%);
  color: white;
}

.verdict-reason {
  font-size: 15px;
  color: #555;
  line-height: 1.8;
  margin: 0;
}

/* 选购贴士 */
.tips-list {
  padding-left: 20px;
}

.tips-list li {
  font-size: 14px;
  line-height: 2;
  color: #555;
}

/* 回到顶部按钮 */
.back-top-button {
  width: 50px;
  height: 50px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  font-weight: bold;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
  cursor: pointer;
  transition: all 0.3s ease;
}

.back-top-button:hover {
  transform: scale(1.1);
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.4);
}

/* 动画 */
@keyframes fadeInDown {
  from {
    opacity: 0;
    transform: translateY(-20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* 响应式 */
@media (max-width: 1200px) {
  .content-wrapper {
    flex-direction: column;
  }
  .side-nav {
    width: 100%;
  }
}
</style>
