<script setup>
import { computed, ref, watch } from 'vue'
import { formatGatewayTime } from '../utils/time'

const props = defineProps({
  recentClients: {
    type: Array,
    required: true,
  },
  recentLogs: {
    type: Array,
    required: true,
  },
  servers: {
    type: Array,
    required: true,
  },
})

// 左侧选中项使用“caller@ip”复合键，确保同一 IP 下的不同调用端不会互相覆盖。
const selectedClientKey = ref('all')
// 失败日志开关：用单独按钮在“全部日志 / 最近失败日志”之间切换。
const errorOnly = ref(false)
const jsonViewer = ref({
  open: false,
  title: '',
  value: null,
})

// 当后端返回的新 IP 列表里已经没有当前选中项时，自动回退到“全部”，
// 避免页面停留在一个已经过期的过滤条件上。
watch(
  () => props.recentClients,
  (clients) => {
    const exists = clients.some((client) => (client.clientKey || client.clientIp) === selectedClientKey.value)
    if (selectedClientKey.value !== 'all' && !exists) {
      selectedClientKey.value = 'all'
    }
  },
  { deep: true },
)

const selectedClient = computed(() => {
  if (selectedClientKey.value === 'all') {
    return null
  }
  return (
    props.recentClients.find((client) => (client.clientKey || client.clientIp) === selectedClientKey.value) ||
    null
  )
})

const filteredLogs = computed(() => {
  const baseLogs =
    selectedClientKey.value === 'all'
      ? props.recentLogs
      : props.recentLogs.filter((log) => {
          const currentKey = `${log.caller || 'unknown'}@${log.clientIp || '-'}`
          return currentKey === selectedClientKey.value
        })

  // 关键筛选：支持只看失败记录，方便快速定位每个调用端的错误日志。
  if (errorOnly.value) {
    return baseLogs.filter((log) => ['error', 'exception', 'circuit_open'].includes(String(log.status)))
  }
  return baseLogs
})

const totalErrors = computed(
  () => props.recentClients.reduce((sum, client) => sum + (client.errorCount || 0), 0),
)

const serverDisplayMap = computed(() => {
  const entries = props.servers.map((server) => [
    server.key,
    server.namespace && server.namespace !== server.key
      ? `${server.key} (${server.namespace})`
      : server.key,
  ])
  return new Map(entries)
})

function selectClient(clientKey) {
  selectedClientKey.value = clientKey
}

function statusClass(status) {
  if (status === 'success' || status === 200) return 'status-success'
  if (status === 'error' || status === 'exception' || status === 'circuit_open') return 'status-error'
  return 'status-neutral'
}

function eventLabel(log) {
  if (log.eventType === 'mcp_request') return 'MCP 请求'
  return log.eventType === 'tool_call' ? 'Tool 调用' : 'HTTP 请求'
}

function formatMcpName(value) {
  if (!value) return '-'
  return serverDisplayMap.value.get(value) || value
}

function mcpLabel(log) {
  if (log.downstream) {
    return formatMcpName(log.downstream)
  }
  if (log.tool && String(log.tool).includes('.')) {
    return formatMcpName(String(log.tool).split('.')[0])
  }
  if (log.eventType === 'mcp_request') {
    return '全部 MCP'
  }
  return '-'
}

function targetLabel(log) {
  if (log.tool) {
    return log.tool
  }
  if (log.method || log.path) {
    return [log.method, log.path].filter(Boolean).join(' ')
  }
  return '-'
}

function hasJson(value) {
  return value !== null && value !== undefined
}

