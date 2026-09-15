import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'

import { createSession } from './api'
import { storeCsrfToken } from './session'

export function LoginPanel({ onAuthenticated }: { onAuthenticated: () => Promise<void> }) {
  const [login, setLogin] = useState('')
  const [password, setPassword] = useState('')
  const session = useMutation({
    mutationFn: () => createSession(login, password),
    onSuccess: async (result) => {
      storeCsrfToken(result.csrf_token)
      setPassword('')
      await onAuthenticated()
    },
  })

  return (
    <main className="app-shell">
      <form
        className="status-card form-card"
        onSubmit={(event) => {
          event.preventDefault()
          session.mutate()
        }}
      >
        <p className="eyebrow">X Follow List</p>
        <h1>登录</h1>
        <label htmlFor="login">登录账号</label>
        <input
          id="login"
          autoComplete="username"
          value={login}
          onChange={(event) => setLogin(event.target.value)}
        />
        <label htmlFor="password">密码</label>
        <input
          id="password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        <button type="submit" disabled={!login || !password || session.isPending}>
          {session.isPending ? '正在登录…' : '登录'}
        </button>
        {session.isError && <p role="alert">账号或密码不正确。</p>}
      </form>
    </main>
  )
}
