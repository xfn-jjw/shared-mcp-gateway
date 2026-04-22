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
                <td data-label="错误" class="mono error-text">{{ log.errorSummary || '-' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
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
  border-right: 1px solid rgba(255, 255, 255, 0.06);
  display: flex;
  flex-direction: column;
  gap: 12px;
  background: rgba(11, 16, 32, 0.25);
}

.client-card {
  width: 100%;
  text-align: left;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(255, 255, 255, 0.02);
  color: #ebf0ff;
  border-radius: 14px;
  padding: 14px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.client-card:hover,
.client-card.active {
  border-color: rgba(96, 165, 250, 0.45);
  background: rgba(96, 165, 250, 0.12);
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
  color: #9aa8d6;
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
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
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
  border: 1px solid rgba(96, 165, 250, 0.28);
  background: rgba(96, 165, 250, 0.08);
  color: #d7e1ff;
  font-size: 13px;
  border-radius: 999px;
  padding: 8px 12px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.filter-toggle:hover {
  border-color: rgba(96, 165, 250, 0.5);
  background: rgba(96, 165, 250, 0.16);
}

.filter-toggle.active {
  border-color: rgba(248, 113, 113, 0.45);
  background: rgba(239, 68, 68, 0.14);
  color: #ffe4e6;
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
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  text-align: left;
  vertical-align: top;
  font-size: 14px;
}

th {
  color: #9aa8d6;
  font-weight: 600;
  font-size: 13px;
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
}

.status-success {
  color: #d1fae5;
  background: rgba(34, 197, 94, 0.12);
  border-color: rgba(34, 197, 94, 0.28);
}

.status-error {
  color: #fee2e2;
  background: rgba(239, 68, 68, 0.12);
  border-color: rgba(239, 68, 68, 0.28);
}

.status-neutral {
  color: #dbeafe;
  background: rgba(96, 165, 250, 0.12);
  border-color: rgba(96, 165, 250, 0.28);
}

.error-text {
  max-width: 360px;
}

@media (max-width: 1080px) {
  .activity-layout {
    grid-template-columns: 1fr;
  }

  .client-sidebar {
    border-right: none;
    border-bottom: 1px solid rgba(255, 255, 255, 0.06);
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
    color: #9aa8d6;
    font-size: 12px;
    margin-bottom: 4px;
  }
}
</style>
