import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import {
  ApiError,
  createAccountRule,
  deleteAccountRule,
  listAccountRules,
  type AccountRule,
} from './api'
import { PageHeader } from './PageHeader'

export function RulesPage({
  accountId,
  csrfToken,
}: {
  accountId: string
  csrfToken: string
}) {
  const [subjectId, setSubjectId] = useState('')
  const [reason, setReason] = useState('')
  const [ruleType, setRuleType] = useState<AccountRule['rule_type']>('BUSINESS_BLOCKLIST')
  const [scanRequired, setScanRequired] = useState(false)
  const queryClient = useQueryClient()
  const rules = useQuery({
    queryKey: ['account-rules', accountId],
    queryFn: () => listAccountRules(accountId),
  })
  const create = useMutation({
    mutationFn: () => createAccountRule(accountId, subjectId.trim(), ruleType, reason.trim(), csrfToken),
    onSuccess: (rule) => {
      setScanRequired(Boolean(rule.scan_required))
      setSubjectId('')
      setReason('')
      void queryClient.invalidateQueries({ queryKey: ['account-rules', accountId] })
      void queryClient.invalidateQueries({ queryKey: ['action-items', accountId] })
    },
  })
  const remove = useMutation({
    mutationFn: (rule: AccountRule) => deleteAccountRule(rule, csrfToken),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['account-rules', accountId] })
      void queryClient.invalidateQueries({ queryKey: ['action-items', accountId] })
    },
  })

  return (
    <section className="dashboard" aria-labelledby="rules-title">
      <PageHeader title="名单规则" titleId="rules-title">
        黑名单只在本系统中标记，不会在 X 上拉黑或取关。一个用户不能同时在两种名单中。
      </PageHeader>
      <form className="card form-card" onSubmit={(event) => {
        event.preventDefault()
        create.mutate()
      }}>
        <label htmlFor="rule-subject">X 用户 ID</label>
        <input id="rule-subject" value={subjectId} onChange={(event) => setSubjectId(event.target.value)} required maxLength={64} />
        <label htmlFor="rule-type">规则类型</label>
        <select id="rule-type" value={ruleType} onChange={(event) => setRuleType(event.target.value as AccountRule['rule_type'])}>
          <option value="BUSINESS_BLOCKLIST">业务黑名单</option>
          <option value="ALLOWLIST">白名单</option>
        </select>
        <label htmlFor="rule-reason">原因</label>
        <input id="rule-reason" value={reason} onChange={(event) => setReason(event.target.value)} required maxLength={500} />
        <button type="submit" disabled={create.isPending}>添加规则</button>
        {scanRequired && <p>请先完成一次成功扫描，再判断是否仍在关注名单中。</p>}
        {create.isError && (
          <p role="alert">
            {create.error instanceof ApiError && create.error.code === 'RULE_CONFLICT'
              ? '先移除该用户已有的规则，再添加另一种名单。'
              : '添加失败，请检查账号权限与输入后重试。'}
          </p>
        )}
      </form>
      <section className="card" aria-label="已有名单规则">
        <h2>已有规则</h2>
        {rules.isError && <p role="alert">规则加载失败，请刷新后重试。</p>}
        {rules.data?.length === 0 && <p>暂无规则。</p>}
        {rules.data?.map((rule) => (
          <article key={rule.id}>
            <h3>{rule.rule_type === 'BUSINESS_BLOCKLIST' ? '业务黑名单' : '白名单'} · {rule.subject_x_user_id}</h3>
            <p>{rule.reason}</p>
            <button type="button" disabled={remove.isPending} onClick={() => remove.mutate(rule)}>移除规则</button>
          </article>
        ))}
        {remove.isError && <p role="alert">规则已变化，请刷新后重新确认。</p>}
      </section>
    </section>
  )
}
