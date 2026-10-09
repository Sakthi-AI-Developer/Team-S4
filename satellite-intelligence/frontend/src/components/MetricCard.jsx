export default function MetricCard({ title, value, subtitle, icon }) {
  return (
    <article className="metric-card">
      <div className="metric-heading"><span>{icon}</span><span>{title}</span></div>
      <strong>{value ?? '--'}</strong>
      <small>{subtitle}</small>
    </article>
  );
}
