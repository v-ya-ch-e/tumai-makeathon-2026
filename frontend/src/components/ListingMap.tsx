import { AdvancedMarker, Map as GoogleMap } from '@vis.gl/react-google-maps'
import { useMemo, useState } from 'react'
import type { Listing } from '../types'

type MapsEntry = {
  id: string
  lat: number
  lng: number
  title: string | null
  score: number | null
  priceEur: number | null
  district: string | null
}

const MUNICH = { lat: 48.1351, lng: 11.582 }
const API_KEY = import.meta.env.VITE_GOOGLE_MAPS_API_KEY as string | undefined

type ListingMapProps = {
  listings: Listing[]
  onOpen: (listing: Listing) => void
}

function markerColor(score: number | null): string {
  if (score === null) return '#9ca3af'
  if (score >= 0.7) return '#56745f'
  if (score >= 0.4) return '#b08d57'
  return '#8d4250'
}

function MarkerDot({ score, hovered }: { score: number | null; hovered: boolean }) {
  return (
    <div
      style={{
        width: 14,
        height: 14,
        borderRadius: '50%',
        background: markerColor(score),
        border: '2px solid white',
        boxShadow: '0 1px 4px rgba(0,0,0,0.3)',
        transform: hovered ? 'scale(1.5)' : 'scale(1)',
        transition: 'transform 120ms ease',
        cursor: 'pointer',
      }}
    />
  )
}

export function ListingMap({ listings, onOpen }: ListingMapProps) {
  const [hoveredId, setHoveredId] = useState<string | null>(null)

  const entries = useMemo<MapsEntry[]>(
    () =>
      listings
        .filter((l): l is Listing & { lat: number; lng: number } => l.lat !== null && l.lng !== null)
        .map((l) => ({
          id: l.id,
          lat: l.lat,
          lng: l.lng,
          title: l.title,
          score: l.score,
          priceEur: l.priceEur,
          district: l.district,
        })),
    [listings],
  )

  const listingById = useMemo(() => new globalThis.Map(listings.map((l) => [l.id, l] as [string, Listing])), [listings])

  const hoveredEntry = hoveredId ? entries.find((e) => e.id === hoveredId) ?? null : null

  if (!API_KEY) {
    return (
      <div className="flex h-[600px] w-full items-center justify-center rounded border border-hairline bg-surface-raised">
        <p className="text-[13px] text-ink-muted">Map unavailable — Google Maps key not set</p>
      </div>
    )
  }

  return (
    <div className="relative h-[600px] w-full overflow-hidden rounded border border-hairline">
      {/* mapId is required for AdvancedMarker; any non-empty string works without a Cloud Console Map ID */}
      <GoogleMap
        defaultCenter={MUNICH}
        defaultZoom={12}
        mapId="wg-hunter-map"
        gestureHandling="greedy"
        disableDefaultUI={false}
      >
        {entries.map((entry) => (
          <AdvancedMarker
            key={entry.id}
            position={{ lat: entry.lat, lng: entry.lng }}
            onClick={() => {
              const listing = listingById.get(entry.id)
              if (listing) onOpen(listing)
            }}
            onMouseEnter={() => setHoveredId(entry.id)}
            onMouseLeave={() => setHoveredId(null)}
          >
            <MarkerDot score={entry.score} hovered={hoveredId === entry.id} />
          </AdvancedMarker>
        ))}
      </GoogleMap>

      {hoveredEntry && (
        <div className="pointer-events-none absolute bottom-4 left-4 z-10 max-w-[240px] rounded border border-hairline bg-surface px-3 py-2 shadow-[0_2px_8px_rgba(0,0,0,0.12)]">
          <p className="truncate text-[13px] font-medium text-ink">
            {hoveredEntry.title ?? `Listing ${hoveredEntry.id}`}
          </p>
          <div className="mt-1 flex flex-wrap gap-2 text-[12px] text-ink-muted">
            {hoveredEntry.score !== null && (
              <span
                style={{ color: markerColor(hoveredEntry.score) }}
                className="font-medium"
              >
                {Math.round(hoveredEntry.score * 100)}%
              </span>
            )}
            {hoveredEntry.priceEur !== null && <span>{hoveredEntry.priceEur} EUR</span>}
            {hoveredEntry.district && <span>{hoveredEntry.district}</span>}
          </div>
        </div>
      )}

      {entries.length < listings.length && (
        <p className="absolute right-3 top-3 z-10 rounded border border-hairline bg-surface px-2 py-1 text-[11px] text-ink-muted">
          {entries.length} of {listings.length} listings have coordinates
        </p>
      )}
    </div>
  )
}
