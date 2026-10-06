// Decide how hard to mask a row (the daemon's "site"), from where the row came.

export type Site = 'prompt' | 'data' | 'code' | 'context' | 'attachment' | 'safe'

const CODE_TOOLS = new Set(['Read', 'Edit', 'Write', 'MultiEdit', 'NotebookEdit', 'Grep', 'Glob', 'LS'])

export type Origin = { kind: string; tool?: string; [k: string]: unknown }

export function isSafeTool(tool: string | undefined, safePrefixes: readonly string[]): boolean {
  if (!tool) return false
  return safePrefixes.some(p => p && tool.startsWith(p))
}

export function siteFor(door: string, origin: Origin, safePrefixes: readonly string[], bashSite: 'data' | 'code'): Site {
  if (origin.kind === 'tool') {
    const tool = origin.tool ?? ''
    if (isSafeTool(tool, safePrefixes)) return 'safe'
    if (CODE_TOOLS.has(tool)) return 'code'
    if (tool === 'Bash') return bashSite
    return 'data'
  }
  if (door === 'prompt' || door === 'command' || door === 'delivery') return 'prompt'
  if (door === 'attachment' || door === 'hook-context' || door === 'note') return 'attachment'
  if (door === 'compaction') return 'data'
  return 'data'
}

export function parsePrefixes(value: unknown): string[] {
  if (typeof value !== 'string') return []
  return value.split(',').map(s => s.trim()).filter(Boolean)
}
