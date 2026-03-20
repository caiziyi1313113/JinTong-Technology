import { useEffect, useRef, useState } from 'react'
import { generateMacroStandaloneReport } from '../api'

type MacroReport = Record<string, any>
type MacroStandaloneState = {
  loading: boolean
  status: string
  report: MacroReport | null
  updated_at: string
}

const STORAGE_MACRO_STATE = 'stockai.macro.standalone.state'

function text(value: unknown): string {
  if (value === null || value === undefined) return '-'
  const raw = String(value).trim()
  return raw || '-'
}

function joinList(value: unknown, sep = '、'): string {
  if (!Array.isArray(value)) return '-'
  const rows = value.map((item) => String(item || '').trim()).filter(Boolean)
  return rows.length > 0 ? rows.join(sep) : '-'
}

export default function MacroStandalone() {
  const mountedRef = useRef(true)
  const hydratedRef = useRef(false)
  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState('')
  const [report, setReport] = useState<MacroReport | null>(null)

  function saveMacroState(next: { loading: boolean; status: string; report: MacroReport | null }) {
    const payload: MacroStandaloneState = {
      loading: next.loading,
      status: next.status,
      report: next.report,
      updated_at: new Date().toISOString(),
    }
    localStorage.setItem(STORAGE_MACRO_STATE, JSON.stringify(payload))
  }

  useEffect(() => {
    mountedRef.current = true
    const saved = localStorage.getItem(STORAGE_MACRO_STATE)
    if (saved) {
      try {
        const parsed = JSON.parse(saved) as Partial<MacroStandaloneState>
        const savedReport = parsed?.report && typeof parsed.report === 'object' ? (parsed.report as MacroReport) : null
        const savedStatus = String(parsed?.status || '').trim()
        const savedLoading = Boolean(parsed?.loading)
        setReport(savedReport)
        if (savedLoading) {
          setLoading(false)
          setStatus(savedStatus ? `${savedStatus}（上次页面切换后未继续跟踪，请重新生成）` : '上次生成未完成，请重新生成。')
        } else {
          setStatus(savedStatus)
        }
      } catch {
        localStorage.removeItem(STORAGE_MACRO_STATE)
      }
    }
    hydratedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  useEffect(() => {
    if (!hydratedRef.current) return
    saveMacroState({ loading, status, report })
  }, [loading, status, report])

  async function handleGenerate() {
    if (loading) return
    const startStatus = '正在生成今日宏观分析，请稍候...'
    setLoading(true)
    setStatus(startStatus)
    saveMacroState({ loading: true, status: startStatus, report })
    try {
      const payload = (await generateMacroStandaloneReport()) as { generated_at?: string; report?: MacroReport }
      const nextReport = payload?.report || null
      const doneStatus = `生成完成${payload?.generated_at ? `（${new Date(payload.generated_at).toLocaleString()}）` : ''}`
      saveMacroState({ loading: false, status: doneStatus, report: nextReport })
      if (mountedRef.current) {
        setReport(nextReport)
        setStatus(doneStatus)
      }
    } catch (err: unknown) {
      const failStatus = `生成失败：${(err as Error).message}`
      saveMacroState({ loading: false, status: failStatus, report })
      if (mountedRef.current) {
        setStatus(failStatus)
      }
    } finally {
      if (mountedRef.current) {
        setLoading(false)
      }
    }
  }

  const overview = report?.macro_overview || {}
  const chinaMacro = report?.china_macro || {}
  const chinaMarket = report?.china_market || {}
  const industry = report?.industry_rotation || {}
  const globalMacro = report?.global_macro || {}
  const implication = report?.market_implication || {}
  const sectors = report?.beneficiaries_and_risks || {}
  const finalConclusion = report?.final_conclusion || {}

  return (
    <section className="screen">
      <div className="paper">
        <div className="paper-header">
          <h2>宏观面独立分析</h2>
          <div className="paper-meta">
            <span>今日宏观环境 / 市场风格 / 行业轮动</span>
          </div>
        </div>

        <div className="control-row">
          <button className={`btn ${loading ? 'invert' : 'solid'}`} type="button" onClick={handleGenerate} disabled={loading}>
            {loading ? '生成中...' : '生成今日宏观分析'}
          </button>
          {status && <span className="row-sub">{status}</span>}
        </div>

        {report && (
          <>
            <div className="paper-header with-top-line">
              <h3>一、今日宏观总判断</h3>
            </div>
            <div className="metric-grid">
              <div className="metric-box"><span>总体判断</span><strong>{text(overview.overall_judgement)}</strong></div>
              <div className="metric-box"><span>风险偏好</span><strong>{text(overview.risk_preference)}</strong></div>
              <div className="metric-box"><span>市场风格</span><strong>{text(overview.market_style)}</strong></div>
              <div className="metric-box"><span>核心观点</span><strong>{text(overview.core_view)}</strong></div>
            </div>

            <div className="paper-header with-top-line">
              <h3>二、中国宏观经济环境分析</h3>
            </div>
            <div className="trade-panel">
              <p>经济增长：{text(chinaMacro.economic_growth)}</p>
              <p>通胀：{text(chinaMacro.inflation)}</p>
              <p>流动性：{text(chinaMacro.liquidity)}</p>
              <p>信用扩张：{text(chinaMacro.credit_expansion)}</p>
              <p>政策信号：{text(chinaMacro.policy_signal)}</p>
              <p>小结：{text(chinaMacro.summary)}</p>
            </div>

            <div className="paper-header with-top-line">
              <h3>三、中国证券市场整体环境分析</h3>
            </div>
            <div className="trade-panel">
              <p>指数状态：{text(chinaMarket.index_state)}</p>
              <p>成交与资金：{text(chinaMarket.turnover_and_funds)}</p>
              <p>风险偏好：{text(chinaMarket.risk_appetite)}</p>
              <p>风格信号：{text(chinaMarket.style_signal)}</p>
              <p>小结：{text(chinaMarket.summary)}</p>
            </div>

            <div className="paper-header with-top-line">
              <h3>四、行业板块轮动与市场风格分析</h3>
            </div>
            <div className="trade-panel">
              <p>强势行业：{joinList(industry.strong_sectors)}</p>
              <p>弱势行业：{joinList(industry.weak_sectors)}</p>
              <p>轮动逻辑：{text(industry.rotation_logic)}</p>
              <p>小结：{text(industry.summary)}</p>
            </div>

            <div className="paper-header with-top-line">
              <h3>五、全球宏观与外部扰动分析</h3>
            </div>
            <div className="trade-panel">
              <p>美联储与利率：{text(globalMacro.fed_and_rates)}</p>
              <p>美元与美债：{text(globalMacro.usd_and_bonds)}</p>
              <p>大宗商品：{text(globalMacro.commodities)}</p>
              <p>地缘政治：{text(globalMacro.geopolitics)}</p>
              <p>小结：{text(globalMacro.summary)}</p>
            </div>

            <div className="paper-header with-top-line">
              <h3>六、宏观环境对A股投资的影响</h3>
            </div>
            <div className="trade-panel">
              <p>短期：{text(implication.short_term)}</p>
              <p>中期：{text(implication.medium_term)}</p>
              <p>结构性：{text(implication.structural)}</p>
            </div>

            <div className="paper-header with-top-line">
              <h3>七、受益方向与承压方向</h3>
            </div>
            <div className="trade-panel">
              <p>受益方向：{joinList(sectors.beneficiary_sectors)}</p>
              <p>承压方向：{joinList(sectors.pressured_sectors)}</p>
            </div>

            <div className="paper-header with-top-line">
              <h3>八、风险提示</h3>
            </div>
            <div className="trade-panel">
              <p>关键风险：{joinList(sectors.key_risks, '；')}</p>
            </div>

            <div className="paper-header with-top-line">
              <h3>九、最终结论</h3>
            </div>
            <div className="trade-panel">
              <p>投资偏向：{text(finalConclusion.macro_to_investment_bias)}</p>
              <p>组合建议：{text(finalConclusion.portfolio_suggestion)}</p>
              <p>一句话总结：{text(finalConclusion.one_sentence_summary)}</p>
            </div>
          </>
        )}
      </div>
    </section>
  )
}
