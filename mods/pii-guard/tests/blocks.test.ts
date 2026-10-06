import { expect, test } from 'claude-code/testing'
import { BLOCKED_TEXT, DROPPED_MEDIA_TEXT, applyTexts, blockedContent, collectTexts, toolArgs } from '../hooks/blocks.ts'
import { siteFor } from '../hooks/policy.ts'

const SAFE = ['mcp__safe-data__']

test('collectTexts walks text and tool_result blocks in order and skips media/thinking', async () => {
  const content = [
    { type: 'thinking', thinking: 'secret reasoning' },
    { type: 'text', text: 'a' },
    { type: 'tool_result', tool_use_id: 't1', content: 'b' },
    { type: 'tool_result', tool_use_id: 't2', content: [{ type: 'text', text: 'c' }, { type: 'image', source: {} }, { type: 'text', text: 'd' }] },
    { type: 'tool_use', id: 'x', name: 'Bash', input: { command: 'ls' } },
  ]
  expect(collectTexts(content)).toEqual(['a', 'b', 'c', 'd'])
})

test('applyTexts writes masked texts back in order, drops media, keeps untouchable blocks', async () => {
  const content = [
    { type: 'text', text: 'a' },
    { type: 'tool_result', tool_use_id: 't2', content: [{ type: 'text', text: 'c' }, { type: 'image', source: {} }] },
    { type: 'tool_use', id: 'x', name: 'Bash', input: { command: 'ls' } },
  ]
  const { content: out, dropped } = applyTexts(content, ['A', 'C'], 'drop')
  expect(dropped).toBe(1)
  expect(out[0]).toEqual({ type: 'text', text: 'A' })
  expect(out[1]).toEqual({ type: 'tool_result', tool_use_id: 't2', content: [{ type: 'text', text: 'C' }] })
  expect(out[2]).toEqual(content[2])
  // input untouched
  expect(content[0]).toEqual({ type: 'text', text: 'a' })
})

test('a tool_result that was only an image keeps its structure with a notice', async () => {
  const content = [{ type: 'tool_result', tool_use_id: 't', content: [{ type: 'image', source: {} }] }]
  const { content: out } = applyTexts(content, [], 'drop')
  expect(out[0]).toEqual({ type: 'tool_result', tool_use_id: 't', content: [{ type: 'text', text: DROPPED_MEDIA_TEXT }] })
  const { content: kept } = applyTexts(content, [], 'pass')
  expect(kept).toEqual(content)
})

test('blockedContent replaces every readable text and drops media', async () => {
  const content = [
    { type: 'text', text: '田中太郎' },
    { type: 'tool_result', tool_use_id: 't', content: '090-1234-5678' },
    { type: 'image', source: {} },
  ]
  const out = blockedContent(content)
  expect(JSON.stringify(out)).not.toContain('田中')
  expect(JSON.stringify(out)).not.toContain('1234')
  expect(out[0]).toEqual({ type: 'text', text: BLOCKED_TEXT })
  expect(out[1]).toEqual({ type: 'tool_result', tool_use_id: 't', content: BLOCKED_TEXT })
})

test('toolArgs strips the envelope fields', async () => {
  expect(toolArgs({ tool: 'Bash', tool_use_id: 'u', consent: 'c', command: 'ls', description: 'd' })).toEqual({ command: 'ls', description: 'd' })
})

test('siteFor routes by origin and door', async () => {
  expect(siteFor('tool-result', { kind: 'tool', tool: 'mcp__safe-data__sql_run' }, SAFE, 'data')).toBe('safe')
  expect(siteFor('tool-result', { kind: 'tool', tool: 'Read' }, SAFE, 'data')).toBe('code')
  expect(siteFor('tool-result', { kind: 'tool', tool: 'Bash' }, SAFE, 'data')).toBe('data')
  expect(siteFor('tool-result', { kind: 'tool', tool: 'Bash' }, SAFE, 'code')).toBe('code')
  expect(siteFor('tool-result', { kind: 'tool', tool: 'mcp__gmail__search' }, SAFE, 'data')).toBe('data')
  expect(siteFor('prompt', { kind: 'person' }, SAFE, 'data')).toBe('prompt')
  expect(siteFor('attachment', { kind: 'engine' }, SAFE, 'data')).toBe('attachment')
})
