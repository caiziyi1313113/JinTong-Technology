import { useEffect, useMemo, useState } from 'react'
import { getProfile, getQuestionnaireTemplate, updateProfile } from '../api'

type QuestionnaireTemplate = {
  required_order?: string[]
  questions?: Record<
    string,
    {
      title?: string
      type?: string
      min?: number
      options?: Record<string, string>
    }
  >
}

type QuestionnaireAnswers = {
  disposable_funds: number
  loss_aversion: string
  risk_comfort: string
  time_horizon: string
  financial_literacy: string
}

const defaultAnswers: QuestionnaireAnswers = {
  disposable_funds: 0,
  loss_aversion: '2',
  risk_comfort: '2',
  time_horizon: '2',
  financial_literacy: '2',
}

export default function Profile() {
  const [profile, setProfile] = useState<any>(null)
  const [template, setTemplate] = useState<QuestionnaireTemplate | null>(null)
  const [status, setStatus] = useState<string>('')
  const [loading, setLoading] = useState<boolean>(false)
  const [currentPassword, setCurrentPassword] = useState<string>('')

  const [assets, setAssets] = useState<string>('0')
  const [income, setIncome] = useState<string>('0')
  const [riskLevel, setRiskLevel] = useState<string>('medium')
  const [horizon, setHorizon] = useState<string>('long')
  const [style, setStyle] = useState<string>('balanced')
  const [answers, setAnswers] = useState<QuestionnaireAnswers>(defaultAnswers)

  useEffect(() => {
    let cancelled = false

    async function init() {
      try {
        const [profileResp, templateResp] = await Promise.all([getProfile(), getQuestionnaireTemplate()])
        if (cancelled) return
        setProfile(profileResp)
        setTemplate(templateResp as QuestionnaireTemplate)

        const qa = (profileResp?.questionnaire_answers || {}) as Record<string, unknown>
        setAssets(String(profileResp?.assets ?? 0))
        setIncome(String(profileResp?.income ?? 0))
        setRiskLevel(profileResp?.risk_level || 'medium')
        setHorizon(profileResp?.investment_horizon || 'long')
        setStyle(profileResp?.style || 'balanced')
        setAnswers({
          disposable_funds: Number(qa.disposable_funds ?? profileResp?.disposable_funds ?? 0),
          loss_aversion: String(qa.loss_aversion ?? '2'),
          risk_comfort: String(qa.risk_comfort ?? '2'),
          time_horizon: String(qa.time_horizon ?? '2'),
          financial_literacy: String(qa.financial_literacy ?? '2'),
        })
      } catch (err: unknown) {
        if (cancelled) return
        setStatus((err as Error).message)
      }
    }

    void init()
    return () => {
      cancelled = true
    }
  }, [])

  const scoring = useMemo(() => profile?.questionnaire_answers?.scoring || null, [profile])

  function updateAnswer<K extends keyof QuestionnaireAnswers>(key: K, value: QuestionnaireAnswers[K]) {
    setAnswers((prev) => ({ ...prev, [key]: value }))
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault()
    setStatus('')
    if (!currentPassword.trim()) {
      setStatus('更新画像需要输入当前密码。')
      return
    }
    if (!Number.isFinite(answers.disposable_funds) || answers.disposable_funds <= 0) {
      setStatus('可支配资金必须大于 0。')
      return
    }

    setLoading(true)
    try {
      const payload = {
        assets: Number(assets || 0),
        income: Number(income || 0),
        risk_level: riskLevel,
        investment_horizon: horizon,
        style,
        questionnaire_answers: {
          disposable_funds: Number(answers.disposable_funds),
          loss_aversion: Number(answers.loss_aversion),
          risk_comfort: Number(answers.risk_comfort),
          time_horizon: Number(answers.time_horizon),
          financial_literacy: Number(answers.financial_literacy),
        },
        current_password: currentPassword,
      }
      const updated = await updateProfile(payload)
      setProfile(updated)
      setCurrentPassword('')
      setStatus('画像与问卷已更新。')
    } catch (err: unknown) {
      setStatus((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  if (!profile) {
    return <div className="paper">正在加载画像...</div>
  }

  const q = template?.questions || {}

  return (
    <section className="screen">
      <div className="paper">
        <div className="paper-header">
          <h2>个人投资者画像</h2>
          <div className="paper-meta">
            <span>风险等级：{profile.risk_level}</span>
            <span>风格：{profile.style}</span>
            <span>画像：{profile.persona}</span>
          </div>
        </div>

        <form onSubmit={handleSave} className="form-grid">
          <label>
            当前密码（保存必填）
            <input
              type="password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              placeholder="请输入当前密码"
              required
            />
          </label>

          <label>
            总资产（人民币）
            <input type="number" min={0} value={assets} onChange={(e) => setAssets(e.target.value)} />
          </label>

          <label>
            年收入（人民币）
            <input type="number" min={0} value={income} onChange={(e) => setIncome(e.target.value)} />
          </label>

          <label>
            风险等级
            <select value={riskLevel} onChange={(e) => setRiskLevel(e.target.value)}>
              <option value="low">低</option>
              <option value="medium">中</option>
              <option value="high">高</option>
            </select>
          </label>

          <label>
            投资期限
            <select value={horizon} onChange={(e) => setHorizon(e.target.value)}>
              <option value="short">短期</option>
              <option value="medium">中期</option>
              <option value="long">长期</option>
            </select>
          </label>

          <label>
            投资风格
            <select value={style} onChange={(e) => setStyle(e.target.value)}>
              <option value="stable">稳健</option>
              <option value="balanced">均衡</option>
              <option value="aggressive">进取</option>
            </select>
          </label>

          <div className="paper-header with-top-line profile-subtitle">
            <h3>风险问卷</h3>
          </div>

          <label>
            {q.disposable_funds?.title || '可支配资金（人民币）'}
            <input
              type="number"
              min={0}
              value={answers.disposable_funds}
              onChange={(e) => updateAnswer('disposable_funds', Number(e.target.value || 0))}
            />
          </label>

          <label>
            {q.loss_aversion?.title || '亏损厌恶'}
            <select value={answers.loss_aversion} onChange={(e) => updateAnswer('loss_aversion', e.target.value)}>
              {Object.entries(q.loss_aversion?.options || {}).map(([value, label]) => (
                <option key={value} value={value}>
                  {value} - {label}
                </option>
              ))}
            </select>
          </label>

          <label>
            {q.risk_comfort?.title || '风险承受偏好'}
            <select value={answers.risk_comfort} onChange={(e) => updateAnswer('risk_comfort', e.target.value)}>
              {Object.entries(q.risk_comfort?.options || {}).map(([value, label]) => (
                <option key={value} value={value}>
                  {value} - {label}
                </option>
              ))}
            </select>
          </label>

          <label>
            {q.time_horizon?.title || '投资期限'}
            <select value={answers.time_horizon} onChange={(e) => updateAnswer('time_horizon', e.target.value)}>
              {Object.entries(q.time_horizon?.options || {}).map(([value, label]) => (
                <option key={value} value={value}>
                  {value} - {label}
                </option>
              ))}
            </select>
          </label>

          <label>
            {q.financial_literacy?.title || '金融知识水平'}
            <select
              value={answers.financial_literacy}
              onChange={(e) => updateAnswer('financial_literacy', e.target.value)}
            >
              {Object.entries(q.financial_literacy?.options || {}).map(([value, label]) => (
                <option key={value} value={value}>
                  {value} - {label}
                </option>
              ))}
            </select>
          </label>

          <button type="submit" className="btn solid" disabled={loading}>
            {loading ? '保存中...' : '保存画像'}
          </button>
        </form>

        {scoring && (
          <div className="trade-panel">
            <h3>问卷评分</h3>
            <p>风险敏感指数：{scoring.risk_sensitivity_index}</p>
            <p>资金分层：{scoring.funds_bucket}</p>
            <p>建议单笔预算：{scoring.suggested_order_budget}</p>
            <p>风险预算：{scoring.risk_budget}</p>
            <p>单票最大仓位：{scoring.max_single_position}</p>
          </div>
        )}

        {status && <div className="inline-status">{status}</div>}
      </div>
    </section>
  )
}
