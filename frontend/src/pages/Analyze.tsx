import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { createAnalysis, getUserId } from '../api'

export default function Analyze() {
  const [symbol, setSymbol] = useState('AAPL')
  const [status, setStatus] = useState('')
  const navigate = useNavigate()

  async function handleAnalyze(e: React.FormEvent) {
    e.preventDefault()
    const userId = getUserId()
    if (!userId) {
      setStatus('Missing user session. Refresh to re-initialize.')
      return
    }
    try {
      const analysis = await createAnalysis(symbol, userId)
      navigate(`/analysis/${analysis.id}`)
    } catch (err: any) {
      setStatus(err.message)
    }
  }

  return (
    <div className="panel">
      <h2>Run Analysis</h2>
      <form onSubmit={handleAnalyze} className="form-row">
        <input
          value={symbol}
          onChange={(e) => setSymbol(e.target.value.toUpperCase())}
          placeholder="Stock symbol"
        />
        <button type="submit" className="primary">Analyze</button>
      </form>
      {status && <p className="status">{status}</p>}
      <div className="hint">
        Tip: Run `python scripts/seed_demo.py` to preload sample data.
      </div>
    </div>
  )
}
