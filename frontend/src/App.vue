<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import SummaryCards from './components/SummaryCards.vue'
import ServerTable from './components/ServerTable.vue'
import RecentClientLogs from './components/RecentClientLogs.vue'
import AgentAccessModal from './components/AgentAccessModal.vue'
import { formatGatewayTime } from './utils/time'

const dashboardData = ref({
  gateway: {},
  indexes: {},
  summary: { total: 0, alive: 0, dead: 0 },
  servers: [],
  agentConfigs: [],
  recentClients: [],
  recentLogs: [],
})
const loading = ref(true)
const refreshing = ref(false)
const refreshMode = ref('idle')
const errorMessage = ref('')
const refreshTimer = ref(null)
const lastRefreshAt = ref('')
const agentAccessVisible = ref(false)
// 只允许最后一次请求回写页面状态，避免自动轮询与手动刷新并发时出现旧数据覆盖。
let activeRequestId = 0
let activeAbortController = null

const panelMeta = computed(() => {
  const gateway = dashboardData.value.gateway || {}
  const heartbeatText = formatGatewayTime(gateway.lastHeartbeatAt)
  const refreshText = lastRefreshAt.value || '未刷新'
  return `网关：${gateway.name || 'shared-gateway'} · 最后心跳：${heartbeatText} · 页面刷新：${refreshText}`
})

const globalRefreshText = computed(() => {
  if (!refreshing.value) {
    return ''
  }
  if (refreshMode.value === 'manual') {
    return '正在刷新整页数据，请稍候...'
  }
  return '后台自动刷新中...'
})

const showPageRefreshOverlay = computed(() => refreshing.value && refreshMode.value === 'manual')
const showGlobalRefreshBar = computed(() => refreshing.value && refreshMode.value === 'manual')

function formatRefreshTime(source) {
  const formatted = formatGatewayTime(source)
  return formatted === '未知' ? '' : formatted
}

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

async function fetchDashboardData(options = {}) {
  const { silent = false } = options
  // 手动刷新进行中时，直接跳过静默轮询，避免可见刷新态被后台请求打断。
  if (silent && refreshing.value && refreshMode.value === 'manual') {
    return
  }

  const startedAt = Date.now()
  const minVisibleMs = silent ? 0 : 700
  const requestId = activeRequestId + 1
  activeRequestId = requestId

  // 新请求发起前先取消旧请求，确保只有最新一次请求能决定页面最终状态。
  if (activeAbortController) {
    activeAbortController.abort()
  }
  activeAbortController = new AbortController()

  try {
    refreshing.value = true
    refreshMode.value = silent ? 'auto' : 'manual'

    const response = await fetch(`/dashboard/data?t=${Date.now()}`, {
      cache: 'no-store',
      headers: {
        'cache-control': 'no-cache',
      },
      signal: activeAbortController.signal,
    })
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }

    const payload = await response.json()
    const remainingMs = minVisibleMs - (Date.now() - startedAt)
    if (remainingMs > 0) {
      await sleep(remainingMs)
    }

    if (requestId !== activeRequestId) {
      return
    }

    dashboardData.value = payload
    lastRefreshAt.value = formatRefreshTime(payload.generatedAt) || new Date().toLocaleString('zh-CN', { hour12: false })
    errorMessage.value = ''
  } catch (error) {
    // 被新请求主动取消的旧请求不应该污染页面错误态。
    if (error instanceof DOMException && error.name === 'AbortError') {
      return
    }

    const remainingMs = minVisibleMs - (Date.now() - startedAt)
    if (remainingMs > 0) {
      await sleep(remainingMs)
    }

    if (requestId !== activeRequestId) {
      return
    }

    errorMessage.value = `加载失败：${error instanceof Error ? error.message : String(error)}`
  } finally {
    if (requestId === activeRequestId) {
      loading.value = false
      refreshing.value = false
      refreshMode.value = 'idle'
      activeAbortController = null
    }
  }
}

