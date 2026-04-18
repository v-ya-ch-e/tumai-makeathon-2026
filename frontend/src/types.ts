export type Gender = 'female' | 'male' | 'diverse' | 'prefer_not_to_say'

export type Mode = 'wg' | 'flat' | 'both'

export type Schedule = 'one_shot' | 'periodic'

export type User = {
  username: string
  age: number
  gender: Gender
  createdAt: string
}

export type SearchProfile = {
  priceMinEur: number
  priceMaxEur: number | null
  mainLocations: string[]
  hasCar: boolean
  hasBike: boolean
  mode: Mode
  moveInFrom: string | null
  moveInUntil: string | null
  preferences: string[]
  rescanIntervalMinutes: number
  schedule: Schedule
  updatedAt: string
}

export type UpsertSearchProfileBody = Omit<SearchProfile, 'updatedAt'>

export type CredentialsStatus = {
  connected: boolean
  savedAt: string | null
}

export type AgentStatus = 'idle' | 'running' | 'rescanning' | 'error'

export type HuntStatusBackend = 'pending' | 'running' | 'done' | 'failed'

export type Action = {
  at: string
  kind: string
  summary: string
  detail: string | null
  listingId: string | null
}

export type Listing = {
  id: string
  huntId: string
  url: string
  title: string | null
  district: string | null
  priceEur: number | null
  sizeM2: number | null
  wgSize: number | null
  availableFrom: string | null
  /** Mirrors backend `available_to`. */
  availableTo: string | null
  description: string | null
  score: number | null
  scoreReason: string | null
  matchReasons: string[]
  mismatchReasons: string[]
}

export type Hunt = {
  id: string
  username: string | null
  status: HuntStatusBackend
  schedule: Schedule
  startedAt: string
  stoppedAt: string | null
  listings: Listing[]
  actions: Action[]
}

export type ListingDetail = {
  listing: Listing
  photos: string[]
  score: number | null
}
