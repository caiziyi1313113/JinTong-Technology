import { NavLink } from 'react-router-dom'

type HeaderProps = {
  onLogout: () => void
}

export default function Header({ onLogout }: HeaderProps) {
  return (
    <header className="header">
      <div className="brand">
        <span className="brand-mark" />
        <div>
          <div className="brand-title">金通科技</div>
          <div className="brand-subtitle">股票智能分析</div>
        </div>
      </div>
      <nav className="segment-nav" aria-label="主导航">
        <NavLink to="/discover" className={({ isActive }) => `segment-link ${isActive ? 'active' : ''}`}>
          选股评审
        </NavLink>
        <NavLink to="/track" className={({ isActive }) => `segment-link ${isActive ? 'active' : ''}`}>
          持仓跟踪
        </NavLink>
        <NavLink to="/query" className={({ isActive }) => `segment-link ${isActive ? 'active' : ''}`}>
          单股分析
        </NavLink>
        <NavLink to="/macro" className={({ isActive }) => `segment-link ${isActive ? 'active' : ''}`}>
          宏观分析
        </NavLink>
        <NavLink to="/profile" className={({ isActive }) => `segment-link ${isActive ? 'active' : ''}`}>
          投资者画像
        </NavLink>
      </nav>
      <button className="nav-action" onClick={onLogout} type="button">退出登录</button>
    </header>
  )
}
