const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api/v1'

export type Token = { access_token: string; token_type: string }

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
  const res = await fetch(`${API_BASE}${path}`, { ...options, headers })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || res.statusText)
  }
  return res.json()
}

export async function createGuest() {
  return apiFetch('/users/guest', { method: 'POST' })
}

export async function login(email: string, password: string): Promise<Token> {
  return apiFetch('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })
}

export async function register(email: string, password: string) {
  return apiFetch('/auth/register', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })
}

export async function getCurrentUser() {
  return apiFetch('/users/me')
}

export async function getProfile() {
  return apiFetch('/profiles/me')
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

export async function getAnalysis(analysisId: string) {
  return apiFetch(`/analysis/${analysisId}`)
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

export async function closePosition(positionId: number, quantity?: number) {
  return apiFetch(`/portfolio/positions/${positionId}/close`, {
    method: 'POST',
    body: JSON.stringify(quantity ? { quantity } : {}),
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
