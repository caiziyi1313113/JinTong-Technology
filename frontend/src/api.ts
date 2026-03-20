const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api/v1'

export type Token = { access_token: string; token_type: string }

export type RegisterProfilePayload = {
  assets?: number
  disposable_funds?: number
  income?: number
  risk_level?: string
  investment_horizon?: string
  style?: string
  persona?: string
  questionnaire_answers?: Record<string, unknown>
}

export function getToken(): string | null {
  return localStorage.getItem('token')
}

export function setToken(token: string) {
  localStorage.setItem('token', token)
}

export function getUserId(): string | null {
  return localStorage.getItem('user_id')
}

export function setUserId(userId: string) {
  localStorage.setItem('user_id', userId)
}

export function clearSession() {
  localStorage.removeItem('token')
  localStorage.removeItem('user_id')
  localStorage.removeItem('stockai.query.symbol')
  localStorage.removeItem('stockai.query.status')
  localStorage.removeItem('stockai.query.task')
  localStorage.removeItem('stockai.query.result')
  localStorage.removeItem('stockai.query.task_map')
  localStorage.removeItem('stockai.query.result_map')
  localStorage.removeItem('stockai.query.active_symbol')
  localStorage.removeItem('stockai.macro.standalone.state')
}

async function apiFetch(path: string, options: RequestInit = {}) {
  const token = getToken()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers || {}),
  }
  if (token) {
    headers.Authorization = `Bearer ${token}`
  }

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers, cache: 'no-store' })
  if (!res.ok) {
    let message = res.statusText || `HTTP ${res.status}`
    const contentType = res.headers.get('content-type') || ''

    if (contentType.includes('application/json')) {
      try {
        const body = await res.json()
        const detail = body?.detail
        if (typeof detail === 'string') {
          message = detail
        } else if (Array.isArray(detail) && detail.length > 0) {
          message = detail
            .map((item: any) => (typeof item === 'string' ? item : item?.msg || JSON.stringify(item)))
            .join('; ')
        } else if (typeof body?.error_message === 'string' && body.error_message) {
          message = body.error_message
        } else if (typeof body?.message === 'string' && body.message) {
          message = body.message
        }
      } catch {
        // ignore json parse error and keep default message
      }
    } else {
      const text = await res.text()
      if (text) message = text
    }

    throw new Error(message)
  }
  if (res.status === 204) return null
  return res.json()
}

