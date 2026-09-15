import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { listRelationships } from './api'
import { PageHeader } from './PageHeader'

const stateLabels: Record<string, string> = {
  MUTUAL: '互相关注',
  NOT_FOLLOWING_BACK: '未回关',
  FOLLOWS_ME_ONLY: '只关注我',
  ABSENT: '已不在列表',
}

export function RelationshipsPage({ accountId }: { accountId: string }) {
  const [state, setState] = useState('')
  const [search, setSearch] = useState('')
  const [filters, setFilters] = useState({ state: '', search: '' })
  const relationships = useQuery({
    queryKey: ['relationships', accountId, filters],
    queryFn: () => listRelationships(accountId, filters),
  })

  return (
    <section className="dashboard" aria-labelledby="relationships-title">
      <PageHeader title="关系结果" titleId="relationships-title">
        按稳定 X ID 展示最近一次成功扫描的关系事实。
      </PageHeader>
      <form
        className="card filter-bar"
        onSubmit={(event) => {
          event.preventDefault()
          setFilters({ state, search: search.trim() })
        }}
      >
        <label htmlFor="relationship-state">关系状态</label>
        <select
          id="relationship-state"
          value={state}
          onChange={(event) => setState(event.target.value)}
        >
          <option value="">全部</option>
          <option value="MUTUAL">互相关注</option>
          <option value="NOT_FOLLOWING_BACK">未回关</option>
          <option value="FOLLOWS_ME_ONLY">只关注我</option>
        </select>
        <label htmlFor="relationship-search">搜索账号</label>
        <input
          id="relationship-search"
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <button type="submit">应用筛选</button>
      </form>
      {relationships.isError && <p role="alert">无法加载关系结果，请稍后重试。</p>}
      <div className="result-list" aria-live="polite">
        {relationships.data?.map((relationship) => (
          <article className="card" key={relationship.x_user_id}>
            <p className="card-label">{stateLabels[relationship.state] ?? relationship.state}</p>
            <h2>{relationship.display_name ?? `@${relationship.username ?? relationship.x_user_id}`}</h2>
            <p>@{relationship.username ?? relationship.x_user_id}</p>
            {relationship.state === 'NOT_FOLLOWING_BACK' && (
              <p>连续 {relationship.non_followback_streak} 次未回关</p>
            )}
          </article>
        ))}
      </div>
    </section>
  )
}
