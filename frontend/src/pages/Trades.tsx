import { useEffect, useState } from 'react'
import { createTradePlan, createTradeSignal, listTradePlans, listTradeSignals } from '../api'

type TradePlan = {
  id: number
  stock_symbol: string
  side: string
  entry_low?: number | null
  entry_high?: number | null
  ladder_prices: number[]
  stop_loss_price?: number | null
  take_profit_price?: number | null
  trailing_stop_pct?: number | null
  reduce_ratio: number
  suggested_shares: number
  hold_days?: string | null
  status: string
  rationale: Record<string, unknown>
  created_at: string
}

type TradeSignal = {
  id: number
  stock_symbol: string
  side: string
  signal_type: string
  trigger_price?: number | null
  suggested_shares: number
  confidence: number
  reason: string
  created_at: string
}

export default function Trades() {
  const [symbol, setSymbol] = useState('AAPL')
  const [plans, setPlans] = useState<TradePlan[]>([])
  const [signals, setSignals] = useState<TradeSignal[]>([])
  const [status, setStatus] = useState('')
  const [priceByPlan, setPriceByPlan] = useState<Record<number, string>>({})

  async function loadPlans() {
    const res = await listTradePlans()
    setPlans((res || []) as TradePlan[])
  }

  async function loadSignals() {
    const res = await listTradeSignals()
    setSignals((res || []) as TradeSignal[])
  }

  async function refreshAll() {
    try {
      await Promise.all([loadPlans(), loadSignals()])
    } catch (err: unknown) {
      setStatus((err as Error).message)
    }
  }

  useEffect(() => {
    refreshAll()
  }, [])

  async function handleCreatePlan(e: React.FormEvent) {
    e.preventDefault()
    setStatus('')
    try {
      await createTradePlan(symbol.toUpperCase())
      setStatus('Trade plan created.')
      await refreshAll()
    } catch (err: unknown) {
      setStatus((err as Error).message)
    }
  }

  async function handleCreateSignal(planId: number) {
    setStatus('')
    try {
      const raw = priceByPlan[planId]
      const currentPrice = raw ? Number(raw) : undefined
      await createTradeSignal({ trade_plan_id: planId, current_price: currentPrice })
      setStatus('Trade signal created.')
      await refreshAll()
    } catch (err: unknown) {
      setStatus((err as Error).message)
    }
  }

  return (
    <div className="panel">
      <h2>Trade Plans And Signals</h2>
      <form onSubmit={handleCreatePlan} className="form-row">
        <input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} placeholder="Stock symbol" />
        <button className="primary" type="submit">Generate Plan</button>
      </form>
      {status && <p className="status">{status}</p>}

      <h3>Plans</h3>
      <div className="grid">
        {plans.map((plan) => (
          <div className="card" key={plan.id}>
            <h4>{plan.stock_symbol} #{plan.id}</h4>
            <p><strong>Side:</strong> {plan.side}</p>
            <p><strong>Hold:</strong> {plan.hold_days}</p>
            <p><strong>Shares:</strong> {plan.suggested_shares}</p>
            <p><strong>Entry:</strong> {plan.entry_low ?? '-'} ~ {plan.entry_high ?? '-'}</p>
            <p><strong>Stop/Take:</strong> {plan.stop_loss_price ?? '-'} / {plan.take_profit_price ?? '-'}</p>
            <p><strong>Trailing:</strong> {plan.trailing_stop_pct ?? '-'}</p>
            {plan.ladder_prices?.length > 0 && (
              <p><strong>Ladder:</strong> {plan.ladder_prices.join(', ')}</p>
            )}
            <div className="form-row compact">
              <input
                type="number"
                step="0.01"
                placeholder="Current price (optional)"
                value={priceByPlan[plan.id] || ''}
                onChange={(e) => setPriceByPlan({ ...priceByPlan, [plan.id]: e.target.value })}
              />
              <button className="primary ghost" type="button" onClick={() => handleCreateSignal(plan.id)}>
                Generate Signal
              </button>
            </div>
          </div>
        ))}
        {plans.length === 0 && <div className="card">No plans yet.</div>}
      </div>

      <h3>Signals</h3>
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Symbol</th>
              <th>Side</th>
              <th>Type</th>
              <th>Trigger</th>
              <th>Shares</th>
              <th>Conf</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            {signals.map((s) => (
              <tr key={s.id}>
                <td>{new Date(s.created_at).toLocaleString()}</td>
                <td>{s.stock_symbol}</td>
                <td>{s.side}</td>
                <td>{s.signal_type}</td>
                <td>{s.trigger_price ?? '-'}</td>
                <td>{s.suggested_shares}</td>
                <td>{s.confidence.toFixed(2)}</td>
                <td>{s.reason}</td>
              </tr>
            ))}
            {signals.length === 0 && (
              <tr>
                <td colSpan={8}>No signals yet.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
