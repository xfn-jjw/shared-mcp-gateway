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
  position: relative;
  overflow: hidden;
  background:
    linear-gradient(145deg, var(--glass-highlight), transparent 38%),
    linear-gradient(180deg, color-mix(in srgb, var(--panel-solid) 76%, transparent), var(--panel));
  border: 1px solid var(--panel-border);
  border-radius: 8px;
  padding: 18px 20px;
  box-shadow: var(--shadow);
  backdrop-filter: blur(18px) saturate(150%);
  transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
}

.summary-card::after {
  content: "";
  position: absolute;
  inset: 1px;
  pointer-events: none;
  border-radius: 7px;
  box-shadow: inset 0 1px 0 var(--raised-edge), inset 0 -18px 36px rgba(0, 0, 0, 0.06);
}

.summary-card:hover {
  transform: translateY(-2px);
  border-color: var(--panel-border-strong);
}

.summary-label {
  color: var(--muted);
  font-size: 13px;
  margin-bottom: 10px;
}

.summary-value {
  font-size: 30px;
  font-weight: 700;
  text-shadow: 0 8px 18px color-mix(in srgb, currentColor 24%, transparent);
}

.tone-blue { color: var(--blue); }
.tone-green { color: var(--green); }
.tone-red { color: var(--red); }
.tone-default { color: var(--text); }
</style>
