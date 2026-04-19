import type {
  Action,
  Gender,
  Hunt,
  Listing,
  ListingDetail,
  SearchProfile,
  TimelineCategory,
  TimelineItem,
  UpsertSearchProfileBody,
  User,
} from '../types'

function snakeToCamelKey(key: string): string {
  return key.replace(/_([a-z])/g, (_, c: string) => c.toUpperCase())
}

function camelToSnakeKey(key: string): string {
  return key.replace(/[A-Z]/g, (letter) => `_${letter.toLowerCase()}`)
}

export function toCamel<T>(obj: unknown): T {
  if (obj === null || obj === undefined) {
    return obj as T
  }
  if (Array.isArray(obj)) {
    return obj.map((item) => toCamel(item)) as T
  }
  if (typeof obj !== 'object') {
    return obj as T
  }
  const record = obj as Record<string, unknown>
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(record)) {
    const nk = snakeToCamelKey(k)
    if (v !== null && typeof v === 'object' && !Array.isArray(v)) {
      out[nk] = toCamel(v)
    } else if (Array.isArray(v)) {
      out[nk] = v.map((item) =>
        item !== null && typeof item === 'object' ? toCamel(item) : item,
      )
    } else {
      out[nk] = v
    }
  }
  return out as T
}

export function toSnake<T>(obj: unknown): T {
  if (obj === null || obj === undefined) {
    return obj as T
  }
  if (Array.isArray(obj)) {
    return obj.map((item) => toSnake(item)) as T
  }
  if (typeof obj !== 'object') {
    return obj as T
  }
  const record = obj as Record<string, unknown>
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(record)) {
    const nk = camelToSnakeKey(k)
    if (v !== null && typeof v === 'object' && !Array.isArray(v)) {
      out[nk] = toSnake(v)
    } else if (Array.isArray(v)) {
      out[nk] = v.map((item) =>
        item !== null && typeof item === 'object' ? toSnake(item) : item,
      )
    } else {
      out[nk] = v
    }
  }
  return out as T
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public body: unknown,
    message?: string,
  ) {
    super(message ?? `HTTP ${status}`)
  }
}

const fetchDefaults: RequestInit = { credentials: 'same-origin' }

async function readBody(res: Response): Promise<unknown> {
  if (res.status === 204) {
    return undefined
  }
  const ct = res.headers.get('content-type') ?? ''
  if (ct.includes('application/json')) {
    const text = await res.text()
    if (!text) {
      return undefined
    }
    return JSON.parse(text) as unknown
  }
  return (await res.text()) as unknown
}

async function requestJson(
  input: RequestInfo,
  init?: RequestInit,
): Promise<unknown> {
  const res = await fetch(input, { ...fetchDefaults, ...init })
  const body = await readBody(res)
  if (!res.ok) {
    throw new ApiError(res.status, body, errorMessage(body))
  }
  if (body === undefined) {
    return undefined
  }
  return toCamel(body)
}

function errorMessage(body: unknown): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const d = (body as { detail: unknown }).detail
    if (typeof d === 'string') {
      return d
    }
    try {
      return JSON.stringify(d)
    } catch {
      return 'Request failed'
    }
  }
  if (typeof body === 'string') {
    return body
  }
  try {
    return JSON.stringify(body ?? 'Request failed')
  } catch {
    return 'Request failed'
  }
}

export async function createUser(body: {
  username: string
  age: number
  gender: Gender
  email: string | null
}): Promise<User> {
  const data = await requestJson('/api/users', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(toSnake(body)),
  })
  return data as User
}

export async function getUser(username: string): Promise<User | null> {
  const res = await fetch(`/api/users/${encodeURIComponent(username)}`, fetchDefaults)
  const body = await readBody(res)
  if (res.status === 404) {
    return null
  }
  if (!res.ok) {
    throw new ApiError(res.status, body, errorMessage(body))
  }
  return toCamel(body) as User
}

