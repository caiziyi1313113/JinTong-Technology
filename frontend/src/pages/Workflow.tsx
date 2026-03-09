import { useMemo, useState } from 'react'
import { runPostCloseReview, runPreOpenScan } from '../api'

type Recap = {
  id: number
  trade_date: string
  market_summary: string
  macro_summary: string
  top_movers: Array<{ symbol: string; pct_change: number; close: number }>
}

type Candidate = {
  id: number
  trade_date: string
  stock_symbol: string
  sentiment_score: number
  data_score: number
  total_score: number
  reasons: string[]
}

type ScanResult = {
  id: number
  scan_date: string
  stock_symbol: string
  rank: number
  score: number
  action: string
  notes: Record<string, unknown>
}

function todayISO() {
  return new Date().toISOString().slice(0, 10)
}

export default function Workflow() {
  const [tradeDate, setTradeDate] = useState(todayISO())
  const [topN, setTopN] = useState(10)
  const [recap, setRecap] = useState<Recap | null>(null)
  const [candidates, setCandidates] = useState<Candidate[]>([])
  const [scanRows, setScanRows] = useState<ScanResult[]>([])
  const [status, setStatus] = useState('')
  const [loading, setLoading] = useState(false)

  const hasReview = useMemo(() => recap !== null || candidates.length > 0, [recap, candidates.length])

  async function handlePostClose() {
    setLoading(true)
    setStatus('')
    try {
      const res = await runPostCloseReview({ trade_date: tradeDate, top_n: topN })
      setRecap(res.recap as Recap)
      setCandidates((res.candidates || []) as Candidate[])
      setStatus('Post-close review generated.')
    } catch (err: unknown) {
      setStatus((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  async function handlePreOpen() {
    setLoading(true)
    setStatus('')
    try {
      const rows = await runPreOpenScan({ scan_date: tradeDate, top_n: topN })
      setScanRows((rows || []) as ScanResult[])
      setStatus('Pre-open scan generated.')
    } catch (err: unknown) {
      setStatus((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="panel">
      <h2>Daily Workflow</h2>
      <div className="form-grid">
        <label>
          Trade Date
          <input type="date" value={tradeDate} onChange={(e) => setTradeDate(e.target.value)} />
        </label>
        <label>
          Top N
          <input
            type="number"
            min={1}
            max={100}
            value={topN}
            onChange={(e) => setTopN(Math.max(1, Number(e.target.value) || 1))}
          />
        </label>
      </div>
      <div className="actions-row">
        <button className="primary" disabled={loading} onClick={handlePostClose}>
          Run Post-Close Review
        </button>
        <button className="primary ghost" disabled={loading} onClick={handlePreOpen}>
          Run Pre-Open Scan
        </button>
      </div>
      {status && <p className="status">{status}</p>}

      {hasReview && recap && (
        <div className="card section-card">
          <h3>Recap</h3>
          <p>{recap.market_summary}</p>
          <p>{recap.macro_summary}</p>
        </div>
      )}

      {candidates.length > 0 && (
        <div className="section-card">
          <h3>Candidate Pool</h3>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Total</th>
                  <th>Sentiment</th>
                  <th>Data</th>
                  <th>Reasons</th>
                </tr>
              </thead>
              <tbody>
                {candidates.map((row) => (
                  <tr key={row.id}>
                    <td>{row.stock_symbol}</td>
                    <td>{row.total_score.toFixed(3)}</td>
                    <td>{row.sentiment_score.toFixed(3)}</td>
                    <td>{row.data_score.toFixed(3)}</td>
                    <td>{row.reasons.join(', ')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {scanRows.length > 0 && (
        <div className="section-card">
          <h3>Pre-Open Top Picks</h3>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Rank</th>
                  <th>Symbol</th>
                  <th>Score</th>
                  <th>Action</th>
                  <th>Alignment</th>
                </tr>
              </thead>
              <tbody>
                {scanRows.map((row) => (
                  <tr key={row.id}>
                    <td>{row.rank}</td>
                    <td>{row.stock_symbol}</td>
                    <td>{row.score.toFixed(3)}</td>
                    <td>
                      <span className={`badge ${row.action}`}>{row.action}</span>
                    </td>
                    <td>{String(row.notes?.alignment || '')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
