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
const reconnectingServers = ref({})
const themeOptions = [
  { key: 'custom', label: '自定义' },
  { key: 'day', label: '白天' },
  { key: 'night', label: '夜晚' },
]
const DEFAULT_CUSTOM_THEME = {
  bgStart: '#101624',
  bgEnd: '#102a34',
  panel: '#111827',
  accent: '#2dd4bf',
}
const savedThemeMode = window.localStorage.getItem('shared-mcp-dashboard-theme')
const customTheme = ref(loadCustomTheme())
const themeMode = ref(themeOptions.some((option) => option.key === savedThemeMode) ? savedThemeMode : 'custom')
applyThemeMode(themeMode.value)
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
const shellClass = computed(() => `theme-${themeMode.value}`)
const customThemeStyle = computed(() => {
  if (themeMode.value !== 'custom') {
    return null
  }
  return buildCustomThemeStyle(customTheme.value)
})

function formatRefreshTime(source) {
  const formatted = formatGatewayTime(source)
  return formatted === '未知' ? '' : formatted
}

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

function loadCustomTheme() {
  try {
    const saved = JSON.parse(window.localStorage.getItem('shared-mcp-dashboard-custom-theme') || '{}')
    return {
      ...DEFAULT_CUSTOM_THEME,
      ...Object.fromEntries(
        Object.entries(saved).filter(([, value]) => /^#[0-9a-fA-F]{6}$/.test(String(value))),
      ),
    }
  } catch {
    return { ...DEFAULT_CUSTOM_THEME }
  }
}

function hexToRgb(hex) {
  const normalized = hex.replace('#', '')
  return {
    r: parseInt(normalized.slice(0, 2), 16),
    g: parseInt(normalized.slice(2, 4), 16),
    b: parseInt(normalized.slice(4, 6), 16),
  }
}

function hexToRgba(hex, alpha) {
  const { r, g, b } = hexToRgb(hex)
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

function buildCustomThemeStyle(theme) {
  return {
    '--page-bg': `linear-gradient(180deg, ${theme.bgStart} 0%, ${theme.bgEnd} 100%)`,
    '--panel': hexToRgba(theme.panel, 0.9),
    '--panel-solid': theme.panel,
    '--panel-strong': theme.bgStart,
    '--panel-muted': hexToRgba(theme.panel, 0.42),
    '--panel-border': hexToRgba(theme.accent, 0.17),
    '--panel-border-strong': hexToRgba(theme.accent, 0.3),
    '--blue': theme.accent,
    '--blue-soft': hexToRgba(theme.accent, 0.1),
    '--blue-strong': hexToRgba(theme.accent, 0.22),
    '--button-text': theme.accent,
    '--modal-bg': `linear-gradient(180deg, ${hexToRgba(theme.panel, 0.98)}, ${hexToRgba(theme.bgStart, 0.98)})`,
    '--code-bg': hexToRgba(theme.bgStart, 0.45),
    '--glass-highlight': hexToRgba('#ffffff', 0.1),
    '--glass-sheen': hexToRgba(theme.accent, 0.08),
    '--shadow': `0 18px 44px ${hexToRgba(theme.bgStart, 0.34)}, inset 0 1px 0 ${hexToRgba('#ffffff', 0.1)}`,
  }
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

async function reconnectServer(server) {
  // 1. 标记当前 MCP 正在重连，避免重复点击打出并发请求。
  const serverKey = server?.key
  if (!serverKey || reconnectingServers.value[serverKey]) {
    return
  }

  reconnectingServers.value = {
    ...reconnectingServers.value,
    [serverKey]: true,
  }

  try {
    // 2. 调用后端单 MCP 重连接口，后端负责拉起新连接并替换当前连接。
    const response = await fetch(`/dashboard/servers/${encodeURIComponent(serverKey)}/reconnect`, {
      method: 'POST',
      cache: 'no-store',
      headers: {
        'cache-control': 'no-cache',
      },
    })
    const payload = await response.json().catch(() => ({}))
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || `HTTP ${response.status}`)
    }

    // 3. 使用接口返回的 dashboard 快照刷新页面，保证按钮结果立即可见。
    if (payload.dashboard) {
      dashboardData.value = payload.dashboard
      lastRefreshAt.value =
        formatRefreshTime(payload.dashboard.generatedAt) || new Date().toLocaleString('zh-CN', { hour12: false })
    } else {
      await fetchDashboardData({ silent: true })
    }
    errorMessage.value = `${serverKey} 已重新连接`
  } catch (error) {
    errorMessage.value = `${serverKey} 重连失败：${error instanceof Error ? error.message : String(error)}`
  } finally {
    const nextState = { ...reconnectingServers.value }
    delete nextState[serverKey]
    reconnectingServers.value = nextState
  }
}

function openAgentAccess() {
  agentAccessVisible.value = true
}

function closeAgentAccess() {
  agentAccessVisible.value = false
}

function applyThemeMode(mode) {
  document.documentElement.dataset.dashboardTheme = mode
  if (mode === 'custom') {
    document.documentElement.style.setProperty('--dashboard-root-bg', customTheme.value.bgStart)
    return
  }
  document.documentElement.style.removeProperty('--dashboard-root-bg')
}

function setThemeMode(mode) {
  themeMode.value = mode
  applyThemeMode(mode)
  window.localStorage.setItem('shared-mcp-dashboard-theme', mode)
}

function updateCustomTheme(key, value) {
  customTheme.value = {
    ...customTheme.value,
    [key]: value,
  }
  window.localStorage.setItem('shared-mcp-dashboard-custom-theme', JSON.stringify(customTheme.value))
  if (themeMode.value === 'custom') {
    applyThemeMode('custom')
  }
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
  <div class="page-shell" :class="shellClass" :style="customThemeStyle">
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
          <div class="theme-switcher" aria-label="页面主题">
            <button
              v-for="option in themeOptions"
              :key="option.key"
              class="theme-button"
              :class="{ active: themeMode === option.key }"
              type="button"
              @click="setThemeMode(option.key)"
            >
              {{ option.label }}
            </button>
          </div>
          <div v-if="themeMode === 'custom'" class="custom-palette" aria-label="自定义配色">
            <label class="color-chip">
              <span>背景</span>
              <input
                type="color"
                :value="customTheme.bgStart"
                @input="updateCustomTheme('bgStart', $event.target.value)"
              >
            </label>
            <label class="color-chip">
              <span>渐变</span>
              <input
                type="color"
                :value="customTheme.bgEnd"
                @input="updateCustomTheme('bgEnd', $event.target.value)"
              >
            </label>
            <label class="color-chip">
              <span>面板</span>
              <input
                type="color"
                :value="customTheme.panel"
                @input="updateCustomTheme('panel', $event.target.value)"
              >
            </label>
            <label class="color-chip">
              <span>强调</span>
              <input
                type="color"
                :value="customTheme.accent"
                @input="updateCustomTheme('accent', $event.target.value)"
              >
            </label>
          </div>
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

      <section v-if="loading" class="summary-grid skeleton-summary" aria-label="汇总加载中">
        <article v-for="index in 4" :key="index" class="summary-skeleton-card">
          <div class="skeleton-line short" />
          <div class="skeleton-line metric" />
        </article>
      </section>
      <SummaryCards v-else :summary="dashboardData.summary" :indexes="dashboardData.indexes" />

      <section class="panel">
        <div class="panel-header">
          <h2>MCP 状态明细</h2>
          <div class="panel-meta">{{ errorMessage || panelMeta }}</div>
        </div>
        <div v-if="loading" class="table-skeleton" aria-label="MCP 状态加载中">
          <div v-for="row in 7" :key="row" class="skeleton-row">
            <div class="skeleton-line name" />
            <div class="skeleton-line pill" />
            <div class="skeleton-line wide" />
          </div>
        </div>
        <ServerTable
          v-else
          :servers="dashboardData.servers"
          :reconnecting-servers="reconnectingServers"
          @reconnect-server="reconnectServer"
        />
      </section>

      <section v-if="loading" class="panel">
        <div class="panel-header">
          <h2>接入端与调用日志</h2>
          <div class="panel-meta">正在加载最近调用记录</div>
        </div>
        <div class="logs-skeleton">
          <aside class="logs-skeleton-sidebar">
            <div v-for="index in 4" :key="index" class="skeleton-client-card">
              <div class="skeleton-line medium" />
              <div class="skeleton-line wide" />
            </div>
          </aside>
          <div class="logs-skeleton-table">
            <div v-for="row in 6" :key="row" class="skeleton-row">
              <div class="skeleton-line name" />
              <div class="skeleton-line wide" />
              <div class="skeleton-line pill" />
            </div>
          </div>
        </div>
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
  color-scheme: dark light;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: #0b1020;
  color: #ebf0ff;
}

html,
body,
#app {
  min-height: 100vh;
}

