import { useState } from 'react'

import type { Account } from './api'

type AccountAction = {
  kind: 'revalidate' | 'unbind'
  account: Account
}

export function BoundAccounts({
  accounts,
  isError,
  onRevalidate,
  onUnbind,
}: {
  accounts: Account[] | undefined
  isError: boolean
  onRevalidate: (account: Account) => void
  onUnbind: (account: Account) => void
}) {
  const [pendingAction, setPendingAction] = useState<AccountAction | null>(null)

  return (
    <section aria-labelledby="bound-accounts-title">
      <h2 id="bound-accounts-title">已绑定账号</h2>
      {isError && <p role="alert">无法加载已绑定账号。</p>}
      {accounts?.length === 0 && <p>暂无已绑定账号。</p>}
      <div className="card-grid">
        {accounts?.map((account) => {
          const name = account.display_name ?? `@${account.username ?? account.x_user_id}`
          return (
            <article className="card" key={account.id}>
              <h2>{name}</h2>
              <p>@{account.username ?? account.x_user_id}</p>
              <p>{account.provider_code} · {account.profile_ref}</p>
              {account.session_status === 'REAUTH_REQUIRED' && (
                <button
                  type="button"
                  onClick={() => setPendingAction({ kind: 'revalidate', account })}
                >
                  重新验证 {name}
                </button>
              )}
              <button
                type="button"
                onClick={() => setPendingAction({ kind: 'unbind', account })}
              >
                解绑 {name}
              </button>
            </article>
          )
        })}
      </div>
      {pendingAction && (
        <section className="card" role="alertdialog" aria-label="确认账号操作">
          <p>
            {pendingAction.kind === 'revalidate'
              ? '重新验证会打开该账号现有的浏览器环境。'
              : '解绑会停止新扫描并删除本地会话引用。'}
          </p>
          <button
            type="button"
            onClick={() => {
              if (pendingAction.kind === 'revalidate') onRevalidate(pendingAction.account)
              else onUnbind(pendingAction.account)
              setPendingAction(null)
            }}
          >
            {pendingAction.kind === 'revalidate' ? '确认重新验证' : '确认解绑'}
          </button>
          <button type="button" onClick={() => setPendingAction(null)}>取消</button>
        </section>
      )}
    </section>
  )
}
