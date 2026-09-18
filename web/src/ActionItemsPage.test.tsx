import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'

import { ActionItemsPage } from './ActionItemsPage'

const server = setupServer(
  http.get('/api/v1/relationship-events', ({ request }) => {
    expect(new URL(request.url).searchParams.get('event_type')).toBeNull()
    return HttpResponse.json({
      items: [
        { id: 'former', event_type: 'UNFOLLOWED_ME_AFTER_MUTUAL', display_name: 'Former mutual', username: 'former', subject_x_user_id: '1', status: 'NEW', version: 1 },
        { id: 'blocked', event_type: 'FOLLOWING_BLOCKLISTED_ACCOUNT', display_name: 'Risk account', username: 'risk', subject_x_user_id: '2', rule_reason: 'Supplier risk', status: 'NEW', version: 1 },
      ],
      next_cursor: null,
    })
  }),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterAll(() => server.close())

describe('priority action items', () => {
  it('shows mutual-unfollow and business-blocklist conflicts separately with reason', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <ActionItemsPage accountId="account-1" csrfToken="csrf-token" />
      </QueryClientProvider>,
    )

    expect(await screen.findByText('Former mutual')).toBeVisible()
    expect(screen.getByText('Risk account')).toBeVisible()
    expect(screen.getByText('Supplier risk')).toBeVisible()
    expect(screen.getByText('当前仍关注黑名单账号')).toBeVisible()
  })
})
