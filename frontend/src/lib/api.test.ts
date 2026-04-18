import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Action } from '../types'
import {
  ApiError,
  createHunt,
  streamHunt,
  toCamel,
  toSnake,
} from './api'

describe('toCamel / toSnake', () => {
  it('converts keys recursively and preserves primitives', () => {
    const snake = {
      username: 'lea',
      created_at: '2026-04-18T00:00:00',
      nested_thing: { inner_key: [1, 2, { deep_key: 'x' }] },
      already_list: ['a', 'b'],
    }
    const camel = toCamel(snake) as Record<string, unknown>
    expect(camel.username).toBe('lea')
    expect(camel.createdAt).toBe('2026-04-18T00:00:00')
    const nested = camel.nestedThing as { innerKey: unknown[] }
    expect(nested.innerKey[0]).toBe(1)
    expect((nested.innerKey[2] as { deepKey: string }).deepKey).toBe('x')
    expect(camel.alreadyList).toEqual(['a', 'b'])
  })

  it('round-trips through toSnake', () => {
    const camel = {
      priceMinEur: 400,
      priceMaxEur: null,
      mainLocations: [
        { label: 'Muenchen', placeId: 'ChIJ2V-Mo_l1nkcRfZixfUq4DAE', lat: 48.1351, lng: 11.582 },
      ],
      hasCar: false,
      huntId: 'abc',
    }
    const snake = toSnake(camel) as Record<string, unknown>
    expect(snake.price_min_eur).toBe(400)
    expect(snake.price_max_eur).toBeNull()
    expect(snake.main_locations).toEqual([
      { label: 'Muenchen', place_id: 'ChIJ2V-Mo_l1nkcRfZixfUq4DAE', lat: 48.1351, lng: 11.582 },
    ])
    expect(snake.has_car).toBe(false)
    expect(snake.hunt_id).toBe('abc')
  })
})

describe('createHunt', () => {
  const originalFetch = globalThis.fetch

  beforeEach(() => {
    globalThis.fetch = vi.fn()
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
  })

  it('POSTs snake_cased body and returns a camelCased Hunt', async () => {
    const now = '2026-04-18T01:00:00'
    ;(globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          id: 'h1',
          username: 'lea',
          status: 'pending',
          schedule: 'one_shot',
          started_at: now,
          stopped_at: null,
          listings: [],
          actions: [
            {
              at: now,
              kind: 'boot',
              summary: 'Hunt queued (one_shot).',
              detail: null,
              listing_id: null,
            },
          ],
        }),
        { status: 201, headers: { 'Content-Type': 'application/json' } },
      ),
    )

    const hunt = await createHunt('lea', { schedule: 'one_shot' })
    expect(hunt.id).toBe('h1')
    expect(hunt.status).toBe('pending')
    expect(hunt.startedAt).toBe(now)
    expect(hunt.actions[0].listingId).toBeNull()
    expect(hunt.actions[0].kind).toBe('boot')

    const fetchMock = globalThis.fetch as ReturnType<typeof vi.fn>
    expect(fetchMock).toHaveBeenCalledOnce()
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/users/lea/hunts')
    expect(init.method).toBe('POST')
    const body = JSON.parse(init.body as string)
    expect(body).toEqual({ schedule: 'one_shot' })
  })

  it('throws ApiError on a 4xx response', async () => {
    ;(globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'Username already taken' }), {
        status: 409,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    await expect(createHunt('lea', { schedule: 'one_shot' })).rejects.toBeInstanceOf(ApiError)
  })
})

describe('streamHunt', () => {
  class MockEventSource {
    static instances: MockEventSource[] = []
    url: string
    onmessage: ((ev: MessageEvent) => void) | null = null
    onerror: ((ev: Event) => void) | null = null
    closed = false

    constructor(url: string) {
      this.url = url
      MockEventSource.instances.push(this)
    }

    emit(data: unknown): void {
      this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(data) }))
    }

    close(): void {
      this.closed = true
    }
  }

  const originalEventSource = globalThis.EventSource

  beforeEach(() => {
    MockEventSource.instances = []
    ;(globalThis as unknown as { EventSource: typeof MockEventSource }).EventSource =
      MockEventSource
  })

  afterEach(() => {
    globalThis.EventSource = originalEventSource
  })

  it('converts each SSE payload to camelCase before invoking onEvent', () => {
    const received: unknown[] = []
    const close = streamHunt('h1', (ev) => received.push(ev))

    expect(MockEventSource.instances).toHaveLength(1)
    const es = MockEventSource.instances[0]
    expect(es.url).toBe('/api/hunts/h1/stream')

    es.emit({
      at: '2026-04-18T01:00:01',
      kind: 'new_listing',
      summary: 'New listing: 42',
      detail: null,
      listing_id: '42',
    })
    es.emit({ kind: 'stream-end', status: 'done', at: '2026-04-18T01:00:02' })

    expect(received).toHaveLength(2)
    const first = received[0] as Action
    expect(first.kind).toBe('new_listing')
    expect(first.listingId).toBe('42')
    expect((received[1] as { kind: string }).kind).toBe('stream-end')

    close()
    expect(es.closed).toBe(true)
  })
})
