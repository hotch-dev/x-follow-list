import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { AccountsPage } from './AccountsPage'

const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function renderPage(pollIntervalMs?: number) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <AccountsPage csrfToken="csrf-token" pollIntervalMs={pollIntervalMs} />
    </QueryClientProvider>,
  )
}

describe('A-12 AdsPower account binding journey', () => {
  it('selects an existing profile and starts a manual-login binding task', async () => {
    let bindingRequest: unknown
    server.use(
      http.get('/api/v1/browser-provider-configs', () =>
        HttpResponse.json({
          items: [
            {
              id: 'config-1',
              provider_code: 'ADSPOWER',
              config_version: 1,
              display_name: '本机 AdsPower',
              has_secret: true,
              created_at: '2026-09-14T10:00:00Z',
              updated_at: '2026-09-14T10:00:00Z',
            },
          ],
        }),
      ),
      http.get('/api/v1/browser-provider-configs/config-1/profiles', () =>
        HttpResponse.json({
          items: [
            { profile_ref: 'profile-3', display_name: '环境 3', is_running: false },
          ],
        }),
      ),
      http.post('/api/v1/x-account-bind-sessions', async ({ request }) => {
        bindingRequest = await request.json()
        return HttpResponse.json(
          {
            id: 'binding-1',
            owner_user_id: 'owner',
            provider_config_id: 'config-1',
            provider_code: 'ADSPOWER',
            provider_config_version: 1,
            profile_ref: 'profile-3',
            target_account_id: null,
            status: 'WAITING_FOR_LOGIN',
            detected_identity: null,
            account_id: null,
            error_code: null,
            expires_at: '2026-09-14T10:15:00Z',
          },
          { status: 202 },
        )
      }),
      http.get('/api/v1/x-account-bind-sessions/binding-1', () =>
        HttpResponse.json({
          id: 'binding-1',
          provider_code: 'ADSPOWER',
          profile_ref: 'profile-3',
          status: 'WAITING_FOR_LOGIN',
          detected_identity: null,
          error_code: null,
        }),
      ),
    )
    const user = userEvent.setup()

    renderPage()
    await screen.findByRole('option', { name: '本机 AdsPower · ADSPOWER' })
    await user.selectOptions(
      screen.getByRole('combobox', { name: '浏览器配置' }),
      'config-1',
    )
    await user.selectOptions(
      await screen.findByRole('combobox', { name: '已有浏览器环境' }),
      'profile-3',
    )
    await user.click(screen.getByRole('button', { name: '开始绑定' }))

    expect(await screen.findByRole('status')).toHaveTextContent(
      '等待在环境 3 中手工登录 X',
    )
    expect(bindingRequest).toEqual({
      provider_config_id: 'config-1',
      profile_ref: 'profile-3',
      x_account_id: null,
    })
    expect(screen.queryByRole('button', { name: /创建|删除|修改/ })).not.toBeInTheDocument()
  })

  it('polls detected identity, asks for confirmation, and stops when complete', async () => {
    let detailRequests = 0
    let confirmedVersion = 0
    server.use(
      http.get('/api/v1/browser-provider-configs', () =>
        HttpResponse.json({
          items: [
            {
              id: 'config-1',
              provider_code: 'ADSPOWER',
              config_version: 1,
              display_name: '本机 AdsPower',
              has_secret: true,
              created_at: '2026-09-14T10:00:00Z',
              updated_at: '2026-09-14T10:00:00Z',
            },
          ],
        }),
      ),
      http.get('/api/v1/browser-provider-configs/config-1/profiles', () =>
        HttpResponse.json({
          items: [{ profile_ref: 'profile-3', display_name: '环境 3', is_running: true }],
        }),
      ),
      http.post('/api/v1/x-account-bind-sessions', () =>
        HttpResponse.json(
          {
            id: 'binding-1',
            provider_code: 'ADSPOWER',
            profile_ref: 'profile-3',
            status: 'WAITING_FOR_LOGIN',
            detected_identity: null,
            error_code: null,
          },
          { status: 202 },
        ),
      ),
      http.get('/api/v1/x-account-bind-sessions/binding-1', () => {
        detailRequests += 1
        return HttpResponse.json({
          id: 'binding-1',
          provider_code: 'ADSPOWER',
          profile_ref: 'profile-3',
          status: 'AWAITING_CONFIRMATION',
          detected_identity: {
            x_user_id: '42',
            username: 'alice',
            display_name: 'Alice',
          },
          error_code: null,
        })
      }),
      http.post('/api/v1/x-account-bind-sessions/binding-1/confirm', async ({ request }) => {
        confirmedVersion += request.headers.get('X-CSRF-Token') === 'csrf-token' ? 1 : 0
        return HttpResponse.json({
          id: 'binding-1',
          provider_code: 'ADSPOWER',
          profile_ref: 'profile-3',
          status: 'COMPLETED',
          detected_identity: {
            x_user_id: '42',
            username: 'alice',
            display_name: 'Alice',
          },
          error_code: null,
        })
      }),
    )
    const user = userEvent.setup()
    renderPage(10)

    await screen.findByRole('option', { name: '本机 AdsPower · ADSPOWER' })
    await user.selectOptions(screen.getByRole('combobox', { name: '浏览器配置' }), 'config-1')
    await screen.findByRole('option', { name: '环境 3 · 已运行' })
    await user.selectOptions(
      screen.getByRole('combobox', { name: '已有浏览器环境' }),
      'profile-3',
    )
    await user.click(screen.getByRole('button', { name: '开始绑定' }))

    expect(await screen.findByRole('heading', { name: 'Alice' })).toBeVisible()
    expect(screen.getByText('@alice')).toBeVisible()
    await user.click(screen.getByRole('button', { name: '确认绑定此账号' }))

    expect(await screen.findByRole('status')).toHaveTextContent('绑定完成')
    expect(confirmedVersion).toBe(1)
    const requestsAtComplete = detailRequests
    expect(requestsAtComplete).toBeGreaterThanOrEqual(1)
    await waitFor(() => expect(detailRequests).toBe(requestsAtComplete), { timeout: 80 })
  })

  it('requires confirmation before revalidating an account with its existing profile', async () => {
    let revalidationRequest: unknown
    server.use(
      http.get('/api/v1/browser-provider-configs', () =>
        HttpResponse.json({ items: [] }),
      ),
      http.get('/api/v1/x-accounts', () =>
        HttpResponse.json({
          items: [
            {
              id: 'account-1',
              x_user_id: '42',
              username: 'alice',
              display_name: 'Alice',
              session_status: 'REAUTH_REQUIRED',
              provider_code: 'ADSPOWER',
              profile_ref: 'profile-3',
              version: 3,
              last_successful_scan_at: null,
            },
          ],
        }),
      ),
      http.post('/api/v1/x-account-bind-sessions', async ({ request }) => {
        revalidationRequest = await request.json()
        return HttpResponse.json(
          {
            id: 'binding-revalidate',
            provider_code: 'ADSPOWER',
            profile_ref: 'profile-3',
            target_account_id: 'account-1',
            status: 'WAITING_FOR_LOGIN',
            detected_identity: null,
            error_code: null,
          },
          { status: 202 },
        )
      }),
      http.get('/api/v1/x-account-bind-sessions/binding-revalidate', () =>
        HttpResponse.json({
          id: 'binding-revalidate',
          provider_code: 'ADSPOWER',
          profile_ref: 'profile-3',
          target_account_id: 'account-1',
          status: 'WAITING_FOR_LOGIN',
          detected_identity: null,
          error_code: null,
        }),
      ),
    )
    const user = userEvent.setup()

    renderPage()
    await user.click(await screen.findByRole('button', { name: '重新验证 Alice' }))

    expect(screen.getByText('重新验证会打开该账号现有的浏览器环境。')).toBeVisible()
    expect(revalidationRequest).toBeUndefined()
    await user.click(screen.getByRole('button', { name: '确认重新验证' }))

    expect(await screen.findByRole('status')).toHaveTextContent(
      '等待在profile-3 中手工登录 X',
    )
    expect(revalidationRequest).toEqual({ x_account_id: 'account-1' })
  })

  it('requires confirmation and the current version before unbinding', async () => {
    let unbindRequest: unknown
    let csrfHeader: string | null = null
    server.use(
      http.get('/api/v1/browser-provider-configs', () =>
        HttpResponse.json({ items: [] }),
      ),
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
              version: 4,
              last_successful_scan_at: null,
            },
          ],
        }),
      ),
      http.delete('/api/v1/x-accounts/account-1', async ({ request }) => {
        unbindRequest = await request.json()
        csrfHeader = request.headers.get('X-CSRF-Token')
        return new HttpResponse(null, { status: 204 })
      }),
    )
    const user = userEvent.setup()

    renderPage()
    await user.click(await screen.findByRole('button', { name: '解绑 Alice' }))

    expect(screen.getByText('解绑会停止新扫描并删除本地会话引用。')).toBeVisible()
    expect(unbindRequest).toBeUndefined()
    await user.click(screen.getByRole('button', { name: '确认解绑' }))

    await waitFor(() => expect(unbindRequest).toEqual({ version: 4, delete_history: false }))
    expect(csrfHeader).toBe('csrf-token')
    expect(screen.queryByRole('heading', { name: 'Alice' })).not.toBeInTheDocument()
  })
})
