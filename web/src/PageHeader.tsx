import type { ReactNode } from 'react'

export function PageHeader({
  title,
  children,
  titleId,
}: {
  title: string
  children: ReactNode
  titleId?: string
}) {
  return (
    <header className="page-header">
      <p className="eyebrow">X Follow List</p>
      <h1 id={titleId}>{title}</h1>
      <p>{children}</p>
    </header>
  )
}
