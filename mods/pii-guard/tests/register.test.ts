import { expect, test } from 'claude-code/testing'
import { BLOCKED_TEXT } from '../hooks/blocks.ts'

// A fake daemon: masks two known values, denies args that contain the name.
function fakeDaemon(on: any, calls: any[] = []) {
  on('ui.log', () => ({ value: undefined }))
  on('ui.status', () => ({ value: undefined }))
  on('http.fetch', ($: any, e: any) => {
    const url: string = e.url ?? e.input ?? ''
    const init = e.init ?? e
    const body = init && typeof init.body === 'string' ? JSON.parse(init.body) : {}
    calls.push({ url, body })
    const reply = (obj: unknown) => ({ value: { status: 200, ok: true, headers: {}, text: JSON.stringify(obj) } })
    if (url.endsWith('/healthz')) return reply({ ok: true, version: 't', dictionary: { entries: 2, persons: 1, generated: 'x' }, vault: 0 })
    if (url.endsWith('/mask')) {
      const texts = (body.texts as string[]).map(t => t.replace(/田中太郎/g, '[P1_NAME]').replace(/090-1234-5678/g, '[PHONE_1]'))
      return reply({ texts, hits: { DICT_NAME: 1 }, tainted: false })
    }
    if (url.endsWith('/unmask')) {
      const caller = (init.headers ?? {})['x-caller']
      if (caller !== 'ui.render') return { value: { status: 403, ok: false, headers: {}, text: '{}' } }
      return reply({ texts: (body.texts as string[]).map(t => t.replace(/\[P1_NAME\]/g, '田中太郎')) })
    }
    if (url.endsWith('/check-args')) {
      const s = JSON.stringify(body.args)
      return reply({ deny: s.includes('田中太郎'), kinds: s.includes('田中太郎') ? ['DICT_NAME'] : [] })
    }
    return { value: { status: 404, ok: false, headers: {}, text: '{}' } }
  })
  return calls
}

const ROW = (content: unknown[], door = 'tool-result', origin: unknown = { kind: 'tool', tool: 'Bash' }) => ({
  message: { type: 'user', role: 'user', content },
  door,
  origin,
  uuid: 'row-1',
})

test('prompt.submit is masked before it enters', async ($, on) => {
  fakeDaemon(on)
  on('prompt.submit', ($: any, e: any) => ({ text: e.text, context: e.context }))
  const r = await $.prompt.submit({ text: '田中太郎さんの電話は 090-1234-5678', wait: false, context: ['memo: 田中太郎'] })
  expect(r.text).toBe('[P1_NAME]さんの電話は [PHONE_1]')
  expect(r.context).toEqual(['memo: [P1_NAME]'])
})

test('prompt.submit is dropped when the daemon is unreachable (fail-closed)', async ($, on) => {
  on('ui.log', () => ({ value: undefined }))
  on('http.fetch', () => ({ deny: 'connection refused' }))
  on('prompt.submit', ($: any, e: any) => ({ text: e.text }))
  const r = await $.prompt.submit({ text: '田中太郎', wait: false })
  expect(r.drop).toBeDefined()
  expect(JSON.stringify(r)).not.toContain('田中')
})

// The kit has no bottom for session.append (the engine keeps the row itself),
// so these tests capture what the mod passed down and ignore the kit's error.
async function appendThrough($: any, on: any, row: any): Promise<any> {
  let seen: any
  on('session.append', ($: any, e: any, next: any) => {
    seen = e
    return next(e)
  })
  try {
    await $.session.append(row)
  } catch {
    // no implementation beneath: expected
  }
  return seen
}

test('session.append masks tool results and drops images', async ($, on) => {
  fakeDaemon(on)
  const seen = await appendThrough($, on, ROW([
    { type: 'tool_result', tool_use_id: 't', content: [{ type: 'text', text: 'user=田中太郎' }, { type: 'image', source: {} }] },
  ]))
  expect(seen.message.content).toEqual([{ type: 'tool_result', tool_use_id: 't', content: [{ type: 'text', text: 'user=[P1_NAME]' }] }])
})

