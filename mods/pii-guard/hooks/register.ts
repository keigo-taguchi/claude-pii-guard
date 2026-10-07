// pii-guard mod: the last net. Masks what the model would read, through the
// local pii-guard daemon, and fails closed when the daemon can't answer.
//
// Every masking hook has a .catch that answers in its place. Without it the
// engine would skip the hook and send the original text (fail-open).

import type { Register } from 'claude-code'
import { BLOCKED_TEXT, applyTexts, blockedContent, collectTexts, toolArgs } from './blocks.ts'
import { isSafeTool, parsePrefixes, siteFor } from './policy.ts'

const DEFAULTS = {
  daemonUrl: 'http://127.0.0.1:8787',
  images: 'drop',
  bashSite: 'data',
  safeTools: 'mcp__safe-data__,mcp__support-intake__,mcp__slack-safe__',
}

type Settings = {
  daemonUrl: string
  images: 'drop' | 'pass'
  bashSite: 'data' | 'code'
  safePrefixes: string[]
}

// Module-level state resets on reload; that is acceptable for a guard.
let enabled = true
let daemonOk = false
let tainted = false
let totalHits = 0
let lastError = ''

const DROP_TEXT = '[pii-guard] 個人情報の検査ができないため送信を止めました。デーモンを起動するか /pii status を確認してください。'
const ARG_DENIAL = '引数に個人情報（実名・患者ID など）が含まれています。値ではなく擬似ID（user_pseudo_id）や札で指定し直してください。'
const GUARD_DENIAL = 'pii-guard が応答しないため、この呼び出しは実行しませんでした。'

function settingsOf(options: Readonly<Record<string, unknown>>): Settings {
  const o = { ...DEFAULTS, ...options }
  return {
    daemonUrl: String(o.daemonUrl).replace(/\/+$/, ''),
    images: o.images === 'pass' ? 'pass' : 'drop',
    bashSite: o.bashSite === 'code' ? 'code' : 'data',
    safePrefixes: parsePrefixes(o.safeTools),
  }
}

// $ may be passed to a top-level function of this file (static validation rule).
async function callDaemon($: any, url: string, body: unknown): Promise<any> {
  const res = await $.http.fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error('pii-guard daemon answered ' + res.status)
  return JSON.parse(res.text)
}

async function maskTexts($: any, s: Settings, site: string, texts: readonly string[]): Promise<string[]> {
  if (texts.length === 0) return []
  const data = await callDaemon($, s.daemonUrl + '/mask', { site, texts })
  if (!Array.isArray(data.texts) || data.texts.length !== texts.length) throw new Error('pii-guard daemon returned a malformed result')
  const hits = data.hits && typeof data.hits === 'object' ? Object.values(data.hits as Record<string, number>) : []
  totalHits += hits.reduce((a, b) => a + Number(b || 0), 0)
  if (data.tainted === true) tainted = true
  daemonOk = true
  return data.texts.map((t: unknown) => String(t))
}

const TOKEN_RE = /\[(?:P\d+_[A-Z_]+|[A-Z_]+_\d+)\]/

function collectStrings(v: unknown, out: string[]): void {
  if (typeof v === 'string') {
    if (TOKEN_RE.test(v)) out.push(v)
  } else if (Array.isArray(v)) v.forEach(x => collectStrings(x, out))
  else if (v && typeof v === 'object') Object.values(v as Record<string, unknown>).forEach(x => collectStrings(x, out))
}

function replaceStrings(v: unknown, map: Map<string, string>): unknown {
  if (typeof v === 'string') return map.get(v) ?? v
  if (Array.isArray(v)) return v.map(x => replaceStrings(x, map))
  if (v && typeof v === 'object') {
    const out: Record<string, unknown> = {}
    for (const [k, x] of Object.entries(v as Record<string, unknown>)) out[k] = replaceStrings(x, map)
    return out
  }
  return v
}

// Display-only restoration. The daemon only answers unmask for X-Caller ui.render.
async function unmaskProps($: any, s: Settings, props: unknown): Promise<unknown> {
  const strings: string[] = []
  collectStrings(props, strings)
  if (strings.length === 0) return props
  const res = await $.http.fetch(s.daemonUrl + '/unmask', {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'x-caller': 'ui.render' },
    body: JSON.stringify({ texts: strings }),
  })
  if (!res.ok) throw new Error('unmask ' + res.status)
  const data = JSON.parse(res.text)
  const map = new Map<string, string>()
  strings.forEach((orig, i) => map.set(orig, String(data.texts[i] ?? orig)))
  return replaceStrings(props, map)
}

async function health($: any, s: Settings): Promise<any> {
  const res = await $.http.fetch(s.daemonUrl + '/healthz', { method: 'GET' })
  if (!res.ok) throw new Error('healthz ' + res.status)
  return JSON.parse(res.text)
}

