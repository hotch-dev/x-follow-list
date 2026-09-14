import { useQuery } from '@tanstack/react-query'

import { ApiError, loadDashboard } from './api'
import { Dashboard } from './Dashboard'

export function App() {
  const dashboard = useQuery({ queryKey: ['dashboard'], queryFn: loadDashboard })

  if (dashboard.isPending) {
    return <main className="app-shell" aria-busy="true">正在加载监控数据…</main>
  }
  if (dashboard.error) {
    const expired = dashboard.error instanceof ApiError && dashboard.error.status === 401
    return (
      <main className="app-shell">
        <section className="status-card" role="alert">
          <p className="eyebrow">X Follow List</p>
          <h1>{expired ? '登录已过期' : '暂时无法加载数据'}</h1>
          <p>{expired ? '请重新登录后继续。' : '请稍后重试，现有数据不会被清空。'}</p>
          <button type="button" onClick={() => void dashboard.refetch()}>
            {expired ? '重新登录' : '重试'}
          </button>
        </section>
      </main>
    )
  }

  return (
    <main className="app-shell">
      <Dashboard data={dashboard.data} />
    </main>
  )
}