test('session.append leaves model responses and safe tools alone', async ($, on) => {
  const calls = fakeDaemon(on)
  const resp = await appendThrough($, on, { ...ROW([], 'response', { kind: 'model', model: 'm' }), message: { type: 'assistant', role: 'assistant', content: [{ type: 'text', text: '田中太郎' }] } })
  expect(resp.message.content[0].text).toBe('田中太郎')
  expect(calls.filter(c => c.url.endsWith('/mask')).length).toBe(0)
})

test('session.append skips tools whose output is already pseudonymized', async ($, on) => {
  const calls = fakeDaemon(on)
  const safe = await appendThrough($, on, ROW([{ type: 'tool_result', tool_use_id: 't', content: '田中太郎' }], 'tool-result', { kind: 'tool', tool: 'mcp__safe-data__sql_run' }))
  expect(safe.message.content[0].content).toBe('田中太郎')
  expect(calls.filter(c => c.url.endsWith('/mask')).length).toBe(0)
})

test('session.append blanks the row when the daemon fails (fail-closed)', async ($, on) => {
  on('ui.log', () => ({ value: undefined }))
  on('http.fetch', () => ({ deny: 'down' }))
  const seen = await appendThrough($, on, ROW([{ type: 'text', text: '田中太郎' }, { type: 'tool_result', tool_use_id: 't', content: '090-1234-5678' }]))
  expect(seen.message.content[0].text).toBe(BLOCKED_TEXT)
  expect(seen.message.content[1].content).toBe(BLOCKED_TEXT)
  expect(JSON.stringify(seen)).not.toContain('田中')
})

test('tool.call denies Bash whose arguments carry a known name, and passes clean calls', async ($, on) => {
  fakeDaemon(on)
  on('tool.call', () => ({ result: 'ran' }))
  const denied = await $.tool.call({ tool: 'Bash', command: "psql -c \"select * from users where name='田中太郎'\"" })
  expect(denied.deny).toBeDefined()
  expect(denied.deny).not.toContain('田中')
  const ok = await $.tool.call({ tool: 'Bash', command: 'ls' })
  expect(ok).toEqual({ result: 'ran' })
})

test('tool.call is denied when the daemon fails (fail-closed); safe tools skip the check', async ($, on) => {
  on('ui.log', () => ({ value: undefined }))
  on('http.fetch', () => ({ deny: 'down' }))
  on('tool.call', () => ({ result: 'ran' }))
  const denied = await $.tool.call({ tool: 'Bash', command: 'ls' })
  expect(denied.deny).toBeDefined()
  const safe = await $.tool.call({ tool: 'mcp__safe-data__schema_describe' })
  expect(safe).toEqual({ result: 'ran' })
})

test('/pii status reports the daemon', async ($, on) => {
  fakeDaemon(on)
  const r = await $.command.run({ command: 'pii', args: '' })
  expect(r.text).toContain('デーモン OK')
})

test('ui.render shows real values on screen for a masked assistant message', async ($, on) => {
  fakeDaemon(on)
  on('ui.render', ($: any, e: any) => ({ type: 'Text', props: {}, children: [String(e.props.text)] }))
  const ui = await $.ui.mount({
    plugin: 'pii-guard', component: 'AssistantMessage', requestId: 'm1', surface: 'terminal',
    viewport: { columns: 100, rows: 30 },
    props: { text: '[P1_NAME] さんの件です', isFirstOfReply: true },
  } as any)
  expect(await ui.find({ type: 'Text', text: '田中太郎 さんの件です' })).toBeDefined()
  await ui.unmount()
})

test('ui.render keeps tokens when the daemon is down', async ($, on) => {
  on('http.fetch', () => ({ deny: 'down' }))
  on('ui.render', ($: any, e: any) => ({ type: 'Text', props: {}, children: [String(e.props.text)] }))
  const ui = await $.ui.mount({
    plugin: 'pii-guard', component: 'AssistantMessage', requestId: 'm2', surface: 'terminal',
    viewport: { columns: 100, rows: 30 },
    props: { text: '[P1_NAME] さん', isFirstOfReply: true },
  } as any)
  expect(await ui.find({ type: 'Text', text: '[P1_NAME] さん' })).toBeDefined()
  await ui.unmount()
})
