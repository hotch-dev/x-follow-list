import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { nextPollDelay } from './polling'
import { ScansPage } from './ScansPage'

const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function scan(status: 'QUEUED' | 'RUNNING' | 'FAILED' | 'SUCCESS') {
  return {
    id: 'run-1',
    x_account_id: 'account-1',
    status,
    progress_stage: status === 'RUNNING' ? 'COLLECTING_FOLLOWERS' : 'CHECKING_SESSION',
    error:
      status === 'FAILED'
        ? { code: 'AUTH_REQUIRED', summary: '登录状态失效，请重新验证账号' }
        : null,
    follower_count: null,
    following_count: null,
    created_at: '2026-09-14T11:00:00Z',
    started_at: status === 'QUEUED' ? null : '2026-09-14T11:00:01Z',
    finished_at: status === 'FAILED' ? '2026-09-14T11:00:03Z' : null,
    last_successful_scan_at: '2026-09-14T10:30:00Z',
  }
}

describe('A-12 manual scan journey', () => {
  it('backs off active scan polling up to a bounded delay', () => {
    expect(nextPollDelay(2000, 0)).toBe(2000)
    expect(nextPollDelay(2000, 1)).toBe(4000)
    expect(nextPollDelay(2000, 2)).toBe(8000)
    expect(nextPollDelay(2000, 8)).toBe(15000)
  })

  it('polls with idempotency until terminal failure and preserves last good data', async () => {
    let detailRequests = 0
    let idempotencyKey: string | null = null
    server.use(
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
        HttpResponse.json({ items: [], next_cursor: null }),
      ),
      http.post('/api/v1/x-accounts/account-1/scan-runs', ({ request }) => {
        idempotencyKey = request.headers.get('Idempotency-Key')
        return HttpResponse.json(scan('QUEUED'), { status: 202 })
      }),
      http.get('/api/v1/scan-runs/run-1', () => {
        detailRequests += 1
        return HttpResponse.json(scan(detailRequests === 1 ? 'RUNNING' : 'FAILED'))
      }),
    )
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const user = userEvent.setup()
    render(
      <QueryClientProvider client={queryClient}>
        <ScansPage csrfToken="csrf-token" pollIntervalMs={10} />
      </QueryClientProvider>,
    )

    await user.click(await screen.findByRole('button', { name: '发起扫描' }))

    expect(await screen.findByText('登录状态失效，请重新验证账号')).toBeVisible()
    expect(screen.getByText('数据仍来自 2026-09-14 10:30 UTC')).toBeVisible()
    expect(idempotencyKey).toMatch(/^[0-9a-f-]{36}$/)
    const requestsAtFailure = detailRequests
    await waitFor(() => expect(detailRequests).toBe(requestsAtFailure), { timeout: 80 })
    expect(detailRequests).toBe(2)
  })

  it('generates and downloads the XLSX artifact for a successful scan', async () => {
    let generated = false
    server.use(
      http.get('/api/v1/x-accounts', () =>
        HttpResponse.json({
          items: [{
            id: 'account-1', x_user_id: '42', username: 'alice', display_name: 'Alice',
            session_status: 'READY', provider_code: 'DIRECT_CHROME', profile_ref: 'profile',
            version: 1, last_successful_scan_at: '2026-09-15T04:00:00Z',
          }],
        }),
      ),
      http.get('/api/v1/scan-runs', () =>
        HttpResponse.json({ items: [scan('SUCCESS')], next_cursor: null }),
      ),
      http.post('/api/v1/scan-runs/run-1/artifacts/xlsx', () => {
        generated = true
        return HttpResponse.json({
          id: 'artifact-1', status: 'READY', sha256: 'abc', byte_size: 123,
          expires_at: '2026-10-15T04:00:00Z', deleted_at: null,
          download_url: '/api/v1/artifacts/artifact-1/download',
        })
      }),
    )
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const user = userEvent.setup()
    render(
      <QueryClientProvider client={queryClient}>
        <ScansPage csrfToken="csrf-token" />
      </QueryClientProvider>,
    )

    await user.click(await screen.findByRole('button', { name: '生成 XLSX' }))

    expect(generated).toBe(true)
    expect(await screen.findByRole('link', { name: '下载 XLSX' })).toHaveAttribute(
      'href', '/api/v1/artifacts/artifact-1/download',
    )
  })

  it('keeps XLSX generation failure visible without offering a stale download', async () => {
    server.use(
      http.get('/api/v1/x-accounts', () =>
        HttpResponse.json({
          items: [{
            id: 'account-1', x_user_id: '42', username: 'alice', display_name: 'Alice',
            session_status: 'READY', provider_code: 'DIRECT_CHROME', profile_ref: 'profile',
            version: 1, last_successful_scan_at: '2026-09-15T04:00:00Z',
          }],
        }),
      ),
      http.get('/api/v1/scan-runs', () =>
        HttpResponse.json({ items: [scan('SUCCESS')], next_cursor: null }),
      ),
      http.post('/api/v1/scan-runs/run-1/artifacts/xlsx', () =>
        HttpResponse.json({ code: 'ARTIFACT_GENERATION_FAILED' }, { status: 500 }),
      ),
    )
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const user = userEvent.setup()
    render(
      <QueryClientProvider client={queryClient}>
        <ScansPage csrfToken="csrf-token" />
      </QueryClientProvider>,
    )

    await user.click(await screen.findByRole('button', { name: '生成 XLSX' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('XLSX 生成失败，请重试。')
    expect(screen.queryByRole('link', { name: '下载 XLSX' })).not.toBeInTheDocument()
  })
})
