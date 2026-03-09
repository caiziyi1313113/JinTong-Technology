import { useEffect, useState } from 'react'
import {
  closePosition,
  createTradePlan,
  createTradeSignal,
  listPositions,
  listTradePlans,
  listTradeSignals,
  upsertPosition,
} from '../api'

type Position = {
  id: number
  stock_symbol: string
  quantity: number
  avg_price: number
  status: string
  updated_at: string
}

type TradePlan = {
  id: number
  stock_symbol: string
  side: string
  hold_days?: string | null
  stop_loss_price?: number | null
  take_profit_price?: number | null
  suggested_shares: number
}

type TradeSignal = {
  id: number
  stock_symbol: string
  side: string
  signal_type: string
  confidence: number
  created_at: string
}

export default function TrackStocks() {
  const [status, setStatus] = useState<string>('')
  const [positions, setPositions] = useState<Position[]>([])
  const [plans, setPlans] = useState<TradePlan[]>([])
  const [signals, setSignals] = useState<TradeSignal[]>([])
  const [symbol, setSymbol] = useState<string>('AAPL')
  const [quantity, setQuantity] = useState<number>(10)
  const [price, setPrice] = useState<number>(100)
  const [loading, setLoading] = useState<boolean>(false)

  async function reloadAll() {
    const [p, t, s] = await Promise.all([
      listPositions(false),
      listTradePlans(),
      listTradeSignals(),
    ])
    setPositions((p || []) as Position[])
    setPlans((t || []) as TradePlan[])
    setSignals((s || []) as TradeSignal[])
  }

  useEffect(() => {
    reloadAll().catch((err: unknown) => setStatus((err as Error).message))
  }, [])

  async function handleAddPosition(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    setStatus('')
    try {
      await upsertPosition({ stock_symbol: symbol.toUpperCase(), quantity, avg_price: price })
      await reloadAll()
      setStatus('持仓更新成功。')
    } catch (err: unknown) {
      setStatus((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  async function handleClosePosition(positionId: number) {
    setLoading(true)
    setStatus('')
    try {
      await closePosition(positionId)
      await reloadAll()
      setStatus('持仓已平仓。')
    } catch (err: unknown) {
      setStatus((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  async function handleGeneratePlan(targetSymbol: string) {
    setLoading(true)
    setStatus('')
    try {
      await createTradePlan(targetSymbol)
      await reloadAll()
      setStatus(`已为 ${targetSymbol} 生成交易计划。`)
    } catch (err: unknown) {
      setStatus((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  async function handleGenerateSignal(planId: number) {
    setLoading(true)
    setStatus('')
    try {
      await createTradeSignal({ trade_plan_id: planId })
      await reloadAll()
      setStatus('已生成交易信号。')
    } catch (err: unknown) {
      setStatus((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className="screen">
      <div className="hero-block reveal-up">
        <h1>跟踪股票</h1>
        <p>集中管理持仓、计划与信号，明确入场和退出规则。</p>
        <form className="inline-form" onSubmit={handleAddPosition}>
          <input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} placeholder="股票代码" />
          <input
            type="number"
            min={1}
            step="1"
            value={quantity}
            onChange={(e) => setQuantity(Number(e.target.value))}
            placeholder="数量"
          />
          <input
            type="number"
            min={0.01}
            step="0.01"
            value={price}
            onChange={(e) => setPrice(Number(e.target.value))}
            placeholder="成本价"
          />
          <button className="btn solid" disabled={loading} type="submit">
            新增/加仓
          </button>
          <button className="btn invert" disabled={loading} onClick={() => handleGeneratePlan(symbol)} type="button">
            生成计划
          </button>
        </form>
        {status && <div className="inline-status">{status}</div>}
      </div>

      <div className="double-paper reveal-up delay-1">
        <section className="paper">
          <div className="paper-header">
            <h2>当前持仓</h2>
          </div>
          <div className="list-stack">
            {positions.map((item) => (
              <article className="list-item" key={item.id}>
                <div className="thumb" />
                <div className="list-content">
                  <div className="row-title">{item.stock_symbol}</div>
                  <div className="row-sub">
                    持仓 {item.quantity.toFixed(2)} 股，成本 {item.avg_price.toFixed(2)}
                  </div>
                  <div className="row-sub">更新时间 {new Date(item.updated_at).toLocaleString()}</div>
                </div>
                <div className="list-side">
                  <span className="chip watch">{item.status}</span>
                  <button className="mini-btn" onClick={() => handleClosePosition(item.id)} type="button">
                    平仓
                  </button>
                </div>
              </article>
            ))}
            {positions.length === 0 && <div className="empty-line">暂无持仓记录。</div>}
          </div>
        </section>

        <section className="paper">
          <div className="paper-header">
            <h2>交易计划与信号</h2>
          </div>
          <div className="list-stack">
            {plans.map((item) => (
              <article className="list-item" key={item.id}>
                <div className="thumb" />
                <div className="list-content">
                  <div className="row-title">{item.stock_symbol} / {item.side}</div>
                  <div className="row-sub">
                    止损 {item.stop_loss_price ?? '-'}，止盈 {item.take_profit_price ?? '-'}
                  </div>
                  <div className="row-sub">
                    建议股数 {item.suggested_shares}，持有周期 {item.hold_days || '-'}
                  </div>
                </div>
                <div className="list-side">
                  <button className="mini-btn" onClick={() => handleGenerateSignal(item.id)} type="button">
                    生成信号
                  </button>
                </div>
              </article>
            ))}
            {plans.length === 0 && <div className="empty-line">暂无交易计划。</div>}
          </div>

          {signals.length > 0 && (
            <div className="signal-strip">
              最近信号：
              {signals.slice(0, 4).map((s) => (
                <span key={s.id} className="signal-pill">
                  {s.stock_symbol} {s.signal_type} {s.confidence.toFixed(2)}
                </span>
              ))}
            </div>
          )}
        </section>
      </div>
    </section>
  )
}
