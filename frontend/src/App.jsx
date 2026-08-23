import { useEffect, useState } from 'react'

// Phase 4 connectivity test only. No map, no styling, no libraries beyond
// React — this exists to prove the frontend can reach the deployed backend.
const BACKEND_URL = import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8000'

function App() {
  const [health, setHealth] = useState('checking...')
  const [facilities, setFacilities] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    fetch(`${BACKEND_URL}/health`)
      .then((res) => res.json())
      .then((data) => setHealth(data.status))
      .catch((err) => setError(`health check failed: ${err.message}`))

    fetch(`${BACKEND_URL}/facilities`)
      .then((res) => res.json())
      .then((data) => setFacilities(data))
      .catch((err) => setError(`facilities fetch failed: ${err.message}`))
  }, [])

  return (
    <div>
      <h1>PHC Supply Resilience — Backend Connectivity Test</h1>
      <p>Backend URL: {BACKEND_URL}</p>
      <p>Health: {health}</p>
      {error && <p style={{ color: 'red' }}>{error}</p>}
      {facilities && (
        <div>
          <p>Facility count: {facilities.length}</p>
          <ul>
            {facilities.slice(0, 5).map((f) => (
              <li key={f.id}>{f.name}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

export default App
