import { describe, expect, it } from 'vitest'
import { toCamel, toSnake } from './api'

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
    }
    const snake = toSnake(camel) as Record<string, unknown>
    expect(snake.price_min_eur).toBe(400)
    expect(snake.price_max_eur).toBeNull()
    expect(snake.main_locations).toEqual([
      { label: 'Muenchen', place_id: 'ChIJ2V-Mo_l1nkcRfZixfUq4DAE', lat: 48.1351, lng: 11.582 },
    ])
    expect(snake.has_car).toBe(false)
  })
})