function manualRefresh() {
  // 手动刷新时打开整页刷新态，并通过最短展示时长保证用户有明确感知。
  fetchDashboardData({ silent: false })
}

function openAgentAccess() {
  agentAccessVisible.value = true
}

function closeAgentAccess() {
  agentAccessVisible.value = false
}

onMounted(() => {
  fetchDashboardData({ silent: false })
  refreshTimer.value = window.setInterval(() => {
    // 自动轮询改为完全静默，避免页面周期性抖动。
    fetchDashboardData({ silent: true })
  }, 10000)
})

onUnmounted(() => {
  if (refreshTimer.value !== null) {
    window.clearInterval(refreshTimer.value)
  }
  if (activeAbortController) {
    activeAbortController.abort()
    activeAbortController = null
  }
})
</script>

<template>
  <div class="page-shell">
    <transition name="refresh-bar">
      <div v-if="showGlobalRefreshBar" class="global-refresh-bar">
        <span class="refresh-dot" />
        <span>{{ globalRefreshText }}</span>
      </div>
    </transition>

    <transition name="refresh-overlay">
      <div v-if="showPageRefreshOverlay" class="page-refresh-overlay">
        <div class="overlay-card">
          <div class="spinner" />
          <div class="overlay-title">正在刷新页面数据</div>
          <div class="overlay-desc">MCP 状态、接入 IP、调用日志都会重新加载。</div>
        </div>
      </div>
    </transition>

    <div class="container">
      <header class="header">
        <div class="title-block">
          <h1>Shared MCP Gateway Dashboard</h1>
          <p>查看当前接入的 MCP 数量、存活状态、接入 IP 与对应调用日志。</p>
        </div>
        <div class="action-block">
          <div class="action-buttons">
            <button
              class="secondary-button"
              type="button"
              @click="openAgentAccess"
            >
              Agent 一键接入
            </button>
            <button
              class="refresh-button"
              :class="{ refreshing }"
              :disabled="refreshing"
              type="button"
              @click="manualRefresh"
            >
              {{ refreshing ? '刷新中...' : '立即刷新' }}
            </button>
          </div>
          <a href="/healthz" target="_blank" rel="noreferrer">查看原始 /healthz JSON</a>
        </div>
      </header>

      <SummaryCards :summary="dashboardData.summary" :indexes="dashboardData.indexes" />

      <section class="panel">
        <div class="panel-header">
          <h2>MCP 状态明细</h2>
          <div class="panel-meta">{{ errorMessage || panelMeta }}</div>
        </div>
        <div v-if="loading" class="loading">正在加载状态数据...</div>
        <ServerTable v-else :servers="dashboardData.servers" />
      </section>

      <RecentClientLogs
        v-if="!loading"
        :recent-clients="dashboardData.recentClients || []"
        :recent-logs="dashboardData.recentLogs || []"
        :servers="dashboardData.servers || []"
      />

      <footer class="footer">页面每 10 秒静默刷新一次；只有手动刷新才会显示整页状态。</footer>
    </div>

    <AgentAccessModal
      :visible="agentAccessVisible"
      :agent-configs="dashboardData.agentConfigs || []"
      @close="closeAgentAccess"
    />
  </div>
</template>

<style>
:root {
  color-scheme: dark;
  --bg: #0b1020;
  --panel: #121933;
  --panel-border: #26304f;
  --text: #ebf0ff;
  --muted: #9aa8d6;
  --blue: #60a5fa;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: linear-gradient(180deg, #0b1020 0%, #111938 100%);
  color: var(--text);
}

#app {
  min-height: 100vh;
}

.page-shell {
  min-height: 100vh;
}

.container {
  width: min(1280px, calc(100vw - 32px));
  margin: 24px auto 48px;
}

