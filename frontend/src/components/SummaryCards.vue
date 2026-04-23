<script setup>
defineProps({
  summary: {
    type: Object,
    required: true,
  },
  indexes: {
    type: Object,
    required: true,
  },
})

// 汇总卡片统一在一个组件里维护，后续增加指标时不需要改表格逻辑。
const cardItems = (summary, indexes) => [
  { label: '当前接入 MCP', value: summary.total ?? 0, tone: 'blue' },
  { label: '存活', value: summary.alive ?? 0, tone: 'green' },
  { label: '死亡', value: summary.dead ?? 0, tone: 'red' },
  { label: '工具总数', value: indexes.tools ?? 0, tone: 'default' },
]
</script>

<template>
  <section class="summary-grid">
    <article
      v-for="item in cardItems(summary, indexes)"
      :key="item.label"
      class="summary-card"
    >
      <div class="summary-label">{{ item.label }}</div>
      <div class="summary-value" :class="`tone-${item.tone}`">{{ item.value }}</div>
    </article>
  </section>
</template>

<style scoped>
.summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 16px;
}

.summary-card {
  background: rgba(18, 25, 51, 0.92);
  border: 1px solid #26304f;
  border-radius: 16px;
  padding: 18px 20px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.18);
}

.summary-label {
  color: #9aa8d6;
  font-size: 13px;
  margin-bottom: 10px;
}

.summary-value {
  font-size: 30px;
  font-weight: 700;
}

.tone-blue { color: #60a5fa; }
.tone-green { color: #22c55e; }
.tone-red { color: #ef4444; }
.tone-default { color: #ebf0ff; }
</style>
