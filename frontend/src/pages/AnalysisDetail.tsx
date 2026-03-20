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
    return <div className="panel">正在加载分析结果...</div>
  }

  const tradeAdvice = analysis.rationale?.trade_advice

  return (
    <div className="panel">
      <h2>决策摘要</h2>
      <div className="summary">
        <div>
          <strong>股票：</strong> {analysis.stock_symbol}
        </div>
        <div>
          <strong>动作：</strong> {analysis.final_action}
        </div>
        <div>
          <strong>仓位：</strong> {(analysis.position_size * 100).toFixed(1)}%
        </div>
        <div>
          <strong>融合分：</strong> {analysis.rationale?.fused_score}
        </div>
        <div>
          <strong>情绪分：</strong> {analysis.rationale?.sentiment_score}
        </div>
        <div>
          <strong>数据分：</strong> {analysis.rationale?.data_score}
        </div>
        <div>
          <strong>一致性：</strong> {analysis.rationale?.alignment}
        </div>
      </div>
      <div className="hint">{analysis.rationale?.decision_note}</div>
      {analysis.rationale?.alignment === 'conflict' && (
        <div className="risk">
          <strong>冲突原因：</strong> {analysis.rationale?.conflict_reason}
        </div>
      )}

      {tradeAdvice && (
        <div className="card">
          <h3>交易建议</h3>
          <p><strong>模式：</strong> {tradeAdvice.mode}</p>
          <p><strong>持有周期：</strong> {tradeAdvice.hold_days}</p>
          {tradeAdvice.entry_range && (
            <p>
              <strong>入场区间：</strong> {tradeAdvice.entry_range[0]} - {tradeAdvice.entry_range[1]}
            </p>
          )}
          {tradeAdvice.ladder_buy_prices && (
            <p>
              <strong>分批价格：</strong> {tradeAdvice.ladder_buy_prices.join(', ')}
            </p>
          )}
          {tradeAdvice.suggested_buy_shares !== undefined && (
            <p><strong>建议买入股数：</strong> {tradeAdvice.suggested_buy_shares}</p>
          )}
          {tradeAdvice.suggested_sell_shares !== undefined && (
            <p><strong>建议卖出股数：</strong> {tradeAdvice.suggested_sell_shares}</p>
          )}
          <p><strong>止损：</strong> {tradeAdvice.stop_loss_price}</p>
          <p><strong>止盈：</strong> {tradeAdvice.take_profit_price}</p>
          <p><strong>移动止损：</strong> {tradeAdvice.trailing_stop_pct}</p>
        </div>
      )}

      <h3>专家信号</h3>
      <div className="grid">
        {analysis.expert_signals.map((signal: any) => (
          <div key={signal.expert_name} className="card">
            <h4>{signal.expert_name}</h4>
            <p>
              信号：<strong>{signal.signal}</strong>
            </p>
            <p>评分：{signal.score.toFixed(2)}</p>
            <p>置信度：{signal.confidence.toFixed(2)}</p>
            <p>周期：{signal.horizon}</p>
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
          <strong>风险提示：</strong> {analysis.risk_notes.join('，')}
        </div>
      )}
      {status && <p className="status">{status}</p>}
    </div>
  )
}
