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
  /** Timestamp the dashboard "new" badge compares `Listing.firstSeenAt`
   * against. Populated by the backend as `backfill_baseline_at`; clients
   * fall back to `createdAt` when it is null (pre-migration rows). */
  backfillBaselineAt: string | null
}

export type HuntStatus = 'pending' | 'running' | 'done' | 'failed'

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

export type AgentStatus = 'idle' | 'running' | 'rescanning' | 'error'

export type Action = {
  at: string
  kind: string
  summary: string
  detail: string | null
  listingId: string | null
}

export type Hunt = {
  id: string
  status: HuntStatus
  startedAt: string
  finishedAt: string | null
  listings: Listing[]
  actions: Action[]
  error: string | null
  /** Total number of listings scheduled for the silent backfill pass. Null
   * when no backfill is currently running. */
  backfillTotal: number | null
  /** Listings the backfill has already finished. Null when no backfill is
   * currently running, `0..backfillTotal` during a run. */
  backfillDone: number | null
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

/**
 * Per-location commute payload as shipped by the backend DTO
 * (`travel_minutes_per_location`): a map from mode name (e.g.
 * `"transit"`, `"bicycle"`, `"drive"`) to minutes for that mode.
 * Mirrors `dict[str, dict[str, int]]` in `wg_agent/dto.py`.
 */
export type CommuteByMode = Record<string, number>

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
  bestCommuteLabel: string | null
  bestCommuteMode: string | null
  /** Mirrors backend `first_seen_at`. Used (together with
   * `User.createdAt`) to flag listings as "new" in the dashboard for the
   * 24h window after they first appear in the global catalogue. */
  firstSeenAt: string | null
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
  travelMinutesPerLocation: Record<string, CommuteByMode> | null
  nearbyPreferencePlaces: NearbyPlace[]
}

export type TimelineItem = {
  title: string
  date: string
  source: string
  category: TimelineCategory
  urgency: UrgencyLevel
}

export type MapsEntry = {
  id: string
  lat: number
  lng: number
  title: string | null
  score: number | null
  priceEur: number | null
  district: string | null
}
