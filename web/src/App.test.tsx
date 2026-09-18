import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { App } from './App'

const server = setupServer(
  http.all('/api/v1/*', () => HttpResponse.json({}, { status: 500 })),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => {
  server.resetHandlers()
  window.history.replaceState({}, '', '/')
  window.sessionStorage.clear()
})
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

  it('recovers an expired session through login instead of showing an empty dashboard', async () => {
    let authenticated = false
    const [, scans, events] = dashboardHandlers()
    server.use(
      http.get('/api/v1/x-accounts', () =>
        authenticated
          ? HttpResponse.json({
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
            })
          : HttpResponse.json(
              { code: 'AUTH_REQUIRED', message: 'Authentication required' },
              { status: 401 },
            ),
      ),
      scans,
      events,
      http.post('/api/v1/auth/session', async ({ request }) => {
        expect(await request.json()).toEqual({
          login: 'owner@example.test',
          password: 'a sufficiently long password',
        })
        authenticated = true
        return HttpResponse.json({ csrf_token: 'new-csrf-token' })
      }),
    )
    const user = userEvent.setup()

    renderApp()

    expect(await screen.findByRole('alert')).toHaveTextContent('登录已过期')
    await user.click(screen.getByRole('button', { name: '重新登录' }))
    await user.type(screen.getByRole('textbox', { name: '登录账号' }), 'owner@example.test')
    await user.type(
      screen.getByLabelText('密码'),
      'a sufficiently long password',
    )
    await user.click(screen.getByRole('button', { name: '登录' }))

    expect(await screen.findByRole('heading', { name: '总览' })).toBeVisible()
    expect(window.sessionStorage.getItem('x-follow-list-csrf')).toBe('new-csrf-token')
    expect(screen.queryByText('暂无监控账号')).not.toBeInTheDocument()
  })

  it('provides keyboard-accessible navigation across the five admin pages', async () => {
    server.use(
      ...dashboardHandlers(),
      http.get('/api/v1/browser-provider-configs', () =>
        HttpResponse.json({ items: [] }),
      ),
      http.get('/api/v1/relationships', () =>
        HttpResponse.json({ items: [], next_cursor: null }),
      ),
    )
    const user = userEvent.setup()
    renderApp()

    const accountsLink = await screen.findByRole('link', { name: 'X 账号' })
    accountsLink.focus()
    expect(accountsLink).toHaveFocus()
    await user.keyboard('{Enter}')

    expect(await screen.findByRole('heading', { name: 'X 账号管理' })).toBeVisible()
    expect(screen.getByRole('navigation', { name: '主导航' })).toBeVisible()
    expect(screen.getAllByRole('link')).toHaveLength(6)
    await user.click(screen.getByRole('link', { name: '扫描任务' }))
    expect(await screen.findByRole('heading', { name: '扫描任务' })).toBeVisible()
    await user.click(screen.getByRole('link', { name: '关系结果' }))
    expect(await screen.findByRole('heading', { name: '关系结果' })).toBeVisible()
    await user.click(screen.getByRole('link', { name: '重点待处理' }))
    expect(await screen.findByRole('heading', { name: '重点待处理' })).toBeVisible()
    await user.click(screen.getByRole('link', { name: '名单规则' }))
    expect(await screen.findByRole('heading', { name: '名单规则' })).toBeVisible()
  })

  it('distinguishes an empty account list from a request failure', async () => {
    server.use(
      http.get('/api/v1/x-accounts', () => HttpResponse.json({ items: [] })),
    )

    renderApp()

    expect(await screen.findByText('暂无监控账号，请先前往账号管理完成绑定。')).toBeVisible()
  })

  it('retries a transient dashboard error without clearing it into an empty state', async () => {
    let attempts = 0
    server.use(
      http.get('/api/v1/x-accounts', () => {
        attempts += 1
        return attempts === 1
          ? HttpResponse.json({ code: 'TEMPORARY_FAILURE' }, { status: 503 })
          : HttpResponse.json({ items: [] })
      }),
    )
    const user = userEvent.setup()
    renderApp()

    expect(await screen.findByRole('alert')).toHaveTextContent('暂时无法加载数据')
    await user.click(screen.getByRole('button', { name: '重试' }))

    expect(await screen.findByText('暂无监控账号，请先前往账号管理完成绑定。')).toBeVisible()
  })

  it('keeps invalid credentials in the login form error state', async () => {
    server.use(
      http.get('/api/v1/x-accounts', () =>
        HttpResponse.json({ code: 'AUTH_REQUIRED' }, { status: 401 }),
      ),
      http.post('/api/v1/auth/session', () =>
        HttpResponse.json({ code: 'AUTH_FAILED' }, { status: 401 }),
      ),
    )
    const user = userEvent.setup()
    renderApp()

    await user.click(await screen.findByRole('button', { name: '重新登录' }))
    await user.type(screen.getByRole('textbox', { name: '登录账号' }), 'wrong')
    await user.type(screen.getByLabelText('密码'), 'wrong password')
    await user.click(screen.getByRole('button', { name: '登录' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('账号或密码不正确')
  })
})
