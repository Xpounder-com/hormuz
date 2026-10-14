export function sendsOnEnter(
  event: { key: string; shiftKey: boolean; isComposing: boolean },
  compositionActive: boolean,
): boolean {
  return event.key === 'Enter' && !event.shiftKey && !event.isComposing && !compositionActive;
}
