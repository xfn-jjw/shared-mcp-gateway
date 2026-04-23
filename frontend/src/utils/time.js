/**
 * 把 gateway 返回的时间统一格式化为浏览器本地时间，格式固定为：
 * yyyy-MM-dd HH:mm:ss
 *
 * 后端当前会返回两类时间：
 * 1. 秒级时间戳，例如 generatedAt；
 * 2. 带时区偏移的字符串，例如 2026-04-17T13:43:31+08:00。
 *
 * 这里在前端统一转换，避免页面展示直接受 Docker 容器时区影响。
 */
function pad(value) {
  return String(value).padStart(2, '0')
}

function formatDate(date) {
  return [
    date.getFullYear(),
    pad(date.getMonth() + 1),
    pad(date.getDate()),
  ].join('-') + ` ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

export function formatGatewayTime(value) {
  if (value === null || value === undefined || value === '') {
    return '未知'
  }

  if (typeof value === 'number') {
    const date = new Date(value * 1000)
    if (Number.isNaN(date.getTime())) {
      return String(value)
    }
    return formatDate(date)
  }

  const text = String(value).trim()
  if (!text) {
    return '未知'
  }

  // 把 `2026-04-17 05:43:31+0000` 这种格式转成浏览器更稳定可解析的 ISO 形式。
  const normalized = text.replace(
    /^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})([+-]\d{2})(\d{2})$/,
    '$1T$2$3:$4',
  )

  const date = new Date(normalized)
  if (Number.isNaN(date.getTime())) {
    return text
  }

  return formatDate(date)
}
