import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  clearPortfolioTracking,
  clearPortfolioTrackingBySymbol,
  closePosition,
  createAnalysisTask,
  createPortfolioTrade,
  createTradeSignal,
  getAnalysisTask,
  getLatestAnalysisBySymbol,
  getUserId,
  listPortfolioTrades,
  listPositions,
  listTradePlans,
  listTradeSignals,
} from '../api'

type Position = {
  id: number
  stock_symbol: string
  quantity: number
  avg_price: number
  status: string
  updated_at: string
}

type PortfolioTrade = {
  id: number
  stock_symbol: string
  side: string
  quantity: number
  price: number
  trade_time: string
  note?: string
}

type TradePlan = {
  id: number
  stock_symbol: string
  side: string
  suggested_shares: number
  stop_loss_price?: number
  take_profit_price?: number
  hold_days?: string
}

type TradeSignal = {
  id: number
  stock_symbol: string
  side: string
  signal_type: string
  confidence: number
  created_at: string
}

type AnalysisTask = {
  task_id: string
  stock_symbol: string
  status: 'queued' | 'running' | 'completed' | 'failed'
  current_step: number
  total_steps: number
  queue_position?: number | null
  stage: string
  message?: string
  error?: string
  analysis_id?: number
}

const ANALYSIS_STAGE_LABELS = [
  '数据刷新',
  '新闻专家',
  '行情专家',
  '宏观专家',
  '财务专家',
  '基本面专家',
  '投资建议专家',
]
const STORAGE_TRACK_ANALYSIS_TASKS = 'stockai.track.analysis_tasks'

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function tradeSideLabel(side: string) {
  if (side === 'buy') return '买入'
  if (side === 'sell') return '卖出'
  return side
}

function analysisStatusLabel(status: AnalysisTask['status']) {
  if (status === 'queued') return '排队中'
  if (status === 'running') return '执行中'
  if (status === 'completed') return '已完成'
  if (status === 'failed') return '失败'
  return status
}

function analysisStageLabel(stage: string) {
  const normalized = String(stage || '').trim().toLowerCase()
  const map: Record<string, string> = {
    queued: '排队中',
    running: '执行中',
    completed: '已完成',
    failed: '失败',
    'submitting task': '提交任务',
    data_refresh: '数据刷新',
    news: '新闻专家',
    stock_data: '行情专家',
    macro: '宏观专家',
    financial: '财务专家',
    fundamental: '基本面专家',
    investment: '投资建议专家',
  }
  return map[normalized] || stage
}

