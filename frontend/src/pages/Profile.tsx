import { useEffect, useState } from 'react'
import { getProfile, updateProfile } from '../api'

const riskOptions = ['low', 'medium', 'high']
const horizonOptions = ['short', 'medium', 'long']
const styleOptions = ['stable', 'balanced', 'aggressive']

export default function Profile() {
  const [profile, setProfile] = useState<any>(null)
  const [status, setStatus] = useState<string>('')

  useEffect(() => {
    getProfile()
      .then(setProfile)
      .catch((err) => setStatus(err.message))
  }, [])

  async function handleSave(e: React.FormEvent) {
    e.preventDefault()
    try {
      const updated = await updateProfile(profile)
      setProfile(updated)
      setStatus('Saved')
    } catch (err: any) {
      setStatus(err.message)
    }
  }

  if (!profile) {
    return <div className="panel">Loading profile...</div>
  }

  return (
    <div className="panel">
      <h2>Investor Profile</h2>
      <form onSubmit={handleSave} className="form-grid">
        <label>
          Risk Level
          <select
            value={profile.risk_level}
            onChange={(e) => setProfile({ ...profile, risk_level: e.target.value })}
          >
            {riskOptions.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <label>
          Investment Horizon
          <select
            value={profile.investment_horizon}
            onChange={(e) => setProfile({ ...profile, investment_horizon: e.target.value })}
          >
            {horizonOptions.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <label>
          Annual Income (USD)
          <input
            type="number"
            value={profile.income}
            onChange={(e) => setProfile({ ...profile, income: Number(e.target.value) })}
          />
        </label>
        <label>
          Total Assets (USD)
          <input
            type="number"
            value={profile.assets}
            onChange={(e) => setProfile({ ...profile, assets: Number(e.target.value) })}
          />
        </label>
        <label>
          Experience (years)
          <input
            type="number"
            value={profile.experience_years}
            onChange={(e) => setProfile({ ...profile, experience_years: Number(e.target.value) })}
          />
        </label>
        <label>
          Max Drawdown (0-1)
          <input
            type="number"
            step="0.01"
            value={profile.max_drawdown}
            onChange={(e) => setProfile({ ...profile, max_drawdown: Number(e.target.value) })}
          />
        </label>
        <label>
          Risk Budget (0-1)
          <input
            type="number"
            step="0.01"
            value={profile.risk_budget ?? 0.02}
            onChange={(e) => setProfile({ ...profile, risk_budget: Number(e.target.value) })}
          />
        </label>
        <label>
          Target Return (0-1)
          <input
            type="number"
            step="0.01"
            value={profile.target_return ?? 0.12}
            onChange={(e) => setProfile({ ...profile, target_return: Number(e.target.value) })}
          />
        </label>
        <label>
          Max Single Position (0-1)
          <input
            type="number"
            step="0.01"
            value={profile.max_single_position ?? 0.15}
            onChange={(e) => setProfile({ ...profile, max_single_position: Number(e.target.value) })}
          />
        </label>
        <label>
          Style
          <select
            value={profile.style ?? 'balanced'}
            onChange={(e) => setProfile({ ...profile, style: e.target.value })}
          >
            {styleOptions.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" className="primary">Save Profile</button>
      </form>
      {status && <p className="status">{status}</p>}
    </div>
  )
}
