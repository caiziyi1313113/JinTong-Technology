import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { getCurrentUser, login, register, setToken, type RegisterProfilePayload } from '../api'

type LoginProps = {
  onLoginSuccess: (userId: string) => void
  onStatus: (message: string) => void
}

export default function Login({ onLoginSuccess, onStatus }: LoginProps) {
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [assets, setAssets] = useState<string>('')
  const [income, setIncome] = useState<string>('')
  const [disposableFunds, setDisposableFunds] = useState<string>('')
  const [lossAversion, setLossAversion] = useState<string>('2')
  const [riskComfort, setRiskComfort] = useState<string>('2')
  const [timeHorizon, setTimeHorizon] = useState<string>('2')
  const [financialLiteracy, setFinancialLiteracy] = useState<string>('2')
  const [loading, setLoading] = useState(false)
  const [localError, setLocalError] = useState('')

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setLocalError('')
    setLoading(true)
    try {
      if (mode === 'register') {
        const funds = Number(disposableFunds)
        if (!Number.isFinite(funds) || funds <= 0) {
          throw new Error('请填写有效的可支配资金金额。')
        }
        const profilePayload: RegisterProfilePayload = {
          assets: assets ? Number(assets) : undefined,
          income: income ? Number(income) : undefined,
          disposable_funds: funds,
          questionnaire_answers: {
            disposable_funds: funds,
            loss_aversion: Number(lossAversion),
            risk_comfort: Number(riskComfort),
            time_horizon: Number(timeHorizon),
            financial_literacy: Number(financialLiteracy),
          },
        }
        await register(email, password, profilePayload)
      }

      const token = await login(email, password)
      setToken(token.access_token)
      const me = await getCurrentUser()
      onLoginSuccess(String(me.id))
      onStatus(mode === 'register' ? '注册并登录成功，请完善投资者问卷。' : '登录成功。')
      navigate(mode === 'register' ? '/profile' : '/discover', { replace: true })
    } catch (err: unknown) {
      const message = (err as Error).message || '登录失败'
      setLocalError(message)
      onStatus('')
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className="login-page reveal-up">
      <div className="login-panel">
        <h1>{mode === 'login' ? '用户登录' : '用户注册'}</h1>
        <form onSubmit={handleSubmit}>
          <label>
            邮箱
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="请输入邮箱"
              required
            />
          </label>
          <label>
            密码
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="请输入密码"
              required
            />
          </label>

          {mode === 'register' && (
            <>
              <div className="paper-header with-top-line">
                <h3>投资者问卷</h3>
              </div>

              <label>
                可支配资金（人民币，必填）
                <input
                  type="number"
                  min={0}
                  value={disposableFunds}
                  onChange={(e) => setDisposableFunds(e.target.value)}
                  placeholder="例如 100000"
                  required={mode === 'register'}
                />
              </label>

              <label>
                总资产（人民币，可选）
                <input
                  type="number"
                  min={0}
                  value={assets}
                  onChange={(e) => setAssets(e.target.value)}
                  placeholder="可选"
                />
              </label>

              <label>
                年收入（人民币，可选）
                <input
                  type="number"
                  min={0}
                  value={income}
                  onChange={(e) => setIncome(e.target.value)}
                  placeholder="可选"
                />
              </label>

              <label>
                亏损厌恶程度
                <select value={lossAversion} onChange={(e) => setLossAversion(e.target.value)}>
                  <option value="1">1 - 亏损 5 千就恐慌</option>
                  <option value="2">2 - 亏损 1.5 万会减仓/离场</option>
                  <option value="3">3 - 可承受 3 万内亏损</option>
                  <option value="4">4 - 可承受较深回撤</option>
                </select>
              </label>

              <label>
                风险承受偏好
                <select value={riskComfort} onChange={(e) => setRiskComfort(e.target.value)}>
                  <option value="1">1 - 跌 20% 全部卖出</option>
                  <option value="2">2 - 跌 20% 部分卖出</option>
                  <option value="3">3 - 持有等待</option>
                  <option value="4">4 - 继续加仓</option>
                </select>
              </label>

              <label>
                投资期限
                <select value={timeHorizon} onChange={(e) => setTimeHorizon(e.target.value)}>
                  <option value="1">1 - 少于 1 年</option>
                  <option value="2">2 - 1 到 3 年</option>
                  <option value="3">3 - 3 到 5 年</option>
                  <option value="4">4 - 超过 5 年</option>
                </select>
              </label>

              <label>
                金融知识水平
                <select value={financialLiteracy} onChange={(e) => setFinancialLiteracy(e.target.value)}>
                  <option value="1">1 - 仅现金/存款</option>
                  <option value="2">2 - 有基础股票/基金经验</option>
                  <option value="3">3 - 经常参与股票/基金交易</option>
                  <option value="4">4 - 有衍生品经验</option>
                </select>
              </label>
            </>
          )}

          <button type="submit" className="login-submit" disabled={loading}>
            {loading ? '处理中...' : mode === 'login' ? '登录' : '注册并登录'}
          </button>
        </form>

        <div className="login-actions">
          <button
            type="button"
            className="text-action"
            onClick={() => setMode(mode === 'login' ? 'register' : 'login')}
          >
            {mode === 'login' ? '没有账号？去注册' : '已有账号？去登录'}
          </button>
          <Link to="/home" className="text-action">返回首页</Link>
        </div>

        {localError && <div className="login-error">{localError}</div>}
      </div>
    </section>
  )
}
