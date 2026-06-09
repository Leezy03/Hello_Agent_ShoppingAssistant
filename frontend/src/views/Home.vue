<template>
  <div class="home-container">
    <!-- 背景装饰 -->
    <div class="bg-decoration">
      <div class="circle circle-1"></div>
      <div class="circle circle-2"></div>
      <div class="circle circle-3"></div>
    </div>

    <!-- 页面标题 -->
    <div class="page-header">
      <div class="icon-wrapper">
        <span class="icon">🛡️</span>
      </div>
      <h1 class="page-title">AI避雷购物助手</h1>
      <p class="page-subtitle">深度排雷，明智消费 — 多Agent协作分析B站/小红书/知乎真实测评</p>
    </div>

    <a-card class="form-card" :bordered="false">
      <a-form
        :model="formData"
        layout="vertical"
        @finish="handleSubmit"
      >
        <!-- 第一步:产品信息 -->
        <div class="form-section">
          <div class="section-header">
            <span class="section-icon">🔍</span>
            <span class="section-title">产品信息</span>
          </div>

          <a-row :gutter="24">
            <a-col :span="12">
              <a-form-item name="product_name" :rules="[{ required: true, message: '请输入产品名称' }]">
                <template #label>
                  <span class="form-label">产品名称</span>
                </template>
                <a-input
                  v-model:value="formData.product_name"
                  placeholder="例如: 洗地机、扫地机器人、空气炸锅"
                  size="large"
                  class="custom-input"
                >
                  <template #prefix>
                    <span style="color: #1890ff;">📦</span>
                  </template>
                </a-input>
              </a-form-item>
            </a-col>
            <a-col :span="12">
              <a-form-item name="usage_scenario">
                <template #label>
                  <span class="form-label">使用场景</span>
                </template>
                <a-input
                  v-model:value="formData.usage_scenario"
                  placeholder="例如: 家用，120平米，有宠物"
                  size="large"
                  class="custom-input"
                >
                  <template #prefix>
                    <span style="color: #1890ff;">🏠</span>
                  </template>
                </a-input>
              </a-form-item>
            </a-col>
          </a-row>
        </div>

        <!-- 第二步:预算与品牌 -->
        <div class="form-section">
          <div class="section-header">
            <span class="section-icon">💰</span>
            <span class="section-title">预算与品牌</span>
          </div>

          <a-row :gutter="24">
            <a-col :span="6">
              <a-form-item name="budget_min">
                <template #label>
                  <span class="form-label">预算下限(元)</span>
                </template>
                <a-input-number
                  v-model:value="formData.budget_min"
                  :min="0"
                  :max="999999"
                  placeholder="最低价"
                  style="width: 100%"
                  size="large"
                />
              </a-form-item>
            </a-col>
            <a-col :span="6">
              <a-form-item name="budget_max">
                <template #label>
                  <span class="form-label">预算上限(元)</span>
                </template>
                <a-input-number
                  v-model:value="formData.budget_max"
                  :min="0"
                  :max="999999"
                  placeholder="最高价"
                  style="width: 100%"
                  size="large"
                />
              </a-form-item>
            </a-col>
            <a-col :span="12">
              <a-form-item name="brand_preferences">
                <template #label>
                  <span class="form-label">品牌偏好（可选，按回车添加）</span>
                </template>
                <a-select
                  v-model:value="formData.brand_preferences"
                  mode="tags"
                  placeholder="输入品牌名称后按回车添加，如：追觅、石头、戴森"
                  size="large"
                  class="custom-select"
                />
              </a-form-item>
            </a-col>
          </a-row>
        </div>

        <!-- 第三步:关注要点 -->
        <div class="form-section">
          <div class="section-header">
            <span class="section-icon">⚙️</span>
            <span class="section-title">关注要点</span>
          </div>

          <a-form-item name="concerns">
            <div class="preference-tags">
              <a-checkbox-group v-model:value="formData.concerns" class="custom-checkbox-group">
                <a-checkbox value="性价比" class="preference-tag">💰 性价比</a-checkbox>
                <a-checkbox value="耐用性" class="preference-tag">🔧 耐用性</a-checkbox>
                <a-checkbox value="噪音" class="preference-tag">🔇 噪音</a-checkbox>
                <a-checkbox value="续航" class="preference-tag">🔋 续航</a-checkbox>
                <a-checkbox value="做工" class="preference-tag">✨ 做工</a-checkbox>
                <a-checkbox value="售后" class="preference-tag">📞 售后</a-checkbox>
                <a-checkbox value="安全性" class="preference-tag">🛡️ 安全性</a-checkbox>
                <a-checkbox value="易用性" class="preference-tag">👆 易用性</a-checkbox>
              </a-checkbox-group>
            </div>
          </a-form-item>
        </div>

        <!-- 第四步:额外要求 -->
        <div class="form-section">
          <div class="section-header">
            <span class="section-icon">💬</span>
            <span class="section-title">额外要求</span>
          </div>

          <a-form-item name="free_text_input">
            <a-textarea
              v-model:value="formData.free_text_input"
              placeholder="请输入您的额外要求，例如：家里有老人需要操作简单、已经用过XX品牌体验不好、希望重点对比某几个型号..."
              :rows="3"
              size="large"
              class="custom-textarea"
            />
          </a-form-item>
        </div>

        <!-- 提交按钮 -->
        <a-form-item>
          <a-button
            type="primary"
            html-type="submit"
            :loading="loading"
            size="large"
            block
            class="submit-button"
          >
            <template v-if="!loading">
              <span class="button-icon">🔍</span>
              <span>开始深度排雷</span>
            </template>
            <template v-else>
              <span>正在分析中...</span>
            </template>
          </a-button>
        </a-form-item>

        <!-- 加载进度条 -->
        <a-form-item v-if="loading">
          <div class="loading-container">
            <a-progress
              :percent="loadingProgress"
              status="active"
              :stroke-color="{
                '0%': '#667eea',
                '100%': '#764ba2',
              }"
              :stroke-width="10"
            />
            <p class="loading-status">
              {{ loadingStatus }}
            </p>
            <p v-if="taskId" class="task-id">任务ID: {{ taskId }}</p>
            <div v-if="taskTrace.length > 0" class="trace-list">
              <div
                v-for="event in taskTrace"
                :key="event.event_id"
                class="trace-item"
                :class="`trace-${event.status}`"
              >
                <span class="trace-status">{{ getTraceStatusText(event.status) }}</span>
                <span class="trace-name">{{ event.step_name }}</span>
                <span v-if="event.duration_ms !== undefined && event.duration_ms !== null" class="trace-duration">
                  {{ formatDuration(event.duration_ms) }}
                  <template v-if="event.attempt_count">
                    · {{ event.attempt_count }}次
                  </template>
                  <template v-if="event.tool_call_count">
                    · {{ event.tool_call_count }}检索
                  </template>
                </span>
                <span class="trace-message">{{ event.message }}</span>
              </div>
            </div>
          </div>
        </a-form-item>
      </a-form>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { createShoppingTask, getShoppingTask } from '@/services/api'