function formatJson(value) {
  if (!hasJson(value)) {
    return '-'
  }
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function openJsonViewer(title, value) {
  if (!hasJson(value)) {
    return
  }
  jsonViewer.value = {
    open: true,
    title,
    value,
  }
}

function closeJsonViewer() {
  jsonViewer.value = {
    open: false,
    title: '',
    value: null,
  }
}

function displayTime(value) {
  return formatGatewayTime(value)
}

function toggleErrorOnly() {
  errorOnly.value = !errorOnly.value
}
</script>

<template>
  <section class="panel">
    <div class="panel-header">
      <h2>接入端与调用日志</h2>
      <div class="panel-meta">
        最近 IP：{{ recentClients.length }} · 最近日志：{{ recentLogs.length }} · 错误：{{ totalErrors }}
      </div>
    </div>

    <div class="activity-layout">
      <aside class="client-sidebar">
        <button
          class="client-card"
          :class="{ active: selectedClientKey === 'all' }"
          type="button"
          @click="selectClient('all')"
        >
          <div class="client-ip">全部客户端</div>
          <div class="client-meta">显示所有接入人的最近 HTTP / Tool 调用与错误</div>
        </button>

        <button
          v-for="client in recentClients"
          :key="client.clientKey || `${client.caller || 'unknown'}@${client.clientIp}`"
          class="client-card"
          :class="{ active: selectedClientKey === (client.clientKey || `${client.caller || 'unknown'}@${client.clientIp}`) }"
          type="button"
          @click="selectClient(client.clientKey || `${client.caller || 'unknown'}@${client.clientIp}`)"
        >
          <div class="client-ip">{{ client.caller || 'unknown' }}</div>
          <div class="client-meta mono">{{ client.clientIp }}</div>
          <div class="client-stats">
            <span>调用 {{ client.eventCount }}</span>
            <span>Tool {{ client.toolCalls }}</span>
            <span>错误 {{ client.errorCount }}</span>
          </div>
          <div class="client-extra">最后活跃：{{ displayTime(client.lastSeenAt) }}</div>
          <div v-if="client.lastDownstream" class="client-extra muted">
            最近 MCP：{{ formatMcpName(client.lastDownstream) }}
          </div>
          <div v-if="client.lastTool || client.lastPath" class="client-extra muted">
            最近目标：{{ client.lastTool || client.lastPath }}
          </div>
        </button>
      </aside>

      <div class="log-panel">
        <div class="selection-bar">
          <div>
            <strong>{{ selectedClient ? (selectedClient.caller || 'unknown') : '全部客户端' }}</strong>
            <span class="muted">
              {{ selectedClient ? ` · ${selectedClient.clientIp || '-'}` : ' · 查看全部最近调用' }}
            </span>
          </div>
          <div class="selection-actions">
            <button
              class="filter-toggle"
              :class="{ active: errorOnly }"
              type="button"
              @click="toggleErrorOnly"
            >
              {{ errorOnly ? '查看全部日志' : '查看最近失败日志' }}
            </button>
            <div v-if="selectedClient" class="muted">
              最近：{{ displayTime(selectedClient.lastSeenAt) }}
            </div>
          </div>
        </div>

        <div v-if="filteredLogs.length === 0" class="empty-state">
          {{ errorOnly ? '暂无错误日志。' : '暂无调用日志。' }}
        </div>
        <div v-else class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>时间</th>
                <th>IP</th>
                <th>Caller</th>
                <th>类型</th>
                <th>MCP</th>
                <th>目标</th>
                <th>状态</th>
                <th>耗时</th>
                <th>详情</th>
                <th>错误</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="log in filteredLogs" :key="`${log.requestId}-${log.timestamp}-${log.eventType}-${log.tool || log.path || '-'}`">
                <td data-label="时间" class="mono">{{ displayTime(log.timestamp) }}</td>
                <td data-label="IP" class="mono">{{ log.clientIp || '-' }}</td>
                <td data-label="Caller">{{ log.caller || '-' }}</td>
                <td data-label="类型">{{ eventLabel(log) }}</td>
                <td data-label="MCP" class="mono">{{ mcpLabel(log) }}</td>
                <td data-label="目标" class="mono">{{ targetLabel(log) }}</td>
                <td data-label="状态">
                  <span class="status-pill" :class="statusClass(log.status)">{{ log.status ?? '-' }}</span>
                </td>
                <td data-label="耗时">{{ log.durationMs ?? '-' }} ms</td>
                <td data-label="详情" class="detail-actions">
                  <button
                    class="detail-button"
                    type="button"
                    :disabled="!hasJson(log.requestJson)"
                    @click="openJsonViewer('调用入参', log.requestJson)"
                  >
                    查看入参
                  </button>
                  <button
                    class="detail-button"
                    type="button"
                    :disabled="!hasJson(log.responseJson)"
                    @click="openJsonViewer('调用出参', log.responseJson)"
                  >
                    查看出参
                  </button>
                </td>
                <td data-label="错误" class="mono error-text">{{ log.errorSummary || '-' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <div v-if="jsonViewer.open" class="json-modal-backdrop" @click.self="closeJsonViewer">
      <section class="json-modal" role="dialog" aria-modal="true" :aria-label="jsonViewer.title">
        <header class="json-modal-header">
          <h3>{{ jsonViewer.title }}</h3>
          <button class="modal-close" type="button" aria-label="关闭" @click="closeJsonViewer">×</button>
        </header>
        <pre class="json-modal-body">{{ formatJson(jsonViewer.value) }}</pre>
      </section>
    </div>
  </section>
</template>

<style scoped>
.activity-layout {
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr);
  min-height: 420px;
}

.client-sidebar {
  padding: 16px;
  border-right: 1px solid var(--panel-border);
  display: flex;
  flex-direction: column;
  gap: 12px;
  background:
    linear-gradient(145deg, var(--glass-sheen), transparent 36%),
    var(--panel-muted);
  backdrop-filter: blur(16px) saturate(140%);
}

.client-card {
  width: 100%;
  text-align: left;
  border: 1px solid var(--panel-border);
  background:
    linear-gradient(145deg, var(--glass-highlight), transparent 38%),
    var(--panel-soft);
  color: var(--text);
  border-radius: 8px;
  padding: 14px;
  cursor: pointer;
  box-shadow: var(--shadow);
  backdrop-filter: blur(14px) saturate(145%);
  transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease, background 0.18s ease;
}

.client-card:hover,
.client-card.active {
  border-color: var(--panel-border-strong);
  background:
    linear-gradient(145deg, var(--glass-highlight), transparent 34%),
    linear-gradient(180deg, var(--blue-soft), var(--panel-soft));
}

.client-card:hover {
  transform: translateY(-2px);
}

.client-card.active {
  box-shadow: var(--pressed-shadow);
}

.client-ip {
  font-size: 16px;
  font-weight: 700;
  margin-bottom: 6px;
}

.client-meta,
.client-extra,
.selection-bar,
.empty-state,
.muted {
  color: var(--muted);
}

.client-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin: 10px 0 8px;
  font-size: 12px;
}

.log-panel {
  min-width: 0;
}

.selection-bar {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  padding: 16px 20px;
  border-bottom: 1px solid var(--panel-border);
  background: linear-gradient(180deg, var(--glass-sheen), transparent);
}

.empty-state {
  padding: 32px 20px;
}

.selection-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.filter-toggle {
  display: inline-flex;
  align-items: center;
  border: 1px solid var(--panel-border-strong);
  background:
    linear-gradient(180deg, var(--glass-highlight), transparent 48%),
    linear-gradient(180deg, var(--blue-strong), var(--blue-soft));
  color: var(--button-text);
  font-size: 13px;
  border-radius: 999px;
  padding: 8px 12px;
  cursor: pointer;
  box-shadow: var(--shadow);
  backdrop-filter: blur(14px) saturate(145%);
  transition: transform 0.16s ease, box-shadow 0.16s ease, background 0.16s ease;
}

.filter-toggle:hover {
  border-color: var(--panel-border-strong);
  background:
    linear-gradient(180deg, var(--glass-highlight), transparent 48%),
    var(--blue-strong);
  transform: translateY(-1px);
}

.filter-toggle.active {
  border-color: color-mix(in srgb, var(--red) 36%, transparent);
  background: var(--warning-soft);
  color: var(--red-text);
}

.table-wrap {
  overflow: auto;
}

table {
  width: 100%;
  border-collapse: collapse;
}

th,
td {
  padding: 14px 16px;
  border-bottom: 1px solid var(--panel-border);
  text-align: left;
  vertical-align: top;
  font-size: 14px;
}

th {
  color: var(--muted);
  font-weight: 600;
  font-size: 13px;
}

tbody tr {
  transition: background 0.16s ease;
}

tbody tr:hover {
  background: linear-gradient(90deg, var(--glass-sheen), var(--panel-soft));
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  word-break: break-all;
}

.status-pill {
  display: inline-flex;
  align-items: center;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  border: 1px solid transparent;
  box-shadow: inset 0 1px 0 var(--raised-edge), 0 6px 14px rgba(0, 0, 0, 0.08);
}

.status-success {
  color: var(--green-text);
  background: var(--green-soft);
  border-color: color-mix(in srgb, var(--green) 28%, transparent);
}

.status-error {
  color: var(--red-text);
  background: var(--red-soft);
  border-color: color-mix(in srgb, var(--red) 28%, transparent);
}

.status-neutral {
  color: var(--button-text);
  background: var(--blue-soft);
  border-color: var(--panel-border-strong);
}

.error-text {
  max-width: 360px;
}

.detail-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  min-width: 168px;
}

