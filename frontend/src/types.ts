export type Gender = 'female' | 'male' | 'diverse' | 'prefer_not_to_say'

export type Mode = 'wg' | 'flat' | 'both'

export type Schedule = 'one_shot' | 'periodic'
export type TimelineCategory = 'deadline' | 'course' | 'sport' | 'event'
export type UrgencyLevel = 'high' | 'medium' | 'low'

export type User = {
  username: string
  age: number
  gender: Gender
  email: string | null
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

export type NearbyPlace = {
  key: string
  label: string
  searched: boolean
  distanceM: number | null
  placeName: string | null
  category: string | null
}

export type Listing = {
  id: string
  username: string | null
  url: string
  title: string | null
  /** `'wg'` (room in a shared flat) or `'flat'` (whole apartment). Optional
   * for backwards-compatibility with payloads that pre-date the column. */
  kind?: 'wg' | 'flat'
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
  bestCommuteMinutes: number | null
  score: number | null
  scoreReason: string | null
  matchReasons: string[]
  mismatchReasons: string[]
  components: Component[]
  vetoReason: string | null
}

export type ListingDetail = {
  listing: Listing
  photos: string[]
  score: number | null
  travelMinutesPerLocation: Record<string, number> | null
  nearbyPreferencePlaces: NearbyPlace[]
}

export type TimelineItem = {
  title: string
  date: string
  source: string
  category: TimelineCategory
  urgency: UrgencyLevel
}
