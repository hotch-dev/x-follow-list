import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import {
  confirmBinding,
  createBinding,
  createRevalidation,
  getBinding,
  listAccounts,
  listProfiles,
  listProviderConfigs,
  unbindAccount,
  type Account,
  type BindingSession,
} from './api'
import { BindingStatus } from './BindingStatus'
import { BoundAccounts } from './BoundAccounts'
import { PageHeader } from './PageHeader'

const terminalStatuses = new Set(['COMPLETED', 'FAILED', 'CANCELLED', 'EXPIRED'])

export function AccountsPage({
  csrfToken,
  pollIntervalMs = 2000,
}: {
  csrfToken: string
  pollIntervalMs?: number
}) {
  const [configId, setConfigId] = useState('')
  const [profileRef, setProfileRef] = useState('')
  const [finalSession, setFinalSession] = useState<BindingSession | null>(null)
  const queryClient = useQueryClient()
  const accounts = useQuery({ queryKey: ['accounts'], queryFn: listAccounts })
  const configs = useQuery({
    queryKey: ['provider-configs'],
    queryFn: listProviderConfigs,
  })
  const profiles = useQuery({
    queryKey: ['provider-profiles', configId],
    queryFn: () => listProfiles(configId),
    enabled: configId !== '',
  })
  const binding = useMutation({
    mutationFn: () => createBinding(configId, profileRef, csrfToken),
  })
  const revalidation = useMutation({
    mutationFn: (account: Account) => createRevalidation(account.id, csrfToken),
  })
  const unbind = useMutation({
    mutationFn: (account: Account) => unbindAccount(account, csrfToken),
    onSuccess: (_data, account) => {
      queryClient.setQueryData<Account[]>(['accounts'], (current = []) =>
        current.filter((item) => item.id !== account.id),
      )
    },
  })
  const bindingId = revalidation.data?.id ?? binding.data?.id
  const detail = useQuery({
    queryKey: ['binding', bindingId],
    queryFn: () => getBinding(bindingId!),
    enabled: Boolean(bindingId) && finalSession === null,
    refetchInterval: (query) => {
      const session = query.state.data as BindingSession | undefined
      return session && terminalStatuses.has(session.status) ? false : pollIntervalMs
    },
  })
  const confirm = useMutation({
    mutationFn: (session: BindingSession) => confirmBinding(session.id, csrfToken),
    onSuccess: setFinalSession,
  })
  const currentSession = finalSession ?? detail.data ?? revalidation.data ?? binding.data
  const selectedProfile = profiles.data?.find((item) => item.profile_ref === profileRef)

  return (
    <section className="dashboard" aria-labelledby="accounts-title">
      <PageHeader title="X 账号管理" titleId="accounts-title">
        选择 Provider 已有的独立浏览器环境，再由你手工完成 X 登录。
      </PageHeader>
      <form
        className="card form-card"
        onSubmit={(event) => {
          event.preventDefault()
          binding.mutate()
        }}
      >
        <label htmlFor="provider-config">浏览器配置</label>
        <select
          id="provider-config"
          value={configId}
          onChange={(event) => {
            setConfigId(event.target.value)
            setProfileRef('')
          }}
        >
          <option value="">请选择配置</option>
          {configs.data?.map((config) => (
            <option key={config.id} value={config.id}>
              {config.display_name} · {config.provider_code}
            </option>
          ))}
        </select>
        <label htmlFor="browser-profile">已有浏览器环境</label>
        <select
          id="browser-profile"
          value={profileRef}
          disabled={!configId || profiles.isPending}
          onChange={(event) => setProfileRef(event.target.value)}
        >
          <option value="">请选择环境</option>
          {profiles.data?.map((profile) => (
            <option key={profile.profile_ref} value={profile.profile_ref}>
              {profile.display_name}{profile.is_running ? ' · 已运行' : ''}
            </option>
          ))}
        </select>
        <button type="submit" disabled={!configId || !profileRef || binding.isPending}>
          {binding.isPending ? '正在创建绑定…' : '开始绑定'}
        </button>
        {binding.isError && <p role="alert">无法启动绑定，请检查 Provider 配置后重试。</p>}
        <BindingStatus
          session={currentSession}
          profileName={selectedProfile?.display_name ?? currentSession?.profile_ref ?? '所选环境'}
          confirming={confirm.isPending}
          onConfirm={(session) => confirm.mutate(session)}
        />
      </form>
      <BoundAccounts
        accounts={accounts.data}
        isError={accounts.isError}
        onRevalidate={(account) => revalidation.mutate(account)}
        onUnbind={(account) => unbind.mutate(account)}
      />
      {(revalidation.isError || unbind.isError) && (
        <p role="alert">账号操作失败，数据可能已变化，请刷新后重试。</p>
      )}
    </section>
  )
}
