import { describe, expect, it, vi } from 'vitest'

vi.mock('../../src/lib/prisma', () => ({
  prisma: {},
}))

import {
  buildMarketplaceSearchUrl,
  ScraperError,
  type MarketplaceSearchParams,
} from '../../src/services/facebook-scraper'

const baseParams: MarketplaceSearchParams = {
  query: 'bike',
  location: 'Philadelphia',
  radius: 65,
}

function buildUrl(overrides: Partial<MarketplaceSearchParams> = {}): URL {
  return new URL(buildMarketplaceSearchUrl({ ...baseParams, ...overrides }))
}

describe('buildMarketplaceSearchUrl', () => {
  describe('location slug', () => {
    it('targets the Facebook Marketplace origin', () => {
      expect(buildUrl().origin).toBe('https://www.facebook.com')
    })

    it('lowercases the location and removes spaces', () => {
      const url = buildUrl({ location: 'New York City' })

      expect(url.pathname).toBe('/marketplace/newyorkcity/search/')
    })

    it('removes tabs, newlines and leading/trailing whitespace from mixed-case locations', () => {
      const url = buildUrl({ location: ' Phil  ADELPHIA\t\n' })

      expect(url.pathname).toBe('/marketplace/philadelphia/search/')
    })
  })

  describe('search parameters', () => {
    it('includes the query as a search parameter', () => {
      expect(buildUrl({ query: 'bike' }).searchParams.get('query')).toBe('bike')
    })

    it('preserves spaces in the query after decoding', () => {
      expect(buildUrl({ query: 'gsxr 1000' }).searchParams.get('query')).toBe(
        'gsxr 1000',
      )
    })

    it('sets exact=false', () => {
      expect(buildUrl().searchParams.get('exact')).toBe('false')
    })

    it('includes the radius as a search parameter', () => {
      expect(buildUrl({ radius: 65 }).searchParams.get('radius')).toBe('65')
    })
  })

  describe('maxPrice', () => {
    it('includes maxPrice when it is a positive number', () => {
      expect(buildUrl({ maxPrice: 6500 }).searchParams.get('maxPrice')).toBe(
        '6500',
      )
    })

    it.each([
      { label: 'null', maxPrice: null },
      { label: 'undefined', maxPrice: undefined },
      { label: '0', maxPrice: 0 },
    ])('omits maxPrice when it is $label', ({ maxPrice }) => {
      expect(buildUrl({ maxPrice }).searchParams.has('maxPrice')).toBe(false)
    })

    it('omits maxPrice when the key is not provided', () => {
      const url = new URL(buildMarketplaceSearchUrl({ ...baseParams }))

      expect(url.searchParams.has('maxPrice')).toBe(false)
    })
  })
})

describe('ScraperError', () => {
  it('is an Error with the ScraperError name and the given message', () => {
    const err = new ScraperError('boom')

    expect(err).toBeInstanceOf(ScraperError)
    expect(err).toBeInstanceOf(Error)
    expect(err.name).toBe('ScraperError')
    expect(err.message).toBe('boom')
  })

  it('keeps the same cause object when one is provided', () => {
    const cause = new Error('root')
    const err = new ScraperError('boom', { cause })

    expect(err.cause).toBe(cause)
  })

  it('has an undefined cause when no options are provided', () => {
    const err = new ScraperError('boom')

    expect(err.cause).toBeUndefined()
  })
})
