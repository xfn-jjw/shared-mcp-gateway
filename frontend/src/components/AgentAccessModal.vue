<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  visible: {
    type: Boolean,
    required: true,
  },
  agentConfigs: {
    type: Array,
    required: true,
  },
})

const emit = defineEmits(['close'])

// 默认选中第一份可用配置，避免用户打开弹窗后还要再点一次。
const selectedKey = ref('')
const selectedMode = ref('config')
const copyState = ref('')

watch(
  () => [props.visible, props.agentConfigs],
  ([visible, configs]) => {
    if (!visible) {
      copyState.value = ''
      return
    }
    if (!['config', 'prompt'].includes(selectedMode.value)) {
      selectedMode.value = 'config'
    }
    if (!selectedKey.value || !configs.some((config) => config.key === selectedKey.value)) {
      selectedKey.value = configs[0]?.key || ''
    }
  },
  { deep: true, immediate: true },
)

const selectedConfig = computed(
  () => props.agentConfigs.find((config) => config.key === selectedKey.value) || null,
)

const selectedText = computed(() => {
  if (!selectedConfig.value) return ''
  return selectedMode.value === 'prompt'
    ? selectedConfig.value.promptContent || ''
    : selectedConfig.value.content || ''
})

const selectedDisplayText = computed(() => {
  if (!selectedConfig.value) return ''
  return selectedMode.value === 'prompt'
    ? selectedConfig.value.displayPromptContent || selectedConfig.value.promptContent || ''
    : selectedConfig.value.displayContent || selectedConfig.value.content || ''
})

const copyButtonLabel = computed(() => {
  if (!selectedConfig.value) return '复制'
  if (copyState.value === `${selectedConfig.value.key}:${selectedMode.value}`) {
    return '已复制'
  }
  return selectedMode.value === 'prompt'
    ? `复制 ${selectedConfig.value.title} 提示词`
    : `复制 ${selectedConfig.value.title} 配置`
})

async function copyConfig() {
  if (!selectedConfig.value) return
  await navigator.clipboard.writeText(selectedText.value)
  copyState.value = `${selectedConfig.value.key}:${selectedMode.value}`
  window.setTimeout(() => {
    if (copyState.value === `${selectedConfig.value?.key}:${selectedMode.value}`) {
      copyState.value = ''
    }
  }, 1500)
}

function closeModal() {
  emit('close')
}
</script>

<template>
  <div v-if="visible" class="modal-backdrop" @click.self="closeModal">
    <div class="modal-card" role="dialog" aria-modal="true" aria-label="Agent 一键接入">
      <div class="modal-header">
        <div>
          <h3>Agent 一键接入</h3>
          <p>支持两种接入方式：直接复制配置，或者复制给 agent 的提示词。</p>
        </div>
        <button class="close-button" type="button" @click="closeModal">关闭</button>
      </div>

      <div v-if="agentConfigs.length === 0" class="empty-state">
        当前没有可用的 agent 配置产物。请先生成 `generated/` 目录下的接入文件。
      </div>

      <template v-else>
        <div class="tab-row">
          <button
            v-for="config in agentConfigs"
            :key="config.key"
            class="tab-button"
            :class="{ active: selectedKey === config.key }"
            type="button"
            @click="selectedKey = config.key"
          >
            {{ config.title }}
          </button>
        </div>

        <div v-if="selectedConfig" class="content-card">
          <div class="mode-row">
            <button
              class="mode-button"
              :class="{ active: selectedMode === 'config' }"
              type="button"
              @click="selectedMode = 'config'"
            >
              配置接入
            </button>
            <button
              class="mode-button"
              :class="{ active: selectedMode === 'prompt' }"
              type="button"
              @click="selectedMode = 'prompt'"
            >
              提示词接入
            </button>
          </div>

          <div class="meta-row">
            <div class="meta-block">
              <div class="meta-label">{{ selectedMode === 'prompt' ? '提示词基于配置文件生成' : '来源文件' }}</div>
              <div class="meta-value mono">{{ selectedConfig.sourceFileDisplay || selectedConfig.sourceFile }}</div>
            </div>
            <div v-if="selectedConfig.targetPath || selectedConfig.targetPathDisplay" class="meta-block">
              <div class="meta-label">{{ selectedMode === 'prompt' ? '提示词里的目标文件' : '建议粘贴到' }}</div>
              <div class="meta-value mono">{{ selectedConfig.targetPathDisplay || selectedConfig.targetPath }}</div>
            </div>
          </div>

          <div class="action-row">
            <div v-if="selectedConfig.displaySanitized" class="hint-text">
              页面展示的是案例路径；复制按钮会复制真实可用{{ selectedMode === 'prompt' ? '提示词' : '配置' }}。
            </div>
            <button class="copy-button" type="button" @click="copyConfig">
              {{ copyButtonLabel }}
            </button>
          </div>

          <pre class="config-block"><code>{{ selectedDisplayText }}</code></pre>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.modal-backdrop {
  position: fixed;
  inset: 0;
  z-index: 90;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(3, 7, 18, 0.72);
  backdrop-filter: blur(8px);
}

.modal-card {
  width: min(1100px, calc(100vw - 32px));
  max-height: min(86vh, 960px);
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
  margin-bottom: 18px;
}

.modal-header h3 {
  margin: 0 0 8px;
  font-size: 26px;
}

.modal-header p,
.meta-label,
.empty-state {
  color: #9aa8d6;
}

.close-button,
.copy-button,
.tab-button,
.mode-button {
  border: 1px solid rgba(96, 165, 250, 0.35);
  background: rgba(96, 165, 250, 0.12);
  color: #dbeafe;
  padding: 8px 12px;
  border-radius: 10px;
  cursor: pointer;
}

.close-button:hover,
.copy-button:hover,
.tab-button:hover,
.tab-button.active,
.mode-button:hover,
.mode-button.active {
  background: rgba(96, 165, 250, 0.2);
}

.tab-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 16px;
}

.mode-row {
  display: flex;
  gap: 10px;
  padding: 16px 16px 0;
}

.content-card {
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.02);
  overflow: hidden;
}

.meta-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 14px;
  padding: 16px 16px 0;
}

.meta-value {
  margin-top: 6px;
  color: #ebf0ff;
}

.action-row {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  padding: 16px;
}

.hint-text {
  margin-right: auto;
  color: #9aa8d6;
  font-size: 13px;
}

.config-block {
  margin: 0;
  padding: 16px;
  overflow: auto;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  background: rgba(11, 16, 32, 0.45);
  color: #dbeafe;
  font-size: 13px;
  line-height: 1.6;
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  word-break: break-all;
}

@media (max-width: 860px) {
  .modal-backdrop {
    padding: 12px;
  }

  .modal-card {
    width: 100%;
    max-height: 90vh;
    padding: 16px;
  }

  .modal-header {
    flex-direction: column;
  }

  .mode-row,
  .action-row {
    flex-direction: column;
    align-items: stretch;
  }

  .hint-text {
    margin-right: 0;
  }
}
</style>
