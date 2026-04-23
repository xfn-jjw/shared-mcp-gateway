<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  servers: {
    type: Array,
    required: true,
  },
})

// 状态排序规则：先显示死亡，再显示存活；同状态下按 key 排序，
// 这样页面一眼就能先看到异常 MCP。
const sortedServers = computed(() => {
  const priority = { dead: 0, alive: 1 }
  return [...props.servers].sort((a, b) => {
    const statusDelta = (priority[a.status] ?? 99) - (priority[b.status] ?? 99)
    if (statusDelta !== 0) return statusDelta
    return String(a.key).localeCompare(String(b.key))
  })
})

// 当前弹窗里正在查看的 MCP。
const selectedServerKey = ref('')

// 通过 key 从最新 props 中回查对象，避免自动刷新后弹窗拿着旧数据。
const selectedServer = computed(
  () => props.servers.find((server) => server.key === selectedServerKey.value) || null,
)

watch(
  () => props.servers,
  (servers) => {
    const validKeys = new Set(servers.map((server) => server.key))
    if (selectedServerKey.value && !validKeys.has(selectedServerKey.value)) {
      selectedServerKey.value = ''
    }
  },
  { deep: true },
)

const probeText = (server) => {
  if (server.probeOk === true) {
    return `OK / ${server.probeDurationMs ?? '-'} ms`
  }
  if (server.probeOk === false) {
    return '失败'
  }
  return '未知'
}

const hasTools = (server) => Array.isArray(server?.tools) && server.tools.length > 0

// 打开弹窗时只记录 key，真正显示内容统一走 computed，方便响应式刷新。
const openToolsModal = (serverKey) => {
  selectedServerKey.value = serverKey
}

const closeToolsModal = () => {
  selectedServerKey.value = ''
}
</script>

<template>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>MCP</th>
          <th>语言</th>
          <th>状态</th>
          <th>工具数</th>
          <th>探活</th>
          <th>熔断器</th>
          <th>错误 / 说明</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="server in sortedServers" :key="server.key">
          <td data-label="MCP">
            <div><strong>{{ server.key }}</strong></div>
            <div class="muted mono">{{ server.namespace }}</div>
          </td>
          <td data-label="语言">{{ server.language || '未知' }}</td>
          <td data-label="状态">
            <span class="badge" :class="server.status">{{ server.statusLabel }}</span>
          </td>
          <td data-label="工具数">{{ server.toolCount }}</td>
          <td data-label="探活">{{ probeText(server) }}</td>
          <td data-label="熔断器">{{ server.breakerState || 'unknown' }}</td>
          <td data-label="错误 / 说明" class="mono">{{ server.errorSummary }}</td>
          <td data-label="操作">
            <button class="tool-button" type="button" @click="openToolsModal(server.key)">
              查看工具
            </button>
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="selectedServer" class="modal-backdrop" @click.self="closeToolsModal">
      <div class="modal-card" role="dialog" aria-modal="true" :aria-label="`${selectedServer.key} 工具列表`">
        <div class="modal-header">
          <div>
            <div class="modal-title-row">
              <h3>{{ selectedServer.key }}</h3>
              <span class="badge" :class="selectedServer.status">{{ selectedServer.statusLabel }}</span>
            </div>
            <div class="modal-subtitle">
              <span class="mono">{{ selectedServer.namespace }}</span>
              <span>·</span>
              <span>{{ selectedServer.language || '未知' }}</span>
              <span>·</span>
              <span>工具 {{ selectedServer.toolCount }}</span>
            </div>
          </div>

          <button class="close-button" type="button" @click="closeToolsModal">关闭</button>
        </div>

        <div class="detail-section">
          <div class="detail-title">这个 MCP 是干什么的</div>
          <div class="detail-text">
            {{ selectedServer.description || '暂未配置说明。可在 registry 里的 [[servers]] 段补充 description。' }}
          </div>
        </div>

        <div class="detail-section">
          <div class="detail-title">工具列表</div>
          <div v-if="hasTools(selectedServer)" class="tool-list">
            <div v-for="tool in selectedServer.tools" :key="`${selectedServer.key}-${tool.name}`" class="tool-item">
              <div class="tool-name mono">{{ tool.name }}</div>
              <div v-if="tool.title" class="tool-title">{{ tool.title }}</div>
              <div class="tool-desc">{{ tool.description || '暂无工具说明' }}</div>
            </div>
          </div>
          <div v-else class="empty-tools">当前未获取到工具列表，可能是 MCP 尚未连接成功。</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
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
  letter-spacing: 0.02em;
}

tbody tr:hover {
  background: rgba(255, 255, 255, 0.03);
}

.badge {
  display: inline-flex;
  align-items: center;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  border: 1px solid transparent;
}

.badge.alive {
  color: #d1fae5;
  background: rgba(34, 197, 94, 0.12);
  border-color: rgba(34, 197, 94, 0.28);
}

.badge.dead {
  color: #fee2e2;
  background: rgba(239, 68, 68, 0.12);
  border-color: rgba(239, 68, 68, 0.28);
}

.tool-button,
.close-button {
  border: 1px solid rgba(96, 165, 250, 0.35);
  background: rgba(96, 165, 250, 0.12);
  color: #dbeafe;
  padding: 8px 12px;
  border-radius: 10px;
  cursor: pointer;
  white-space: nowrap;
}

.tool-button:hover,
.close-button:hover {
  background: rgba(96, 165, 250, 0.2);
}

.modal-backdrop {
  position: fixed;
  inset: 0;
  z-index: 80;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(3, 7, 18, 0.72);
  backdrop-filter: blur(8px);
}

.modal-card {
  width: min(980px, calc(100vw - 32px));
  max-height: min(80vh, 900px);
  overflow: auto;
  padding: 20px;
  border-radius: 18px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: linear-gradient(180deg, rgba(18, 25, 51, 0.98), rgba(11, 16, 32, 0.98));
  box-shadow: 0 24px 80px rgba(0, 0, 0, 0.45);
}

.modal-header {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
  margin-bottom: 20px;
}

.modal-title-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.modal-title-row h3 {
  margin: 0;
  font-size: 26px;
}

.modal-subtitle {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
  color: #9aa8d6;
}

.detail-section + .detail-section {
  margin-top: 20px;
}

.detail-title {
  margin-bottom: 10px;
  color: #bfdbfe;
  font-weight: 700;
}

.detail-text,
.tool-desc,
.tool-title,
.empty-tools {
  color: #c9d5f6;
}

.tool-list {
  display: grid;
  gap: 12px;
}

.tool-item {
  padding: 12px 14px;
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.06);
  background: rgba(255, 255, 255, 0.02);
}

.tool-name {
  color: #ffffff;
  font-weight: 700;
}

.tool-title {
  margin-top: 4px;
}

.tool-desc {
  margin-top: 6px;
  line-height: 1.5;
  white-space: pre-wrap;
}

.muted {
  color: #9aa8d6;
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  word-break: break-all;
}

@media (max-width: 860px) {
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

  .modal-backdrop {
    padding: 12px;
  }

  .modal-card {
    width: 100%;
    max-height: 88vh;
    padding: 16px;
  }

  .modal-header {
    flex-direction: column;
  }
}
</style>
