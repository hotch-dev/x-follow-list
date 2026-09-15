import type { MouseEvent } from 'react'

const items = [
  { href: '/', label: '总览' },
  { href: '/accounts', label: 'X 账号' },
  { href: '/scans', label: '扫描任务' },
  { href: '/relationships', label: '关系结果' },
  { href: '/action-items', label: '重点待处理' },
]

export function Navigation({ path, onNavigate }: { path: string; onNavigate: (path: string) => void }) {
  const navigate = (event: MouseEvent<HTMLAnchorElement>, href: string) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return
    }
    event.preventDefault()
    window.history.pushState({}, '', href)
    onNavigate(href)
  }

  return (
    <header className="site-header">
      <span className="brand">X Follow List</span>
      <nav aria-label="主导航">
        {items.map((item) => (
          <a
            key={item.href}
            href={item.href}
            aria-current={path === item.href ? 'page' : undefined}
            onClick={(event) => navigate(event, item.href)}
          >
            {item.label}
          </a>
        ))}
      </nav>
    </header>
  )
}
