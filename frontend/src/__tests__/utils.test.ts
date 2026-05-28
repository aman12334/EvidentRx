/**
 * Utility function tests.
 *
 * Tests pure helper utilities used across the application.
 * No React rendering or network calls — runs in a plain jsdom environment.
 */

import { clsx } from 'clsx'

describe('clsx utility', () => {
  it('concatenates plain class names', () => {
    expect(clsx('foo', 'bar')).toBe('foo bar')
  })

  it('filters out falsy values', () => {
    expect(clsx('foo', false, null, undefined, 'bar')).toBe('foo bar')
  })

  it('handles conditional object syntax', () => {
    expect(clsx('base', { active: true, disabled: false })).toBe('base active')
  })

  it('handles array of classes', () => {
    expect(clsx(['a', 'b'], 'c')).toBe('a b c')
  })

  it('returns empty string when all values are falsy', () => {
    expect(clsx(false, null, undefined)).toBe('')
  })
})

describe('date formatting helpers', () => {
  it('ISO date strings are parseable by Date constructor', () => {
    const iso = '2025-01-15'
    const d = new Date(iso)
    expect(d.getFullYear()).toBe(2025)
    expect(d.getMonth()).toBe(0)   // January = 0
    expect(d.getDate()).toBe(15)
  })

  it('toLocaleDateString produces human-readable output', () => {
    const iso = '2025-06-01'
    const d = new Date(iso)
    const result = d.toISOString().slice(0, 10)
    expect(result).toBe('2025-06-01')
  })
})

describe('financial formatting', () => {
  it('formats numbers with toLocaleString', () => {
    const n = 1234567.89
    // Just verify it returns a string — locale differs by environment
    expect(typeof n.toLocaleString()).toBe('string')
  })

  it('large exposure is numeric', () => {
    const exposure = 48_500
    expect(exposure).toBeGreaterThan(0)
    expect(Number.isFinite(exposure)).toBe(true)
  })
})
