import { useMemo, useState } from 'react'
import { runPostCloseReview, runPreOpenScan } from '../api'

type Candidate = {
  id: number
  stock_symbol: string
  sentiment_score: number
  data_score: number
  total_score: number
  reasons: string[]
}

type ScanResult = {
  id: number
  stock_symbol: string
  rank: number
  score: number
  action: string
  notes: Record<string, unknown>
}

type Recap = {
  market_summary: string
  macro_summary: string
}

type DisplayRow = {
  key: string
  symbol: string
  score: number
  action: string
  reasonText: string
}

function todayISO(): string {
  return new Date().toISOString().slice(0, 10)
}

function toText(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

export default function Discover() {
  const [tradeDate, setTradeDate] = useState<string>(todayISO())
  const [topN, setTopN] = useState<number>(12)
  const [keyword, setKeyword] = useState<string>('')
  const [loading, setLoading] = useState<boolean>(false)
  const [status, setStatus] = useState<string>('')
  const [recap, setRecap] = useState<Recap | null>(null)
  const [candidates, setCandidates] = useState<Candidate[]>([])
  const [scanRows, setScanRows] = useState<ScanResult[]>([])

  const rows = useMemo<DisplayRow[]>(() => {
    const baseRows: DisplayRow[] =
      scanRows.length > 0
        ? scanRows.map((row) => {
            const noteReason = toText(row.notes?.alignment)
            return {
              key: `scan-${row.id}`,
              symbol: row.stock_symbol,
              score: row.score,
              action: row.action,
              reasonText: noteReason || '开盘前重评分',
            }
          })
        : candidates.map((row) => ({
            key: `candidate-${row.id}`,
            symbol: row.stock_symbol,
            score: row.total_score,
            action: row.total_score >= 0.62 ? 'buy' : 'watch',
            reasonText: row.reasons.slice(0, 2).join(' / ') || '收盘后候选池',
          }))

    const q = keyword.trim().toUpperCase()
    if (!q) {
      return baseRows
    }
    return baseRows.filter((row) => row.symbol.includes(q))
  }, [candidates, keyword, scanRows])

  async function handlePostClose() {
    setLoading(true)
    setStatus('')
    try {
      const response = await runPostCloseReview({ trade_date: tradeDate, top_n: topN })
      setRecap((response.recap || null) as Recap | null)
      setCandidates((response.candidates || []) as Candidate[])
      setStatus(`已生成 ${response.candidates?.length || 0} 个候选标地。`)
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
      const response = await runPreOpenScan({ scan_date: tradeDate, top_n: topN })
      setScanRows((response || []) as ScanResult[])
      setStatus(`已完成开盘前扫描，返回 ${(response || []).length} 个结果。`)
    } catch (err: unknown) {
      setStatus((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className="screen">
      <div className="hero-block reveal-up">
        <h1>寻找潜力标地</h1>
        <p>收盘后复盘，开盘前重评分，帮助你快速聚焦。</p>
        <div className="search-pill">
          <input
            value={keyword}
            onChange={(e) => setKeyword(e.target.value.toUpperCase())}
            placeholder="输入股票代码过滤，例如 AAPL"
          />
          <span className="search-mark">⌕</span>
        </div>
        <div className="control-row">
          <label>
            日期
            <input type="date" value={tradeDate} onChange={(e) => setTradeDate(e.target.value)} />
          </label>
          <label>
            TopN
            <input
              type="number"
              min={1}
              max={50}
              value={topN}
              onChange={(e) => setTopN(Math.max(1, Number(e.target.value) || 1))}
            />
          </label>
          <button className="btn solid" disabled={loading} onClick={handlePostClose}>
            收盘复盘
          </button>
          <button className="btn invert" disabled={loading} onClick={handlePreOpen}>
            开盘扫描
          </button>
        </div>
        {status && <div className="inline-status">{status}</div>}
      </div>

      <div className="paper reveal-up delay-1">
        <div className="paper-header">
          <h2>候选列表</h2>
          {recap && (
            <div className="paper-meta">
              <span>{recap.market_summary}</span>
              <span>{recap.macro_summary}</span>
            </div>
          )}
        </div>
        <div className="list-stack">
          {rows.map((row) => (
            <article className="list-item" key={row.key}>
              <div className="thumb" />
              <div className="list-content">
                <div className="row-title">{row.symbol}</div>
                <div className="row-sub">{row.reasonText}</div>
              </div>
              <div className="list-side">
                <span className={`chip ${row.action}`}>{row.action}</span>
                <strong>{row.score.toFixed(3)}</strong>
              </div>
            </article>
          ))}
          {rows.length === 0 && <div className="empty-line">暂无结果，先执行“收盘复盘”或“开盘扫描”。</div>}
        </div>
      </div>
    </section>
  )
}