import type { ShoppingFormData, TaskTraceEvent } from '@/types'

const router = useRouter()
const loading = ref(false)
const loadingProgress = ref(0)
const loadingStatus = ref('')
const taskId = ref('')
const taskTrace = ref<TaskTraceEvent[]>([])

const formData = reactive<ShoppingFormData>({
  product_name: '',
  budget_min: undefined,
  budget_max: undefined,
  brand_preferences: [],
  usage_scenario: '',
  concerns: [],
  free_text_input: ''
})

const handleSubmit = async () => {
  if (!formData.product_name) {
    message.error('请输入产品名称')
    return
  }

  loading.value = true
  loadingProgress.value = 0
  loadingStatus.value = '正在创建后台任务...'
  taskId.value = ''
  taskTrace.value = []

  try {
    const createdTask = await createShoppingTask(formData)
    taskId.value = createdTask.task_id
    loadingStatus.value = '任务已创建，正在等待 Agent 工作流执行...'

    let completedTask = null
    for (let i = 0; i < 300; i += 1) {
      const task = await getShoppingTask(taskId.value)
      loadingProgress.value = task.progress
      loadingStatus.value = task.message || '正在分析中...'
      taskTrace.value = task.trace || []

      if (['succeeded', 'partial', 'failed'].includes(task.status)) {
        completedTask = task
        break
      }

      await sleep(1200)
    }

    if (!completedTask) {
      throw new Error('任务等待超时，请稍后查询任务状态')
    }

    if (completedTask.status === 'failed') {
      throw new Error(completedTask.error || completedTask.message || '分析失败')
    }

    if (!completedTask.report) {
      throw new Error('任务已结束但没有返回报告')
    }

    loadingProgress.value = 100
    loadingStatus.value = completedTask.status === 'partial' ? '分析完成，部分步骤已降级' : '分析完成'

    sessionStorage.setItem('shoppingReport', JSON.stringify(completedTask.report))
    sessionStorage.setItem('shoppingTrace', JSON.stringify(completedTask.trace || []))
    sessionStorage.setItem('shoppingTaskId', completedTask.task_id)

    if (completedTask.status === 'partial') {
      message.warning('报告已生成，但部分步骤已降级，请查看Trace')
    } else {
      message.success('避雷报告生成成功！')
    }

    setTimeout(() => {
      router.push('/result')
    }, 500)
  } catch (error: any) {
    message.error(error.message || '生成避雷报告失败，请稍后重试')
  } finally {
    setTimeout(() => {
      loading.value = false
      loadingProgress.value = 0
      loadingStatus.value = ''
      taskId.value = ''
    }, 1000)
  }
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

const getTraceStatusText = (status: string): string => {
  if (status === 'running') return '运行中'
  if (status === 'success') return '成功'
  if (status === 'partial') return '降级'
  if (status === 'failed') return '失败'
  return status
}

const formatDuration = (durationMs: number): string => {
  if (durationMs < 1000) return `${durationMs}ms`
  return `${(durationMs / 1000).toFixed(1)}s`
}
</script>

<style scoped>
.home-container {
  min-height: 100vh;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  padding: 60px 20px;
  position: relative;
  overflow: hidden;
}

/* 背景装饰 */
.bg-decoration {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  overflow: hidden;
}

.circle {
  position: absolute;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.1);
  animation: float 20s infinite ease-in-out;
}

