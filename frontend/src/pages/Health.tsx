import { useEffect, useState } from 'react'

type HealthBody = { status: string }

export default function HealthPage() {
  const [message, setMessage] = useState<string>('Loading…')

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const res = await fetch('/api/health')
        const data = (await res.json()) as HealthBody
        if (!res.ok) throw new Error(JSON.stringify(data))
        if (!cancelled) {
          setMessage(data.status === 'ok' ? 'status: ok' : `status: ${data.status}`)
        }
      } catch (e) {
        if (!cancelled) setMessage(e instanceof Error ? e.message : String(e))
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  return <p className="p-4 font-sans">{message}</p>
}
