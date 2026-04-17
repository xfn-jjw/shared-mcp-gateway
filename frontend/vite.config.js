import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 关键说明：仪表盘最终由 Python 网关挂在 /dashboard/ 路径下，
// 因此前端构建产物也需要固定使用这个 base，避免静态资源路径错乱。
export default defineConfig({
  plugins: [vue()],
  base: '/dashboard/',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
