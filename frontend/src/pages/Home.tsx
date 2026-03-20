import { Link } from 'react-router-dom'

type HomeProps = {
  authenticated: boolean
}

const samples = [
  { title: '收盘复盘', desc: '汇总异动、新闻与宏观事件，形成候选池。', image: '/picture/windows1.jpg' },
  { title: '开盘前扫描', desc: '开盘前重评分候选标的，输出前N观察列表。', image: '/picture/windows2.jpg' },
  { title: '情绪层', desc: '抽取新闻、公告和宏观文本情绪信号。', image: '/picture/windows6.jpg' },
  { title: '数据层', desc: '跟踪价格趋势、波动与动量择时信号。', image: '/picture/window4.jpg' },
  { title: '风险控制', desc: '管理止损、止盈与移动止损规则。', image: '/picture/windows5.jpg' },
  { title: '交易计划', desc: '按风险预算生成仓位与入场/离场区间。', image: '/picture/windows3.jpg' },
]

export default function Home({ authenticated }: HomeProps) {
  return (
    <section className="home-page reveal-up">
      <div className="home-top">
        <div className="home-brand-mark" />
        <nav className="home-nav">
          <a href="#features">功能</a>
          <a href="#workflow">流程</a>
          <a href="#risk">风控</a>
          <a href="#contact">联系</a>
        </nav>
        <div className="home-cta">
          {authenticated ? (
            <Link className="home-btn dark" to="/discover">进入系统</Link>
          ) : (
            <Link className="home-btn dark" to="/login">登录</Link>
          )}
        </div>
      </div>

      <div className="home-hero">
        <h1>金通科技</h1>
        <p>多专家协同的 A 股研究与决策系统</p>
      </div>

      <div className="home-banners">
        <div className="banner-placeholder banner-cover">
          <img className="banner-image" src="/picture/cover_picture.jpg" alt="首页封面" />
        </div>
      </div>

      <section id="features" className="home-section">
        <h2>项目概览</h2>
        <p>多源数据驱动 + 多专家融合，服务个人投资决策。</p>
      </section>

      <section id="workflow" className="home-grid">
        {samples.map((item) => (
          <article className="home-card" key={item.title}>
            <div className="mini-thumb">
              <img src={item.image} alt={`${item.title}示意图`} />
            </div>
            <h3>{item.title}</h3>
            <p>{item.desc}</p>
          </article>
        ))}
      </section>
    </section>
  )
}
