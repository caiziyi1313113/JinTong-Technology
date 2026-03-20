import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation, useParams } from 'react-router-dom'
import {
  computeStockSentiment,
  getAnalysis,
  getLatestAnalysisBySymbol,
  getLatestRanking,
  getLatestStockSentiment,
  getStock,
  getStockKline,
  listPortfolioTrades,
  listPositions,
} from '../api'

type RankingItem = {
  id: number
  stock_symbol: string
  rank: number
  total_score: number
  news_score: number
  stock_score: number
  macro_score: number
  financial_score: number
  fundamental_score: number
  data_drive_score: number
  emotion_drive_score: number
  conflict_signal: boolean
  recommendation_action: string
  recommendation_confidence: number
  recommendation_summary?: string
  expert_payload: Record<string, any>
  investment_payload: Record<string, any>
}

type ExpertSignal = {
  expert_name: string
  signal: string
  score: number
  confidence: number
  key_factors: string[]
  risk_flags: string[]
  evidence: Array<{ type: string; detail: string }>
}

type AnalysisResult = {
  id: number
  stock_symbol: string
  final_action: string
  position_size: number
  rationale: {
    aggregate?: Record<string, any>
    investment?: Record<string, any>
    experts?: Record<string, any>
  }
  expert_signals: ExpertSignal[]
  risk_notes: string[]
}

type RankingSnapshot = {
  id: number
  snapshot_date: string
  snapshot_type: string
  items: RankingItem[]
}

