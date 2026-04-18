import type {
  Action,
  CredentialsStatus,
  Gender,
  Hunt,
  ListingDetail,
  Schedule,
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
}): Promise<User> {
  const data = await requestJson('/api/users', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
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
  body: { age: number; gender: Gender },
): Promise<User> {
  const data = await requestJson(`/api/users/${encodeURIComponent(username)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
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

export async function getCredentialsStatus(username: string): Promise<CredentialsStatus> {
  const res = await fetch(
    `/api/users/${encodeURIComponent(username)}/credentials`,
    fetchDefaults,
  )
  const body = await readBody(res)
  if (!res.ok) {
    throw new ApiError(res.status, body, errorMessage(body))
  }
  return toCamel(body) as CredentialsStatus
}

export async function putCredentials(
  username: string,
  body: { email: string; password: string } | { storageState: object },
): Promise<void> {
  const jsonBody =
    'storageState' in body
      ? { storage_state: body.storageState }
      : { email: body.email, password: body.password }
  const res = await fetch(
    `/api/users/${encodeURIComponent(username)}/credentials`,
    {
      ...fetchDefaults,
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(jsonBody),
    },
  )
  const resBody = await readBody(res)
  if (!res.ok) {
    throw new ApiError(res.status, resBody, errorMessage(resBody))
  }
}

export async function deleteCredentials(username: string): Promise<void> {
  const res = await fetch(
    `/api/users/${encodeURIComponent(username)}/credentials`,
    { ...fetchDefaults, method: 'DELETE' },
  )
  const resBody = await readBody(res)
  if (!res.ok) {
    throw new ApiError(res.status, resBody, errorMessage(resBody))
  }
}

export async function createHunt(
  username: string,
  body: { schedule: Schedule; rescanIntervalMinutes?: number },
): Promise<Hunt> {
  const data = await requestJson(
    `/api/users/${encodeURIComponent(username)}/hunts`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(toSnake(body)),
    },
  )
  return data as Hunt
}

export async function stopHunt(huntId: string): Promise<Hunt> {
  const data = await requestJson(`/api/hunts/${encodeURIComponent(huntId)}/stop`, {
    method: 'POST',
  })
  return data as Hunt
}

export async function getHunt(huntId: string): Promise<Hunt | null> {
  const res = await fetch(`/api/hunts/${encodeURIComponent(huntId)}`, fetchDefaults)
  const body = await readBody(res)
  if (res.status === 404) {
    return null
  }
  if (!res.ok) {
    throw new ApiError(res.status, body, errorMessage(body))
  }
  return toCamel(body) as Hunt
}

export async function getListingDetail(
  listingId: string,
  huntId: string,
): Promise<ListingDetail | null> {
  const q = new URLSearchParams({ hunt_id: huntId })
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

export function streamHunt(
  huntId: string,
  onEvent: (
    a:
      | Action
      | { kind: 'stream-end'; status: string; at: string; summary?: string },
  ) => void,
  onError?: (err: unknown) => void,
): () => void {
  const es = new EventSource(`/api/hunts/${encodeURIComponent(huntId)}/stream`)
  es.onmessage = (e) => {
    try {
      const raw = JSON.parse(e.data) as unknown
      onEvent(toCamel(raw) as Action | { kind: 'stream-end'; status: string; at: string })
    } catch (err) {
      onError?.(err)
    }
  }
  es.onerror = (e) => onError?.(e)
  return () => es.close()
}
