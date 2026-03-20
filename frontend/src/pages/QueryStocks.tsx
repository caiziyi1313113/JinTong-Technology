import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  computeStockSentiment,
  createAnalysisTask,
  getAnalysisTask,
  getLatestAnalysisBySymbol,
  getLatestStockSentiment,
  getStockKline,
  getUserId,
} from '../api'

type ExpertSignal = {
  expert_name: string
  signal: string
  score: number
  confidence: number
  horizon: string
  key_factors: string[]
  risk_flags: string[]
}

type AnalysisResult = {
  id: number
  stock_symbol: string
  final_action: string
  position_size: number
  risk_notes: string[]
  rationale: {
    aggregate?: Record<string, any>
    investment?: Record<string, any>
    context?: Record<string, any>
  }
  expert_signals: ExpertSignal[]
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
  result?: AnalysisResult
  created_at?: string
  updated_at?: string
}

type KlinePeriod = 'daily' | 'weekly' | 'monthly'

type KlinePoint = {
  trade_date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

type KlineResponse = {
  symbol: string
  period: KlinePeriod
  items: KlinePoint[]
}

type SentimentItem = {
  source_type: 'news' | 'guba'
  external_id?: string | null
  source_url?: string | null
  title?: string | null
  text: string
  label: string
  positive_prob: number
  neutral_prob: number
  negative_prob: number
  score_raw: number
  score_norm: number
  published_at?: string | null
  extra?: Record<string, unknown>
}

type SentimentDaily = {
  trade_date: string
  news_count: number
  guba_count: number
  news_score_raw: number
  news_score_norm: number
  guba_score_raw: number
  guba_score_norm: number
  combined_score_raw: number
  combined_score_norm: number
  sentiment_label: string
  trend_deltas: number[]
  trend_5d?: number | null
  trend_signal: string
  trend_conclusion?: string | null
  valuation_level: string
  valuation_reason?: string | null
  strategy_matrix_advice?: string | null
  strategy_summary?: string | null
  corr_with_next_return?: number | null
  corr_sample_size: number
  reliability_level: string
  open?: number | null
  high?: number | null
  low?: number | null
  close?: number | null
  volume?: number | null
}

type SentimentResult = {
  symbol: string
  trade_date: string
  latest: SentimentDaily
  recent_series: SentimentDaily[]
  news_items: SentimentItem[]
  guba_items: SentimentItem[]
}

const STAGE_LABELS = ['数据刷新', '新闻专家', '行情专家', '宏观专家', '财务专家', '基本面专家', '投资专家']

const STORAGE_SYMBOL = 'stockai.query.symbol'
const STORAGE_STATUS = 'stockai.query.status'
const STORAGE_TASK = 'stockai.query.task' // backward compatibility key (legacy single-task shape)
const STORAGE_RESULT = 'stockai.query.result' // backward compatibility key (legacy single-result shape)
const STORAGE_TASK_MAP = 'stockai.query.task_map'
const STORAGE_RESULT_MAP = 'stockai.query.result_map'
const STORAGE_ACTIVE_SYMBOL = 'stockai.query.active_symbol'
const SHOW_INLINE_RESULT_PANEL = false
const SHOW_QUERY_SENTIMENT_PANEL = false
const CHART_WIDTH = 920
const CHART_HEIGHT = 430
const PAD_LEFT = 58
const PAD_RIGHT = 14
const PRICE_TOP = 18
const PRICE_BOTTOM = 256
const VOL_TOP = 286
const VOL_BOTTOM = 394
const MIN_WINDOW = 20
const DEFAULT_WINDOW = 80

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value))
}

function clampWindow(start: number, size: number, total: number): { start: number; size: number } {
  if (total <= 0) return { start: 0, size: 0 }
  const finalSize = clamp(size, Math.min(MIN_WINDOW, total), total)
  const finalStart = clamp(start, 0, Math.max(0, total - finalSize))
  return { start: finalStart, size: finalSize }
}

function formatNumber(value: number | undefined, digits = 2): string {
  if (!Number.isFinite(value)) return '-'
  return Number(value).toFixed(digits)
}

function decisionLabel(action: string | undefined): string {
  const normalized = String(action || '').trim().toLowerCase()
  if (normalized === 'buy') return '买入'
  if (normalized === 'sell') return '卖出'
  if (normalized === 'reduce') return '减仓'
  if (normalized === 'hold') return '持有'
  return '不买入'
}

function taskStatusLabel(status: AnalysisTask['status'] | string | undefined) {
  const normalized = String(status || '').trim().toLowerCase()
  if (normalized === 'queued') return '排队中'
  if (normalized === 'running') return '运行中'
  if (normalized === 'completed') return '已完成'
  if (normalized === 'failed') return '失败'
  return status || '-'
}

