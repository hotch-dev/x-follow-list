import { useQuery } from '@tanstack/react-query'
import { useEffect, useState, type MouseEvent } from 'react'

import { AccountsPage } from './AccountsPage'
import { ActionItemsPage } from './ActionItemsPage'
import { ApiError, loadDashboard } from './api'
import { Dashboard } from './Dashboard'
import { RelationshipsPage } from './RelationshipsPage'
import { ScansPage } from './ScansPage'

const navigation = [
  { href: '/', label: '总览' },
  { href: '/accounts', label: 'X 账号' },
  { href: '/scans', label: '扫描任务' },
  { href: '/relationships', label: '关系结果' },
  { href: '/action-items', label: '重点待处理' },
]

export function App() {
  const [path, setPath] = useState(window.location.pathname)
  const dashboard = useQuery({ queryKey: ['dashboard'], queryFn: loadDashboard })
  useEffect(() => {
    const navigate = () => setPath(window.location.pathname)
    window.addEventListener('popstate', navigate)
    return () => window.removeEventListener('popstate', navigate)
  }, [])

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

  const accountId = dashboard.data.accounts[0]?.id
  const csrfToken = window.sessionStorage.getItem('x-follow-list-csrf') ?? ''
  const navigate = (event: MouseEvent<HTMLAnchorElement>, href: string) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return
    }
    event.preventDefault()
    window.history.pushState({}, '', href)
    setPath(href)
  }
  let page = <Dashboard data={dashboard.data} />
  if (path === '/accounts') page = <AccountsPage csrfToken={csrfToken} />
  if (path === '/scans') page = <ScansPage csrfToken={csrfToken} />
  if (path === '/relationships' && accountId) {
    page = <RelationshipsPage accountId={accountId} />
  }
  if (path === '/action-items' && accountId) {
    page = <ActionItemsPage accountId={accountId} csrfToken={csrfToken} />
  }

  return (
    <>
      <header className="site-header">
        <span className="brand">X Follow List</span>
        <nav aria-label="主导航">
          {navigation.map((item) => (
            <a
              key={item.href}
              href={item.href}
              aria-current={path === item.href ? 'page' : undefined}
              onClick={(event) => navigate(event, item.href)}
            >
              {item.label}
            </a>
          ))}
        </nav>
      </header>
      <main className="app-shell">{page}</main>
    </>
  )
}
