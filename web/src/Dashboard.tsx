import type { Account, ScanRun } from './api'
import { formatUtc } from './format'
import { PageHeader } from './PageHeader'

interface DashboardData {
  accounts: Account[]
  scans: ScanRun[]
  actionItems: { id: string }[]
}

const providerNames: Record<string, string> = {
  ADSPOWER: 'AdsPower',
  DIRECT_CHROME: 'Direct Chrome',
}

const sessionNames: Record<string, string> = {
  READY: '已连接',
  REAUTH_REQUIRED: '需要重新验证',
  DISABLED: '已停用',
}

export function Dashboard({ data }: { data: DashboardData }) {
  const account = data.accounts[0]
  if (!account) {
    return (
      <section className="status-card">
        <p className="eyebrow">X Follow List</p>
        <h1>总览</h1>
        <p>暂无监控账号，请先前往账号管理完成绑定。</p>
      </section>
    )
  }
  const latestScan = data.scans[0]
  const lastSuccess = latestScan?.last_successful_scan_at ?? account.last_successful_scan_at

  return (
    <div className="dashboard">
      <PageHeader title="总览">
        关系变化一目了然，异常扫描不会覆盖最后一次可靠数据。
      </PageHeader>
      <section className="summary-grid" aria-label="账号摘要">
        <article className="card account-card">
          <p className="card-label">监控账号</p>
          <h2>{account.display_name ?? `@${account.username ?? account.x_user_id}`}</h2>
          <p className="status-line">
            {providerNames[account.provider_code] ?? account.provider_code} ·{' '}
            {sessionNames[account.session_status] ?? account.session_status}
          </p>
        </article>
        <article className="card">
          <p className="card-label">可靠数据截至</p>
          <p className="metric-time">{formatUtc(lastSuccess, '尚无成功扫描')}</p>
          {latestScan?.status === 'FAILED' && (
            <p className="data-note">数据仍来自上次成功扫描</p>
          )}
        </article>
        <a
          className="card action-card"
          href="/action-items"
          aria-label={`互关后取消关注 ${data.actionItems.length} 项`}
        >
          <span className="card-label">重点待处理</span>
          <strong>互关后取消关注 {data.actionItems.length} 项</strong>
          <span>查看并确认事件 →</span>
        </a>
      </section>
      {latestScan?.status === 'FAILED' && (
        <section className="card failure-card" aria-labelledby="latest-failure">
          <p className="card-label">最近异常</p>
          <h2 id="latest-failure">扫描未完成</h2>
          <p>{latestScan.error?.summary ?? '扫描失败，请查看任务详情。'}</p>
        </section>
      )}
    </div>
  )
}
