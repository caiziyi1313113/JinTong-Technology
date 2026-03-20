import { useEffect, useState } from 'react'
import { closePosition, listPositions, upsertPosition } from '../api'

type Position = {
  id: number
  stock_symbol: string
  quantity: number
  avg_price: number
  status: string
  opened_at: string
  updated_at: string
  closed_at?: string | null
}

function positionStatusLabel(status: string) {
  const normalized = String(status || '').trim().toLowerCase()
  if (normalized === 'open') return '持仓中'
  if (normalized === 'closed') return '已平仓'
  return status || '-'
}

export default function Portfolio() {
  const [positions, setPositions] = useState<Position[]>([])
  const [includeClosed, setIncludeClosed] = useState(false)
  const [symbol, setSymbol] = useState('000001')
  const [quantity, setQuantity] = useState(10)
  const [avgPrice, setAvgPrice] = useState(100)
  const [status, setStatus] = useState('')
  const [closeQtyMap, setCloseQtyMap] = useState<Record<number, string>>({})

  async function loadPositions(flag = includeClosed) {
    try {
      const rows = await listPositions(flag)
      setPositions((rows || []) as Position[])
    } catch (err: unknown) {
      setStatus((err as Error).message)
    }
  }

  useEffect(() => {
    loadPositions(includeClosed)
  }, [includeClosed])

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault()
    setStatus('')
    try {
      await upsertPosition({
        stock_symbol: symbol.toUpperCase(),
        quantity: Number(quantity),
        avg_price: Number(avgPrice),
      })
      setStatus('持仓已更新。')
      await loadPositions()
    } catch (err: unknown) {
      setStatus((err as Error).message)
    }
  }

  async function handleClose(positionId: number, closeAll = false) {
    setStatus('')
    try {
      const raw = closeQtyMap[positionId]
      const qty = !closeAll && raw ? Number(raw) : undefined
      await closePosition(positionId, qty)
      setStatus(closeAll ? '持仓已全部平仓。' : '持仓已减仓。')
      await loadPositions()
    } catch (err: unknown) {
      setStatus((err as Error).message)
    }
  }

  return (
    <div className="panel">
      <h2>持仓管理</h2>
      <form onSubmit={handleAdd} className="form-row">
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
          value={avgPrice}
          onChange={(e) => setAvgPrice(Number(e.target.value))}
          placeholder="均价"
        />
        <button className="primary" type="submit">新增/加仓</button>
      </form>

      <label className="checkbox-row">
        <input
          type="checkbox"
          checked={includeClosed}
          onChange={(e) => setIncludeClosed(e.target.checked)}
        />
        显示已平仓记录
      </label>

      {status && <p className="status">{status}</p>}

      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>股票代码</th>
              <th>数量</th>
              <th>均价</th>
              <th>状态</th>
              <th>更新时间</th>
              <th>平仓数量</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((pos) => (
              <tr key={pos.id}>
                <td>{pos.stock_symbol}</td>
                <td>{pos.quantity.toFixed(2)}</td>
                <td>{pos.avg_price.toFixed(2)}</td>
                <td>{positionStatusLabel(pos.status)}</td>
                <td>{new Date(pos.updated_at).toLocaleString()}</td>
                <td>
                  <input
                    disabled={pos.status !== 'open'}
                    className="small-input"
                    placeholder="输入减仓数量"
                    value={closeQtyMap[pos.id] || ''}
                    onChange={(e) => setCloseQtyMap({ ...closeQtyMap, [pos.id]: e.target.value })}
                  />
                </td>
                <td>
                  <div className="actions-row tight">
                    <button
                      className="primary ghost"
                      disabled={pos.status !== 'open'}
                      onClick={() => handleClose(pos.id, false)}
                      type="button"
                    >
                      减仓
                    </button>
                    <button
                      className="primary danger"
                      disabled={pos.status !== 'open'}
                      onClick={() => handleClose(pos.id, true)}
                      type="button"
                    >
                      全部平仓
                    </button>
                  </div>
                </td>
              </tr>
            ))}
            {positions.length === 0 && (
              <tr>
                <td colSpan={7}>暂无持仓。</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