export const register: Register = (on, options) => {
  const s = settingsOf(options)

  on('session.start', async ($, e, next) => {
    try {
      await health($, s)
      daemonOk = true
    } catch (err) {
      daemonOk = false
      lastError = err instanceof Error ? err.message : String(err)
      $.ui.status('pii-guard: デーモンに到達できません。プロンプトは送信されません（/pii status）')
    }
    try {
      await $.command.register({ name: 'pii', description: 'pii-guard: status / untaint / reload / off / on', argumentHint: '[status|untaint|reload|off|on]' })
    } catch {
      // already registered after a reload
    }
    return next(e)
  })

  // ---- what the user types -------------------------------------------------
  on('prompt.submit', async ($, e, next) => {
    if (!enabled) return next(e)
    const [text, ...context] = await maskTexts($, s, 'prompt', [e.text, ...(e.context ?? [])])
    return next({ ...e, text: text ?? e.text, ...(e.context !== undefined && { context }) })
  }).catch(($, e, next) => {
    lastError = next.error?.message ?? 'prompt.submit failed'
    $.ui.log('pii-guard: prompt.submit ' + (next.error?.kind ?? 'failed') + ' — dropped', { to: 'debug' })
    return { drop: DROP_TEXT }
  })

  // ---- every row the model will read ---------------------------------------
  on('session.append', async ($, e, next) => {
    if (!enabled) return next(e)
    if (e.message.role === undefined || e.door === 'response') return next(e)
    const site = siteFor(e.door, e.origin as any, s.safePrefixes, s.bashSite)
    if (site === 'safe') return next(e)
    const texts = collectTexts(e.message.content as any)
    const masked = await maskTexts($, s, site, texts)
    const { content, dropped } = applyTexts(e.message.content as any, masked, s.images)
    if (dropped > 0) $.ui.log('pii-guard: ' + dropped + ' media block(s) dropped from a ' + e.door + ' row', { to: 'debug' })
    return next({ ...e, message: { ...e.message, content } })
  }).catch(($, e, next) => {
    if (e.message.role === undefined || e.door === 'response') return next(e)
    lastError = next.error?.message ?? 'session.append failed'
    $.ui.log('pii-guard: session.append ' + (next.error?.kind ?? 'failed') + ' — row blanked', { to: 'debug' })
    return next({ ...e, message: { ...e.message, content: blockedContent(e.message.content as any) } })
  })

  // ---- CLAUDE.md, memory, git status ---------------------------------------
  on('prompt.context', async ($, e, next) => {
    const ctx = await next(e)
    if (!enabled) return ctx
    const texts = ctx.blocks.map((b: any) => String(b.text ?? ''))
    const masked = await maskTexts($, s, 'context', texts)
    return { ...ctx, blocks: ctx.blocks.map((b: any, i: number) => ({ ...b, text: masked[i] ?? b.text })) }
  }).catch(($, e, next) => {
    lastError = next.error?.message ?? 'prompt.context failed'
    $.ui.log('pii-guard: prompt.context ' + (next.error?.kind ?? 'failed') + ' — context withheld', { to: 'debug' })
    return { blocks: [] }
  })

  // ---- arguments Claude writes (the classifier and the transcript see them) -
  on('tool.call', { tool: /^(?:Bash|mcp__)/ }, async ($, e, next) => {
    if (!enabled) return next(e)
    if (isSafeTool(e.tool, s.safePrefixes)) return next(e)
    const verdict = await callDaemon($, s.daemonUrl + '/check-args', { tool: e.tool, args: toolArgs(e as any) })
    daemonOk = true
    if (verdict.deny) return { deny: ARG_DENIAL + '（' + (verdict.kinds ?? []).join(', ') + '）' }
    return next(e)
  }).catch(($, e, next) => {
    lastError = next.error?.message ?? 'tool.call failed'
    return { deny: GUARD_DENIAL }
  })

  // ---- kill switch while tainted -------------------------------------------
  on('turn.step', async function* ($, e, next) {
    if (enabled && tainted) {
      return { turnId: e.turnId, index: e.index, answer: BLOCKED_TEXT, toolUses: [], stopReason: 'end_turn', usage: null }
    }
    return yield* next(e)
  })

  // ---- show real values on screen only (the model and the transcript keep tokens)
  on('ui.render', { component: ['UserMessage', 'AssistantMessage', 'ToolUse', 'ToolResult'] }, async ($, e, next) => {
    if (!enabled) return next(e)
    const props = await unmaskProps($, s, e.props)
    return next({ ...e, props } as any)
  }).catch(($, e, next) => next(e))

  // ---- /pii ------------------------------------------------------------------
  on('command.run', { command: 'pii' }, async ($, e) => {
    const arg = (e.args || 'status').trim()
    if (arg === 'untaint') {
      tainted = false
      return { text: 'pii-guard: 汚染フラグを解除しました' }
    }
    if (arg === 'off') {
      enabled = false
      return { text: 'pii-guard: このセッションでは無効化しました（個人情報に触れる作業では使わないこと）' }
    }
    if (arg === 'on') {
      enabled = true
      return { text: 'pii-guard: 有効化しました' }
    }
    if (arg === 'reload') {
      try {
        const r = await callDaemon($, s.daemonUrl + '/dict/reload', {})
        return { text: 'pii-guard: 辞書を再読込しました（' + r.entries + ' 件）' }
      } catch (err) {
        return { text: 'pii-guard: 再読込に失敗: ' + (err instanceof Error ? err.message : String(err)) }
      }
    }
    try {
      const h = await health($, s)
      daemonOk = true
      return {
        text:
          'pii-guard: ' + (enabled ? '有効' : '無効') + ' / デーモン OK (v' + h.version + ') / 辞書 ' +
          h.dictionary.entries + ' 形態・' + h.dictionary.persons + ' 名 (' + (h.dictionary.generated || '未生成') + ') / vault ' +
          h.vault + ' 件 / このセッションの置換 ' + totalHits + ' 件' + (tainted ? ' / 汚染あり（/pii untaint）' : ''),
      }
    } catch (err) {
      daemonOk = false
      return { text: 'pii-guard: デーモンに到達できません (' + (err instanceof Error ? err.message : String(err)) + ')。最後のエラー: ' + lastError }
    }
  })
}
