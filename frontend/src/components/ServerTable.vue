<script setup>
defineProps({
  servers: {
    type: Array,
    required: true,
  },
})

// 状态排序规则：先显示死亡，再显示存活；同状态下按 key 排序，
// 这样页面一眼就能先看到异常 MCP。
const sortedServers = (servers) => {
  const priority = { dead: 0, alive: 1 }
  return [...servers].sort((a, b) => {
    const statusDelta = (priority[a.status] ?? 99) - (priority[b.status] ?? 99)
    if (statusDelta !== 0) return statusDelta
    return String(a.key).localeCompare(String(b.key))
  })
}

const probeText = (server) => {
  if (server.probeOk === true) {
    return `OK / ${server.probeDurationMs ?? '-'} ms`
  }
  if (server.probeOk === false) {
    return '失败'
  }
  return '未知'
}
</script>

<template>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>MCP</th>
          <th>状态</th>
          <th>工具数</th>
          <th>探活</th>
          <th>熔断器</th>
          <th>错误 / 说明</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="server in sortedServers(servers)" :key="server.key">
          <td data-label="MCP">
            <div><strong>{{ server.key }}</strong></div>
            <div class="muted mono">{{ server.namespace }}</div>
          </td>
          <td data-label="状态">
            <span class="badge" :class="server.status">{{ server.statusLabel }}</span>
          </td>
          <td data-label="工具数">{{ server.toolCount }}</td>
          <td data-label="探活">{{ probeText(server) }}</td>
          <td data-label="熔断器">{{ server.breakerState || 'unknown' }}</td>
          <td data-label="错误 / 说明" class="mono">{{ server.errorSummary }}</td>
        </tr>
      </tbody>
    </table>
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
}
</style>
