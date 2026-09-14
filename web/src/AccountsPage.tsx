import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { createBinding, listProfiles, listProviderConfigs } from './api'
import { PageHeader } from './PageHeader'

export function AccountsPage({ csrfToken }: { csrfToken: string }) {
  const [configId, setConfigId] = useState('')
  const [profileRef, setProfileRef] = useState('')
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
        {binding.data?.status === 'WAITING_FOR_LOGIN' && (
          <p role="status">
            等待在{selectedProfile?.display_name ?? '所选环境'} 中手工登录 X
          </p>
        )}
      </form>
    </section>
  )
}
