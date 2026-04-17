import { createApp } from 'vue'
import App from './App.vue'

// 前端入口保持极简：只负责挂载 Vue 根组件，
// 页面逻辑和状态刷新都收敛到 App.vue，便于后续扩展路由或状态管理。
createApp(App).mount('#app')