type StockInfo = {
  symbol: string
  name: string
  market: string
  sector?: string | null
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

type LocationState = {
  rankingItem?: RankingItem
  snapshotMeta?: {
    snapshot_id?: number
    snapshot_date?: string
    snapshot_type?: string
  }
  analysisId?: number
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

type Position = {
  id: number
  stock_symbol: string
  quantity: number
  avg_price: number
  status: string
  updated_at: string
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

function formatSentimentStatus(message: string | undefined): string {
  const text = String(message || '').trim()
  if (!text) return ''
  if (/sentiment data not found/i.test(text)) {
    return '\u6682\u65e0\u60c5\u7eea\u6570\u636e'
  }
  return text
}

function normalizeStockSymbol(value: string | undefined): string {
  const raw = String(value || '')
    .trim()
    .toUpperCase()
    .replace(/^(SH|SZ|BJ)/, '')
  if (/^\d{1,6}$/.test(raw)) {
    return raw.padStart(6, '0')
  }
  return raw
}

function buyDecisionLabel(action: string | undefined): string {
  const normalized = String(action || '').trim().toLowerCase()
  if (normalized === 'buy') return '买入'
  if (normalized === 'sell') return '卖出'
  if (normalized === 'reduce') return '减仓'
  if (normalized === 'hold') return '持有'
  return '不买入'
}

function readNumber(value: unknown): number | null {
  const num = Number(value)
  if (!Number.isFinite(num)) return null
  return num
}

function formatExpertPoint(point: unknown): string {
  if (typeof point === 'string') return point
  if (!point || typeof point !== 'object') return String(point || '')
  const row = point as Record<string, unknown>
  const fact = String(row.fact || '').trim()
  const interpretation = String(row.interpretation || '').trim()
  const meaning = String(row.investment_meaning || '').trim()
  return [fact, interpretation, meaning].filter(Boolean).join(' | ')
}

function formatExpertRisk(risk: unknown): string {
  if (typeof risk === 'string') return risk
  if (!risk || typeof risk !== 'object') return String(risk || '')
  const row = risk as Record<string, unknown>
  const main = String(row.risk || '').trim()
  const trigger = String(row.trigger || '').trim()
  const impact = String(row.impact || '').trim()
  return [main, trigger && `触发条件：${trigger}`, impact && `影响：${impact}`]
    .filter(Boolean)
    .join(' | ')
}

const EXPERT_LABEL_MAP: Record<string, string> = {
  news: '新闻专家',
  stock_data: '行情专家',
  macro: '宏观专家',
  financial: '财务专家',
  fundamental: '基本面专家',
}

function tradeSideLabel(side: string): string {
  const normalized = String(side || '').trim().toLowerCase()
  if (normalized === 'buy') return '买入'
  if (normalized === 'sell') return '卖出'
  return side
}
function formatFactorValue(value: unknown): string {
  const num = Number(value)
  if (!Number.isFinite(num)) return '-'
  return Math.abs(num) >= 1000 ? num.toLocaleString(undefined, { maximumFractionDigits: 2 }) : num.toFixed(4)
}

function normalizeQuantFactors(value: unknown): Array<{ name: string; value: number; unit?: string }> {
  if (!Array.isArray(value)) return []
  return value
    .map((item) => {
      if (!item || typeof item !== 'object') return null
      const row = item as Record<string, unknown>
      const name = String(row.name || '').trim()
      const num = Number(row.value)
      if (!name || !Number.isFinite(num)) return null
      const unit = String(row.unit || '').trim()
      return { name, value: num, unit: unit || undefined }
    })
    .filter(Boolean) as Array<{ name: string; value: number; unit?: string }>
}

function normalizeTextArray(value: unknown, limit = 8): string[] {
  if (!Array.isArray(value)) return []
  return value
    .map((item) => String(item || '').trim())
    .filter(Boolean)
    .slice(0, limit)
}

function formatWeightMap(value: unknown): string {
  if (!value || typeof value !== 'object') return '-'
  const entries = Object.entries(value as Record<string, unknown>)
    .map(([k, v]) => {
      const num = Number(v)
      if (Number.isFinite(num)) return `${k}:${num.toFixed(2)}`
      return `${k}:${String(v)}`
    })
    .filter(Boolean)
  return entries.length > 0 ? entries.join(' | ') : '-'
}

export default function StockDetail() {
  const { symbol = '' } = useParams()
  const location = useLocation()
  const state = (location.state || {}) as LocationState
  const normalized = normalizeStockSymbol(symbol)
  const searchParams = useMemo(() => new URLSearchParams(location.search), [location.search])
  const requestedAnalysisId = Number(searchParams.get('analysis_id') || state.analysisId || 0) || null
  const routeRankingItem =
    state.rankingItem && normalizeStockSymbol(state.rankingItem.stock_symbol) === normalized ? state.rankingItem : null

  const [rankingItem, setRankingItem] = useState<RankingItem | null>(routeRankingItem)
  const [analysisResult, setAnalysisResult] = useState<AnalysisResult | null>(null)
  const [stockInfo, setStockInfo] = useState<StockInfo | null>(null)
  const [symbolTrades, setSymbolTrades] = useState<PortfolioTrade[]>([])
  const [symbolPosition, setSymbolPosition] = useState<Position | null>(null)
  const [sentimentResult, setSentimentResult] = useState<SentimentResult | null>(null)
  const [sentimentLoading, setSentimentLoading] = useState<boolean>(false)
  const [sentimentUpdating, setSentimentUpdating] = useState<boolean>(false)
  const [sentimentStatus, setSentimentStatus] = useState<string>('')
  const [status, setStatus] = useState<string>('')

  const [period, setPeriod] = useState<KlinePeriod>('daily')
  const [klineItems, setKlineItems] = useState<KlinePoint[]>([])
  const [chartStatus, setChartStatus] = useState<string>('')
  const [chartLoading, setChartLoading] = useState<boolean>(false)
  const [windowStart, setWindowStart] = useState<number>(0)
  const [windowSize, setWindowSize] = useState<number>(DEFAULT_WINDOW)
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null)
  const [isDragging, setIsDragging] = useState<boolean>(false)
  const dragRef = useRef<{ pointerId: number; startX: number; originStart: number } | null>(null)

  useEffect(() => {
    const item = state.rankingItem
    if (item && normalizeStockSymbol(item.stock_symbol) === normalized) {
      setRankingItem(item)
      return
    }
    setRankingItem((prev) => (prev && normalizeStockSymbol(prev.stock_symbol) === normalized ? prev : null))
  }, [normalized, state.rankingItem])

  useEffect(() => {
    let cancelled = false

    async function loadStockInfo() {
      if (!normalized) return
      try {
        const row = (await getStock(normalized)) as StockInfo
        if (cancelled) return
        setStockInfo(row)
      } catch (err: unknown) {
        if (cancelled) return
        setStatus((prev) => prev || `加载股票资料失败：${(err as Error).message}`)
      }
    }

    async function loadAnalysis() {
      if (!normalized) return
      try {
        if (requestedAnalysisId) {
          const row = (await getAnalysis(String(requestedAnalysisId))) as AnalysisResult
          if (cancelled) return
          setAnalysisResult(row)
          return
        }
        const latest = (await getLatestAnalysisBySymbol(normalized)) as AnalysisResult
        if (cancelled) return
        setAnalysisResult(latest)
      } catch {
        if (!cancelled) {
          setAnalysisResult(null)
        }
      }
    }

    async function loadPortfolio() {
      if (!normalized) return
      const [tradeResult, positionResult] = await Promise.allSettled([
        listPortfolioTrades(normalized, 300),
        listPositions(true),
      ])
      if (cancelled) return

      if (tradeResult.status === 'fulfilled') {
        const typedTrades = (tradeResult.value || []) as PortfolioTrade[]
        setSymbolTrades(
          typedTrades.filter((row) => normalizeStockSymbol(row.stock_symbol) === normalized)
        )
      } else {
        setSymbolTrades([])
        setStatus((prev) => prev || `加载该股票交易记录失败：${(tradeResult.reason as Error).message}`)
      }

      if (positionResult.status === 'fulfilled') {
        const typedPositions = (positionResult.value || []) as Position[]
        setSymbolPosition(
          typedPositions.find(
            (row) => normalizeStockSymbol(row.stock_symbol) === normalized && row.status === 'open'
          ) || null
        )
      } else {
        setSymbolPosition(null)
        setStatus((prev) => prev || `加载该股票持仓失败：${(positionResult.reason as Error).message}`)
      }
    }

    async function loadRankingItemIfMissing() {
      if (!normalized) return
      if (requestedAnalysisId) return
      if (rankingItem && normalizeStockSymbol(rankingItem.stock_symbol) === normalized) return
      try {
        const snapshotType = state.snapshotMeta?.snapshot_type || 'post_close'
        const snapshot = (await getLatestRanking(snapshotType as 'post_close' | 'pre_open' | 'realtime')) as RankingSnapshot
        if (cancelled) return
        const target = snapshot.items.find((item) => normalizeStockSymbol(item.stock_symbol) === normalized)
        if (!target) {
          setRankingItem(null)
          setStatus('最新快照中未找到该股票的排名详情。')
          return
        }
        setRankingItem(target)
      } catch (err: unknown) {
        if (cancelled) return
        setStatus(`加载排名详情失败：${(err as Error).message}`)
      }
    }

    void loadStockInfo()
    void loadAnalysis()
    void loadPortfolio()
    void loadRankingItemIfMissing()

    return () => {
      cancelled = true
    }
  }, [normalized, rankingItem, state.snapshotMeta?.snapshot_type, requestedAnalysisId])

  useEffect(() => {
    let cancelled = false

    async function loadKline() {
      if (!normalized) return
      setChartLoading(true)
      setChartStatus('')
      try {
        const response = (await getStockKline(normalized, period, 420)) as KlineResponse
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
  }, [normalized, period])

  useEffect(() => {
    let cancelled = false

    async function loadSentiment() {
      if (!normalized) return
      setSentimentLoading(true)
      setSentimentStatus('')
      try {
        const row = (await getLatestStockSentiment(normalized, 30, 10)) as SentimentResult
        if (cancelled) return
        setSentimentResult(row)
      } catch (err: unknown) {
        if (cancelled) return
        setSentimentResult(null)
        setSentimentStatus(formatSentimentStatus((err as Error).message))
      } finally {
        if (!cancelled) setSentimentLoading(false)
      }
    }

    void loadSentiment()
    return () => {
      cancelled = true
    }
  }, [normalized])

  const expertRows = useMemo(() => {
    if (analysisResult?.rationale?.experts && typeof analysisResult.rationale.experts === 'object') {
      return Object.entries(analysisResult.rationale.experts as Record<string, any>)
    }
    if (analysisResult?.expert_signals?.length) {
      return analysisResult.expert_signals
        .filter((row) => row.expert_name !== 'investment')
        .map((row) => [
          row.expert_name,
          {
            signal: row.signal,
            score: row.score,
            confidence: row.confidence,
            summary: row.key_factors?.[0] || '',
            key_points: row.key_factors || [],
            risks: row.risk_flags || [],
            evidence: row.evidence || [],
            fallback: false,
          },
        ]) as Array<[string, any]>
    }
    return Object.entries(rankingItem?.expert_payload || {})
  }, [analysisResult, rankingItem?.expert_payload])
  const investmentPayload =
    (analysisResult?.rationale?.investment as Record<string, any> | undefined) || rankingItem?.investment_payload || {}
  const aggregatePayload =
    (analysisResult?.rationale?.aggregate as Record<string, any> | undefined) || null
  const explanationSteps = useMemo(() => {
    const raw = investmentPayload?.explanation_steps
    if (!Array.isArray(raw)) return []
    return raw.map((item) => String(item || '').trim()).filter(Boolean).slice(0, 6)
  }, [investmentPayload?.explanation_steps])
  const explanationPanel = useMemo(() => {
    const raw = investmentPayload?.explanation_panel
    if (!raw || typeof raw !== 'object') return null
    return raw as Record<string, any>
  }, [investmentPayload?.explanation_panel])
  const hasAnalysisPayload = Boolean(analysisResult || rankingItem)
  const scoreBreakdown =
    (aggregatePayload?.score_breakdown && typeof aggregatePayload.score_breakdown === 'object'
      ? (aggregatePayload.score_breakdown as Record<string, number>)
      : null) || null
  const displayDecision = buyDecisionLabel(
    (investmentPayload?.signal ||
      investmentPayload?.final_signal ||
      analysisResult?.final_action ||
      aggregatePayload?.recommendation_action ||
      rankingItem?.recommendation_action) as string | undefined
  )
  const displayTotal = readNumber(aggregatePayload?.total_score ?? rankingItem?.total_score)
  const displayConfidence = readNumber(
    aggregatePayload?.recommendation_confidence ?? rankingItem?.recommendation_confidence
  )
  const displayNewsScore = readNumber(scoreBreakdown?.news ?? rankingItem?.news_score)
  const displayStockScore = readNumber(scoreBreakdown?.stock_data ?? rankingItem?.stock_score)
  const displayMacroScore = readNumber(scoreBreakdown?.macro ?? rankingItem?.macro_score)
  const displayFinancialScore = readNumber(scoreBreakdown?.financial ?? rankingItem?.financial_score)
  const displayFundamentalScore = readNumber(scoreBreakdown?.fundamental ?? rankingItem?.fundamental_score)
  const displayConflict =
    Boolean(aggregatePayload?.conflict_signal) || Boolean(rankingItem?.conflict_signal)
  const latestSentiment = sentimentResult?.latest || null
  const recentSentimentSeries = sentimentResult?.recent_series || []
  const newsSentimentItems = sentimentResult?.news_items || []
  const gubaSentimentItems = sentimentResult?.guba_items || []

  const displayPositionRatio = readNumber(
    investmentPayload?.position_management?.position_ratio ?? investmentPayload?.position_ratio
  )
  const displaySuggestedShares = readNumber(
    investmentPayload?.position_management?.suggested_shares ?? investmentPayload?.suggested_shares
  )
  const buyRange = useMemo(() => {
    if (Array.isArray(investmentPayload?.buy_strategy?.price_range)) {
      const [min, max] = investmentPayload.buy_strategy.price_range
      return [readNumber(min), readNumber(max)] as const
    }
    if (investmentPayload?.buy_range && typeof investmentPayload.buy_range === 'object') {
      return [readNumber(investmentPayload.buy_range.min), readNumber(investmentPayload.buy_range.max)] as const
    }
    return [null, null] as const
  }, [investmentPayload])
  const stopLossPrice = readNumber(
    investmentPayload?.stop_loss_plan?.stop_loss_price ?? investmentPayload?.stop_loss?.price
  )
  const executionLogic = useMemo(() => {
    const raw = Array.isArray(investmentPayload?.execution_logic) ? investmentPayload.execution_logic : []
    return raw
      .map((item: any) => {
        if (!item || typeof item !== 'object') return null
        const title = String(item.title || '').trim()
        const content = String(item.content || '').trim()
        if (!title && !content) return null
        return { title, content }
      })
      .filter(Boolean) as Array<{ title: string; content: string }>
  }, [investmentPayload?.execution_logic])
  const buyConditions = useMemo(
    () => normalizeTextArray(investmentPayload?.buy_strategy?.conditions, 8),
    [investmentPayload?.buy_strategy?.conditions]
  )
  const stagedEntry = useMemo(
    () => normalizeTextArray(investmentPayload?.buy_strategy?.staged_entry, 8),
    [investmentPayload?.buy_strategy?.staged_entry]
  )
  const dynamicAdjustments = useMemo(
    () => normalizeTextArray(investmentPayload?.dynamic_adjustment, 8),
    [investmentPayload?.dynamic_adjustment]
  )
  const waitConditions = useMemo(
    () => normalizeTextArray(investmentPayload?.wait_conditions, 8),
    [investmentPayload?.wait_conditions]
  )
  const synthesisBullish = useMemo(
    () => normalizeTextArray(investmentPayload?.expert_synthesis?.bullish_factors, 8),
    [investmentPayload?.expert_synthesis?.bullish_factors]
  )
  const synthesisBearish = useMemo(
    () => normalizeTextArray(investmentPayload?.expert_synthesis?.bearish_factors, 8),
    [investmentPayload?.expert_synthesis?.bearish_factors]
  )
  const synthesisConflicts = useMemo(
    () => normalizeTextArray(investmentPayload?.expert_synthesis?.conflicts, 8),
    [investmentPayload?.expert_synthesis?.conflicts]
  )
  const takeProfitPlan = useMemo(() => {
    const raw = Array.isArray(investmentPayload?.take_profit_plan) ? investmentPayload.take_profit_plan : []
    return raw
      .map((item: any) => {
        if (!item || typeof item !== 'object') return null
        const target = readNumber(item.target_price)
        const ratio = readNumber(item.sell_ratio)
        const condition = String(item.condition || '').trim()
        if (target === null && ratio === null && !condition) return null
        return { target, ratio, condition }
      })
      .filter(Boolean) as Array<{ target: number | null; ratio: number | null; condition: string }>
  }, [investmentPayload?.take_profit_plan])
  const stopLossCondition = String(
    investmentPayload?.stop_loss_plan?.hard_exit_condition ?? investmentPayload?.stop_loss?.condition ?? ''
  ).trim()
  const capitalToUse = readNumber(investmentPayload?.position_management?.capital_to_use)

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

  const orderedTrades = useMemo(
    () =>
      [...symbolTrades].sort(
        (a, b) => new Date(a.trade_time).getTime() - new Date(b.trade_time).getTime()
      ),
    [symbolTrades]
  )

  const effectivePosition = useMemo(() => {
    if (
      symbolPosition &&
      String(symbolPosition.status || '').toLowerCase() === 'open' &&
      Number(symbolPosition.quantity) > 0
    ) {
      return {
        quantity: Number(symbolPosition.quantity),
        avgPrice: Number(symbolPosition.avg_price),
        derived: false,
      }
    }

    // Fallback: derive holdings from trade sequence when open-position row is missing/stale.
    let qty = 0
    let avg = 0
    for (const trade of orderedTrades) {
      const tradeQty = Number(trade.quantity) || 0
      const tradePrice = Number(trade.price) || 0
      if (tradeQty <= 0 || tradePrice <= 0) continue
      const side = String(trade.side || '').toLowerCase()
      if (side === 'buy') {
        const totalCost = avg * qty + tradePrice * tradeQty
        qty += tradeQty
        avg = qty > 0 ? totalCost / qty : 0
      } else if (side === 'sell') {
        qty = Math.max(0, qty - tradeQty)
      }
    }
    if (qty <= 0) return null
    return { quantity: qty, avgPrice: avg, derived: true }
  }, [orderedTrades, symbolPosition])

  const tradeSequenceModel = useMemo(() => {
    // Trade sequence chart uses trade index (not calendar time) on X-axis.
    const width = 760
    const height = 220
    const padX = 36
    const padY = 18
    if (orderedTrades.length === 0) {
      return {
        minPrice: 0,
        maxPrice: 1,
        points: [] as Array<{ x: number; y: number; side: string; price: number; idx: number; trade: PortfolioTrade }>,
        width,
        height,
        padX,
        padY,
      }
    }
    const prices = orderedTrades.map((row) => Number(row.price))
    let minPrice = Math.min(...prices)
    let maxPrice = Math.max(...prices)
    if (Math.abs(maxPrice - minPrice) < 1e-9) {
      maxPrice += 1
      minPrice -= 1
    }
    const step = orderedTrades.length > 1 ? (width - padX * 2) / (orderedTrades.length - 1) : 0
    const points = orderedTrades.map((trade, index) => {
      const ratio = (trade.price - minPrice) / Math.max(1e-9, maxPrice - minPrice)
      const x = padX + step * index
      const y = height - padY - ratio * (height - padY * 2)
      return { x, y, side: trade.side, price: trade.price, idx: index + 1, trade }
    })
    return { minPrice, maxPrice, points, width, height, padX, padY }
  }, [orderedTrades])

  const positionEvolutionModel = useMemo(() => {
    // Position evolution tracks cumulative shares after each trade event.
    const width = 760
    const height = 200
    const padX = 36
    const padY = 18
    if (orderedTrades.length === 0) {
      return {
        maxShares: 1,
        points: [] as Array<{ x: number; y: number; shares: number; idx: number }>,
        width,
        height,
        padX,
        padY,
      }
    }
    let shares = 0
    const rawPoints: Array<{ idx: number; shares: number }> = orderedTrades.map((trade, index) => {
      shares += trade.side === 'buy' ? Number(trade.quantity) : -Number(trade.quantity)
      return { idx: index + 1, shares }
    })
    const maxShares = Math.max(1, ...rawPoints.map((row) => Math.abs(row.shares)))
    const step = rawPoints.length > 1 ? (width - padX * 2) / (rawPoints.length - 1) : 0
    const points = rawPoints.map((row, index) => {
      const ratio = (row.shares + maxShares) / (maxShares * 2)
      const x = padX + step * index
      const y = height - padY - ratio * (height - padY * 2)
      return { x, y, shares: row.shares, idx: row.idx }
    })
    return { maxShares, points, width, height, padX, padY }
  }, [orderedTrades])

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
    const idxInView = clamp(Math.round((xSvg - PAD_LEFT) / Math.max(1e-9, chartModel.step)), 0, visibleItems.length - 1)
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

  async function handleComputeTodaySentiment() {
    if (!normalized || sentimentUpdating) return
    setSentimentUpdating(true)
    setSentimentStatus('正在计算今日情绪...')
    try {
      const today = new Date().toISOString().slice(0, 10)
      const row = (await computeStockSentiment(normalized, { trade_date: today, persist: true })) as SentimentResult
      setSentimentResult(row)
      setSentimentStatus(`情绪数据已更新：${today}`)
    } catch (err: unknown) {
      setSentimentStatus(formatSentimentStatus((err as Error).message))
    } finally {
      setSentimentUpdating(false)
    }
  }

  return (
    <section className="screen">
      <div className="paper">
        <div className="paper-header">
          <h2>{normalized} 分析详情</h2>
          <div className="paper-meta">
            <span>名称：{stockInfo?.name || '-'}</span>
            <span>市场：{stockInfo?.market || '-'}</span>
            <span>行业：{stockInfo?.sector || '-'}</span>
          </div>
        </div>

        <div className="paper-header with-top-line">
          <h3>价格图表</h3>
        </div>
        <div className="control-row">
          <button className={`btn ${period === 'daily' ? 'solid' : 'invert'}`} onClick={() => setPeriod('daily')} type="button">
            日线
          </button>
          <button className={`btn ${period === 'weekly' ? 'solid' : 'invert'}`} onClick={() => setPeriod('weekly')} type="button">
            周线
          </button>
          <button className={`btn ${period === 'monthly' ? 'solid' : 'invert'}`} onClick={() => setPeriod('monthly')} type="button">
            月线
          </button>
          <span className="row-sub">拖拽平移 | 滚轮缩放 | 点击K线查看详情</span>
        </div>

        <div
          className={`kline-board ${isDragging ? 'dragging' : ''}`}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
          onPointerLeave={endDrag}
          onWheel={handleWheel}
          role="presentation"
        >
          <svg viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`} className="kline-svg">
            <rect x={0} y={0} width={CHART_WIDTH} height={CHART_HEIGHT} fill="#f9fbfe" />
            <line x1={PAD_LEFT} y1={PRICE_BOTTOM} x2={CHART_WIDTH - PAD_RIGHT} y2={PRICE_BOTTOM} stroke="#d3dbe7" />
            <line x1={PAD_LEFT} y1={VOL_BOTTOM} x2={CHART_WIDTH - PAD_RIGHT} y2={VOL_BOTTOM} stroke="#d3dbe7" />

            {[0.25, 0.5, 0.75].map((ratio) => {
              const y = PRICE_TOP + (PRICE_BOTTOM - PRICE_TOP) * ratio
              return <line key={ratio} x1={PAD_LEFT} y1={y} x2={CHART_WIDTH - PAD_RIGHT} y2={y} stroke="#edf2f8" />
            })}

            {visibleItems.map((row, idx) => {
              const x = xFor(idx)
              const openY = yForPrice(row.open)
              const closeY = yForPrice(row.close)
              const highY = yForPrice(row.high)
              const lowY = yForPrice(row.low)
              const up = row.close >= row.open
              const color = up ? '#d4514f' : '#209867'
              const bodyY = Math.min(openY, closeY)
              const bodyHeight = Math.max(1, Math.abs(openY - closeY))
              const bodyWidth = Math.max(2, (chartModel.plotWidth / Math.max(1, visibleItems.length)) * 0.58)
              const volTop = yForVolume(row.volume)
              return (
                <g key={`${row.trade_date}-${idx}`}>
                  <line x1={x} y1={highY} x2={x} y2={lowY} stroke={color} strokeWidth={1.2} />
                  <rect
                    x={x - bodyWidth / 2}
                    y={bodyY}
                    width={bodyWidth}
                    height={bodyHeight}
                    fill={up ? '#f6d7d6' : '#cde9dc'}
                    stroke={color}
                    strokeWidth={1.1}
                  />
                  <rect
                    x={x - bodyWidth / 2}
                    y={volTop}
                    width={bodyWidth}
                    height={Math.max(1, VOL_BOTTOM - volTop)}
                    fill={up ? '#e9b5b4' : '#99d3b7'}
                    opacity={0.92}
                  />
                </g>
              )
            })}

            {selectedVisibleIndex !== null && selectedVisibleIndex >= 0 && selectedVisibleIndex < visibleItems.length && (
              <g>
                <line
                  x1={xFor(selectedVisibleIndex)}
                  y1={PRICE_TOP}
                  x2={xFor(selectedVisibleIndex)}
                  y2={VOL_BOTTOM}
                  stroke="#6d7f96"
                  strokeDasharray="4 4"
                />
                <circle
                  cx={xFor(selectedVisibleIndex)}
                  cy={yForPrice(visibleItems[selectedVisibleIndex].close)}
                  r={4}
                  fill="#1d4f8a"
                />
              </g>
            )}

            <text x={10} y={PRICE_TOP + 12} fill="#5f6b7a" fontSize={11}>
              {formatNumber(chartModel.maxPrice)}
            </text>
            <text x={10} y={PRICE_BOTTOM - 3} fill="#5f6b7a" fontSize={11}>
              {formatNumber(chartModel.minPrice)}
            </text>
            <text x={10} y={VOL_TOP + 12} fill="#5f6b7a" fontSize={11}>
              成交量 {formatNumber(chartModel.maxVolume, 0)}
            </text>

            {visibleItems.length > 0 && (
              <>
                <text x={PAD_LEFT} y={CHART_HEIGHT - 8} fill="#5f6b7a" fontSize={11}>
                  {visibleItems[0].trade_date}
                </text>
                <text x={CHART_WIDTH - PAD_RIGHT - 86} y={CHART_HEIGHT - 8} fill="#5f6b7a" fontSize={11}>
                  {visibleItems[visibleItems.length - 1].trade_date}
                </text>
              </>
            )}
          </svg>
        </div>

        {chartLoading && <div className="inline-status">图表加载中...</div>}
        {chartStatus && <div className="inline-status">{chartStatus}</div>}

        {selectedPoint && (
          <div className="trade-panel">
            <h3>选中K线</h3>
            <p>日期：{selectedPoint.trade_date}</p>
            <p>开盘：{formatNumber(selectedPoint.open)}</p>
            <p>最高：{formatNumber(selectedPoint.high)}</p>
            <p>最低：{formatNumber(selectedPoint.low)}</p>
            <p>收盘：{formatNumber(selectedPoint.close)}</p>
            <p>成交量：{formatNumber(selectedPoint.volume, 0)}</p>
          </div>
        )}

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
        {sentimentLoading && <div className="inline-status">情绪数据加载中...</div>}
        {sentimentStatus && <div className="inline-status">{sentimentStatus}</div>}
        {latestSentiment ? (
          <>
            <div className="metric-grid">
              <div className="metric-box"><span>交易日</span><strong>{latestSentiment.trade_date}</strong></div>
              <div className="metric-box"><span>综合分</span><strong>{latestSentiment.combined_score_norm.toFixed(3)}</strong></div>
              <div className="metric-box"><span>情绪标签</span><strong>{latestSentiment.sentiment_label}</strong></div>
              <div className="metric-box"><span>新闻分</span><strong>{latestSentiment.news_score_norm.toFixed(3)}</strong></div>
              <div className="metric-box"><span>股吧分</span><strong>{latestSentiment.guba_score_norm.toFixed(3)}</strong></div>
              <div className="metric-box"><span>样本数</span><strong>{latestSentiment.news_count + latestSentiment.guba_count}</strong></div>
              <div className="metric-box"><span>趋势信号</span><strong>{latestSentiment.trend_signal}</strong></div>
              <div className="metric-box"><span>5日趋势</span><strong>{latestSentiment.trend_5d === null || latestSentiment.trend_5d === undefined ? '-' : latestSentiment.trend_5d.toFixed(3)}</strong></div>
              <div className="metric-box"><span>可靠性</span><strong>{latestSentiment.reliability_level}</strong></div>
            </div>

            <div className="trade-panel">
              <h3>策略应用</h3>
              <p>矩阵建议：{latestSentiment.strategy_matrix_advice || '-'}</p>
              <p>估值水平：{latestSentiment.valuation_level}</p>
              <p>估值依据：{latestSentiment.valuation_reason || '-'}</p>
              <p>
                情绪-价格相关性：
                {latestSentiment.corr_with_next_return === null || latestSentiment.corr_with_next_return === undefined
                  ? ' -'
                  : ` ${latestSentiment.corr_with_next_return.toFixed(3)}\uFF08${latestSentiment.corr_sample_size} \u4E2A\u6837\u672C\uFF09`}
              </p>
              <p>
                变化序列：
                {latestSentiment.trend_deltas?.length
                  ? ` ${latestSentiment.trend_deltas.map((v, idx) => `d${idx + 1}:${v >= 0 ? '+' : ''}${v.toFixed(3)}`).join(' | ')}`
                  : ' -'}
              </p>
              <p>趋势结论：{latestSentiment.trend_conclusion || '今日无明显特征。'}</p>
              <p>总结：{latestSentiment.strategy_summary || '-'}</p>
              <p>指标解释：趋势信号表示近几日情绪方向，常见值为 up/down/none（上行/下行/无明显趋势）。</p>
              <p>指标解释：5日趋势 = 近5个交易日综合情绪分变化值；大于0通常代表情绪回暖，小于0通常代表情绪走弱，绝对值越大波动越明显。</p>
              <p>指标解释：可靠性基于样本量与历史情绪-价格关系稳定度给出，仅用于辅助判断，不代表确定性预测。</p>
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
                <p>暂无情绪历史数据。</p>
              )}
            </div>

            <div className="trade-panel">
              <h3>最新新闻情绪样本</h3>
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
              <h3>最新股吧情绪样本</h3>
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

        {hasAnalysisPayload ? (
          <>
            <div className="paper-header with-top-line">
              <h3>分析指标</h3>
            </div>
            <div className="metric-grid">
              <div className="metric-box"><span>决策</span><strong>{displayDecision}</strong></div>
              <div className="metric-box"><span>综合分</span><strong>{displayTotal === null ? '-' : displayTotal.toFixed(2)}</strong></div>
              <div className="metric-box"><span>置信度</span><strong>{displayConfidence === null ? '-' : displayConfidence.toFixed(3)}</strong></div>
              <div className="metric-box"><span>新闻</span><strong>{displayNewsScore === null ? '-' : displayNewsScore.toFixed(1)}</strong></div>
              <div className="metric-box"><span>行情</span><strong>{displayStockScore === null ? '-' : displayStockScore.toFixed(1)}</strong></div>
              <div className="metric-box"><span>宏观</span><strong>{displayMacroScore === null ? '-' : displayMacroScore.toFixed(1)}</strong></div>
              <div className="metric-box"><span>财务</span><strong>{displayFinancialScore === null ? '-' : displayFinancialScore.toFixed(1)}</strong></div>
              <div className="metric-box"><span>基本面</span><strong>{displayFundamentalScore === null ? '-' : displayFundamentalScore.toFixed(1)}</strong></div>
              <div className="metric-box"><span>冲突</span><strong>{displayConflict ? '有冲突' : '无冲突'}</strong></div>
            </div>

            <div className="paper-header with-top-line">
              <h3>五大专家分析</h3>
            </div>
            <div className="list-stack">
              {expertRows.map(([key, value]) => (
                <article className="list-item" key={key}>
                  <div className="list-content">
                    <div className="row-title">{EXPERT_LABEL_MAP[key] || key}</div>
                    <div className="row-sub">
                      信号 {buyDecisionLabel((value as any).signal)} | 得分 {(value as any).score} | 置信度 {(value as any).confidence} | 回退 {(value as any).fallback ? '是' : '否'}
                    </div>
                    {key === 'financial' &&
                      (() => {
                        const quantFactors = normalizeQuantFactors((value as any).quant_factors).slice(0, 24)
                        if (quantFactors.length === 0) return null
                        return (
                          <div className="factor-board">
                            <div className="row-sub"><strong>量化因子（财务分析前置）</strong></div>
                            {(value as any).expert_score_formula && (
                              <div className="row-sub">评分公式：{String((value as any).expert_score_formula)}</div>
                            )}
                            {(value as any).expert_score_weights &&
                              typeof (value as any).expert_score_weights === 'object' && (
                                <div className="row-sub">
                                  权重：{formatWeightMap((value as any).expert_score_weights)}
                                </div>
                              )}
                            <div className="factor-grid">
                              {quantFactors.map((factor, idx) => (
                                <div className="factor-chip" key={`factor-${idx}`}>
                                  <span>{factor.name}</span>
                                  <strong>{formatFactorValue(factor.value)}{factor.unit ? ` ${factor.unit}` : ''}</strong>
                                </div>
                              ))}
                            </div>
                          </div>
                        )
                      })()}
                    {String((value as any).summary || '').trim() && (
                      <div className="row-sub">{String((value as any).summary || '').trim()}</div>
                    )}
                    {Array.isArray((value as any).key_points) &&
                      (value as any).key_points.slice(0, 3).map((point: any, idx: number) => (
                        <div className="row-sub" key={`${key}-point-${idx}`}>- {formatExpertPoint(point)}</div>
                      ))}
                    {Array.isArray((value as any).risks) &&
                      (value as any).risks.slice(0, 2).map((risk: any, idx: number) => (
                        <div className="row-sub" key={`${key}-risk-${idx}`}>风险：{formatExpertRisk(risk)}</div>
                      ))}
                    {Array.isArray((value as any).evidence) &&
                      (value as any).evidence.slice(0, 2).map((ev: any, idx: number) => (
                        <div className="row-sub" key={`${key}-ev-${idx}`}>
                          证据[{String(ev?.type || '上下文')}]: {String(ev?.detail || '')}
                        </div>
                      ))}
                  </div>
                </article>
              ))}
            </div>

            <div className="trade-panel">
              <h3>投资建议</h3>
              <p>信号：{displayDecision}</p>
              <p>回退：{investmentPayload?.fallback ? '是' : '否'}</p>
              <p>总结：{investmentPayload?.summary || '-'}</p>
              <p>建议仓位：{displayPositionRatio === null ? '-' : displayPositionRatio}</p>
              <p>建议资金：{capitalToUse === null ? '-' : capitalToUse.toLocaleString(undefined, { maximumFractionDigits: 2 })}</p>
              <p>建议股数：{displaySuggestedShares === null ? '-' : Math.round(displaySuggestedShares)}</p>
              <p>
                买入区间：
                {buyRange[0] !== null && buyRange[1] !== null
                  ? `${buyRange[0].toFixed(2)} - ${buyRange[1].toFixed(2)}`
                  : '-'}
              </p>
              <p>止损价：{stopLossPrice === null ? '-' : stopLossPrice.toFixed(2)}</p>
              {stopLossCondition && <p>硬性离场条件：{stopLossCondition}</p>}
              {buyConditions.length > 0 && (
                <>
                  <h4>买入条件</h4>
                  <ul className="report-list">
                    {buyConditions.map((item, idx) => (
                      <li key={`buy-cond-${idx}`}>{item}</li>
                    ))}
                  </ul>
                </>
              )}
              {stagedEntry.length > 0 && (
                <>
                  <h4>分批建仓</h4>
                  <ul className="report-list">
                    {stagedEntry.map((item, idx) => (
                      <li key={`staged-${idx}`}>{item}</li>
                    ))}
                  </ul>
                </>
              )}
              {takeProfitPlan.length > 0 && (
                <>
                  <h4>止盈计划</h4>
                  <ul className="report-list">
                    {takeProfitPlan.map((row, idx) => (
                      <li key={`tp-${idx}`}>
                        目标价 {row.target === null ? '-' : row.target.toFixed(2)} | 卖出比例{' '}
                        {row.ratio === null ? '-' : `${(row.ratio * 100).toFixed(1)}%`}
                        {row.condition ? ` | ${row.condition}` : ''}
                      </li>
                    ))}
                  </ul>
                </>
              )}
              {executionLogic.length > 0 && (
                <>
                  <h4>执行逻辑</h4>
                  {executionLogic.slice(0, 6).map((item, idx) => (
                    <p key={`logic-${idx}`}>
                      {item.title ? `${item.title}: ` : ''}
                      {item.content}
                    </p>
                  ))}
                </>
              )}
              {dynamicAdjustments.length > 0 && (
                <>
                  <h4>动态调整</h4>
                  <ul className="report-list">
                    {dynamicAdjustments.map((item, idx) => (
                      <li key={`dyn-${idx}`}>{item}</li>
                    ))}
                  </ul>
                </>
              )}
              {waitConditions.length > 0 && (
                <>
                  <h4>等待条件</h4>
                  <ul className="report-list">
                    {waitConditions.map((item, idx) => (
                      <li key={`wait-${idx}`}>{item}</li>
                    ))}
                  </ul>
                </>
              )}
            </div>

            <div className="trade-panel">
              <h3>决策解释</h3>
              {explanationPanel?.headline && <p>{String(explanationPanel.headline)}</p>}
              {explanationSteps.length > 0 ? (
                <ol className="list-stack">
                  {explanationSteps.map((step, idx) => (
                    <li className="list-item" key={`${idx}-${step}`}>
                      <div className="list-content">
                        <div className="row-sub">{step}</div>
                      </div>
                    </li>
                  ))}
                </ol>
              ) : (
                <p>暂无解释步骤。</p>
              )}
              {synthesisBullish.length > 0 && (
                <>
                  <h4>看多驱动因素</h4>
                  <ul className="report-list">
                    {synthesisBullish.map((item, idx) => (
                      <li key={`bull-${idx}`}>{item}</li>
                    ))}
                  </ul>
                </>
              )}
              {synthesisBearish.length > 0 && (
                <>
                  <h4>看空驱动因素</h4>
                  <ul className="report-list">
                    {synthesisBearish.map((item, idx) => (
                      <li key={`bear-${idx}`}>{item}</li>
                    ))}
                  </ul>
                </>
              )}
              {synthesisConflicts.length > 0 && (
                <>
                  <h4>冲突消解</h4>
                  <ul className="report-list">
                    {synthesisConflicts.map((item, idx) => (
                      <li key={`conflict-${idx}`}>{item}</li>
                    ))}
                  </ul>
                </>
              )}
              {Array.isArray(investmentPayload?.risk_warnings) && investmentPayload.risk_warnings.length > 0 && (
                <>
                  <h4>风险提示</h4>
                  <ul className="list-stack">
                    {investmentPayload.risk_warnings.slice(0, 6).map((risk: any, idx: number) => (
                      <li className="list-item" key={`${idx}-${risk}`}>
                        <div className="list-content">
                          <div className="row-sub">{String(risk)}</div>
                        </div>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          </>
        ) : (
          <div className="empty-line">正在加载分析详情...</div>
        )}

        {status && <div className="inline-status">{status}</div>}
        <div className="control-row">
          <Link className="btn invert" to="/discover">返回排名页</Link>
          <Link className="btn solid" to={`/query?symbol=${normalized}`}>重新发起分析</Link>
        </div>
      </div>
    </section>
  )
}

