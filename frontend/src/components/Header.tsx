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
          <div className="brand-title">Jintong Tech</div>
          <div className="brand-subtitle">Stock Intelligence</div>
        </div>
      </div>
      <nav className="segment-nav" aria-label="primary">
        <NavLink to="/discover" className={({ isActive }) => `segment-link ${isActive ? 'active' : ''}`}>
          Find Targets
        </NavLink>
        <NavLink to="/track" className={({ isActive }) => `segment-link ${isActive ? 'active' : ''}`}>
          Track Stocks
        </NavLink>
        <NavLink to="/query" className={({ isActive }) => `segment-link ${isActive ? 'active' : ''}`}>
          Query Stock
        </NavLink>
      </nav>
      <button className="nav-action" onClick={onLogout} type="button">Logout</button>
    </header>
  )
}
