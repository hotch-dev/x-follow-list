export interface Account {
  id: string
  x_user_id: string
  username: string | null
  display_name: string | null
  session_status: string
  provider_code: string
  profile_ref: string
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

export interface RelationshipEvent {
  id: string
  event_type: string
  status: string
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
  return (await response.json()) as T
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
  return request<BindingSession>('/x-account-bind-sessions', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRF-Token': csrfToken,
    },
    body: JSON.stringify({
      provider_config_id: providerConfigId,
      profile_ref: profileRef,
      x_account_id: null,
    }),
  })
}

export async function listAccounts() {
  return (await request<ItemList<Account>>('/x-accounts')).items
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