.global-refresh-bar {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 40;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  min-height: 40px;
  padding: 10px 16px;
  background: linear-gradient(90deg, rgba(96, 165, 250, 0.25), rgba(59, 130, 246, 0.45), rgba(96, 165, 250, 0.25));
  border-bottom: 1px solid rgba(96, 165, 250, 0.28);
  color: #dbeafe;
  font-size: 14px;
  backdrop-filter: blur(10px);
}

.refresh-dot {
  width: 10px;
  height: 10px;
  border-radius: 999px;
  background: #bfdbfe;
  box-shadow: 0 0 0 0 rgba(191, 219, 254, 0.7);
  animation: pulse-dot 1.2s infinite;
}

.page-refresh-overlay {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(6, 10, 22, 0.42);
  backdrop-filter: blur(3px);
}

.overlay-card {
  min-width: min(420px, calc(100vw - 48px));
  padding: 28px 30px;
  border: 1px solid rgba(96, 165, 250, 0.26);
  border-radius: 18px;
  background: rgba(18, 25, 51, 0.96);
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.28);
  text-align: center;
}

.spinner {
  width: 42px;
  height: 42px;
  margin: 0 auto 16px;
  border-radius: 999px;
  border: 4px solid rgba(96, 165, 250, 0.18);
  border-top-color: #60a5fa;
  animation: rotate 0.8s linear infinite;
}

.overlay-title {
  font-size: 18px;
  font-weight: 700;
  margin-bottom: 8px;
}

.overlay-desc {
  font-size: 14px;
  color: var(--muted);
}

.header {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: end;
  margin-bottom: 20px;
}

.title-block h1 {
  margin: 0 0 8px;
  font-size: 28px;
}

.title-block p {
  margin: 0;
  color: var(--muted);
}

.action-block {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 10px;
}

.action-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  justify-content: flex-end;
}

.action-block a {
  color: var(--blue);
  text-decoration: none;
  font-size: 14px;
}

.refresh-button,
.secondary-button {
  border: 1px solid rgba(96, 165, 250, 0.35);
  background: rgba(96, 165, 250, 0.16);
  color: #dbeafe;
  border-radius: 10px;
  padding: 8px 14px;
  cursor: pointer;
  min-width: 96px;
}

.refresh-button:disabled {
  cursor: wait;
  opacity: 0.75;
}

.refresh-button.refreshing {
  background: rgba(96, 165, 250, 0.28);
}

.panel {
  margin-top: 20px;
  background: rgba(18, 25, 51, 0.92);
  border: 1px solid var(--panel-border);
  border-radius: 16px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.18);
  overflow: hidden;
}

.panel-header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  padding: 18px 20px;
  border-bottom: 1px solid var(--panel-border);
}

.panel-header h2 {
  margin: 0;
  font-size: 18px;
}

.panel-meta {
  color: var(--muted);
  font-size: 13px;
}

.loading {
  padding: 24px 20px;
  color: var(--muted);
}

.footer {
  margin-top: 14px;
  color: var(--muted);
  font-size: 13px;
}

.refresh-bar-enter-active,
.refresh-bar-leave-active,
.refresh-overlay-enter-active,
.refresh-overlay-leave-active {
  transition: opacity 0.2s ease;
}

.refresh-bar-enter-from,
.refresh-bar-leave-to,
.refresh-overlay-enter-from,
.refresh-overlay-leave-to {
  opacity: 0;
}

@keyframes pulse-dot {
  0% {
    box-shadow: 0 0 0 0 rgba(191, 219, 254, 0.7);
  }
  70% {
    box-shadow: 0 0 0 8px rgba(191, 219, 254, 0);
  }
  100% {
    box-shadow: 0 0 0 0 rgba(191, 219, 254, 0);
  }
}

@keyframes rotate {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 860px) {
  .header {
    flex-direction: column;
    align-items: start;
  }

  .action-block {
    align-items: flex-start;
  }

  .panel-header {
    flex-direction: column;
    align-items: start;
  }

  .global-refresh-bar {
    justify-content: flex-start;
  }
}
</style>
