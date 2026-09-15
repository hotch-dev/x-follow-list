import type { BindingSession } from './api'

export function BindingStatus({
  session,
  profileName,
  confirming,
  onConfirm,
}: {
  session: BindingSession | undefined
  profileName: string
  confirming: boolean
  onConfirm: (session: BindingSession) => void
}) {
  if (session?.status === 'WAITING_FOR_LOGIN') {
    return <p role="status">等待在{profileName} 中手工登录 X</p>
  }
  if (session?.status === 'AWAITING_CONFIRMATION' && session.detected_identity) {
    const identity = session.detected_identity
    return (
      <section className="identity-confirmation" aria-label="检测到的 X 账号">
        <h2>{identity.display_name ?? `@${identity.username ?? identity.x_user_id}`}</h2>
        <p>@{identity.username ?? identity.x_user_id}</p>
        <button type="button" disabled={confirming} onClick={() => onConfirm(session)}>
          确认绑定此账号
        </button>
      </section>
    )
  }
  if (session?.status === 'COMPLETED') {
    return <p role="status">绑定完成</p>
  }
  return null
}