export async function updateUser(
  username: string,
  body: { age: number; gender: Gender; email: string | null },
): Promise<User> {
  const data = await requestJson(`/api/users/${encodeURIComponent(username)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(toSnake(body)),
  })
  return data as User
}

export async function putSearchProfile(
  username: string,
  body: UpsertSearchProfileBody,
): Promise<SearchProfile> {
  const data = await requestJson(
    `/api/users/${encodeURIComponent(username)}/search-profile`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(toSnake(body)),
    },
  )
  return data as SearchProfile
}

export async function getSearchProfile(username: string): Promise<SearchProfile | null> {
  const res = await fetch(
    `/api/users/${encodeURIComponent(username)}/search-profile`,
    fetchDefaults,
  )
  const body = await readBody(res)
  if (res.status === 404) {
    return null
  }
  if (!res.ok) {
    throw new ApiError(res.status, body, errorMessage(body))
  }
  return toCamel(body) as SearchProfile
}

export async function getUserListings(username: string): Promise<Listing[]> {
  const data = await requestJson(
    `/api/users/${encodeURIComponent(username)}/listings`,
  )
  return data as Listing[]
}

function huntIdForUser(username: string): string {
  return `user:${encodeURIComponent(username)}`
}

function usernameFromHuntId(huntId: string): string | null {
  if (!huntId.startsWith('user:')) return null
  try {
    return decodeURIComponent(huntId.slice(5))
  } catch {
    return null
  }
}

async function buildHunt(username: string): Promise<Hunt> {
  const [status, listings, actions] = await Promise.all([
    getAgentStatus(username),
    getUserListings(username),
    getUserActions(username, 200),
  ])
  const running = status.running
  return {
    id: huntIdForUser(username),
    status: running ? 'running' : 'done',
    startedAt: actions[0]?.at ?? new Date().toISOString(),
    finishedAt: running ? null : actions.at(-1)?.at ?? null,
    listings,
    actions,
    error: null,
    backfillTotal: status.backfillTotal ?? null,
    backfillDone: status.backfillDone ?? null,
  }
}

export async function createHunt(
  username: string,
  _body: { schedule: SearchProfile['schedule'] },
): Promise<Hunt> {
  await startAgent(username)
  return buildHunt(username)
}

export async function getHunt(huntId: string): Promise<Hunt | null> {
  const username = usernameFromHuntId(huntId)
  if (!username) return null
  return buildHunt(username)
}

export async function stopHunt(huntId: string): Promise<Hunt> {
  const username = usernameFromHuntId(huntId)
  if (!username) {
    throw new ApiError(400, { detail: 'Invalid hunt id' }, 'Invalid hunt id')
  }
  await pauseAgent(username)
  return buildHunt(username)
}

export async function getUserActions(
  username: string,
  limit?: number,
): Promise<Action[]> {
  const q = new URLSearchParams()
  if (limit !== undefined) {
    q.set('limit', String(limit))
  }
  const suffix = q.toString() ? `?${q.toString()}` : ''
  const data = await requestJson(
    `/api/users/${encodeURIComponent(username)}/actions${suffix}`,
  )
  return data as Action[]
}

export type AgentStatusDTO = {
  running: boolean
  backfillTotal: number | null
  backfillDone: number | null
}

export async function getAgentStatus(username: string): Promise<AgentStatusDTO> {
  const data = await requestJson(
    `/api/users/${encodeURIComponent(username)}/agent`,
  )
  const payload = (data ?? {}) as Partial<AgentStatusDTO>
  return {
    running: Boolean(payload.running),
    backfillTotal: payload.backfillTotal ?? null,
    backfillDone: payload.backfillDone ?? null,
  }
}

export async function startAgent(username: string): Promise<void> {
  await requestJson(
    `/api/users/${encodeURIComponent(username)}/agent/start`,
    { method: 'POST' },
  )
}

export async function pauseAgent(username: string): Promise<void> {
  await requestJson(
    `/api/users/${encodeURIComponent(username)}/agent/pause`,
    { method: 'POST' },
  )
}

export async function getListingDetail(
  listingId: string,
  username: string,
): Promise<ListingDetail | null> {
  const q = new URLSearchParams({ username })
  const res = await fetch(
    `/api/listings/${encodeURIComponent(listingId)}?${q.toString()}`,
    fetchDefaults,
  )
  const body = await readBody(res)
  if (res.status === 404) {
    return null
  }
  if (!res.ok) {
    throw new ApiError(res.status, body, errorMessage(body))
  }
  return toCamel(body) as ListingDetail
}

export async function getTimelineItems(
  category?: TimelineCategory,
): Promise<TimelineItem[]> {
  const q = new URLSearchParams()
  if (category) {
    q.set('category', category)
  }
  const suffix = q.toString() ? `?${q.toString()}` : ''
  const data = await requestJson(`/api/deadline/timeline${suffix}`)
  return data as TimelineItem[]
}

export function streamUser(
  username: string,
  onEvent: (action: Action) => void,
  onError?: (err: unknown) => void,
): () => void {
  const es = new EventSource(
    `/api/users/${encodeURIComponent(username)}/stream`,
  )
  es.onmessage = (e) => {
    try {
      const raw = JSON.parse(e.data) as unknown
      onEvent(toCamel(raw) as Action)
    } catch (err) {
      onError?.(err)
    }
  }
  es.onerror = (e) => onError?.(e)
  return () => es.close()
}

export function streamHunt(
  huntId: string,
  onEvent: (action: Action) => void,
  onError?: (err: unknown) => void,
): () => void {
  const username = usernameFromHuntId(huntId)
  if (!username) {
    throw new ApiError(400, { detail: 'Invalid hunt id' }, 'Invalid hunt id')
  }
  return streamUser(username, onEvent, onError)
}
