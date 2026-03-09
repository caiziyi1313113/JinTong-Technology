import { useState } from 'react'
import { createAnalysis, getUserId } from '../api'

type ExpertSignal = {
  expert_name: string
  signal: string
  score: number
  confidence: number
  horizon: string
  key_factors: string[]
}

type TradeAdvice = {
  mode?: string
  hold_days?: string
  entry_range?: number[]
  stop_loss_price?: number
  take_profit_price?: number
  trailing_stop_pct?: number
  suggested_buy_shares?: number
  suggested_sell_shares?: number
}

type Rationale = {
  fused_score?: number
  sentiment_score?: number
  data_score?: number
  alignment?: string
  conflict_reason?: string
  decision_note?: string
  trade_advice?: TradeAdvice
}

type AnalysisResult = {
  stock_symbol: string
  final_action: string
  position_size: number
  risk_notes: string[]
  rationale: Rationale
  expert_signals: ExpertSignal[]
}

export default function QueryStocks() {
  const [symbol, setSymbol] = useState<string>('AAPL')
  const [loading, setLoading] = useState<boolean>(false)
  const [status, setStatus] = useState<string>('')
  const [result, setResult] = useState<AnalysisResult | null>(null)

  async function handleQuery(e: React.FormEvent) {
    e.preventDefault()
    const userId = getUserId()
    if (!userId) {
      setStatus('会话失效，请刷新页面。')
      return
    }
    setLoading(true)
    setStatus('')
    try {
      const response = await createAnalysis(symbol.toUpperCase(), userId)
      setResult(response as AnalysisResult)
      setStatus('查询完成。')
    } catch (err: unknown) {
      setStatus((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  const tradeAdvice = result?.rationale?.trade_advice

  return (
    <section className="screen">
      <div className="hero-block reveal-up">
        <h1>查询股票</h1>
        <p>输入股票代码，融合情绪面与数据面，返回结构化建议。</p>
        <form className="search-pill" onSubmit={handleQuery}>
          <input
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            placeholder="输入股票代码，例如 AAPL"
          />
          <button className="pill-button" type="submit" disabled={loading}>
            查询
          </button>
        </form>
        {status && <div className="inline-status">{status}</div>}
      </div>

      {result && (
        <div className="paper reveal-up delay-1">
          <div className="paper-header">
            <h2>{result.stock_symbol} 决策摘要</h2>
          </div>
          <div className="metric-grid">
            <div className="metric-box"><span>建议动作</span><strong>{result.final_action}</strong></div>
            <div className="metric-box"><span>仓位比例</span><strong>{(result.position_size * 100).toFixed(1)}%</strong></div>
            <div className="metric-box"><span>融合分数</span><strong>{(result.rationale?.fused_score ?? 0).toFixed(3)}</strong></div>
            <div className="metric-box"><span>情绪分数</span><strong>{(result.rationale?.sentiment_score ?? 0).toFixed(3)}</strong></div>
            <div className="metric-box"><span>数据分数</span><strong>{(result.rationale?.data_score ?? 0).toFixed(3)}</strong></div>
            <div className="metric-box"><span>一致性</span><strong>{result.rationale?.alignment || '-'}</strong></div>
          </div>

          {result.rationale?.decision_note && <div className="notice-line">{result.rationale.decision_note}</div>}
          {result.rationale?.conflict_reason && (
            <div className="warn-line">冲突原因：{result.rationale.conflict_reason}</div>
          )}

          {tradeAdvice && (
            <div className="trade-panel">
              <h3>交易建议</h3>
              <p>模式：{tradeAdvice.mode || '-'}</p>
              <p>持有期：{tradeAdvice.hold_days || '-'}</p>
              <p>
                入场区间：
                {tradeAdvice.entry_range && tradeAdvice.entry_range.length === 2
                  ? `${tradeAdvice.entry_range[0]} - ${tradeAdvice.entry_range[1]}`
                  : '-'}
              </p>
              <p>止损：{tradeAdvice.stop_loss_price ?? '-'}</p>
              <p>止盈：{tradeAdvice.take_profit_price ?? '-'}</p>
              <p>移动止损：{tradeAdvice.trailing_stop_pct ?? '-'}</p>
              <p>建议买入股数：{tradeAdvice.suggested_buy_shares ?? '-'}</p>
              <p>建议卖出股数：{tradeAdvice.suggested_sell_shares ?? '-'}</p>
            </div>
          )}

          <div className="paper-header with-top-line">
            <h3>专家观点</h3>
          </div>
          <div className="list-stack">
            {result.expert_signals.map((item) => (
              <article className="list-item" key={item.expert_name}>
                <div className="thumb" />
                <div className="list-content">
                  <div className="row-title">{item.expert_name}</div>
                  <div className="row-sub">
                    信号 {item.signal}，分数 {item.score.toFixed(3)}，置信度 {item.confidence.toFixed(3)}
                  </div>
                  <div className="row-sub">周期 {item.horizon}</div>
                  <div className="row-sub">{item.key_factors.slice(0, 2).join(' / ')}</div>
                </div>
              </article>
            ))}
          </div>

          {result.risk_notes?.length > 0 && (
            <div className="signal-strip">
              风险标签：
              {result.risk_notes.map((note) => (
                <span key={note} className="signal-pill">{note}</span>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  )
}
