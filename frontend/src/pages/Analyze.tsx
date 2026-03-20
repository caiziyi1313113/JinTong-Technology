import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { createAnalysis, getUserId } from '../api'

export default function Analyze() {
  const [symbol, setSymbol] = useState('000001')
  const [status, setStatus] = useState('')
  const navigate = useNavigate()

  async function handleAnalyze(e: React.FormEvent) {
    e.preventDefault()
    const userId = getUserId()
    if (!userId) {
      setStatus('用户会话缺失，请刷新后重试。')
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
      <h2>运行分析</h2>
      <form onSubmit={handleAnalyze} className="form-row">
        <input
          value={symbol}
          onChange={(e) => setSymbol(e.target.value.toUpperCase())}
          placeholder="股票代码"
        />
        <button type="submit" className="primary">开始分析</button>
      </form>
      {status && <p className="status">{status}</p>}
      <div className="hint">
        提示：可运行 `python scripts/seed_demo.py` 预加载示例数据。
      </div>
    </div>
  )
}
