import { Link } from 'react-router-dom'

type HomeProps = {
  authenticated: boolean
}

const samples = [
  { title: 'Post-Close Review', desc: 'Summarize movers, news and macro events to build candidate pool.' },
  { title: 'Pre-Open Scan', desc: 'Re-score candidates before open and output TopN watchlist.' },
  { title: 'Sentiment Layer', desc: 'Extract sentiment from news, announcements and macro text.' },
  { title: 'Data Layer', desc: 'Track price trend, volatility and momentum timing signals.' },
  { title: 'Risk Control', desc: 'Manage stop-loss, take-profit and trailing-stop rules.' },
  { title: 'Trade Plan', desc: 'Generate share size and entry/exit zones from risk budget.' },
]

export default function Home({ authenticated }: HomeProps) {
  return (
    <section className="home-page reveal-up">
      <div className="home-top">
        <div className="home-brand-mark" />
        <nav className="home-nav">
          <a href="#features">Feature</a>
          <a href="#workflow">Workflow</a>
          <a href="#risk">Risk</a>
          <a href="#contact">Contact</a>
        </nav>
        <div className="home-cta">
          {authenticated ? (
            <Link className="home-btn dark" to="/discover">Enter App</Link>
          ) : (
            <Link className="home-btn dark" to="/login">Login</Link>
          )}
        </div>
      </div>

      <div className="home-hero">
        <h1>Jintong Tech</h1>
        <p>Subtitle</p>
      </div>

      <div className="home-banners">
        <div className="banner-placeholder" />
        <div className="banner-placeholder" />
      </div>

      <section id="features" className="home-section">
        <h2>Project Overview</h2>
        <p>Multi-source signals plus multi-expert fusion for personal investment decisions.</p>
      </section>

      <section id="workflow" className="home-grid">
        {samples.map((item) => (
          <article className="home-card" key={item.title}>
            <div className="mini-thumb" />
            <h3>{item.title}</h3>
            <p>{item.desc}</p>
          </article>
        ))}
      </section>
    </section>
  )
}
