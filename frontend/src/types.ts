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
  // Matcher v2 (MATCHER.md §2.1, §5.6, §3.4). Wizard does not yet expose
  // these; the engine degrades gracefully when they are null.
  desiredMinMonths?: number | null
  flatmateSelfGender?: Gender | null
  flatmateSelfAge?: number | null
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

export type CommuteInfo = {
  minutes: number
  mode: string
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
  bestCommuteLabel: string | null
  bestCommuteMode: string | null
  score: number | null
  scoreReason: string | null
  matchReasons: string[]
  mismatchReasons: string[]
  components: Component[]
  vetoReason: string | null
  // Matcher v2 (MATCHER.md §2.2). Drawer can show "+20% Kalt uplift" badge
  // and per-listing upfront-cost evidence. Optional everywhere so legacy
  // payloads stay valid.
  priceBasis?: 'warm' | 'kalt_uplift' | 'unknown' | null
  depositMonths?: number | null
  furnitureBuyoutEur?: number | null
}

export type ListingDetail = {
  listing: Listing
  photos: string[]
  score: number | null
  travelMinutesPerLocation: Record<string, CommuteInfo> | null
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
