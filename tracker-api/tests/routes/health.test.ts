import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import express from 'express'
import type { Server } from 'node:http'
import type { AddressInfo } from 'node:net'

import healthRouter from '../../src/routes/health'

describe('health router', () => {
  let server: Server | undefined
  let baseUrl: string

  beforeAll(async () => {
    const app = express()
    app.use(healthRouter)

    await new Promise<void>((resolve, reject) => {
      const s = app.listen(0, '127.0.0.1', () => resolve())
      s.once('error', reject)
      server = s
    })

    const { port } = (server as Server).address() as AddressInfo
    baseUrl = `http://127.0.0.1:${port}`
  })

  afterAll(async () => {
    const s = server
    if (!s) return
    s.closeAllConnections?.()
    await new Promise<void>((resolve, reject) => {
      s.close((err) => (err ? reject(err) : resolve()))
    })
  })

  it('GET / returns 200 with JSON { status: "ok" }', async () => {
    const res = await fetch(`${baseUrl}/`)

    expect(res.status).toBe(200)
    expect(res.headers.get('content-type')).toMatch(/application\/json/)
    expect(await res.json()).toEqual({ status: 'ok' })
  })

  it('POST / is not handled and returns 404', async () => {
    const res = await fetch(`${baseUrl}/`, { method: 'POST' })
    await res.text()

    expect(res.status).toBe(404)
  })
})
