import { describe, expect, it, vi } from 'vitest'

vi.mock('../../src/lib/prisma', () => ({
  prisma: {},
}))

import { RunSearchError } from '../../src/services/search-runner'

describe('RunSearchError', () => {
  it('is an Error with the RunSearchError name and given message', () => {
    const err = new RunSearchError('boom')

    expect(err).toBeInstanceOf(RunSearchError)
    expect(err).toBeInstanceOf(Error)
    expect(err.name).toBe('RunSearchError')
    expect(err.message).toBe('boom')
  })

  it('preserves the cause passed in options', () => {
    const cause = new Error('root')
    const err = new RunSearchError('boom', { cause })

    expect(err.cause).toBe(cause)
  })

  it('has an undefined cause when no options are given', () => {
    const err = new RunSearchError('boom')

    expect(err.cause).toBeUndefined()
  })
})
