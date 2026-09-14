import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { App } from './App'

describe('App', () => {
  it('identifies the relationship monitoring console', () => {
    render(<App />)

    expect(
      screen.getByRole('heading', { name: '关注关系监控' }),
    ).toBeInTheDocument()
    expect(screen.getByText('系统初始化完成，即将连接监控账号。')).toBeVisible()
  })
})

