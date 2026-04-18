export type Gender = 'female' | 'male' | 'diverse' | 'prefer_not_to_say'

export type Mode = 'wg' | 'flat' | 'both'

export type Schedule = 'one_shot' | 'periodic'

export type User = {
  username: string
  age: number
  gender: Gender
  createdAt: string
}

export type PlaceLocation = {
  label: string
  placeId: string
  lat: number
  lng: number
  maxCommuteMinutes: number | null
}

export type PreferenceWeight = {
  key: string
  weight: number
}

export type SearchProfile = {
  priceMinEur: number
  priceMaxEur: number | null
  mainLocations: PlaceLocation[]
  hasCar: boolean
  hasBike: boolean
  mode: Mode
  moveInFrom: string | null
  moveInUntil: string | null
  preferences: PreferenceWeight[]
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

export type Component = {
  key: string
  score: number
  weight: number
  evidence: string[]
  hardCap: number | null
  missingData: boolean
}

export type Listing = {
  id: string
  huntId: string
  url: string
  title: string | null
  district: string | null
  lat: number | null
  lng: number | null
  priceEur: number | null
  sizeM2: number | null
  wgSize: number | null
  availableFrom: string | null
  /** Mirrors backend `available_to`. */
  availableTo: string | null
  description: string | null
  coverPhotoUrl: string | null
  score: number | null
  scoreReason: string | null
  matchReasons: string[]
  mismatchReasons: string[]
  components: Component[]
  vetoReason: string | null
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
  travelMinutesPerLocation: Record<string, number> | null
}
