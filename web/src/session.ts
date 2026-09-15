const csrfStorageKey = 'x-follow-list-csrf'

export function getCsrfToken() {
  return window.sessionStorage.getItem(csrfStorageKey) ?? ''
}

export function storeCsrfToken(token: string) {
  window.sessionStorage.setItem(csrfStorageKey, token)
}
