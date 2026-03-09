import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { getCurrentUser, login, register, setToken } from '../api'

type LoginProps = {
  onLoginSuccess: (userId: string) => void
  onStatus: (message: string) => void
}

export default function Login({ onLoginSuccess, onStatus }: LoginProps) {
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [loading, setLoading] = useState(false)
  const [localError, setLocalError] = useState('')

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setLocalError('')
    setLoading(true)
    try {
      if (mode === 'register') {
        await register(email, password)
      }

      const token = await login(email, password)
      setToken(token.access_token)
      const me = await getCurrentUser()
      onLoginSuccess(String(me.id))
      onStatus(mode === 'register' ? 'Register and login success.' : 'Login success.')
      navigate('/discover', { replace: true })
    } catch (err: unknown) {
      const message = (err as Error).message || 'Login failed'
      setLocalError(message)
      onStatus('')
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className="login-page reveal-up">
      <div className="login-panel">
        <h1>{mode === 'login' ? 'User Login' : 'User Register'}</h1>
        <form onSubmit={handleSubmit}>
          <label>
            Email
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              required
            />
          </label>
          <label>
            Password
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Enter password"
              required
            />
          </label>

          <button type="submit" className="login-submit" disabled={loading}>
            {loading ? 'Processing...' : mode === 'login' ? 'Login' : 'Register + Login'}
          </button>
        </form>

        <div className="login-actions">
          <button
            type="button"
            className="text-action"
            onClick={() => setMode(mode === 'login' ? 'register' : 'login')}
          >
            {mode === 'login' ? 'No account? Register' : 'Have account? Login'}
          </button>
          <Link to="/home" className="text-action">Back to home</Link>
        </div>

        {localError && <div className="login-error">{localError}</div>}
      </div>
    </section>
  )
}
