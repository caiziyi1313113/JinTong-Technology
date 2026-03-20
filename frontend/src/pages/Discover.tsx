import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  createDailySyncTask,
  createRankingTask,
  getDailySyncTask,
  getLatestRanking,
  getRankingTask,
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

type RankingSnapshot = {
  id: number
  snapshot_date: string
  snapshot_type: string
  status: string
  summary: Record<string, any>
  generated_at: string
  items: RankingItem[]
}

type SyncLog = {
  id: number
  status: string
  error_message?: string
}

function todayISO(): string {
  return new Date().toISOString().slice(0, 10)
}

function buyDecisionLabel(action: string | undefined): string {
  return action === 'buy' ? '买入' : '不买入'
}

export default function Discover() {
  const [snapshotType, setSnapshotType] = useState<'post_close' | 'pre_open' | 'realtime'>('post_close')
  const [snapshotDate, setSnapshotDate] = useState<string>(todayISO())
  const [topN, setTopN] = useState<number>(30)
  const [keyword, setKeyword] = useState<string>('')
  const [status, setStatus] = useState<string>('')
  const [loading, setLoading] = useState<boolean>(false)
  const [rankingRefreshing, setRankingRefreshing] = useState<boolean>(false)
  const [snapshot, setSnapshot] = useState<RankingSnapshot | null>(null)

  async function pollRankingTask(taskId: string): Promise<number> {
    const timeoutMs = 1000 * 60 * 15
    const started = Date.now()
    let round = 0
    while (Date.now() - started < timeoutMs) {
      const task = await getRankingTask(taskId)
      if (task.status === 'completed' && task.snapshot_id) {
        return task.snapshot_id as number
      }
      if (task.status === 'failed') {
        throw new Error(task.error || '排名任务失败')
      }
      round += 1
      const waitMs = round > 120 ? 3500 : 1800
      await new Promise((resolve) => setTimeout(resolve, waitMs))
    }
    throw new Error('排名任务超时')
  }

  async function pollDailySyncTask(taskId: number): Promise<SyncLog> {
    const timeoutMs = 1000 * 60 * 20
    const started = Date.now()
    let round = 0
    while (Date.now() - started < timeoutMs) {
      const task = (await getDailySyncTask(taskId)) as SyncLog
      if (task.status === 'completed' || task.status === 'failed') {
        return task
      }
      round += 1
      const waitMs = round > 120 ? 3500 : 1800
      await new Promise((resolve) => setTimeout(resolve, waitMs))
    }
    throw new Error('每日同步任务超时')
  }

  const rows = useMemo(() => {
    if (!snapshot?.items) return []
    const q = keyword.trim().toUpperCase()
    if (!q) return snapshot.items
    return snapshot.items.filter((row) => row.stock_symbol.includes(q))
  }, [snapshot, keyword])

  useEffect(() => {
    let cancelled = false

    async function bootLoadLatest() {
      try {
        const result = (await getLatestRanking(snapshotType)) as RankingSnapshot
        if (cancelled) return
        setSnapshot(result)
        setSnapshotDate(result.snapshot_date)
        setStatus(`已加载最新快照 #${result.id}`)
      } catch (err: unknown) {
        if (cancelled) return
        const message = (err as Error).message
        if (message.includes('No ranking snapshot found')) {
          setStatus('暂无快照，请先点击“同步并生成”。')
          return
        }
        setStatus(`加载最新快照失败：${message}`)
      }
    }

    bootLoadLatest()
    return () => {
      cancelled = true
    }
  }, [snapshotType])

  async function handleDataAndRanking() {
    setLoading(true)
    setStatus('')
    try {
      setStatus('正在提交每日同步任务...')
      const syncTask = (await createDailySyncTask({
        trade_date: snapshotDate,
        history_days: 120,
        include_block_trade: true,
        include_news: true,
        include_macro: true,
      })) as SyncLog
      setStatus(`每日同步任务 #${syncTask.id} 运行中...`)
      const syncLog = await pollDailySyncTask(syncTask.id)

      const syncWarning =
        syncLog?.status === 'completed' ? '' : `每日同步部分失败：${syncLog?.error_message || '未知错误'}`
      if (syncWarning) setStatus(syncWarning)

      setRankingRefreshing(true)
      setStatus('正在分析并生成新一期排名...')
      const task = await createRankingTask({
        snapshot_date: snapshotDate,
        snapshot_type: snapshotType,
        top_n: topN,
      })
      await pollRankingTask(task.task_id as string)
      const result = (await getLatestRanking(snapshotType)) as RankingSnapshot
      setSnapshot(result)

      if ((result.items?.length || 0) === 0) {
        setStatus('排名已生成，但无结果，请检查同步数据和股票池。')
      } else {
        setStatus(`完成：共排名 ${result.items?.length || 0} 只股票。`)
      }
    } catch (err: unknown) {
      setStatus((err as Error).message)
    } finally {
      setRankingRefreshing(false)
      setLoading(false)
    }
  }

  async function handleRunRankingOnly() {
    setLoading(true)
    setStatus('')
    try {
      setRankingRefreshing(true)
      setStatus('正在分析并生成新一期排名...')
      const task = await createRankingTask({
        snapshot_date: snapshotDate,
        snapshot_type: snapshotType,
        top_n: topN,
      })
      await pollRankingTask(task.task_id as string)
      const result = (await getLatestRanking(snapshotType)) as RankingSnapshot
      setSnapshot(result)

      if ((result.items?.length || 0) === 0) {
        setStatus('本次排名结果为空，请先执行“同步并生成”。')
      } else {
        setStatus(`排名已生成：${result.items?.length || 0} 只股票。`)
      }
    } catch (err: unknown) {
      setStatus((err as Error).message)
    } finally {
      setRankingRefreshing(false)
      setLoading(false)
    }
  }

  async function handleLoadLatest() {
    setLoading(true)
    setStatus('')
    try {
      const result = (await getLatestRanking(snapshotType)) as RankingSnapshot
      setSnapshot(result)
      setSnapshotDate(result.snapshot_date)
      setStatus(`已加载最新快照 #${result.id}`)
    } catch (err: unknown) {
      const message = (err as Error).message
      if (message.includes('No ranking snapshot found')) {
        setStatus('未找到快照，请先生成。')
      } else {
        setStatus(message)
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className="screen">
      <div className="hero-block reveal-up">
        <h1>评审与排名</h1>
        <p>同步市场数据，运行五专家分析，生成可解释的股票排名。</p>
        <div className="control-row">
          <label>
            类型
            <select value={snapshotType} onChange={(e) => setSnapshotType(e.target.value as any)}>
              <option value="post_close">收盘后</option>
              <option value="pre_open">开盘前</option>
              <option value="realtime">盘中实时</option>
            </select>
          </label>
          <label>
            日期
            <input type="date" value={snapshotDate} onChange={(e) => setSnapshotDate(e.target.value)} />
          </label>
          <label>
            前N
            <input
              type="number"
              min={1}
              max={100}
              value={topN}
              onChange={(e) => setTopN(Math.max(1, Number(e.target.value) || 1))}
            />
          </label>
          <button className="btn solid" disabled={loading} onClick={handleDataAndRanking}>
            同步并生成
          </button>
          <button className="btn invert" disabled={loading} onClick={handleRunRankingOnly}>
            仅生成排名
          </button>
          <button className="btn invert" disabled={loading} onClick={handleLoadLatest}>
            加载最新
          </button>
        </div>

        <div className="search-pill">
          <input
            value={keyword}
            onChange={(e) => setKeyword(e.target.value.toUpperCase())}
            placeholder="按代码筛选，例如 000001"
          />
        </div>
        {status && <div className="inline-status">{status}</div>}
      </div>

      {rankingRefreshing && (
        <div className="inline-status">正在分析并生成新一期排名...</div>
      )}

      <div className="paper reveal-up delay-1">
        <div className="paper-header">
          <h2>排名结果</h2>
          {snapshot && (
            <div className="paper-meta">
              <span>日期更新 {snapshot.snapshot_date}</span>
              <span>前N {snapshot.summary?.top_n ?? '-'}</span>
            </div>
          )}
        </div>

        <div className="list-stack">
          {rows.map((row) => (
            <article className="ranking-row" key={row.id}>
              <div className="ranking-main">
                <div className="ranking-head">
                  <span className="ranking-no">#{row.rank}</span>
                  <Link
                    className="ranking-symbol-link"
                    to={`/stock/${row.stock_symbol}`}
                    state={{
                      rankingItem: row,
                      snapshotMeta: {
                        snapshot_id: snapshot?.id,
                        snapshot_date: snapshot?.snapshot_date,
                        snapshot_type: snapshot?.snapshot_type,
                      },
                    }}
                  >
                    {row.stock_symbol}
                  </Link>
                  <Link
                    className="ranking-link"
                    to={`/stock/${row.stock_symbol}`}
                    state={{
                      rankingItem: row,
                      snapshotMeta: {
                        snapshot_id: snapshot?.id,
                        snapshot_date: snapshot?.snapshot_date,
                        snapshot_type: snapshot?.snapshot_type,
                      },
                    }}
                  >
                    查看详情
                  </Link>
                </div>
                <div className="ranking-scores">
                  <span className="score-pill"><strong>总分</strong> {row.total_score.toFixed(2)}</span>
                  <span className="score-pill"><strong>新闻</strong> {row.news_score.toFixed(1)}</span>
                  <span className="score-pill"><strong>数据</strong> {row.stock_score.toFixed(1)}</span>
                  <span className="score-pill"><strong>宏观</strong> {row.macro_score.toFixed(1)}</span>
                  <span className="score-pill"><strong>财务</strong> {row.financial_score.toFixed(1)}</span>
                  <span className="score-pill"><strong>基本面</strong> {row.fundamental_score.toFixed(1)}</span>
                </div>
              </div>
              <div className="ranking-actions">
                <span className={`chip ${row.recommendation_action === 'buy' ? 'buy' : 'hold'}`}>
                  {buyDecisionLabel(row.recommendation_action)}
                </span>
                <span className={`chip ${row.conflict_signal ? 'sell' : 'buy'}`}>
                  {row.conflict_signal ? '信号冲突' : '信号一致'}
                </span>
              </div>
            </article>
          ))}
          {rows.length === 0 && <div className="empty-line">暂无排名结果，请先执行生成。</div>}
        </div>
      </div>
    </section>
  )
}
