export interface Account {
  id: string
  x_user_id: string
  username: string | null
  display_name: string | null
  session_status: string
  provider_code: string
  profile_ref: string
  version: number
  last_successful_scan_at: string | null
}

export interface ScanError {
  code: string
  summary: string
}

export interface ScanRun {
  id: string
  x_account_id: string
  status: string
  progress_stage: string | null
  error: ScanError | null
  follower_count: number | null
  following_count: number | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  last_successful_scan_at: string | null
}

export interface XlsxArtifact {
  id: string
  status: string
  sha256: string | null
  byte_size: number | null
  expires_at: string
  deleted_at: string | null
  download_url: string
}

export interface RelationshipEvent {
  id: string
  x_account_id: string
  scan_run_id: string
  subject_x_user_id: string
  username: string | null
  display_name: string | null
  category: string
  event_type: string
  status: string
  version: number
  created_at: string
  acknowledged_at: string | null
}

export interface Relationship {
  x_user_id: string
  username: string | null
  display_name: string | null
  state: string
  non_followback_streak: number
  snapshot_id: string
}

interface ItemList<T> {
  items: T[]
}

export interface ProviderConfigSummary {
  id: string
  provider_code: string
  config_version: number
  display_name: string
  has_secret: boolean
}

export interface ProfileSummary {
  profile_ref: string
  display_name: string
  is_running: boolean
}

export interface BindingSession {
  id: string
  provider_code: string
  profile_ref: string
  status: string
  detected_identity: {
    x_user_id: string
    username: string | null
    display_name: string | null
  } | null
  error_code: string | null
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super(code)
  }
}

export async function createSession(login: string, password: string) {
  return request<{ csrf_token: string }>('/auth/session', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ login, password }),
  })
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    credentials: 'same-origin',
    ...init,
    headers: { Accept: 'application/json', ...init.headers },
  })
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as {
      code?: unknown
    }
    throw new ApiError(
      response.status,
      typeof payload.code === 'string' ? payload.code : 'REQUEST_FAILED',
    )
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

function jsonMutation(body: object, csrfToken: string): RequestInit {
  return {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRF-Token': csrfToken,
    },
    body: JSON.stringify(body),
  }
}

export async function listProviderConfigs() {
  return (await request<ItemList<ProviderConfigSummary>>('/browser-provider-configs')).items
}

export async function listProfiles(configId: string) {
  return (
    await request<ItemList<ProfileSummary>>(
      `/browser-provider-configs/${encodeURIComponent(configId)}/profiles`,
    )
  ).items
}

export async function createBinding(
  providerConfigId: string,
  profileRef: string,
  csrfToken: string,
) {
  return request<BindingSession>(
    '/x-account-bind-sessions',
    jsonMutation(
      {
      provider_config_id: providerConfigId,
      profile_ref: profileRef,
      x_account_id: null,
      },
      csrfToken,
    ),
  )
}

export async function createRevalidation(accountId: string, csrfToken: string) {
  return request<BindingSession>(
    '/x-account-bind-sessions',
    jsonMutation({ x_account_id: accountId }, csrfToken),
  )
}

export async function getBinding(bindingId: string) {
  return request<BindingSession>(
    `/x-account-bind-sessions/${encodeURIComponent(bindingId)}`,
  )
}

export async function confirmBinding(bindingId: string, csrfToken: string) {
  return request<BindingSession>(
    `/x-account-bind-sessions/${encodeURIComponent(bindingId)}/confirm`,
    jsonMutation({}, csrfToken),
  )
}

export async function listAccounts() {
  return (await request<ItemList<Account>>('/x-accounts')).items
}

export async function unbindAccount(account: Account, csrfToken: string) {
  return request<void>(`/x-accounts/${encodeURIComponent(account.id)}`, {
    method: 'DELETE',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRF-Token': csrfToken,
    },
    body: JSON.stringify({ version: account.version, delete_history: false }),
  })
}

export async function listScans() {
  return (await request<ItemList<ScanRun>>('/scan-runs?limit=50')).items
}

export async function getScan(runId: string) {
  return request<ScanRun>(`/scan-runs/${encodeURIComponent(runId)}`)
}

export async function createScan(accountId: string, csrfToken: string) {
  return request<ScanRun>(`/x-accounts/${encodeURIComponent(accountId)}/scan-runs`, {
    method: 'POST',
    headers: {
      'Idempotency-Key': crypto.randomUUID(),
      'X-CSRF-Token': csrfToken,
    },
  })
}

export async function createXlsxArtifact(runId: string, csrfToken: string) {
  return request<XlsxArtifact>(
    `/scan-runs/${encodeURIComponent(runId)}/artifacts/xlsx`,
    { method: 'POST', headers: { 'X-CSRF-Token': csrfToken } },
  )
}

export async function listRelationships(
  accountId: string,
  filters: { state: string; search: string },
) {
  const query = new URLSearchParams({ x_account_id: accountId, limit: '50' })
  if (filters.state) query.set('state', filters.state)
  if (filters.search) query.set('search', filters.search)
  return (await request<ItemList<Relationship>>(`/relationships?${query}`)).items
}

export async function listActionItems(accountId: string) {
  const query = new URLSearchParams({
    x_account_id: accountId,
    category: 'ACTION_ITEM',
    event_type: 'UNFOLLOWED_ME_AFTER_MUTUAL',
    status: 'NEW',
    limit: '50',
  })
  return (await request<ItemList<RelationshipEvent>>(`/relationship-events?${query}`)).items
}

export async function acknowledgeEvent(
  eventId: string,
  version: number,
  csrfToken: string,
) {
  return request<RelationshipEvent>(
    `/relationship-events/${encodeURIComponent(eventId)}/acknowledge`,
    jsonMutation({ version }, csrfToken),
  )
}

export async function loadDashboard() {
  const accounts = await listAccounts()
  const account = accounts[0]
  if (!account) {
    return { accounts: [], scans: [], actionItems: [] }
  }
  const query = new URLSearchParams({
    x_account_id: account.id,
    event_type: 'UNFOLLOWED_ME_AFTER_MUTUAL',
    status: 'NEW',
    limit: '50',
  })
  const [scans, events] = await Promise.all([
    request<ItemList<ScanRun>>('/scan-runs?limit=5'),
    request<ItemList<RelationshipEvent>>(`/relationship-events?${query}`),
  ])
  return {
    accounts,
    scans: scans.items,
    actionItems: events.items,
  }
}