function taskStageLabel(stage: string | undefined) {
  const normalized = String(stage || '').trim().toLowerCase()
  const map: Record<string, string> = {
    submitting: '提交任务',
    completed: '已完成',
    failed: '失败',
    data_refresh: '数据刷新',
    news: '新闻专家',
    stock_data: '行情专家',
    macro: '宏观专家',
    financial: '财务专家',
    fundamental: '基本面专家',
    investment: '投资专家',
  }
  return map[normalized] || stage || '-'
}
export default function QueryStocks() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [symbol, setSymbol] = useState<string>('000001')
  const [activeSymbol, setActiveSymbol] = useState<string>('')
  const [loading, setLoading] = useState<boolean>(false)
  const [status, setStatus] = useState<string>('')
  const [analysisTasks, setAnalysisTasks] = useState<Record<string, AnalysisTask>>({})
  const [analysisResults, setAnalysisResults] = useState<Record<string, AnalysisResult>>({})
  const [period, setPeriod] = useState<KlinePeriod>('daily')
  const [klineItems, setKlineItems] = useState<KlinePoint[]>([])
  const [chartStatus, setChartStatus] = useState<string>('')
  const [chartLoading, setChartLoading] = useState<boolean>(false)
  const [sentimentResult, setSentimentResult] = useState<SentimentResult | null>(null)
  const [sentimentLoading, setSentimentLoading] = useState<boolean>(false)
  const [sentimentUpdating, setSentimentUpdating] = useState<boolean>(false)
  const [sentimentStatus, setSentimentStatus] = useState<string>('')
  const [windowStart, setWindowStart] = useState<number>(0)
  const [windowSize, setWindowSize] = useState<number>(DEFAULT_WINDOW)
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null)
  const [isDragging, setIsDragging] = useState<boolean>(false)
  const mountedRef = useRef<boolean>(true)
  const hydratedRef = useRef<boolean>(false)
  const pollTokenRef = useRef<Record<string, number>>({})
  const dragRef = useRef<{ pointerId: number; startX: number; originStart: number } | null>(null)

  const result = activeSymbol ? analysisResults[activeSymbol] || null : null

  useEffect(() => {
    // React.StrictMode in development mounts/unmounts effects twice.
    // Always reset mounted flag on effect enter to keep async guards valid.
    mountedRef.current = true

    const urlSymbol = (searchParams.get('symbol') || '').trim().toUpperCase()
    const savedSymbol = localStorage.getItem(STORAGE_SYMBOL)
    const savedStatus = localStorage.getItem(STORAGE_STATUS)
    const savedActiveSymbol = (localStorage.getItem(STORAGE_ACTIVE_SYMBOL) || '').trim().toUpperCase()
    const initialSymbol = urlSymbol || savedActiveSymbol || (savedSymbol || '').trim().toUpperCase()
    if (initialSymbol) {
      setSymbol(initialSymbol)
      setActiveSymbol(initialSymbol)
    } else if (savedSymbol) {
      setSymbol(savedSymbol)
    }
    if (savedStatus) setStatus(savedStatus)

    let parsedTaskMap: Record<string, AnalysisTask> = {}
    const savedTaskMap = localStorage.getItem(STORAGE_TASK_MAP)
    if (savedTaskMap) {
      try {
        const parsed = JSON.parse(savedTaskMap) as Record<string, AnalysisTask>
        if (parsed && typeof parsed === 'object') {
          parsedTaskMap = parsed
        }
      } catch {
        localStorage.removeItem(STORAGE_TASK_MAP)
      }
    }
    // backward compatibility for legacy single-task storage.
    if (Object.keys(parsedTaskMap).length === 0) {
      const savedTask = localStorage.getItem(STORAGE_TASK)
      try {
        const single = savedTask ? (JSON.parse(savedTask) as AnalysisTask) : null
        if (single?.stock_symbol) {
          parsedTaskMap = { [single.stock_symbol.toUpperCase()]: single }
        }
      } catch {
        localStorage.removeItem(STORAGE_TASK)
      }
    }
    setAnalysisTasks(parsedTaskMap)

    let parsedResultMap: Record<string, AnalysisResult> = {}
    const savedResultMap = localStorage.getItem(STORAGE_RESULT_MAP)
    if (savedResultMap) {
      try {
        const parsed = JSON.parse(savedResultMap) as Record<string, AnalysisResult>
        if (parsed && typeof parsed === 'object') {
          parsedResultMap = parsed
        }
      } catch {
        localStorage.removeItem(STORAGE_RESULT_MAP)
      }
    }
    // backward compatibility for legacy single-result storage.
    if (Object.keys(parsedResultMap).length === 0) {
      const savedResult = localStorage.getItem(STORAGE_RESULT)
      try {
        const single = savedResult ? (JSON.parse(savedResult) as AnalysisResult) : null
        if (single?.stock_symbol) {
          parsedResultMap = { [single.stock_symbol.toUpperCase()]: single }
        }
      } catch {
        localStorage.removeItem(STORAGE_RESULT)
      }
    }
    setAnalysisResults(parsedResultMap)
    if (!initialSymbol) {
      const fallbackActive = Object.keys(parsedResultMap)[0] || Object.keys(parsedTaskMap)[0] || ''
      if (fallbackActive) {
        setActiveSymbol(fallbackActive)
        setSymbol(fallbackActive)
      }
    }
    if (Object.keys(parsedTaskMap).length === 0 && Object.keys(parsedResultMap).length === 0 && initialSymbol) {
      void (async () => {
        try {
          const latest = (await getLatestAnalysisBySymbol(initialSymbol)) as AnalysisResult
          if (!mountedRef.current || !latest?.stock_symbol) return
          const normalized = latest.stock_symbol.toUpperCase()
          setAnalysisResults((prev) => ({ ...prev, [normalized]: latest }))
          setAnalysisTasks((prev) => ({
            ...prev,
            [normalized]: {
              task_id: `history-${latest.id}`,
              stock_symbol: normalized,
              status: 'completed',
              current_step: STAGE_LABELS.length,
              total_steps: STAGE_LABELS.length,
              stage: 'completed',
              message: '已恢复历史分析结果',
              analysis_id: latest.id,
              result: latest,
            },
          }))
          setActiveSymbol(normalized)
          setSymbol(normalized)
          setStatus(`已恢复 ${normalized} 的最近分析`)
        } catch {
          // ignore when no historical analysis exists
        }
      })()
    }
    for (const [stockSymbol, savedTask] of Object.entries(parsedTaskMap)) {
      if (
        savedTask &&
        savedTask.task_id &&
        savedTask.task_id !== 'pending' &&
        (savedTask.status === 'queued' || savedTask.status === 'running')
      ) {
        void pollTask(stockSymbol, savedTask.task_id, 900)
      }
    }
    hydratedRef.current = true

    return () => {
      mountedRef.current = false
      pollTokenRef.current = {}
    }
  }, [])

  useEffect(() => {
    localStorage.setItem(STORAGE_SYMBOL, symbol)
  }, [symbol])

  useEffect(() => {
    if (status) localStorage.setItem(STORAGE_STATUS, status)
    else localStorage.removeItem(STORAGE_STATUS)
  }, [status])

  useEffect(() => {
    if (activeSymbol) localStorage.setItem(STORAGE_ACTIVE_SYMBOL, activeSymbol)
    else localStorage.removeItem(STORAGE_ACTIVE_SYMBOL)
  }, [activeSymbol])

  useEffect(() => {
    if (!hydratedRef.current) return
    if (Object.keys(analysisTasks).length > 0) {
      localStorage.setItem(STORAGE_TASK_MAP, JSON.stringify(analysisTasks))
    }
    // clear legacy single-task cache to avoid stale polling ids
    localStorage.removeItem(STORAGE_TASK)
  }, [analysisTasks])

  useEffect(() => {
    if (!hydratedRef.current) return
    if (Object.keys(analysisResults).length > 0) {
      localStorage.setItem(STORAGE_RESULT_MAP, JSON.stringify(analysisResults))
    }
    // clear legacy single-result cache
    localStorage.removeItem(STORAGE_RESULT)
  }, [analysisResults])

  useEffect(() => {
    let cancelled = false

    async function loadKline() {
      const targetSymbol = result?.stock_symbol?.trim().toUpperCase()
      if (!targetSymbol) {
        setKlineItems([])
        setChartStatus('')
        setWindowStart(0)
        setWindowSize(DEFAULT_WINDOW)
        setSelectedIndex(null)
        return
      }
      setChartLoading(true)
      setChartStatus('')
      try {
        const response = (await getStockKline(targetSymbol, period, 420)) as KlineResponse
        if (cancelled) return
        const items = response.items || []
        setKlineItems(items)
        if (items.length === 0) {
          setWindowStart(0)
          setWindowSize(0)
          setSelectedIndex(null)
          setChartStatus('该周期暂无K线数据。')
          return
        }
        const initSize = Math.min(DEFAULT_WINDOW, items.length)
        const initStart = Math.max(0, items.length - initSize)
        setWindowStart(initStart)
        setWindowSize(initSize)
        setSelectedIndex(items.length - 1)
      } catch (err: unknown) {
        if (cancelled) return
        setChartStatus((err as Error).message)
      } finally {
        if (!cancelled) setChartLoading(false)
      }
    }

    void loadKline()
    return () => {
      cancelled = true
    }
  }, [result?.stock_symbol, period])

  useEffect(() => {
    let cancelled = false

    async function loadSentiment() {
      if (!SHOW_QUERY_SENTIMENT_PANEL) {
        setSentimentResult(null)
        setSentimentStatus('')
        setSentimentLoading(false)
        return
      }
      const targetSymbol = result?.stock_symbol?.trim().toUpperCase()
      if (!targetSymbol) {
        setSentimentResult(null)
        setSentimentStatus('')
        return
      }
      setSentimentLoading(true)
      setSentimentStatus('')
      try {
        const row = (await getLatestStockSentiment(targetSymbol, 30, 10)) as SentimentResult
        if (cancelled) return
        setSentimentResult(row)
      } catch (err: unknown) {
        if (cancelled) return
        setSentimentResult(null)
        setSentimentStatus((err as Error).message)
      } finally {
        if (!cancelled) setSentimentLoading(false)
      }
    }

    void loadSentiment()
    return () => {
      cancelled = true
    }
  }, [result?.stock_symbol])

  async function resolveLatestAnalysisForSymbol(stockSymbol: string): Promise<AnalysisResult | null> {
    try {
      return (await getLatestAnalysisBySymbol(stockSymbol)) as AnalysisResult
    } catch {
      return null
    }
  }

  async function pollTask(stockSymbol: string, taskId: string, maxRounds = 600): Promise<boolean> {
    const token = Date.now()
    pollTokenRef.current[stockSymbol] = token
    let finished = false

    for (let i = 0; i < maxRounds; i += 1) {
      if (!mountedRef.current || pollTokenRef.current[stockSymbol] !== token) return false
      let latest: AnalysisTask
      try {
        latest = (await getAnalysisTask(taskId)) as AnalysisTask
      } catch (err: unknown) {
        const message = (err as Error).message || ''
        const maybeNotFound = /not found/i.test(message)
        if (maybeNotFound) {
          // Backend in-memory tasks may disappear after restart/cleanup.
          // Fallback to latest analysis record and recover UI state.
          const fallback = await resolveLatestAnalysisForSymbol(stockSymbol)
          if (fallback) {
            const recoveredTask: AnalysisTask = {
              task_id: taskId,
              stock_symbol: stockSymbol,
              status: 'completed',
              current_step: STAGE_LABELS.length,
              total_steps: STAGE_LABELS.length,
              stage: 'completed',
              message: '已从最新分析记录恢复',
              analysis_id: fallback.id,
              result: fallback,
            }
            setAnalysisTasks((prev) => ({ ...prev, [stockSymbol]: recoveredTask }))
            setAnalysisResults((prev) => ({ ...prev, [stockSymbol]: fallback }))
            setActiveSymbol(stockSymbol)
            setStatus(`分析已完成：编号=${fallback.id}`)
            return true
          }
        }
        const retryMs = i > 80 ? 3500 : 1800
        await sleep(retryMs)
        if (i >= maxRounds - 1) {
          setStatus(`任务轮询失败：${message}`)
        }
        continue
      }
      if (!mountedRef.current || pollTokenRef.current[stockSymbol] !== token) return false

      setAnalysisTasks((prev) => ({ ...prev, [stockSymbol]: latest }))
      if (latest.message) {
        setStatus(latest.message)
      }

      if (latest.status === 'completed') {
        let resolvedResult = latest.result as AnalysisResult | undefined
        if (!resolvedResult?.id) {
          resolvedResult = (await resolveLatestAnalysisForSymbol(stockSymbol)) || undefined
        }
        if (resolvedResult?.id) {
          setAnalysisResults((prev) => ({ ...prev, [stockSymbol]: resolvedResult! }))
          setStatus(`分析已完成：编号=${resolvedResult.id}`)
        } else if (latest.analysis_id) {
          setStatus(`分析已完成：编号=${latest.analysis_id}`)
        } else {
          setStatus('分析已完成')
        }
        setActiveSymbol(stockSymbol)
        finished = true
        break
      }

      if (latest.status === 'failed') {
        setStatus(latest.error || '分析失败')
        finished = true
        break
      }
      const elapsedRounds = i + 1
      const baseWaitMs = elapsedRounds > 120 ? 3000 : 1500
      const waitMs = document.visibilityState === 'hidden' ? baseWaitMs + 2000 : baseWaitMs
      await sleep(waitMs)
    }

    return finished
  }

  async function runAnalysisForSymbol(rawSymbol: string) {
    const userId = getUserId()
    if (!userId) {
      setStatus('登录态已失效，请重新登录。')
      return
    }

    setLoading(true)
    setStatus('')
    const normalizedSymbol = rawSymbol.toUpperCase().trim()
    setSymbol(normalizedSymbol)
    setActiveSymbol(normalizedSymbol)
    setSentimentResult(null)
    setSentimentStatus('')
    const pendingTask: AnalysisTask = {
      task_id: 'pending',
      stock_symbol: normalizedSymbol,
      status: 'queued',
      current_step: 0,
      total_steps: STAGE_LABELS.length,
      stage: 'submitting',
      message: '任务正在提交，等待后端排队',
    }
    setAnalysisTasks((prev) => ({ ...prev, [normalizedSymbol]: pendingTask }))
    setStatus('任务提交中：将先检查数据是否更新，再按需复用或重算专家结果')
    setSearchParams({ symbol: normalizedSymbol }, { replace: true })

    try {
      const created = (await createAnalysisTask(normalizedSymbol, userId)) as AnalysisTask
      if (!mountedRef.current) return
      setAnalysisTasks((prev) => ({ ...prev, [normalizedSymbol]: created }))
      setStatus(`任务已创建：${created.task_id.slice(0, 8)}，正在排队/运行`)
      void pollTask(normalizedSymbol, created.task_id)
    } catch (err: unknown) {
      if (!mountedRef.current) return
      setAnalysisTasks((prev) => ({
        ...prev,
        [normalizedSymbol]: {
          ...pendingTask,
          status: 'failed',
          stage: 'failed',
          error: (err as Error).message,
          message: '任务提交失败',
        },
      }))
      setStatus((err as Error).message)
    } finally {
      if (mountedRef.current) {
        setLoading(false)
      }
    }
  }

  async function handleQuery(e: React.FormEvent) {
    e.preventDefault()
    await runAnalysisForSymbol(symbol)
  }

  async function handleOpenStockDetail(stockSymbol: string) {
    let analysisId = analysisResults[stockSymbol]?.id
    if (!analysisId) {
      const latest = await resolveLatestAnalysisForSymbol(stockSymbol)
      if (latest?.id) {
        analysisId = latest.id
        setAnalysisResults((prev) => ({ ...prev, [stockSymbol]: latest }))
      }
    }
    const search = analysisId ? `?analysis_id=${analysisId}` : ''
    navigate(`/stock/${stockSymbol}${search}`, { state: { from: 'query' } })
  }

  async function handleComputeTodaySentiment() {
    const targetSymbol = result?.stock_symbol?.trim().toUpperCase()
    if (!targetSymbol || sentimentUpdating) return
    setSentimentUpdating(true)
    setSentimentStatus('正在计算今日情绪...')
    try {
      const today = new Date().toISOString().slice(0, 10)
      const row = (await computeStockSentiment(targetSymbol, { trade_date: today, persist: true })) as SentimentResult
      setSentimentResult(row)
      setSentimentStatus(`情绪数据已更新：${today}`)
    } catch (err: unknown) {
      setSentimentStatus((err as Error).message)
    } finally {
      setSentimentUpdating(false)
    }
  }

  const aggregate = result?.rationale?.aggregate
  const investment = result?.rationale?.investment
  const latestSentiment = sentimentResult?.latest || null
  const recentSentimentSeries = sentimentResult?.recent_series || []
  const newsSentimentItems = sentimentResult?.news_items || []
  const gubaSentimentItems = sentimentResult?.guba_items || []
  const analysisCards = useMemo(() => {
    const symbols = new Set<string>([...Object.keys(analysisTasks), ...Object.keys(analysisResults)])
    return Array.from(symbols)
      .map((stockSymbol) => ({
        stockSymbol,
        task: analysisTasks[stockSymbol] || null,
        result: analysisResults[stockSymbol] || null,
      }))
      .sort((a, b) => {
        const aTaskTime = a.task?.updated_at ? new Date(a.task.updated_at).getTime() : 0
        const bTaskTime = b.task?.updated_at ? new Date(b.task.updated_at).getTime() : 0
        if (aTaskTime !== bTaskTime) return bTaskTime - aTaskTime
        const aResultTime = a.result?.id || 0
        const bResultTime = b.result?.id || 0
        return bResultTime - aResultTime
      })
  }, [analysisTasks, analysisResults])

  const visibleItems = useMemo(() => {
    if (klineItems.length === 0 || windowSize <= 0) return []
    const end = Math.min(klineItems.length, windowStart + windowSize)
    return klineItems.slice(windowStart, end)
  }, [klineItems, windowStart, windowSize])

  const chartModel = useMemo(() => {
    const plotWidth = CHART_WIDTH - PAD_LEFT - PAD_RIGHT
    const priceHeight = PRICE_BOTTOM - PRICE_TOP
    const volumeHeight = VOL_BOTTOM - VOL_TOP
    if (visibleItems.length === 0) {
      return {
        plotWidth,
        priceHeight,
        volumeHeight,
        maxPrice: 1,
        minPrice: 0,
        maxVolume: 1,
        step: plotWidth,
      }
    }

    let maxPrice = Number.NEGATIVE_INFINITY
    let minPrice = Number.POSITIVE_INFINITY
    let maxVolume = 0
    for (const row of visibleItems) {
      maxPrice = Math.max(maxPrice, row.high)
      minPrice = Math.min(minPrice, row.low)
      maxVolume = Math.max(maxVolume, row.volume)
    }

    const range = Math.max(1e-9, maxPrice - minPrice)
    const pad = range * 0.08
    maxPrice += pad
    minPrice -= pad

    return {
      plotWidth,
      priceHeight,
      volumeHeight,
      maxPrice,
      minPrice,
      maxVolume: Math.max(1, maxVolume),
      step: visibleItems.length > 1 ? plotWidth / (visibleItems.length - 1) : plotWidth,
    }
  }, [visibleItems])

  const selectedPoint = useMemo(() => {
    if (selectedIndex === null) return null
    if (selectedIndex < 0 || selectedIndex >= klineItems.length) return null
    return klineItems[selectedIndex]
  }, [klineItems, selectedIndex])

  const selectedVisibleIndex = useMemo(() => {
    if (selectedIndex === null) return null
    const idx = selectedIndex - windowStart
    if (idx < 0 || idx >= visibleItems.length) return null
    return idx
  }, [selectedIndex, windowStart, visibleItems.length])

  function xFor(index: number): number {
    return PAD_LEFT + chartModel.step * index
  }

  function yForPrice(value: number): number {
    const ratio = (value - chartModel.minPrice) / Math.max(1e-9, chartModel.maxPrice - chartModel.minPrice)
    return PRICE_BOTTOM - ratio * chartModel.priceHeight
  }

  function yForVolume(value: number): number {
    return VOL_BOTTOM - (value / chartModel.maxVolume) * chartModel.volumeHeight
  }

  function selectByClientX(clientX: number, element: HTMLElement) {
    if (visibleItems.length === 0) return
    const rect = element.getBoundingClientRect()
    const xRatio = clamp((clientX - rect.left) / Math.max(1, rect.width), 0, 1)
    const xSvg = xRatio * CHART_WIDTH
    const idxInView = clamp(
      Math.round((xSvg - PAD_LEFT) / Math.max(1e-9, chartModel.step)),
      0,
      visibleItems.length - 1,
    )
    setSelectedIndex(windowStart + idxInView)
  }

  function handlePointerDown(event: React.PointerEvent<HTMLDivElement>) {
    if (visibleItems.length === 0) return
    event.currentTarget.setPointerCapture(event.pointerId)
    dragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      originStart: windowStart,
    }
    setIsDragging(true)
    selectByClientX(event.clientX, event.currentTarget)
  }

  function handlePointerMove(event: React.PointerEvent<HTMLDivElement>) {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== event.pointerId || visibleItems.length === 0) return
    const rect = event.currentTarget.getBoundingClientRect()
    const plotWidthPx = (rect.width * (CHART_WIDTH - PAD_LEFT - PAD_RIGHT)) / CHART_WIDTH
    const stepPx = plotWidthPx / Math.max(1, visibleItems.length - 1)
    const shift = Math.round(-(event.clientX - drag.startX) / Math.max(1e-9, stepPx))
    const clamped = clampWindow(drag.originStart + shift, windowSize, klineItems.length)
    setWindowStart(clamped.start)
    selectByClientX(event.clientX, event.currentTarget)
  }

  function endDrag(event: React.PointerEvent<HTMLDivElement>) {
    const drag = dragRef.current
    if (drag && drag.pointerId === event.pointerId) {
      dragRef.current = null
      setIsDragging(false)
    }
  }

  function handleWheel(event: React.WheelEvent<HTMLDivElement>) {
    if (klineItems.length === 0) return
    event.preventDefault()
    const rect = event.currentTarget.getBoundingClientRect()
    const ratio = clamp((event.clientX - rect.left) / Math.max(1, rect.width), 0, 1)
    const nextSize = event.deltaY < 0 ? Math.round(windowSize * 0.85) : Math.round(windowSize * 1.15)
    const centerIndex = windowStart + Math.round((windowSize - 1) * ratio)
    const tentativeStart = Math.round(centerIndex - ratio * (nextSize - 1))
    const clamped = clampWindow(tentativeStart, nextSize, klineItems.length)
    setWindowSize(clamped.size)
    setWindowStart(clamped.start)
  }
  return (
    <section className="screen">
      <div className="hero-block reveal-up">
        <h1>查询股票</h1>
        <p>输入股票代码，发起分析任务并查看每次查询卡片。</p>
        <form className="search-pill" onSubmit={handleQuery}>
          <input
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            placeholder="输入股票代码，例如 000001"
          />
          <button className="pill-button" type="submit" disabled={loading}>
            {loading ? '提交中...' : '查询'}
          </button>
        </form>
        {status && <div className="inline-status">{status}</div>}
      </div>

      <div className="paper reveal-up delay-1">
        <div className="paper-header with-top-line">
          <h3>查询记录</h3>
        </div>
        {analysisCards.length > 0 ? (
          <div className="list-stack">
            {analysisCards.map(({ stockSymbol, task, result: cardResult }) => {
              const totalSteps = Math.max(1, task?.total_steps || STAGE_LABELS.length)
              const currentStep = clamp(task?.current_step || 0, 0, totalSteps)
              const progress = Math.round((currentStep / totalSteps) * 100)
              const isActive = stockSymbol === activeSymbol
              return (
                <article className="list-item" key={stockSymbol}>
                  <div className="list-content">
                    <div className="row-title">{stockSymbol}{isActive ? '（当前）' : ''}</div>
                    <div className="row-sub">
                      状态 {taskStatusLabel(task?.status)} | 阶段 {taskStageLabel(task?.stage)} | 进度 {currentStep}/{totalSteps} ({progress}%)
                    </div>
                    {task?.queue_position !== null && task?.queue_position !== undefined && (
                      <div className="row-sub">排队位置 {task.queue_position}</div>
                    )}
                    {task?.message && <div className="row-sub">{task.message}</div>}
                    {task?.error && <div className="row-sub">{task.error}</div>}
                    {cardResult && (
                      <div className="row-sub">
                        决策 {decisionLabel(cardResult.final_action)}
                      </div>
                    )}
                    <div className="control-row">
                      <button
                        className="btn invert"
                        type="button"
                        onClick={() => {
                          setActiveSymbol(stockSymbol)
                          setSymbol(stockSymbol)
                          setSearchParams({ symbol: stockSymbol }, { replace: true })
                        }}
                      >
                        选中
                      </button>
                      <button
                        className="btn solid"
                        type="button"
                        onClick={() => runAnalysisForSymbol(stockSymbol)}
                        disabled={loading}
                      >
                        重新分析
                      </button>
                      {cardResult && (
                        <button className="btn solid" type="button" onClick={() => handleOpenStockDetail(stockSymbol)}>
                          打开详情
                        </button>
                      )}
                    </div>
                  </div>
                </article>
              )
            })}
          </div>
        ) : (
          <div className="empty-line">暂无查询记录，请先输入股票代码发起分析。</div>
        )}
      </div>

      {SHOW_QUERY_SENTIMENT_PANEL && activeSymbol && (
        <>
          <div className="paper-header with-top-line">
            <h3>情绪分析</h3>
          </div>
          <div className="control-row">
            <button
              className={`btn ${sentimentUpdating ? 'invert' : 'solid'}`}
              type="button"
              onClick={handleComputeTodaySentiment}
              disabled={sentimentUpdating}
            >
              {sentimentUpdating ? '计算中...' : '重新生成今日情绪'}
            </button>
          </div>
          {sentimentLoading && <div className="inline-status">加载情绪数据中...</div>}
          {sentimentStatus && <div className="inline-status">{sentimentStatus}</div>}
          {latestSentiment ? (
            <>
              <div className="metric-grid">
                <div className="metric-box"><span>交易日</span><strong>{latestSentiment.trade_date}</strong></div>
                <div className="metric-box"><span>综合分</span><strong>{latestSentiment.combined_score_norm.toFixed(3)}</strong></div>
                <div className="metric-box"><span>情绪标签</span><strong>{latestSentiment.sentiment_label}</strong></div>
                <div className="metric-box"><span>新闻得分</span><strong>{latestSentiment.news_score_norm.toFixed(3)}</strong></div>
                <div className="metric-box"><span>股吧得分</span><strong>{latestSentiment.guba_score_norm.toFixed(3)}</strong></div>
                <div className="metric-box"><span>样本数</span><strong>{latestSentiment.news_count + latestSentiment.guba_count}</strong></div>
                <div className="metric-box"><span>趋势信号</span><strong>{latestSentiment.trend_signal}</strong></div>
                <div className="metric-box"><span>5日趋势</span><strong>{latestSentiment.trend_5d === null || latestSentiment.trend_5d === undefined ? '-' : latestSentiment.trend_5d.toFixed(3)}</strong></div>
                <div className="metric-box"><span>可靠性(一致率)</span><strong>{latestSentiment.reliability_level}</strong></div>
              </div>

              <div className="trade-panel">
                <h3>策略应用</h3>
                <p>矩阵建议：{latestSentiment.strategy_matrix_advice || '-'}</p>
                <p>估值水平：{latestSentiment.valuation_level}</p>
                <p>估值依据：{latestSentiment.valuation_reason || '-'}</p>
                <p>
                  情绪-价格同日信号相关性：
                  {latestSentiment.corr_with_next_return === null || latestSentiment.corr_with_next_return === undefined
                    ? ' -'
                    : ` ${latestSentiment.corr_with_next_return.toFixed(3)}（${latestSentiment.corr_sample_size} 个样本）`}
                </p>
                <p>
                  变化序列：
                  {latestSentiment.trend_deltas?.length
                    ? ` ${latestSentiment.trend_deltas.map((v, idx) => `d${idx + 1}:${v >= 0 ? '+' : ''}${v.toFixed(3)}`).join(' | ')}`
                    : ' -'}
                </p>
                <p>趋势结论：{latestSentiment.trend_conclusion || '今日无明显特征。'}</p>
                <p>总结：{latestSentiment.strategy_summary || '-'}</p>
              </div>

              <div className="trade-panel">
                <h3>近期情绪序列</h3>
                {recentSentimentSeries.length > 0 ? (
                  <div className="list-stack">
                    {recentSentimentSeries.slice(0, 10).map((row) => (
                      <article className="list-item" key={row.trade_date}>
                        <div className="list-content">
                          <div className="row-title">{row.trade_date}</div>
                          <div className="row-sub">
                            综合 {row.combined_score_norm.toFixed(3)} | 新闻 {row.news_score_norm.toFixed(3)} |
                            股吧 {row.guba_score_norm.toFixed(3)} | 标签 {row.sentiment_label}
                          </div>
                          <div className="row-sub">
                            收盘 {row.close === null || row.close === undefined ? '-' : row.close.toFixed(2)} |
                            样本 {row.news_count + row.guba_count}
                          </div>
                        </div>
                      </article>
                    ))}
                  </div>
                ) : (
                  <p>暂无情绪历史。</p>
                )}
              </div>

              <div className="trade-panel">
                <h3>最新新闻样本</h3>
                {newsSentimentItems.length > 0 ? (
                  <div className="list-stack">
                    {newsSentimentItems.slice(0, 5).map((item, idx) => (
                      <article className="list-item" key={`news-${idx}-${item.title || item.text.slice(0, 16)}`}>
                        <div className="list-content">
                          <div className="row-title">{item.title || '新闻'}</div>
                          <div className="row-sub">
                            标签 {item.label} | 评分 {item.score_norm.toFixed(3)} | 正向 {item.positive_prob.toFixed(3)} |
                            负向 {item.negative_prob.toFixed(3)}
                          </div>
                          <div className="row-sub">{item.text}</div>
                        </div>
                      </article>
                    ))}
                  </div>
                ) : (
                  <p>暂无新闻情绪样本。</p>
                )}
              </div>

              <div className="trade-panel">
                <h3>最新股吧样本</h3>
                {gubaSentimentItems.length > 0 ? (
                  <div className="list-stack">
                    {gubaSentimentItems.slice(0, 5).map((item, idx) => (
                      <article className="list-item" key={`guba-${idx}-${item.external_id || item.text.slice(0, 16)}`}>
                        <div className="list-content">
                          <div className="row-title">{item.title || '股吧帖子'}</div>
                          <div className="row-sub">
                            标签 {item.label} | 评分 {item.score_norm.toFixed(3)} | 正向 {item.positive_prob.toFixed(3)} |
                            负向 {item.negative_prob.toFixed(3)}
                          </div>
                          <div className="row-sub">{item.text}</div>
                        </div>
                      </article>
                    ))}
                  </div>
                ) : (
                  <p>暂无股吧情绪样本。</p>
                )}
              </div>
            </>
          ) : (
            !sentimentLoading && <div className="empty-line">暂无情绪数据，请点击“重新生成今日情绪”。</div>
          )}
        </>
      )}

      {SHOW_INLINE_RESULT_PANEL && result && (
        <div className="paper reveal-up delay-1">
          <div className="paper-header with-top-line">
            <h3>分析指标</h3>
          </div>
          <div className="metric-grid">
            <div className="metric-box"><span>决策</span><strong>{decisionLabel(result.final_action)}</strong></div>
            <div className="metric-box"><span>仓位</span><strong>{(result.position_size * 100).toFixed(1)}%</strong></div>
            <div className="metric-box"><span>总分</span><strong>{aggregate?.total_score ?? '-'}</strong></div>
            <div className="metric-box"><span>数据驱动</span><strong>{aggregate?.data_drive_score ?? '-'}</strong></div>
            <div className="metric-box"><span>情绪驱动</span><strong>{aggregate?.emotion_drive_score ?? '-'}</strong></div>
            <div className="metric-box"><span>冲突</span><strong>{aggregate?.conflict_signal ? '冲突' : '一致'}</strong></div>
          </div>

          <div className="paper-header with-top-line">
            <h3>专家输出</h3>
          </div>
          <div className="list-stack">
            {result.expert_signals
              .filter((item) => item.expert_name !== 'investment')
              .map((item) => (
                <article className="list-item" key={item.expert_name}>
                  <div className="list-content">
                    <div className="row-title">{item.expert_name}</div>
                    <div className="row-sub">信号 {decisionLabel(item.signal)} | 评分 {item.score.toFixed(2)} | 置信度 {item.confidence.toFixed(3)}</div>
                    <div className="row-sub">{item.key_factors?.slice(0, 4).join(' | ') || '-'}</div>
                    <div className="row-sub">风险：{item.risk_flags?.slice(0, 2).join(' | ') || '-'}</div>
                  </div>
                </article>
              ))}
          </div>

          <div className="trade-panel">
            <h3>投资计划</h3>
            <p>信号：{decisionLabel(investment?.final_signal || investment?.signal || result.final_action)}</p>
            <p>总结：{investment?.summary || '-'}</p>
            <p>仓位比例：{investment?.position_management?.position_ratio ?? investment?.position_ratio ?? '-'}</p>
            <p>建议股数：{investment?.position_management?.suggested_shares ?? investment?.suggested_shares ?? '-'}</p>
            <p>
              买入区间：
              {Array.isArray(investment?.buy_strategy?.price_range)
                ? `${investment.buy_strategy.price_range[0]} - ${investment.buy_strategy.price_range[1]}`
                : investment?.buy_range?.min !== undefined && investment?.buy_range?.max !== undefined
                  ? `${investment.buy_range.min} - ${investment.buy_range.max}`
                  : '-'}
            </p>
            <p>止损：{investment?.stop_loss_plan?.stop_loss_price ?? investment?.stop_loss?.price ?? '-'}</p>
            {Array.isArray(investment?.execution_logic) && investment.execution_logic.length > 0 && (
              <>
                <p><strong>执行逻辑</strong></p>
                {investment.execution_logic.slice(0, 3).map((row: any, idx: number) => (
                  <p key={`exec-${idx}`}>{row?.title ? `${row.title}: ` : ''}{row?.content || '-'}</p>
                ))}
              </>
            )}
          </div>

          {result.risk_notes?.length > 0 && (
            <div className="signal-strip">
              风险：
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