.detail-button {
  border: 1px solid var(--panel-border-strong);
  background:
    linear-gradient(180deg, var(--glass-highlight), transparent 48%),
    linear-gradient(180deg, var(--blue-strong), var(--blue-soft));
  color: var(--button-text);
  border-radius: 999px;
  padding: 6px 10px;
  font-size: 12px;
  cursor: pointer;
  box-shadow: 0 8px 18px rgba(0, 0, 0, 0.1), inset 0 1px 0 var(--raised-edge);
  backdrop-filter: blur(12px) saturate(145%);
  transition: transform 0.16s ease, box-shadow 0.16s ease, background 0.16s ease;
}

.detail-button:hover:not(:disabled) {
  border-color: var(--panel-border-strong);
  background:
    linear-gradient(180deg, var(--glass-highlight), transparent 48%),
    var(--blue-strong);
  transform: translateY(-1px);
}

.detail-button:disabled {
  cursor: not-allowed;
  opacity: 0.42;
}

.json-modal-backdrop {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: var(--modal-backdrop);
  backdrop-filter: blur(10px) saturate(130%);
}

.json-modal {
  width: min(920px, 100%);
  max-height: min(760px, calc(100vh - 48px));
  border: 1px solid var(--panel-border);
  border-radius: 8px;
  background:
    linear-gradient(145deg, var(--glass-highlight), transparent 34%),
    var(--modal-bg);
  color: var(--text);
  box-shadow: var(--shadow);
  overflow: hidden;
  backdrop-filter: blur(20px) saturate(150%);
}

