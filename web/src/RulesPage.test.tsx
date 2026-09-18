import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { RulesPage } from './RulesPage'

const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <RulesPage accountId="account-1" csrfToken="csrf-token" />
    </QueryClientProvider>,
  )
}

describe('business blocklist management', () => {
  it('adds a reasoned rule and explains when a first scan is required', async () => {
    let created = false
    server.use(
      http.get('/api/v1/account-rules', () =>
        HttpResponse.json({
          items: created
            ? [{ id: 'rule-1', x_account_id: 'account-1', subject_x_user_id: '42', rule_type: 'BUSINESS_BLOCKLIST', reason: 'Supplier risk', version: 1 }]
            : [],
          next_cursor: null,
        }),
      ),
      http.post('/api/v1/account-rules', async ({ request }) => {
        expect(request.headers.get('X-CSRF-Token')).toBe('csrf-token')
        expect(request.headers.get('Idempotency-Key')).toBeTruthy()
        expect(await request.json()).toEqual({
          x_account_id: 'account-1',
          subject_x_user_id: '42',
          rule_type: 'BUSINESS_BLOCKLIST',
          reason: 'Supplier risk',
        })
        created = true
        return HttpResponse.json({
          id: 'rule-1', x_account_id: 'account-1', subject_x_user_id: '42',
          rule_type: 'BUSINESS_BLOCKLIST', reason: 'Supplier risk', version: 1,
          scan_required: true,
        }, { status: 201 })
      }),
    )
    const user = userEvent.setup()
    renderPage()

    await user.type(screen.getByRole('textbox', { name: 'X 用户 ID' }), '42')
    await user.type(screen.getByRole('textbox', { name: '原因' }), 'Supplier risk')
    await user.click(screen.getByRole('button', { name: '添加规则' }))

    expect(await screen.findByText('请先完成一次成功扫描，再判断是否仍在关注名单中。')).toBeVisible()
    expect(await screen.findByText('Supplier risk')).toBeVisible()
  })

  it('shows a conflict instead of replacing the opposite rule', async () => {
    server.use(
      http.get('/api/v1/account-rules', () => HttpResponse.json({ items: [], next_cursor: null })),
      http.post('/api/v1/account-rules', () =>
        HttpResponse.json({ code: 'RULE_CONFLICT' }, { status: 409 }),
      ),
    )
    const user = userEvent.setup()
    renderPage()
    await user.type(screen.getByRole('textbox', { name: 'X 用户 ID' }), '42')
    await user.type(screen.getByRole('textbox', { name: '原因' }), 'Supplier risk')
    await user.click(screen.getByRole('button', { name: '添加规则' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('先移除该用户已有的规则')
  })

  it('removes a rule with its current version', async () => {
    let removed = false
    server.use(
      http.get('/api/v1/account-rules', () =>
        HttpResponse.json({
          items: removed ? [] : [{ id: 'rule-1', x_account_id: 'account-1', subject_x_user_id: '42', rule_type: 'BUSINESS_BLOCKLIST', reason: 'Supplier risk', version: 3 }],
          next_cursor: null,
        }),
      ),
      http.delete('/api/v1/account-rules/rule-1', async ({ request }) => {
        expect(await request.json()).toEqual({ version: 3 })
        removed = true
        return new HttpResponse(null, { status: 204 })
      }),
    )
    const user = userEvent.setup()
    renderPage()
    expect(await screen.findByText('Supplier risk')).toBeVisible()
    await user.click(screen.getByRole('button', { name: '移除规则' }))
    await waitFor(() => expect(screen.queryByText('Supplier risk')).not.toBeInTheDocument())
  })
})
