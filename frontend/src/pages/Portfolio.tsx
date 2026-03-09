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

export default function Portfolio() {
  const [positions, setPositions] = useState<Position[]>([])
  const [includeClosed, setIncludeClosed] = useState(false)
  const [symbol, setSymbol] = useState('AAPL')
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
      setStatus('Position updated.')
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
      setStatus(closeAll ? 'Position closed.' : 'Position reduced.')
      await loadPositions()
    } catch (err: unknown) {
      setStatus((err as Error).message)
    }
  }

  return (
    <div className="panel">
      <h2>Portfolio</h2>
      <form onSubmit={handleAdd} className="form-row">
        <input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} placeholder="Symbol" />
        <input
          type="number"
          min={1}
          step="1"
          value={quantity}
          onChange={(e) => setQuantity(Number(e.target.value))}
          placeholder="Qty"
        />
        <input
          type="number"
          min={0.01}
          step="0.01"
          value={avgPrice}
          onChange={(e) => setAvgPrice(Number(e.target.value))}
          placeholder="Avg Price"
        />
        <button className="primary" type="submit">Add / Increase</button>
      </form>

      <label className="checkbox-row">
        <input
          type="checkbox"
          checked={includeClosed}
          onChange={(e) => setIncludeClosed(e.target.checked)}
        />
        Include closed positions
      </label>

      {status && <p className="status">{status}</p>}

      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Qty</th>
              <th>Avg Price</th>
              <th>Status</th>
              <th>Updated</th>
              <th>Close Qty</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((pos) => (
              <tr key={pos.id}>
                <td>{pos.stock_symbol}</td>
                <td>{pos.quantity.toFixed(2)}</td>
                <td>{pos.avg_price.toFixed(2)}</td>
                <td>{pos.status}</td>
                <td>{new Date(pos.updated_at).toLocaleString()}</td>
                <td>
                  <input
                    disabled={pos.status !== 'open'}
                    className="small-input"
                    placeholder="Partial qty"
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
                      Reduce
                    </button>
                    <button
                      className="primary danger"
                      disabled={pos.status !== 'open'}
                      onClick={() => handleClose(pos.id, true)}
                      type="button"
                    >
                      Close All
                    </button>
                  </div>
                </td>
              </tr>
            ))}
            {positions.length === 0 && (
              <tr>
                <td colSpan={7}>No positions yet.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