export async function login(email: string, password: string): Promise<Token> {
  return apiFetch('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })
}

export async function register(email: string, password: string, profile?: RegisterProfilePayload) {
  return apiFetch('/auth/register', {
    method: 'POST',
    body: JSON.stringify({ email, password, profile }),
  })
}

export async function getCurrentUser() {
  return apiFetch('/users/me')
}

export async function getProfile() {
  return apiFetch('/profiles/me')
}

export async function getQuestionnaireTemplate() {
  return apiFetch('/profiles/questionnaire/template')
}

export async function updateProfile(payload: Record<string, unknown>) {
  return apiFetch('/profiles/me', {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export async function createAnalysis(stockSymbol: string, userId: string) {
  return apiFetch('/analysis', {
    method: 'POST',
    body: JSON.stringify({ stock_symbol: stockSymbol, user_id: Number(userId) }),
  })
}

export async function createAnalysisTask(stockSymbol: string, userId: string) {
  return apiFetch('/analysis/tasks', {
    method: 'POST',
    body: JSON.stringify({ stock_symbol: stockSymbol, user_id: Number(userId) }),
  })
}

export async function getAnalysisTask(taskId: string) {
  return apiFetch(`/analysis/tasks/${taskId}`)
}

export async function getAnalysis(analysisId: string) {
  return apiFetch(`/analysis/${analysisId}`)
}

export async function getLatestAnalysisBySymbol(symbol: string) {
  return apiFetch(`/analysis/symbol/${encodeURIComponent(symbol)}/latest`)
}

export async function generateMacroStandaloneReport() {
  return apiFetch('/analysis/macro/standalone', {
    method: 'POST',
  })
}

export async function getStock(symbol: string) {
  return apiFetch(`/stocks/${symbol}`)
}

export async function getStockKline(symbol: string, period: 'daily' | 'weekly' | 'monthly', limit = 240) {
  return apiFetch(`/stocks/${symbol}/kline?period=${period}&limit=${limit}`)
}

export async function getLatestStockSentiment(symbol: string, days = 30, itemLimit = 20) {
  return apiFetch(
    `/sentiment/${encodeURIComponent(symbol)}/latest?days=${days}&item_limit=${itemLimit}`
  )
}

export async function computeStockSentiment(
  symbol: string,
  payload: {
    trade_date?: string
    max_pages?: number
    max_news?: number
    max_guba?: number
    persist?: boolean
  } = {}
) {
  return apiFetch(`/sentiment/${encodeURIComponent(symbol)}/compute`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function runPostCloseReview(payload: { trade_date?: string; top_n?: number }) {
  return apiFetch('/workflow/post-close-review', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function runPreOpenScan(payload: { scan_date?: string; top_n?: number }) {
  return apiFetch('/workflow/pre-open-scan', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function runRankingSnapshot(payload: {
  snapshot_date?: string
  snapshot_type?: 'post_close' | 'pre_open' | 'realtime'
  top_n?: number
  symbols?: string[]
}) {
  return apiFetch('/workflow/ranking/run', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function createRankingTask(payload: {
  snapshot_date?: string
  snapshot_type?: 'post_close' | 'pre_open' | 'realtime'
  top_n?: number
  symbols?: string[]
}) {
  return apiFetch('/workflow/ranking/tasks', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function getRankingTask(taskId: string) {
  return apiFetch(`/workflow/ranking/tasks/${taskId}`)
}

export async function getLatestRanking(snapshotType?: 'post_close' | 'pre_open' | 'realtime') {
  const query = snapshotType ? `?snapshot_type=${snapshotType}` : ''
  return apiFetch(`/workflow/ranking/latest${query}`)
}

export async function getRanking(snapshotId: number) {
  return apiFetch(`/workflow/ranking/${snapshotId}`)
}

export async function runDailySync(payload: {
  trade_date?: string
  symbols?: string[]
  history_days?: number
  include_block_trade?: boolean
  include_news?: boolean
  include_macro?: boolean
}) {
  return apiFetch('/data/sync/daily', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function createDailySyncTask(payload: {
  trade_date?: string
  symbols?: string[]
  history_days?: number
  include_block_trade?: boolean
  include_news?: boolean
  include_macro?: boolean
}) {
  return apiFetch('/data/sync/daily/tasks', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function getDailySyncTask(taskId: number) {
  return apiFetch(`/data/sync/daily/tasks/${taskId}`)
}

export async function runStaticSync(payload: { symbols: string[] }) {
  return apiFetch('/data/sync/static', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function listSyncLogs(limit = 20) {
  return apiFetch(`/data/sync/logs?limit=${limit}`)
}

export async function listPositions(includeClosed = false) {
  return apiFetch(`/portfolio/positions?include_closed=${includeClosed}`)
}

export async function upsertPosition(payload: {
  stock_symbol: string
  quantity: number
  avg_price: number
}) {
  return apiFetch('/portfolio/positions', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function closePosition(positionId: number, payload?: { quantity?: number; price?: number; note?: string }) {
  return apiFetch(`/portfolio/positions/${positionId}/close`, {
    method: 'POST',
    body: JSON.stringify(payload || {}),
  })
}

export async function listPortfolioTrades(stockSymbol?: string, limit = 200) {
  const params = new URLSearchParams()
  params.set('limit', String(limit))
  if (stockSymbol) params.set('stock_symbol', stockSymbol)
  return apiFetch(`/portfolio/trades?${params.toString()}`)
}

export async function createPortfolioTrade(payload: {
  stock_symbol: string
  side: 'buy' | 'sell'
  quantity: number
  price: number
  trade_time?: string
  note?: string
}) {
  return apiFetch('/portfolio/trades', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function clearPortfolioTracking() {
  return apiFetch('/portfolio/trades/all', {
    method: 'DELETE',
  })
}

export async function clearPortfolioTrackingBySymbol(stockSymbol: string) {
  return apiFetch(`/portfolio/symbol/${encodeURIComponent(stockSymbol)}`, {
    method: 'DELETE',
  })
}

export async function createTradePlan(stockSymbol: string) {
  return apiFetch('/trades/plans', {
    method: 'POST',
    body: JSON.stringify({ stock_symbol: stockSymbol }),
  })
}

export async function listTradePlans() {
  return apiFetch('/trades/plans')
}

export async function createTradeSignal(payload: { trade_plan_id: number; current_price?: number }) {
  return apiFetch('/trades/signals', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function listTradeSignals() {
  return apiFetch('/trades/signals')
}
