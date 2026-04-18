import { useMapsLibrary } from '@vis.gl/react-google-maps'
import clsx from 'clsx'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { PlaceLocation } from '../types'
import { Input } from './ui'

export type PlaceAutocompleteProps = {
  value: PlaceLocation[]
  onChange: (next: PlaceLocation[]) => void
  id?: string
  placeholder?: string
}

type Prediction = {
  placeId: string
  mainText: string
  secondaryText: string | null
  prediction: google.maps.places.PlacePrediction
}

const COUNTRY_CODES = ['de']

export function PlaceAutocomplete({
  value,
  onChange,
  id,
  placeholder = 'Search for a city, university, or address',
}: PlaceAutocompleteProps) {
  const places = useMapsLibrary('places')
  const [query, setQuery] = useState('')
  const [suggestions, setSuggestions] = useState<Prediction[]>([])
  const [open, setOpen] = useState(false)
  const [highlight, setHighlight] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const sessionTokenRef = useRef<google.maps.places.AutocompleteSessionToken | null>(null)
  const listboxId = useMemo(() => `${id ?? 'place-autocomplete'}-listbox`, [id])
  const containerRef = useRef<HTMLDivElement | null>(null)

  const ensureSessionToken = useCallback(() => {
    if (!places) return null
    if (!sessionTokenRef.current) {
      sessionTokenRef.current = new places.AutocompleteSessionToken()
    }
    return sessionTokenRef.current
  }, [places])

  const resetSessionToken = useCallback(() => {
    sessionTokenRef.current = null
  }, [])

  useEffect(() => {
    if (!places || !query.trim()) {
      setSuggestions([])
      return
    }

    let cancelled = false
    const handle = window.setTimeout(() => {
      void (async () => {
        try {
          setBusy(true)
          setError(null)
          const token = ensureSessionToken()
          const request: google.maps.places.AutocompleteRequest = {
            input: query,
            includedRegionCodes: COUNTRY_CODES,
            ...(token ? { sessionToken: token } : {}),
          }
          const { suggestions: raw } =
            await places.AutocompleteSuggestion.fetchAutocompleteSuggestions(request)
          if (cancelled) return
          const mapped: Prediction[] = raw
            .map((s) => s.placePrediction)
            .filter((p): p is google.maps.places.PlacePrediction => p !== null)
            .map((p) => ({
              placeId: p.placeId,
              mainText: p.mainText?.text ?? p.text.text,
              secondaryText: p.secondaryText?.text ?? null,
              prediction: p,
            }))
          setSuggestions(mapped)
          setHighlight(0)
        } catch (err) {
          if (!cancelled) {
            setError(err instanceof Error ? err.message : 'Autocomplete failed')
            setSuggestions([])
          }
        } finally {
          if (!cancelled) setBusy(false)
        }
      })()
    }, 180)

    return () => {
      cancelled = true
      window.clearTimeout(handle)
    }
  }, [places, query, ensureSessionToken])

  useEffect(() => {
    if (!open) return
    const onDocumentClick = (ev: MouseEvent) => {
      if (!containerRef.current) return
      if (!containerRef.current.contains(ev.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onDocumentClick)
    return () => document.removeEventListener('mousedown', onDocumentClick)
  }, [open])

  const pickSuggestion = useCallback(
    async (pred: Prediction) => {
      try {
        setBusy(true)
        const place = pred.prediction.toPlace()
        await place.fetchFields({ fields: ['location', 'displayName', 'formattedAddress'] })
        const loc = place.location
        if (!loc) return
        const next: PlaceLocation = {
          label:
            place.displayName ??
            place.formattedAddress ??
            pred.mainText +
              (pred.secondaryText ? `, ${pred.secondaryText}` : ''),
          placeId: pred.placeId,
          lat: loc.lat(),
          lng: loc.lng(),
        }
        if (!value.some((v) => v.placeId === next.placeId)) {
          onChange([...value, next])
        }
        setQuery('')
        setSuggestions([])
        setOpen(false)
        resetSessionToken()
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to resolve place')
      } finally {
        setBusy(false)
      }
    },
    [value, onChange, resetSessionToken],
  )

  const removeAt = useCallback(
    (idx: number) => {
      onChange(value.filter((_, i) => i !== idx))
    },
    [value, onChange],
  )

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!open || suggestions.length === 0) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHighlight((h) => Math.min(h + 1, suggestions.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHighlight((h) => Math.max(h - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const pick = suggestions[highlight]
      if (pick) void pickSuggestion(pick)
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
  }

  const sdkMissing = !places
  const apiKeyMissing = !import.meta.env.VITE_GOOGLE_MAPS_API_KEY

  return (
    <div ref={containerRef} className="relative">
      {value.length > 0 && (
        <ul className="mb-2 flex flex-wrap gap-2">
          {value.map((loc, idx) => (
            <li key={loc.placeId}>
              <button
                type="button"
                onClick={() => removeAt(idx)}
                className="inline-flex h-8 items-center gap-1.5 rounded-full border border-accent bg-accent-muted px-3 text-[13px] text-ink transition-colors duration-150 ease-out hover:border-bad hover:text-bad"
                aria-label={`Remove ${loc.label}`}
              >
                <span className="truncate max-w-[240px]">{loc.label}</span>
                <span aria-hidden="true">×</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <Input
        id={id}
        value={query}
        placeholder={apiKeyMissing ? 'Google Maps key missing — see .env' : placeholder}
        disabled={apiKeyMissing}
        onChange={(e) => {
          setQuery(e.target.value)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
        role="combobox"
        aria-expanded={open}
        aria-controls={listboxId}
        aria-autocomplete="list"
        aria-activedescendant={
          open && suggestions[highlight]
            ? `${listboxId}-${suggestions[highlight].placeId}`
            : undefined
        }
      />
      {open && !sdkMissing && (suggestions.length > 0 || busy || error) && (
        <ul
          id={listboxId}
          role="listbox"
          className="absolute z-20 mt-1 max-h-72 w-full overflow-y-auto rounded border border-hairline bg-surface-raised shadow-sm"
        >
          {busy && suggestions.length === 0 && (
            <li className="px-3 py-2 text-[13px] text-ink-muted">Searching…</li>
          )}
          {error && (
            <li className="px-3 py-2 text-[13px] text-bad">{error}</li>
          )}
          {suggestions.map((s, i) => (
            <li
              key={s.placeId}
              id={`${listboxId}-${s.placeId}`}
              role="option"
              aria-selected={i === highlight}
              onMouseEnter={() => setHighlight(i)}
              onMouseDown={(e) => {
                e.preventDefault()
                void pickSuggestion(s)
              }}
              className={clsx(
                'cursor-pointer px-3 py-2 text-[14px]',
                i === highlight ? 'bg-accent-muted text-ink' : 'text-ink',
              )}
            >
              <div className="truncate">{s.mainText}</div>
              {s.secondaryText && (
                <div className="truncate text-[12px] text-ink-muted">
                  {s.secondaryText}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      {sdkMissing && !apiKeyMissing && (
        <p className="mt-1 text-[12px] text-ink-muted">Loading Google Maps…</p>
      )}
    </div>
  )
}
