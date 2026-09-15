const maximumPollDelayMs = 15_000

export function nextPollDelay(baseDelayMs: number, completedPolls: number) {
  return Math.min(baseDelayMs * 2 ** completedPolls, maximumPollDelayMs)
}
