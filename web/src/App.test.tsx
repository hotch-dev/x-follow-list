import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { App } from './App'

const server = setupServer(
  http.all('/api/v1/*', () => HttpResponse.json({}, { status: 500 })),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function renderApp() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>,
  )
}

function dashboardHandlers() {
  return [
    http.get('/api/v1/x-accounts', () =>
      HttpResponse.json({
        items: [
          {
            id: 'account-1',
            x_user_id: '42',
            username: 'alice',
            display_name: 'Alice',
            session_status: 'READY',
            provider_code: 'ADSPOWER',
            profile_ref: 'profile-3',
            last_successful_scan_at: '2026-09-14T10:30:00Z',
          },
        ],
      }),
    ),
    http.get('/api/v1/scan-runs', () =>
      HttpResponse.json({
        items: [
          {
            id: 'run-1',
            x_account_id: 'account-1',
            status: 'FAILED',
            progress_stage: 'CHECKING_SESSION',
            error: {
              code: 'AUTH_REQUIRED',
              summary: '登录已过期，请重新验证',
            },
            follower_count: null,
            following_count: null,
            created_at: '2026-09-14T11:00:00Z',
            started_at: '2026-09-14T11:00:01Z',
            finished_at: '2026-09-14T11:00:03Z',
            last_successful_scan_at: '2026-09-14T10:30:00Z',
          },
        ],
        next_cursor: null,
      }),
    ),
    http.get('/api/v1/relationship-events', () =>
      HttpResponse.json({
        items: [
          {
            id: 'event-1',
            x_account_id: 'account-1',
            scan_run_id: 'run-1',
            subject_x_user_id: '99',
            username: 'former_mutual',
            display_name: 'Former Mutual',
            category: 'ACTION_ITEM',
            event_type: 'UNFOLLOWED_ME_AFTER_MUTUAL',
            status: 'NEW',
            version: 1,
            created_at: '2026-09-14T11:00:03Z',
            acknowledged_at: null,
          },
        ],
        next_cursor: null,
      }),
    ),
  ]
}

describe('A-12 dashboard journey', () => {
  it('shows account health, last good data, recent failure, and priority action items', async () => {
    server.use(...dashboardHandlers())

    renderApp()

    expect(await screen.findByRole('heading', { name: '总览' })).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Alice' })).toBeVisible()
    expect(screen.getByText('AdsPower · 已连接')).toBeVisible()
    expect(screen.getByText('登录已过期，请重新验证')).toBeVisible()
    expect(screen.getByText('数据仍来自上次成功扫描')).toBeVisible()
    expect(screen.getByText('2026-09-14 10:30 UTC')).toBeVisible()
    expect(
      screen.getByRole('link', { name: '互关后取消关注 1 项' }),
    ).toHaveAttribute('href', '/action-items')
  })

  it('shows a session-expired state instead of an empty dashboard', async () => {
    server.use(
      http.get('/api/v1/x-accounts', () =>
        HttpResponse.json(
          { code: 'AUTH_REQUIRED', message: 'Authentication required' },
          { status: 401 },
        ),
      ),
    )

    renderApp()

    expect(await screen.findByRole('alert')).toHaveTextContent('登录已过期')
    expect(screen.getByRole('button', { name: '重新登录' })).toBeVisible()
    expect(screen.queryByText('暂无监控账号')).not.toBeInTheDocument()
  })
})