.json-modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 18px;
  border-bottom: 1px solid var(--panel-border);
}

.json-modal-header h3 {
  margin: 0;
  font-size: 16px;
}

.modal-close {
  width: 32px;
  height: 32px;
  border: 1px solid var(--panel-border);
  border-radius: 999px;
  background:
    linear-gradient(180deg, var(--glass-highlight), transparent 48%),
    var(--panel-soft);
  color: var(--text);
  font-size: 20px;
  line-height: 1;
  cursor: pointer;
  box-shadow: var(--shadow);
}

.modal-close:hover {
  background: var(--blue-soft);
}

.json-modal-body {
  margin: 0;
  max-height: calc(100vh - 150px);
  padding: 18px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  background: color-mix(in srgb, var(--code-bg) 82%, transparent);
  color: var(--button-text);
  font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

@media (max-width: 1080px) {
  .activity-layout {
    grid-template-columns: 1fr;
  }

  .client-sidebar {
    border-right: none;
    border-bottom: 1px solid var(--panel-border);
  }
}

@media (max-width: 860px) {
  .selection-bar {
    flex-direction: column;
    align-items: flex-start;
  }

  table,
  thead,
  tbody,
  th,
  td,
  tr {
    display: block;
  }

  thead {
    display: none;
  }

  tr {
    padding: 10px 14px;
  }

  td {
    padding: 8px 0;
    border: none;
  }

  td::before {
    content: attr(data-label);
    display: block;
    color: var(--muted);
    font-size: 12px;
    margin-bottom: 4px;
  }
}
</style>
