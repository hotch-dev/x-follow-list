export function formatUtc(value: string | null, empty = '尚无成功数据') {
  return value ? `${value.slice(0, 16).replace('T', ' ')} UTC` : empty
}