export default function TrackStocks() {
  const navigate = useNavigate()
  const [status, setStatus] = useState<string>('')
  const [loading, setLoading] = useState<boolean>(false)

  const [positions, setPositions] = useState<Position[]>([])
  const [trades, setTrades] = useState<PortfolioTrade[]>([])
  const [plans, setPlans] = useState<TradePlan[]>([])
  const [signals, setSignals] = useState<TradeSignal[]>([])

  const [symbol, setSymbol] = useState<string>('000001')
  const [side, setSide] = useState<'buy' | 'sell'>('buy')
  const [quantity, setQuantity] = useState<number>(100)
  const [price, setPrice] = useState<number>(10)
  const [note, setNote] = useState<string>('')
  const [analysisTasks, setAnalysisTasks] = useState<Record<string, AnalysisTask>>({})
  const pollTokenRef = useRef<Record<string, number>>({})
  const mountedRef = useRef<boolean>(true)

  const reloadAll = useCallback(async () => {
    const [positionRows, tradeRows, planRows, signalRows] = await Promise.all([
      listPositions(false),
      listPortfolioTrades(undefined, 300),
      listTradePlans(),
      listTradeSignals(),
    ])
    setPositions((positionRows || []) as Position[])
    setTrades((tradeRows || []) as PortfolioTrade[])
    setPlans((planRows || []) as TradePlan[])
    setSignals((signalRows || []) as TradeSignal[])
  }, [])

  useEffect(() => {
    mountedRef.current = true
    const savedTasks = localStorage.getItem(STORAGE_TRACK_ANALYSIS_TASKS)
    if (savedTasks) {
      try {
        const parsed = JSON.parse(savedTasks) as Record<string, AnalysisTask>
        if (parsed && typeof parsed === 'object') {
          setAnalysisTasks(parsed)
          Object.entries(parsed).forEach(([savedSymbol, task]) => {
            if (!task || typeof task !== 'object') return
            if (task.task_id === 'pending') return
            if (task.status === 'queued' || task.status === 'running') {
              void pollAnalysisTaskForSymbol(savedSymbol, task.task_id)
            }
          })
        }
      } catch {
        localStorage.removeItem(STORAGE_TRACK_ANALYSIS_TASKS)
      }
    }
    reloadAll().catch((err: unknown) => setStatus((err as Error).message))
    return () => {
      mountedRef.current = false
      pollTokenRef.current = {}
    }
  }, [reloadAll])

  useEffect(() => {
    if (Object.keys(analysisTasks).length === 0) {
      localStorage.removeItem(STORAGE_TRACK_ANALYSIS_TASKS)
      return
    }
    localStorage.setItem(STORAGE_TRACK_ANALYSIS_TASKS, JSON.stringify(analysisTasks))
  }, [analysisTasks])

  useEffect(() => {
    const refreshFromServer = () => {
      reloadAll().catch((err: unknown) => setStatus((err as Error).message))
    }
    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        refreshFromServer()
      }
    }

    window.addEventListener('focus', refreshFromServer)
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () => {
      window.removeEventListener('focus', refreshFromServer)
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [reloadAll])

  const positionBySymbol = useMemo(() => {
    const map = new Map<string, Position>()
    for (const row of positions) map.set(row.stock_symbol, row)
    return map
  }, [positions])

  const tradesBySymbol = useMemo(() => {
    const map = new Map<string, PortfolioTrade[]>()
    for (const row of trades) {
      const list = map.get(row.stock_symbol) || []
      list.push(row)
      map.set(row.stock_symbol, list)
    }
    return map
  }, [trades])

  const plansBySymbol = useMemo(() => {
    const map = new Map<string, TradePlan[]>()
    for (const row of plans) {
      const list = map.get(row.stock_symbol) || []
      list.push(row)
      map.set(row.stock_symbol, list)
    }
    return map
  }, [plans])

  const signalsBySymbol = useMemo(() => {
    const map = new Map<string, TradeSignal[]>()
    for (const row of signals) {
      const list = map.get(row.stock_symbol) || []
      list.push(row)
      map.set(row.stock_symbol, list)
    }
    return map
  }, [signals])

  const groupedSymbols = useMemo(() => {
    const set = new Set<string>()
    for (const row of positions) set.add(row.stock_symbol)
    for (const row of trades) set.add(row.stock_symbol)
    for (const key of Object.keys(analysisTasks)) set.add(key)
    return Array.from(set).sort()
  }, [analysisTasks, positions, trades])

  async function handleSubmitTrade(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    setStatus('')
    try {
      const target = symbol.toUpperCase().trim()
      await createPortfolioTrade({
        stock_symbol: target,
        side,
        quantity,
        price,
        note: note || undefined,
      })
      await reloadAll()
      setStatus(`交易已记录：${target} ${tradeSideLabel(side)} ${quantity} 股，价格 ${price}`)
    } catch (err: unknown) {
      setStatus((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  async function handleClosePosition(position: Position) {
    setLoading(true)
    setStatus('')
    try {
      await closePosition(position.id, { quantity: position.quantity, price, note: 'manual_close' })
      await reloadAll()
      setStatus(`已平仓：${position.stock_symbol}`)
    } catch (err: unknown) {
      setStatus((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  async function pollAnalysisTaskForSymbol(stockSymbol: string, taskId: string, maxRounds = 900) {
    const token = Date.now()
    pollTokenRef.current[stockSymbol] = token

    for (let i = 0; i < maxRounds; i += 1) {
      if (!mountedRef.current) return
      if (pollTokenRef.current[stockSymbol] !== token) return
      let latest: AnalysisTask
      try {
        latest = (await getAnalysisTask(taskId)) as AnalysisTask
      } catch (err: unknown) {
        const message = (err as Error).message || ''
        if (/not found/i.test(message)) {
          try {
            const recovered = (await getLatestAnalysisBySymbol(stockSymbol)) as { id: number }
            if (recovered?.id) {
              setAnalysisTasks((prev) => ({
                ...prev,
                [stockSymbol]: {
                  task_id: taskId,
                  stock_symbol: stockSymbol,
                  status: 'completed',
                  current_step: ANALYSIS_STAGE_LABELS.length,
                  total_steps: ANALYSIS_STAGE_LABELS.length,
                  stage: 'completed',
                  message: '已从最新分析记录恢复',
                  analysis_id: recovered.id,
                },
              }))
              setStatus(`已恢复分析任务：${stockSymbol}`)
              return
            }
          } catch {
            // fall through to retry loop
          }
        }
        const retryMs = i > 60 ? 3500 : 1800
        await sleep(retryMs)
        if (i >= maxRounds - 1) {
          setStatus(`分析轮询失败（${stockSymbol}）：${message}`)
        }
        continue
      }
      if (!mountedRef.current) return
      setAnalysisTasks((prev) => ({ ...prev, [stockSymbol]: latest }))
      if (latest.message) {
        setStatus(latest.message)
      }
      if (latest.status === 'completed' || latest.status === 'failed') {
        if (latest.status === 'completed') {
          setStatus(`分析已完成：${stockSymbol}`)
        } else {
          setStatus(latest.error || `分析失败：${stockSymbol}`)
        }
        return
      }
      const baseWait = i > 120 ? 3500 : 1800
      const waitMs = document.visibilityState === 'hidden' ? baseWait + 2000 : baseWait
      await sleep(waitMs)
    }
    setStatus(`分析超时：${stockSymbol}`)
  }

  async function handleGenerateAnalysis(stockSymbol: string) {
    const userId = getUserId()
    if (!userId) {
      setStatus('登录状态已过期，请重新登录。')
      return
    }
    setStatus('')
    const pendingTask: AnalysisTask = {
      task_id: 'pending',
      stock_symbol: stockSymbol,
      status: 'queued',
      current_step: 0,
      total_steps: ANALYSIS_STAGE_LABELS.length,
      stage: '提交任务',
      message: '正在提交分析任务...',
    }
    setAnalysisTasks((prev) => ({ ...prev, [stockSymbol]: pendingTask }))
    try {
      const created = (await createAnalysisTask(stockSymbol, userId)) as AnalysisTask
      setAnalysisTasks((prev) => ({ ...prev, [stockSymbol]: created }))
      setStatus(`已创建分析任务：${stockSymbol}`)
      await pollAnalysisTaskForSymbol(stockSymbol, created.task_id)
    } catch (err: unknown) {
      setAnalysisTasks((prev) => ({
        ...prev,
        [stockSymbol]: {
          ...pendingTask,
          status: 'failed',
          error: (err as Error).message,
          stage: 'failed',
        },
      }))
      setStatus((err as Error).message)
    }
  }

  async function handleViewDetail(stockSymbol: string) {
    const currentTask = analysisTasks[stockSymbol]
    let analysisId = currentTask?.analysis_id
    if (!analysisId) {
      try {
        const latest = (await getLatestAnalysisBySymbol(stockSymbol)) as { id: number }
        analysisId = latest.id
      } catch {
        // keep navigation without analysis_id, detail page can still load market/ranking parts.
      }
    }
    const search = analysisId ? `?analysis_id=${analysisId}` : ''
    navigate(`/stock/${stockSymbol}${search}`, { state: { from: 'track' } })
  }

  async function handleGenerateSignal(planId: number, stockSymbol: string) {
    setLoading(true)
    setStatus('')
    try {
      await createTradeSignal({ trade_plan_id: planId })
      await reloadAll()
      setStatus(`已生成交易信号：${stockSymbol}`)
    } catch (err: unknown) {
      setStatus((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  async function handleRefresh() {
    setLoading(true)
    setStatus('')
    try {
      await reloadAll()
      setStatus('已从服务端刷新数据。')
    } catch (err: unknown) {
      setStatus((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  async function handleClearSymbol(stockSymbol: string) {
    const confirmed = window.confirm(`确认删除 ${stockSymbol} 的全部跟踪记录吗？`)
    if (!confirmed) return
    setLoading(true)
    setStatus('')
    try {
      const result = (await clearPortfolioTrackingBySymbol(stockSymbol)) as {
        deleted_trades: number
        deleted_positions: number
        deleted_holdings: number
        deleted_trade_plans: number
        deleted_trade_signals: number
      }
      pollTokenRef.current[stockSymbol] = Date.now()
      setAnalysisTasks((prev) => {
        const next = { ...prev }
        delete next[stockSymbol]
        return next
      })
      await reloadAll()
      setStatus(
        `${stockSymbol} 已清空：交易 ${result.deleted_trades}，持仓 ${result.deleted_positions}，汇总持仓 ${result.deleted_holdings}，计划 ${result.deleted_trade_plans}，信号 ${result.deleted_trade_signals}`
      )
    } catch (err: unknown) {
      setStatus((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  async function handleClearAll() {
    const confirmed = window.confirm(
      '该操作会永久删除所有跟踪数据：交易、持仓、持仓汇总、交易计划和交易信号。是否继续？'
    )
    if (!confirmed) return

    setLoading(true)
    setStatus('')
    try {
      const result = (await clearPortfolioTracking()) as {
        deleted_trades: number
        deleted_positions: number
        deleted_holdings: number
        deleted_trade_plans: number
        deleted_trade_signals: number
      }
      // Immediately clear local state to avoid any stale records rendered from delayed/cached reads.
      setPositions([])
      setTrades([])
      setPlans([])
      setSignals([])
      setAnalysisTasks({})
      pollTokenRef.current = {}
      setStatus(
        `已全部清空：交易 ${result.deleted_trades}，持仓 ${result.deleted_positions}，汇总持仓 ${result.deleted_holdings}，计划 ${result.deleted_trade_plans}，信号 ${result.deleted_trade_signals}`
      )
    } catch (err: unknown) {
      // Reload to recover view if clear request failed.
      await reloadAll().catch(() => undefined)
      setStatus((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className="screen">
      <div className="hero-block reveal-up">
        <h1>持仓跟踪</h1>
        <p>记录真实交易、跟踪持仓，并按股票生成交易计划与建议。</p>
        <form className="inline-form" onSubmit={handleSubmitTrade}>
          <input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} placeholder="股票代码" />
          <select value={side} onChange={(e) => setSide(e.target.value as 'buy' | 'sell')}>
            <option value="buy">买入</option>
            <option value="sell">卖出</option>
          </select>
          <input
            type="number"
            min={1}
            value={quantity}
            onChange={(e) => setQuantity(Number(e.target.value))}
            placeholder="股数"
          />
          <input
            type="number"
            min={0.01}
            step="0.01"
            value={price}
            onChange={(e) => setPrice(Number(e.target.value))}
            placeholder="价格"
          />
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="备注（可选）" />
          <button className="btn solid" type="submit" disabled={loading}>记录交易</button>
          <button className="btn invert" type="button" onClick={handleRefresh} disabled={loading}>
            刷新
          </button>
          <button className="btn danger" type="button" onClick={handleClearAll} disabled={loading}>
            清空记录
          </button>
        </form>
        {status && <div className="inline-status">{status}</div>}
      </div>

      <div className="paper reveal-up delay-1">
        <div className="paper-header">
          <h2>当前持仓（按股票分组）</h2>
        </div>

        <div className="list-stack">
          {groupedSymbols.map((stockSymbol) => {
            const position = positionBySymbol.get(stockSymbol)
            const stockTrades = (tradesBySymbol.get(stockSymbol) || []).slice(0, 8)
            const stockPlans = (plansBySymbol.get(stockSymbol) || []).slice(0, 3)
            const stockSignals = (signalsBySymbol.get(stockSymbol) || []).slice(0, 4)
            return (
              <article className="list-item" key={stockSymbol}>
                <div className="list-content">
                  <div className="row-title">{stockSymbol}</div>
                  {position ? (
                    <>
                      <div className="row-sub">
                        持仓 {position.quantity.toFixed(2)} 股 | 均价 {position.avg_price.toFixed(2)} | {position.status}
                      </div>
                      <div className="row-sub">更新时间 {new Date(position.updated_at).toLocaleString()}</div>
                    </>
                  ) : (
                    <div className="row-sub">当前无持仓（仅保留历史交易）。</div>
                  )}

                  <div className="row-sub">交易记录：</div>
                  {stockTrades.length > 0 ? (
                    stockTrades.map((row) => (
                      <div className="row-sub" key={row.id}>
                        {new Date(row.trade_time).toLocaleString()} | {tradeSideLabel(row.side)} {row.quantity} 股 @ {row.price}
                        {row.note ? ` | ${row.note}` : ''}
                      </div>
                    ))
                  ) : (
                    <div className="row-sub">暂无交易记录。</div>
                  )}

                  {stockPlans.length > 0 && (
                    <>
                      <div className="row-sub">近期计划：</div>
                      {stockPlans.map((row) => (
                        <div className="row-sub" key={row.id}>
                          #{row.id} {tradeSideLabel(row.side)} | 数量 {row.suggested_shares} | 止损 {row.stop_loss_price ?? '-'} | 止盈 {row.take_profit_price ?? '-'}
                        </div>
                      ))}
                    </>
                  )}

                  {stockSignals.length > 0 && (
                    <>
                      <div className="row-sub">近期信号：</div>
                      {stockSignals.map((row) => (
                        <div className="row-sub" key={row.id}>
                          {new Date(row.created_at).toLocaleString()} | {row.signal_type} {tradeSideLabel(row.side)} | 置信度 {row.confidence.toFixed(2)}
                        </div>
                      ))}
                    </>
                  )}
                </div>

                <div className="list-side">
                  {position && (
                    <button className="mini-btn" type="button" onClick={() => handleClosePosition(position)}>
                      平仓
                    </button>
                  )}
                  <button className="mini-btn" type="button" onClick={() => handleGenerateAnalysis(stockSymbol)}>
                    生成分析
                  </button>
                  {stockPlans.length > 0 && (
                    <button
                      className="mini-btn"
                      type="button"
                      onClick={() => handleGenerateSignal(stockPlans[0].id, stockSymbol)}
                    >
                      生成信号
                    </button>
                  )}
                  {(analysisTasks[stockSymbol]?.status === 'completed' || analysisTasks[stockSymbol]?.analysis_id) && (
                    <button className="mini-btn" type="button" onClick={() => handleViewDetail(stockSymbol)}>
                      查看详情
                    </button>
                  )}
                  <button className="mini-btn" type="button" onClick={() => handleClearSymbol(stockSymbol)}>
                    清空该股票
                  </button>
                </div>
                {analysisTasks[stockSymbol] && (
                  <div style={{ gridColumn: '1 / -1', marginTop: 6 }}>
                    <div className="row-sub" style={{ marginBottom: 6 }}>
                      分析状态：{analysisStatusLabel(analysisTasks[stockSymbol].status)} | 当前阶段：{analysisStageLabel(analysisTasks[stockSymbol].stage)}
                    </div>
                    <div
                      style={{
                        width: '100%',
                        height: 8,
                        borderRadius: 999,
                        background: 'rgba(31,39,50,0.12)',
                        overflow: 'hidden',
                      }}
                    >
                      <div
                        style={{
                          width: `${Math.min(
                            100,
                            Math.round(
                              ((analysisTasks[stockSymbol].current_step || 0) /
                                Math.max(1, analysisTasks[stockSymbol].total_steps || ANALYSIS_STAGE_LABELS.length)) *
                                100
                            )
                          )}%`,
                          height: '100%',
                          background:
                            analysisTasks[stockSymbol].status === 'failed'
                              ? '#c94848'
                              : analysisTasks[stockSymbol].status === 'completed'
                                ? '#66d18f'
                                : '#4f87d8',
                          transition: 'width .3s ease',
                        }}
                      />
                    </div>
                    <div className="row-sub" style={{ marginTop: 6 }}>
                      {ANALYSIS_STAGE_LABELS.map((label, idx) => {
                        const done = idx < (analysisTasks[stockSymbol].current_step || 0)
                        return (
                          <span
                            key={`${stockSymbol}-${label}`}
                            className="signal-pill"
                            style={{
                              marginRight: 6,
                              opacity: done ? 1 : 0.35,
                              borderColor: done ? '#66d18f' : undefined,
                            }}
                          >
                            {label}
                          </span>
                        )
                      })}
                    </div>
                    {analysisTasks[stockSymbol].message && (
                      <div className="row-sub" style={{ marginTop: 6 }}>
                        {analysisTasks[stockSymbol].message}
                      </div>
                    )}
                    {analysisTasks[stockSymbol].error && (
                      <div className="row-sub" style={{ marginTop: 6, color: '#9f3d3d' }}>
                        {analysisTasks[stockSymbol].error}
                      </div>
                    )}
                  </div>
                )}
              </article>
            )
          })}
          {groupedSymbols.length === 0 && <div className="empty-line">暂无持仓或交易记录。</div>}
        </div>
      </div>
    </section>
  )
}