.circle-1 {
  width: 300px;
  height: 300px;
  top: -100px;
  left: -100px;
  animation-delay: 0s;
}

.circle-2 {
  width: 200px;
  height: 200px;
  top: 50%;
  right: -50px;
  animation-delay: 5s;
}

.circle-3 {
  width: 150px;
  height: 150px;
  bottom: -50px;
  left: 30%;
  animation-delay: 10s;
}

@keyframes float {
  0%, 100% {
    transform: translateY(0) rotate(0deg);
  }
  50% {
    transform: translateY(-30px) rotate(180deg);
  }
}

/* 页面标题 */
.page-header {
  text-align: center;
  margin-bottom: 50px;
  animation: fadeInDown 0.8s ease-out;
  position: relative;
  z-index: 1;
}

.icon-wrapper {
  margin-bottom: 20px;
}

.icon {
  font-size: 80px;
  display: inline-block;
  animation: bounce 2s infinite;
}

@keyframes bounce {
  0%, 100% {
    transform: translateY(0);
  }
  50% {
    transform: translateY(-20px);
  }
}

.page-title {
  font-size: 56px;
  font-weight: 800;
  color: #ffffff;
  margin-bottom: 16px;
  text-shadow: 3px 3px 6px rgba(0, 0, 0, 0.3);
  letter-spacing: 2px;
}

.page-subtitle {
  font-size: 20px;
  color: rgba(255, 255, 255, 0.95);
  margin: 0;
  font-weight: 300;
}

/* 表单卡片 */
.form-card {
  max-width: 1400px;
  margin: 0 auto;
  border-radius: 24px;
  box-shadow: 0 30px 80px rgba(0, 0, 0, 0.4);
  animation: fadeInUp 0.8s ease-out;
  position: relative;
  z-index: 1;
  backdrop-filter: blur(10px);
  background: rgba(255, 255, 255, 0.98) !important;
}

