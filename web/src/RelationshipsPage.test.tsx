import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { ActionItemsPage } from './ActionItemsPage'
import { RelationshipsPage } from './RelationshipsPage'

const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function wrapper(children: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(<QueryClientProvider client={client}>{children}</QueryClientProvider>)
}

describe('A-12 relationship result journeys', () => {
  it('searches and filters relationship facts through the account-scoped API', async () => {
    let requestedQuery = ''
    server.use(
      http.get('/api/v1/relationships', ({ request }) => {
        requestedQuery = new URL(request.url).search
        return HttpResponse.json({
          items: [
            {
              x_user_id: '102',
              username: 'waiting',
              display_name: 'Waiting User',
              state: 'NOT_FOLLOWING_BACK',
              non_followback_streak: 3,
              snapshot_id: 'snapshot-1',
            },
          ],
          next_cursor: null,
        })
      }),
    )
    const user = userEvent.setup()
    wrapper(<RelationshipsPage accountId="account-1" />)

    await user.selectOptions(
      screen.getByRole('combobox', { name: '关系状态' }),
      'NOT_FOLLOWING_BACK',
    )
    await user.type(screen.getByRole('searchbox', { name: '搜索账号' }), 'wait')
    await user.click(screen.getByRole('button', { name: '应用筛选' }))

    expect(await screen.findByRole('heading', { name: 'Waiting User' })).toBeVisible()
    expect(screen.getByText('连续 3 次未回关')).toBeVisible()
    expect(requestedQuery).toContain('x_account_id=account-1')
    expect(requestedQuery).toContain('state=NOT_FOLLOWING_BACK')
    expect(requestedQuery).toContain('search=wait')
  })

  it('acknowledges an unfollowed-after-mutual action item with its version', async () => {
    let acknowledgeBody: unknown
    server.use(
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
              version: 4,
              created_at: '2026-09-14T11:00:03Z',
              acknowledged_at: null,
            },
          ],
          next_cursor: null,
        }),
      ),
      http.post('/api/v1/relationship-events/event-1/acknowledge', async ({ request }) => {
        acknowledgeBody = await request.json()
        return HttpResponse.json({
          id: 'event-1',
          x_account_id: 'account-1',
          scan_run_id: 'run-1',
          subject_x_user_id: '99',
          username: 'former_mutual',
          display_name: 'Former Mutual',
          category: 'ACTION_ITEM',
          event_type: 'UNFOLLOWED_ME_AFTER_MUTUAL',
          status: 'ACKNOWLEDGED',
          version: 5,
          created_at: '2026-09-14T11:00:03Z',
          acknowledged_at: '2026-09-15T01:00:00Z',
        })
      }),
    )
    const user = userEvent.setup()
    wrapper(<ActionItemsPage accountId="account-1" csrfToken="csrf-token" />)

    expect(await screen.findByRole('heading', { name: 'Former Mutual' })).toBeVisible()
    await user.click(screen.getByRole('button', { name: '确认已查看' }))

    expect(await screen.findByText('已确认')).toBeVisible()
    expect(acknowledgeBody).toEqual({ version: 4 })
  })

  it('keeps relationship load and stale event errors actionable', async () => {
    server.use(
      http.get('/api/v1/relationships', () =>
        HttpResponse.json({ code: 'TEMPORARY_FAILURE' }, { status: 503 }),
      ),
      http.get('/api/v1/relationship-events', () =>
        HttpResponse.json({
          items: [
            {
              id: 'event-1',
              x_account_id: 'account-1',
              scan_run_id: 'run-1',
              subject_x_user_id: '99',
              username: null,
              display_name: null,
              category: 'ACTION_ITEM',
              event_type: 'UNFOLLOWED_ME_AFTER_MUTUAL',
              status: 'NEW',
              version: 4,
              created_at: '2026-09-14T11:00:03Z',
              acknowledged_at: null,
            },
          ],
          next_cursor: null,
        }),
      ),
      http.post('/api/v1/relationship-events/event-1/acknowledge', () =>
        HttpResponse.json({ code: 'RESOURCE_VERSION_CONFLICT' }, { status: 409 }),
      ),
    )
    const user = userEvent.setup()
    wrapper(
      <>
        <RelationshipsPage accountId="account-1" />
        <ActionItemsPage accountId="account-1" csrfToken="csrf-token" />
      </>,
    )

    expect(await screen.findByText('无法加载关系结果，请稍后重试。')).toBeVisible()
    expect(await screen.findByRole('heading', { name: '@99' })).toBeVisible()
    await user.click(screen.getByRole('button', { name: '确认已查看' }))
    expect(await screen.findByText('事件已变化，请刷新后重新确认。')).toBeVisible()
  })
})
