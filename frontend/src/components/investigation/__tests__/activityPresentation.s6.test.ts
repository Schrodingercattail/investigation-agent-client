// WP-2 Version B S6 (user-approved adjusted scope, item A) — mixed
// outcome mapping unit tests for resolveActivityPresentation.
//
// SCOPE HONESTY (user-approved adjustment, kickoff §13.10): these tests
// verify the FRONTEND MAPPING LOGIC ONLY, over explicitly constructed
// synthetic inputs. They do NOT claim and CANNOT prove that the backend
// execution can produce a mixed multi-target outcome — that capability
// is V1-NOT-SUPPORTED and its real-browser verification is DEFERRED
// (kickoff §13.10 item C).
//
// The mixed terminal combination produced by the Version B design is:
//   agent_status=completed, stop_reason=clarification_needed,
//   goal_outcome=partial
// (admitted targets delivered + admission-rejected targets pending
// clarification). The mapping must render it as a PARTIAL result with
// the clarification hint — never as success, never collapsed into a
// generic completion label.

import { describe, expect, it } from 'vitest'
import { resolveActivityPresentation } from '../activityPresentation'

const MIXED_TERMINAL = {
  agent_status: 'completed',
  stop_reason: 'clarification_needed',
  goal_outcome: 'partial',
  is_terminal: true,
} as const

describe('S6: mixed-terminal outcome mapping (WP-2 Version B)', () => {
  it('maps the mixed terminal (completed + clarification_needed + partial) to a PARTIAL presentation — never success', () => {
    const p = resolveActivityPresentation(MIXED_TERMINAL)
    expect(p.tone).toBe('partial')
    expect(p.label).toMatch(/Partially completed/)
    expect(p.tone).not.toBe('success')
  })

  it('carries the clarification hint for the mixed terminal (the pending part is why it is partial)', () => {
    const p = resolveActivityPresentation(MIXED_TERMINAL)
    expect(p.hint).toContain('clarification')
  })

  it('keeps the mixed terminal PARTIAL even if extra unknown fields ride along (conservative total mapping)', () => {
    const p = resolveActivityPresentation({
      ...MIXED_TERMINAL,
      is_terminal: true,
    })
    expect(p.tone).toBe('partial')
  })

  it('is order-insensitive to field destructuring: same verdict when fields are read in a different order', () => {
    // the mapping must depend only on the VALUES, not on property order
    const a = resolveActivityPresentation({
      agent_status: MIXED_TERMINAL.agent_status,
      stop_reason: MIXED_TERMINAL.stop_reason,
      goal_outcome: MIXED_TERMINAL.goal_outcome,
      is_terminal: true,
    })
    const reordered = {
      goal_outcome: MIXED_TERMINAL.goal_outcome,
      is_terminal: true,
      agent_status: MIXED_TERMINAL.agent_status,
      stop_reason: MIXED_TERMINAL.stop_reason,
    }
    const b = resolveActivityPresentation(reordered)
    expect(a).toEqual(b)
  })

  it('still maps the pre-existing adjacent combos exactly as the product contract requires (regression guards around the mixed pin)', () => {
    // blocked + clarification: the BLOCKED terminal keeps its own tone
    const blocked = resolveActivityPresentation({
      agent_status: 'blocked',
      stop_reason: 'clarification_needed',
      goal_outcome: 'partial',
      is_terminal: true,
    })
    expect(blocked.tone).toBe('blocked')

    // bounded partial stays the data-limited partial (never success)
    const bounded = resolveActivityPresentation({
      agent_status: 'completed',
      stop_reason: 'bounded_data',
      goal_outcome: 'partial',
      is_terminal: true,
    })
    expect(bounded.tone).toBe('partial')
    expect(bounded.label).toMatch(/data range limited/)

    // goal_satisfied + capped partial stays the heuristic-contract
    // success presentation (U00047 calibration, untouched by S6)
    const capped = resolveActivityPresentation({
      agent_status: 'completed',
      stop_reason: 'goal_satisfied',
      goal_outcome: 'partial',
      is_terminal: true,
    })
    expect(capped.tone).toBe('success')
  })
})
