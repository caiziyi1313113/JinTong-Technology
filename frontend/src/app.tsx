import { useEffect, useMemo, useState } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import Header from './components/Header'
import { clearSession, getCurrentUser, getToken, setUserId } from './api'
import Discover from './pages/Discover'
import Home from './pages/Home'
import Login from './pages/Login'
import QueryStocks from './pages/QueryStocks'
import TrackStocks from './pages/TrackStocks'

export default function App() {
  const [ready, setReady] = useState(false)
  const [authenticated, setAuthenticated] = useState(false)
  const [status, setStatus] = useState('')
  const location = useLocation()

  useEffect(() => {
    async function init() {
      const token = getToken()
      if (!token) {
        setAuthenticated(false)
        setReady(true)
        return
      }

      try {
        const me = await getCurrentUser()
        setUserId(String(me.id))
        setAuthenticated(true)
      } catch {
        clearSession()
        setAuthenticated(false)
      } finally {
        setReady(true)
      }
    }

    init()
  }, [])

  const showHeader = useMemo(() => {
    if (!authenticated) {
      return false
    }
    return ['/discover', '/track', '/query'].some((path) => location.pathname.startsWith(path))
  }, [authenticated, location.pathname])

  function handleLoginSuccess(userId: string) {
    setUserId(userId)
    setAuthenticated(true)
    setStatus('')
  }

  function handleLogout() {
    clearSession()
    setAuthenticated(false)
    setStatus('Logged out.')
  }

  if (!ready) {
    return <div className="boot-screen">Initializing session...</div>
  }

  return (
    <div className="app">
      {showHeader && <Header onLogout={handleLogout} />}
      {status && <div className="global-status">{status}</div>}
      <main className="main-stage">
        <Routes>
          <Route path="/" element={<Navigate to="/home" replace />} />
          <Route path="/home" element={<Home authenticated={authenticated} />} />
          <Route
            path="/login"
            element={
              authenticated ? (
                <Navigate to="/discover" replace />
              ) : (
                <Login onLoginSuccess={handleLoginSuccess} onStatus={setStatus} />
              )
            }
          />

          <Route
            path="/discover"
            element={authenticated ? <Discover /> : <Navigate to="/login" replace />}
          />
          <Route
            path="/track"
            element={authenticated ? <TrackStocks /> : <Navigate to="/login" replace />}
          />
          <Route
            path="/query"
            element={authenticated ? <QueryStocks /> : <Navigate to="/login" replace />}
          />

          <Route path="*" element={<Navigate to="/home" replace />} />
        </Routes>
      </main>
    </div>
  )
}