html[data-dashboard-theme="day"],
html[data-dashboard-theme="day"] body,
html[data-dashboard-theme="day"] #app {
  background: #f7fafc;
  color: #172033;
}

html[data-dashboard-theme="night"],
html[data-dashboard-theme="night"] body,
html[data-dashboard-theme="night"] #app {
  background: #050816;
  color: #f8fbff;
}

html[data-dashboard-theme="custom"],
html[data-dashboard-theme="custom"] body,
html[data-dashboard-theme="custom"] #app {
  background: var(--dashboard-root-bg, #101624);
  color: #ebf0ff;
}

.page-shell {
  min-height: 100vh;
  --page-bg: linear-gradient(180deg, #0b1020 0%, #111938 100%);
  --panel: rgba(18, 25, 51, 0.92);
  --panel-solid: #121933;
  --panel-strong: #0b1020;
  --panel-soft: rgba(255, 255, 255, 0.03);
  --panel-muted: rgba(11, 16, 32, 0.32);
  --panel-border: rgba(148, 163, 184, 0.18);
  --panel-border-strong: rgba(148, 163, 184, 0.28);
  --text: #ebf0ff;
  --muted: #9aa8d6;
  --subtle: #c9d5f6;
  --blue: #60a5fa;
  --blue-soft: rgba(96, 165, 250, 0.12);
  --blue-strong: rgba(96, 165, 250, 0.22);
  --green: #22c55e;
  --green-soft: rgba(34, 197, 94, 0.12);
  --green-text: #d1fae5;
  --red: #ef4444;
  --red-soft: rgba(239, 68, 68, 0.12);
  --red-text: #fee2e2;
  --warning-soft: rgba(239, 68, 68, 0.14);
  --button-text: #dbeafe;
  --shadow: 0 18px 44px rgba(0, 0, 0, 0.22), inset 0 1px 0 rgba(255, 255, 255, 0.09);
  --modal-backdrop: rgba(2, 6, 23, 0.72);
  --modal-bg: linear-gradient(180deg, rgba(18, 25, 51, 0.98), rgba(11, 16, 32, 0.98));
  --code-bg: rgba(11, 16, 32, 0.45);
  --skeleton: rgba(148, 163, 184, 0.18);
  --skeleton-glint: rgba(255, 255, 255, 0.2);
  --glass-highlight: rgba(255, 255, 255, 0.11);
  --glass-sheen: rgba(96, 165, 250, 0.08);
  --raised-edge: rgba(255, 255, 255, 0.08);
  --pressed-shadow: inset 0 2px 8px rgba(0, 0, 0, 0.22), inset 0 1px 0 rgba(255, 255, 255, 0.06);
  background: var(--page-bg);
  color: var(--text);
  padding-top: 24px;
  transition: background 0.18s ease, color 0.18s ease;
}

.page-shell.theme-day {
  --page-bg: linear-gradient(180deg, #f7fafc 0%, #eef4ff 100%);
  --panel: rgba(255, 255, 255, 0.9);
  --panel-solid: #ffffff;
  --panel-strong: #f8fafc;
  --panel-soft: rgba(15, 23, 42, 0.035);
  --panel-muted: rgba(226, 232, 240, 0.58);
  --panel-border: rgba(15, 23, 42, 0.12);
  --panel-border-strong: rgba(37, 99, 235, 0.22);
  --text: #172033;
  --muted: #64748b;
  --subtle: #334155;
  --blue: #2563eb;
  --blue-soft: rgba(37, 99, 235, 0.1);
  --blue-strong: rgba(37, 99, 235, 0.16);
  --green: #16a34a;
  --green-soft: rgba(22, 163, 74, 0.1);
  --green-text: #166534;
  --red: #dc2626;
  --red-soft: rgba(220, 38, 38, 0.1);
  --red-text: #991b1b;
  --warning-soft: rgba(220, 38, 38, 0.08);
  --button-text: #1d4ed8;
  --shadow: 0 14px 32px rgba(15, 23, 42, 0.11), inset 0 1px 0 rgba(255, 255, 255, 0.88);
  --modal-backdrop: rgba(15, 23, 42, 0.32);
  --modal-bg: linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(248, 250, 252, 0.98));
  --code-bg: rgba(241, 245, 249, 0.86);
  --skeleton: rgba(100, 116, 139, 0.16);
  --skeleton-glint: rgba(255, 255, 255, 0.72);
  --glass-highlight: rgba(255, 255, 255, 0.88);
  --glass-sheen: rgba(37, 99, 235, 0.07);
  --raised-edge: rgba(255, 255, 255, 0.9);
  --pressed-shadow: inset 0 2px 8px rgba(15, 23, 42, 0.1), inset 0 1px 0 rgba(255, 255, 255, 0.7);
}

.page-shell.theme-night {
  --page-bg: linear-gradient(180deg, #050816 0%, #0f172a 100%);
  --panel: rgba(12, 18, 34, 0.94);
  --panel-solid: #0c1222;
  --panel-strong: #070b16;
  --panel-soft: rgba(255, 255, 255, 0.025);
  --panel-muted: rgba(3, 7, 18, 0.38);
  --panel-border: rgba(148, 163, 184, 0.16);
  --panel-border-strong: rgba(96, 165, 250, 0.28);
  --text: #f8fbff;
  --muted: #93a4c7;
  --subtle: #cbd5e1;
  --blue: #38bdf8;
  --blue-soft: rgba(56, 189, 248, 0.1);
  --blue-strong: rgba(56, 189, 248, 0.2);
  --shadow: 0 18px 50px rgba(0, 0, 0, 0.34), inset 0 1px 0 rgba(255, 255, 255, 0.08);
  --glass-highlight: rgba(255, 255, 255, 0.1);
  --glass-sheen: rgba(56, 189, 248, 0.08);
}

.page-shell.theme-custom {
  --page-bg: linear-gradient(180deg, #101624 0%, #162238 50%, #102a34 100%);
  --panel: rgba(17, 24, 39, 0.9);
  --panel-solid: #111827;
  --panel-strong: #0c1220;
  --panel-muted: rgba(15, 23, 42, 0.42);
  --panel-border: rgba(45, 212, 191, 0.17);
  --panel-border-strong: rgba(45, 212, 191, 0.28);
  --blue: #2dd4bf;
  --blue-soft: rgba(45, 212, 191, 0.1);
  --blue-strong: rgba(45, 212, 191, 0.2);
  --button-text: #ccfbf1;
  --shadow: 0 18px 44px rgba(0, 0, 0, 0.3), inset 0 1px 0 rgba(255, 255, 255, 0.1);
}

.container {
  width: min(1280px, calc(100vw - 32px));
  margin: 0 auto 48px;
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
  background: linear-gradient(90deg, var(--blue-soft), var(--blue-strong), var(--blue-soft));
  border-bottom: 1px solid var(--panel-border-strong);
  color: var(--button-text);
  font-size: 14px;
  backdrop-filter: blur(10px);
}

.refresh-dot {
  width: 10px;
  height: 10px;
  border-radius: 999px;
  background: var(--button-text);
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
  background: var(--modal-backdrop);
  backdrop-filter: blur(3px);
}

.overlay-card {
  min-width: min(420px, calc(100vw - 48px));
  padding: 28px 30px;
  border: 1px solid var(--panel-border-strong);
  border-radius: 8px;
  background: var(--panel-solid);
  box-shadow: var(--shadow);
  text-align: center;
}

.spinner {
  width: 42px;
  height: 42px;
  margin: 0 auto 16px;
  border-radius: 999px;
  border: 4px solid var(--blue-soft);
  border-top-color: var(--blue);
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

.theme-switcher {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px;
  border: 1px solid var(--panel-border);
  border-radius: 999px;
  background: linear-gradient(180deg, var(--glass-highlight), var(--panel-soft));
  box-shadow: var(--shadow);
  backdrop-filter: blur(16px) saturate(145%);
}

.theme-button {
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: var(--muted);
  padding: 6px 11px;
  cursor: pointer;
  font-size: 13px;
}

.theme-button.active {
  background: linear-gradient(180deg, var(--blue-strong), var(--blue-soft));
  color: var(--text);
  box-shadow: var(--pressed-shadow);
}

.custom-palette {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
  max-width: 420px;
}

.color-chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 9px;
  border: 1px solid var(--panel-border);
  border-radius: 999px;
  background: linear-gradient(180deg, var(--glass-highlight), var(--panel-soft));
  color: var(--muted);
  font-size: 12px;
  cursor: pointer;
  box-shadow: var(--shadow);
  backdrop-filter: blur(14px) saturate(145%);
}

.color-chip input {
  width: 24px;
  height: 24px;
  padding: 0;
  border: 1px solid var(--panel-border-strong);
  border-radius: 999px;
  background: transparent;
  cursor: pointer;
}

.color-chip input::-webkit-color-swatch-wrapper {
  padding: 2px;
}

.color-chip input::-webkit-color-swatch {
  border: 0;
  border-radius: 999px;
}

.action-block a {
  color: var(--blue);
  text-decoration: none;
  font-size: 14px;
}

.refresh-button,
.secondary-button {
  border: 1px solid var(--panel-border-strong);
  background:
    linear-gradient(180deg, var(--glass-highlight), transparent 48%),
    linear-gradient(180deg, var(--blue-strong), var(--blue-soft));
  color: var(--button-text);
  border-radius: 8px;
  padding: 8px 14px;
  cursor: pointer;
  min-width: 96px;
  box-shadow: var(--shadow);
  backdrop-filter: blur(14px) saturate(145%);
  transition: transform 0.16s ease, box-shadow 0.16s ease, background 0.16s ease;
}

.refresh-button:hover:not(:disabled),
.secondary-button:hover {
  transform: translateY(-1px);
}

.refresh-button:active:not(:disabled),
.secondary-button:active {
  transform: translateY(1px);
  box-shadow: var(--pressed-shadow);
}

.refresh-button:disabled {
  cursor: wait;
  opacity: 0.75;
}

.refresh-button.refreshing {
  background: linear-gradient(180deg, var(--blue-strong), var(--blue-soft));
}

.panel {
  margin-top: 20px;
  background:
    linear-gradient(145deg, var(--glass-highlight), transparent 34%),
    linear-gradient(180deg, color-mix(in srgb, var(--panel-solid) 78%, transparent), var(--panel));
  border: 1px solid var(--panel-border);
  border-radius: 8px;
  box-shadow: var(--shadow);
  overflow: hidden;
  backdrop-filter: blur(18px) saturate(150%);
}

.panel-header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  padding: 18px 20px;
  border-bottom: 1px solid var(--panel-border);
  background: linear-gradient(180deg, var(--glass-sheen), transparent);
}

.panel-header h2 {
  margin: 0;
  font-size: 18px;
}

.panel-meta {
  color: var(--muted);
  font-size: 13px;
}

.footer {
  margin-top: 14px;
  color: var(--muted);
  font-size: 13px;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 16px;
}

.summary-skeleton-card,
.skeleton-client-card {
  padding: 18px 20px;
  border: 1px solid var(--panel-border);
  border-radius: 8px;
  background:
    linear-gradient(145deg, var(--glass-highlight), transparent 38%),
    var(--panel);
  box-shadow: var(--shadow);
  backdrop-filter: blur(18px) saturate(145%);
}

.table-skeleton,
.logs-skeleton-table {
  padding: 8px 0;
}

.skeleton-row {
  display: grid;
  grid-template-columns: minmax(160px, 0.9fr) minmax(96px, 0.35fr) minmax(260px, 1.3fr);
  gap: 16px;
  align-items: center;
  padding: 14px 20px;
  border-bottom: 1px solid var(--panel-border);
}

.logs-skeleton {
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr);
}

.logs-skeleton-sidebar {
  display: grid;
  gap: 12px;
  padding: 16px;
  border-right: 1px solid var(--panel-border);
  background: var(--panel-muted);
}

.skeleton-line {
  position: relative;
  height: 12px;
  overflow: hidden;
  border-radius: 999px;
  background: var(--skeleton);
}

.skeleton-line::after {
  content: "";
  position: absolute;
  inset: 0;
  transform: translateX(-100%);
  background: linear-gradient(90deg, transparent, var(--skeleton-glint), transparent);
  animation: skeleton-glint 1.4s ease-in-out infinite;
}

.skeleton-line.short { width: 42%; }
.skeleton-line.medium { width: 56%; }
.skeleton-line.wide { width: 86%; }
.skeleton-line.name { width: 72%; }
.skeleton-line.pill { width: 82px; height: 22px; }
.skeleton-line.metric { width: 88px; height: 34px; margin-top: 14px; }

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

@keyframes skeleton-glint {
  to {
    transform: translateX(100%);
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

  .logs-skeleton {
    grid-template-columns: 1fr;
  }

  .logs-skeleton-sidebar {
    border-right: none;
    border-bottom: 1px solid var(--panel-border);
  }

  .skeleton-row {
    grid-template-columns: 1fr;
  }
}
</style>
