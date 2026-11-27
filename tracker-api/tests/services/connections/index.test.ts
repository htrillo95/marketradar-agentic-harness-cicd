import { describe, expect, it } from 'vitest'

import {
  getConnectionService,
  listConnectionServices,
} from '../../../src/services/connections'
import { facebookConnectionService } from '../../../src/services/connections/facebook'

describe('getConnectionService', () => {
  it('returns the facebook connection service for "facebook"', () => {
    expect(getConnectionService('facebook')).toBe(facebookConnectionService)
  })

  it.each([
    'toString',
    'constructor',
    '__proto__',
    'hasOwnProperty',
    'craigslist',
    '',
  ])('returns null for unregistered provider id %j', (providerId) => {
    expect(getConnectionService(providerId)).toBeNull()
  })
})

describe('listConnectionServices', () => {
  it('returns exactly the facebook connection service', () => {
    const services = listConnectionServices()

    expect(services).toEqual([facebookConnectionService])
    expect(services).toHaveLength(1)
    expect(services[0]).toBe(facebookConnectionService)
  })
})