/* 表单分区 */
.form-section {
  margin-bottom: 32px;
  padding: 24px;
  background: linear-gradient(135deg, #f5f7fa 0%, #ffffff 100%);
  border-radius: 16px;
  border: 1px solid #e8e8e8;
  transition: all 0.3s ease;
}

.form-section:hover {
  box-shadow: 0 8px 24px rgba(102, 126, 234, 0.15);
  transform: translateY(-2px);
}

.section-header {
  display: flex;
  align-items: center;
  margin-bottom: 20px;
  padding-bottom: 12px;
  border-bottom: 2px solid #667eea;
}

.section-icon {
  font-size: 24px;
  margin-right: 12px;
}

.section-title {
  font-size: 18px;
  font-weight: 600;
  color: #333;
}

/* 表单标签 */
.form-label {
  font-size: 15px;
  font-weight: 500;
  color: #555;
}

/* 自定义输入框 */
.custom-input :deep(.ant-input),
.custom-input :deep(.ant-picker) {
  border-radius: 12px;
  border: 2px solid #e8e8e8;
  transition: all 0.3s ease;
}

.custom-input :deep(.ant-input:hover),
.custom-input :deep(.ant-picker:hover) {
  border-color: #667eea;
}

.custom-input :deep(.ant-input:focus),
.custom-input :deep(.ant-picker-focused) {
  border-color: #667eea;
  box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
}

/* 自定义选择框 */
.custom-select :deep(.ant-select-selector) {
  border-radius: 12px !important;
  border: 2px solid #e8e8e8 !important;
  transition: all 0.3s ease;
}

.custom-select:hover :deep(.ant-select-selector) {
  border-color: #667eea !important;
}

.custom-select :deep(.ant-select-focused .ant-select-selector) {
  border-color: #667eea !important;
  box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1) !important;
}

/* 天数显示 - 紧凑版 */
.days-display-compact {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 40px;
  padding: 8px 16px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-radius: 12px;
  color: white;
}

.days-display-compact .days-value {
  font-size: 24px;
  font-weight: 700;
  margin-right: 4px;
}

.days-display-compact .days-unit {
  font-size: 14px;
}

/* 偏好标签 */
.preference-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.custom-checkbox-group {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  width: 100%;
}

.preference-tag :deep(.ant-checkbox-wrapper) {
  margin: 0 !important;
  padding: 8px 16px;
  border: 2px solid #e8e8e8;
  border-radius: 20px;
  transition: all 0.3s ease;
  background: white;
  font-size: 14px;
}

.preference-tag :deep(.ant-checkbox-wrapper:hover) {
  border-color: #667eea;
  background: #f5f7ff;
}

.preference-tag :deep(.ant-checkbox-wrapper-checked) {
  border-color: #667eea;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
}

/* 自定义文本域 */
.custom-textarea :deep(.ant-input) {
  border-radius: 12px;
  border: 2px solid #e8e8e8;
  transition: all 0.3s ease;
}

.custom-textarea :deep(.ant-input:hover) {
  border-color: #667eea;
}

.custom-textarea :deep(.ant-input:focus) {
  border-color: #667eea;
  box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
}

/* 提交按钮 */
.submit-button {
  height: 56px;
  border-radius: 28px;
  font-size: 18px;
  font-weight: 600;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border: none;
  box-shadow: 0 8px 24px rgba(102, 126, 234, 0.4);
  transition: all 0.3s ease;
}

.submit-button:hover {
  transform: translateY(-2px);
  box-shadow: 0 12px 32px rgba(102, 126, 234, 0.5);
}

.submit-button:active {
  transform: translateY(0);
}

.button-icon {
  margin-right: 8px;
  font-size: 20px;
}

/* 加载容器 */
.loading-container {
  text-align: center;
  padding: 24px;
  background: linear-gradient(135deg, #f5f7fa 0%, #ffffff 100%);
  border-radius: 16px;
  border: 2px dashed #667eea;
}

.loading-status {
  margin-top: 16px;
  color: #667eea;
  font-size: 18px;
  font-weight: 500;
}

.task-id {
  margin: 8px 0 0;
  color: #666;
  font-size: 12px;
  word-break: break-all;
}

.trace-list {
  margin-top: 18px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  text-align: left;
}

.trace-item {
  display: grid;
  grid-template-columns: 64px minmax(96px, 160px) 64px 1fr;
  gap: 10px;
  align-items: center;
  padding: 10px 12px;
  border-radius: 10px;
  background: #ffffff;
  border: 1px solid #e8e8e8;
  font-size: 13px;
}

.trace-status {
  font-weight: 700;
}

.trace-name {
  color: #333;
  font-weight: 600;
}

.trace-duration {
  color: #666;
  font-variant-numeric: tabular-nums;
}

.trace-message {
  color: #666;
  line-height: 1.5;
}

.trace-running .trace-status {
  color: #1677ff;
}

.trace-success .trace-status {
  color: #52c41a;
}

.trace-partial .trace-status {
  color: #faad14;
}

.trace-failed .trace-status {
  color: #ff4d4f;
}

/* 动画 */
@keyframes fadeInDown {
  from {
    opacity: 0;
    transform: translateY(-30px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(30px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>
