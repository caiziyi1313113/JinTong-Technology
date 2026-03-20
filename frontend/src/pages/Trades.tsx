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
  const [symbol, setSymbol] = useState('000001')
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
      setStatus('交易计划已生成。')
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
      setStatus('交易信号已生成。')
      await refreshAll()
    } catch (err: unknown) {
      setStatus((err as Error).message)
    }
  }

  return (
    <div className="panel">
      <h2>交易计划与信号</h2>
      <form onSubmit={handleCreatePlan} className="form-row">
        <input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} placeholder="股票代码" />
        <button className="primary" type="submit">生成计划</button>
      </form>
      {status && <p className="status">{status}</p>}

      <h3>计划列表</h3>
      <div className="grid">
        {plans.map((plan) => (
          <div className="card" key={plan.id}>
            <h4>{plan.stock_symbol} #{plan.id}</h4>
            <p><strong>方向：</strong> {plan.side}</p>
            <p><strong>持有周期：</strong> {plan.hold_days}</p>
            <p><strong>股数：</strong> {plan.suggested_shares}</p>
            <p><strong>入场：</strong> {plan.entry_low ?? '-'} ~ {plan.entry_high ?? '-'}</p>
            <p><strong>止损/止盈：</strong> {plan.stop_loss_price ?? '-'} / {plan.take_profit_price ?? '-'}</p>
            <p><strong>移动止损：</strong> {plan.trailing_stop_pct ?? '-'}</p>
            {plan.ladder_prices?.length > 0 && (
              <p><strong>分批：</strong> {plan.ladder_prices.join(', ')}</p>
            )}
            <div className="form-row compact">
              <input
                type="number"
                step="0.01"
                placeholder="当前价格（可选）"
                value={priceByPlan[plan.id] || ''}
                onChange={(e) => setPriceByPlan({ ...priceByPlan, [plan.id]: e.target.value })}
              />
              <button className="primary ghost" type="button" onClick={() => handleCreateSignal(plan.id)}>
                生成信号
              </button>
            </div>
          </div>
        ))}
        {plans.length === 0 && <div className="card">暂无计划。</div>}
      </div>

      <h3>信号列表</h3>
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>时间</th>
              <th>代码</th>
              <th>方向</th>
              <th>类型</th>
              <th>触发价</th>
              <th>股数</th>
              <th>置信度</th>
              <th>原因</th>
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
                <td colSpan={8}>暂无信号。</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
