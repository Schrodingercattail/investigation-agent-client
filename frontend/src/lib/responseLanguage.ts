/**
 * Per-turn response language (client mirror of the backend's
 * `app/response_language.py` bounded zh/en rule).
 *
 * The client uses this ONLY for its own UI-generated conversation
 * narratives (the finding/event selection confirmations). The ANSWER
 * language is server-owned — the backend detects it from the current
 * request; nothing here influences it.
 */

export type ResponseLanguage = 'zh' | 'en'

const CJK_RE = /[一-鿿]/g

/** Explicit zh markers (mirror of the backend's _EXPLICIT_ZH tier): the
 * canonical zh case-reference command 调查/查詢 <case> must resolve zh
 * even when the case id dominates the CJK ratio. */
const EXPLICIT_ZH_RE =
    /用中文(回答|回复|说明|解释)|以中文(回答|回复)|中文回答|回答用中文|请?(用|以)中文|调查|查詢/

/** Dominant natural language of one user request (bounded zh/en).
 * Mirrors the backend ratio rule: ≥2 CJK chars at ≥30% of the non-space
 * text is a zh request; otherwise en (technical terms never flip it). */
export function detectResponseLanguage(
  text: string | null | undefined,
): ResponseLanguage {
  const t = (text ?? '').trim()
  if (!t) return 'en'
  if (EXPLICIT_ZH_RE.test(t)) return 'zh'
  const cjk = (t.match(CJK_RE) ?? []).length
  if (cjk === 0) return 'en'
  const nonSpace = t.replace(/\s/g, '').length
  if (cjk >= 2 && nonSpace > 0 && cjk / nonSpace >= 0.3) return 'zh'
  return 'en'
}

/** Selection-confirmation narrative in the conversation's language.
 * Finding/finding-ID labels stay verbatim (data, never translated). */
export function investigatingText(
  lang: ResponseLanguage,
  label: string,
): string {
  return lang === 'zh'
    ? `正在调查 ${label}。`
    : `You're now investigating ${label}.`
}

export function investigatingEventText(
  lang: ResponseLanguage,
  eventId: string,
  label: string,
): string {
  return lang === 'zh'
    ? `正在调查 ${label} 中的事件 ${eventId}。`
    : `You're now investigating event ${eventId} of ${label}.`
}
