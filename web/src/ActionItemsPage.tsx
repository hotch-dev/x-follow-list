import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { acknowledgeEvent, listActionItems } from './api'
import { PageHeader } from './PageHeader'

export function ActionItemsPage({
  accountId,
  csrfToken,
}: {
  accountId: string
  csrfToken: string
}) {
  const [acknowledgedId, setAcknowledgedId] = useState<string | null>(null)
  const events = useQuery({
    queryKey: ['action-items', accountId],
    queryFn: () => listActionItems(accountId),
  })
  const acknowledge = useMutation({
    mutationFn: ({ id, version }: { id: string; version: number }) =>
      acknowledgeEvent(id, version, csrfToken),
    onSuccess: (event) => setAcknowledgedId(event.id),
  })

  return (
    <section className="dashboard" aria-labelledby="actions-title">
      <PageHeader title="重点待处理" titleId="actions-title">
        确认事件只记录你已查看，不会替你关注、取关或修改 X。
      </PageHeader>
      {events.data?.map((event) => (
        <article className="card action-item" key={event.id}>
          <p className="card-label">之前互关，现在仅我关注</p>
          <h2>{event.display_name ?? `@${event.username ?? event.subject_x_user_id}`}</h2>
          <p>@{event.username ?? event.subject_x_user_id}</p>
          {acknowledgedId === event.id ? (
            <p className="status-line">已确认</p>
          ) : (
            <button
              type="button"
              disabled={acknowledge.isPending}
              onClick={() => acknowledge.mutate({ id: event.id, version: event.version })}
            >
              确认已查看
            </button>
          )}
        </article>
      ))}
      {acknowledge.isError && <p role="alert">事件已变化，请刷新后重新确认。</p>}
    </section>
  )
}
