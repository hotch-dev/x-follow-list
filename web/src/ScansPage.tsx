import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { createScan, getScan, listAccounts, listScans, type ScanRun } from './api'
import { formatUtc } from './format'
import { PageHeader } from './PageHeader'
import { nextPollDelay } from './polling'

const terminalStatuses = new Set(['SUCCESS', 'FAILED', 'CANCELLED'])

export function ScansPage({
  csrfToken,
  pollIntervalMs = 2000,
}: {
  csrfToken: string
  pollIntervalMs?: number
}) {
  const [activeRunId, setActiveRunId] = useState<string | null>(null)
  const accounts = useQuery({ queryKey: ['accounts'], queryFn: listAccounts })
  const history = useQuery({ queryKey: ['scans'], queryFn: listScans })
  const account = accounts.data?.[0]
  const create = useMutation({
    mutationFn: () => createScan(account!.id, csrfToken),
    onSuccess: (run) => setActiveRunId(run.id),
  })
  const activeRun = useQuery({
    queryKey: ['scan', activeRunId],
    queryFn: () => getScan(activeRunId!),
    enabled: activeRunId !== null,
    refetchInterval: (query) => {
      const run = query.state.data as ScanRun | undefined
      return run && terminalStatuses.has(run.status)
        ? false
        : nextPollDelay(pollIntervalMs, query.state.dataUpdateCount)
    },
  })
  const run = activeRun.data ?? create.data ?? history.data?.[0]

  return (
    <section className="dashboard" aria-labelledby="scans-title">
      <PageHeader title="扫描任务" titleId="scans-title">
        手工扫描异步执行；任务失败时继续保留并标明上一份可靠数据。
      </PageHeader>
      <div className="card toolbar">
        <span>{account ? `当前账号：${account.display_name ?? account.username}` : '正在加载账号…'}</span>
        <button
          type="button"
          disabled={!account || create.isPending || Boolean(run && !terminalStatuses.has(run.status))}
          onClick={() => create.mutate()}
        >
          {create.isPending ? '正在排队…' : '发起扫描'}
        </button>
      </div>
      {create.isError && <p role="alert">无法创建扫描，请刷新后重试。</p>}
      {run && (
        <article className="card scan-card" aria-live="polite">
          <p className="card-label">任务状态</p>
          <h2>{run.status}</h2>
          {run.progress_stage && <p>当前阶段：{run.progress_stage}</p>}
          {run.status === 'FAILED' && (
            <div className="failure-detail">
              <p>{run.error?.summary ?? '扫描失败，请查看详情。'}</p>
              <p>数据仍来自 {formatUtc(run.last_successful_scan_at)}</p>
            </div>
          )}
        </article>
      )}
    </section>
  )
}
