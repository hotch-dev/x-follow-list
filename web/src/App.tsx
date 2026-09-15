import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { AccountsPage } from './AccountsPage'
import { ActionItemsPage } from './ActionItemsPage'
import { ApiError, loadDashboard } from './api'
import { Dashboard } from './Dashboard'
import { LoginPanel } from './LoginPanel'
import { Navigation } from './Navigation'
import { RelationshipsPage } from './RelationshipsPage'
import { ScansPage } from './ScansPage'

export function App() {
  const [path, setPath] = useState(window.location.pathname)
  const [showLogin, setShowLogin] = useState(false)
  const dashboard = useQuery({ queryKey: ['dashboard'], queryFn: loadDashboard })
  useEffect(() => {
    const navigate = () => setPath(window.location.pathname)
    window.addEventListener('popstate', navigate)
    return () => window.removeEventListener('popstate', navigate)
  }, [])

  if (showLogin) {
    return (
      <LoginPanel
        onAuthenticated={async () => {
          await dashboard.refetch()
          setShowLogin(false)
        }}
      />
    )
  }

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
          <button
            type="button"
            onClick={() => {
              if (expired) setShowLogin(true)
              else void dashboard.refetch()
            }}
          >
            {expired ? '重新登录' : '重试'}
          </button>
        </section>
      </main>
    )
  }

  const accountId = dashboard.data.accounts[0]?.id
  const csrfToken = window.sessionStorage.getItem('x-follow-list-csrf') ?? ''
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
      <Navigation path={path} onNavigate={setPath} />
      <main className="app-shell">{page}</main>
    </>
  )
}
