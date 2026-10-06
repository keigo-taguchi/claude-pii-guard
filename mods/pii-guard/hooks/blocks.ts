// Pure helpers for walking a row's content blocks. No $ here, so they can be
// unit-tested without the test kit and imported from register.ts.

export type Block = { type: string; [field: string]: unknown }
export type ImagePolicy = 'drop' | 'pass'

const MEDIA = new Set(['image', 'document', 'audio'])
// Blocks the engine owns; a hook may not change them.
const UNTOUCHABLE = new Set(['thinking', 'redacted_thinking', 'tool_use', 'server_tool_use'])

export const BLOCKED_TEXT = '[pii-guard] 個人情報の検査ができなかったため、この内容は伏せました。デーモンの状態は /pii で確認できます。'
export const DROPPED_MEDIA_TEXT = '[pii-guard] 伏せ字にできない画像・文書を除外しました。'

/** Every text the model would read from these blocks, in traversal order. */
export function collectTexts(content: readonly Block[]): string[] {
  const out: string[] = []
  const walk = (blocks: readonly Block[]) => {
    for (const b of blocks) {
      if (UNTOUCHABLE.has(b.type) || MEDIA.has(b.type)) continue
      if (b.type === 'text' && typeof b.text === 'string') out.push(b.text)
      else if (b.type === 'tool_result') {
        if (typeof b.content === 'string') out.push(b.content)
        else if (Array.isArray(b.content)) walk(b.content as Block[])
      }
    }
  }
  walk(content)
  return out
}

/**
 * Write masked texts back in the same traversal order and apply the media
 * policy. Returns a new array; the input is never mutated (events are frozen).
 */
export function applyTexts(content: readonly Block[], masked: readonly string[], images: ImagePolicy): { content: Block[]; dropped: number } {
  let i = 0
  let dropped = 0
  const walk = (blocks: readonly Block[], nested: boolean): Block[] => {
    const out: Block[] = []
    for (const b of blocks) {
      if (MEDIA.has(b.type)) {
        if (images === 'drop') {
          dropped += 1
          continue
        }
        out.push(b)
        continue
      }
      if (UNTOUCHABLE.has(b.type)) {
        out.push(b)
        continue
      }
      if (b.type === 'text' && typeof b.text === 'string') {
        out.push({ ...b, text: masked[i++] ?? b.text })
        continue
      }
      if (b.type === 'tool_result') {
        if (typeof b.content === 'string') {
          out.push({ ...b, content: masked[i++] ?? b.content })
        } else if (Array.isArray(b.content)) {
          const inner = walk(b.content as Block[], true)
          out.push({ ...b, content: inner.length > 0 ? inner : [{ type: 'text', text: DROPPED_MEDIA_TEXT }] })
        } else {
          out.push(b)
        }
        continue
      }
      out.push(b)
    }
    if (!nested && dropped > 0 && out.length === 0) out.push({ type: 'text', text: DROPPED_MEDIA_TEXT })
    return out
  }
  return { content: walk(content, false), dropped }
}

/** Fail-closed replacement: every readable text becomes the blocked notice. */
export function blockedContent(content: readonly Block[]): Block[] {
  const texts = collectTexts(content)
  return applyTexts(content, texts.map(() => BLOCKED_TEXT), 'drop').content
}

/** Fields of a tool.call event that are the tool's own arguments. */
export function toolArgs(e: Record<string, unknown>): Record<string, unknown> {
  const skip = new Set(['tool', 'tool_use_id', 'consent', 'agentId'])
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(e)) if (!skip.has(k)) out[k] = v
  return out
}
