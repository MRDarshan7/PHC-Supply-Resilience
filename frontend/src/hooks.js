import { useEffect, useState } from 'react'

// Seconds since `active` became true; 0 while inactive. Drives the
// "still waiting" copy on long fetches.
export function useElapsed(active) {
  const [sec, setSec] = useState(0)
  useEffect(() => {
    if (!active) return undefined
    const t0 = Date.now()
    const id = setInterval(() => setSec(Math.floor((Date.now() - t0) / 1000)), 1000)
    return () => clearInterval(id)
  }, [active])
  return active ? sec : 0
}

// True when the viewport matches `query`; tracks changes (rotation, resize).
export function useMediaQuery(query) {
  const get = () => (typeof window !== 'undefined' && window.matchMedia ? window.matchMedia(query).matches : false)
  const [matches, setMatches] = useState(get)
  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return undefined
    const mq = window.matchMedia(query)
    const onChange = () => setMatches(mq.matches)
    onChange()
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [query])
  return matches
}

export const PHONE_QUERY = '(max-width: 640px)'
