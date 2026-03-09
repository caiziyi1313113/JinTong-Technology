import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getAnalysis } from '../api'

export default function AnalysisDetail() {
  const { id } = useParams()
  const [analysis, setAnalysis] = useState<any>(null)
  const [status, setStatus] = useState('')

  useEffect(() => {
    if (!id) return
    getAnalysis(id)
      .then(setAnalysis)
      .catch((err) => setStatus(err.message))
  }, [id])

  if (!analysis) {
    return <div className="panel">Loading analysis...</div>
  }

  const tradeAdvice = analysis.rationale?.trade_advice

  return (
    <div className="panel">
      <h2>Decision Summary</h2>
      <div className="summary">
        <div>
          <strong>Stock:</strong> {analysis.stock_symbol}
        </div>
        <div>
          <strong>Action:</strong> {analysis.final_action}
        </div>
        <div>
          <strong>Position Size:</strong> {(analysis.position_size * 100).toFixed(1)}%
        </div>
        <div>
          <strong>Fused Score:</strong> {analysis.rationale?.fused_score}
        </div>
        <div>
          <strong>Sentiment Score:</strong> {analysis.rationale?.sentiment_score}
        </div>
        <div>
          <strong>Data Score:</strong> {analysis.rationale?.data_score}
        </div>
        <div>
          <strong>Alignment:</strong> {analysis.rationale?.alignment}
        </div>
      </div>
      <div className="hint">{analysis.rationale?.decision_note}</div>
      {analysis.rationale?.alignment === 'conflict' && (
        <div className="risk">
          <strong>Conflict Reason:</strong> {analysis.rationale?.conflict_reason}
        </div>
      )}

      {tradeAdvice && (
        <div className="card">
          <h3>Trade Advice</h3>
          <p><strong>Mode:</strong> {tradeAdvice.mode}</p>
          <p><strong>Hold:</strong> {tradeAdvice.hold_days}</p>
          {tradeAdvice.entry_range && (
            <p>
              <strong>Entry Range:</strong> {tradeAdvice.entry_range[0]} - {tradeAdvice.entry_range[1]}
            </p>
          )}
          {tradeAdvice.ladder_buy_prices && (
            <p>
              <strong>Ladder Prices:</strong> {tradeAdvice.ladder_buy_prices.join(', ')}
            </p>
          )}
          {tradeAdvice.suggested_buy_shares !== undefined && (
            <p><strong>Suggested Buy Shares:</strong> {tradeAdvice.suggested_buy_shares}</p>
          )}
          {tradeAdvice.suggested_sell_shares !== undefined && (
            <p><strong>Suggested Sell Shares:</strong> {tradeAdvice.suggested_sell_shares}</p>
          )}
          <p><strong>Stop Loss:</strong> {tradeAdvice.stop_loss_price}</p>
          <p><strong>Take Profit:</strong> {tradeAdvice.take_profit_price}</p>
          <p><strong>Trailing Stop:</strong> {tradeAdvice.trailing_stop_pct}</p>
        </div>
      )}

      <h3>Expert Signals</h3>
      <div className="grid">
        {analysis.expert_signals.map((signal: any) => (
          <div key={signal.expert_name} className="card">
            <h4>{signal.expert_name}</h4>
            <p>
              Signal: <strong>{signal.signal}</strong>
            </p>
            <p>Score: {signal.score.toFixed(2)}</p>
            <p>Confidence: {signal.confidence.toFixed(2)}</p>
            <p>Horizon: {signal.horizon}</p>
            <ul>
              {signal.key_factors.map((item: string) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      {analysis.risk_notes?.length > 0 && (
        <div className="risk">
          <strong>Risk Notes:</strong> {analysis.risk_notes.join(', ')}
        </div>
      )}
      {status && <p className="status">{status}</p>}
    </div>
  )
}
