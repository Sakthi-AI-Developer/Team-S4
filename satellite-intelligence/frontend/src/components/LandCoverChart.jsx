import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

const COLORS = ['#d2a85e', '#4b9c68', '#55a8db', '#d66f5e', '#b59576'];

export default function LandCoverChart({ result }) {
  const distribution = result?.class_distribution;
  const data = distribution ? Object.values(distribution).map((item) => ({
    name: item.name,
    percentage: item.percentage,
  })) : [];
  const distributionTotal = data.reduce((total, item) => total + (Number.isFinite(item.percentage) ? item.percentage : 0), 0);
  return (
    <article id="landcover" className="panel chart-panel">
      <div className="panel-header">
        <div><p className="eyebrow">BASELINE CLASSIFICATION</p><h2>Land-cover distribution</h2></div>
        <span className="method-badge">HEURISTIC</span>
      </div>
      {data.length ? (
        <div className="landcover-chart">
          <ResponsiveContainer width="100%" height={235}>
            <BarChart data={data} margin={{ top: 8, right: 10, left: -16, bottom: 5 }}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#27343a" />
              <XAxis dataKey="name" stroke="#8b9aa1" tick={{ fontSize: 9 }} interval={0} />
              <YAxis stroke="#8b9aa1" unit="%" tick={{ fontSize: 9 }} />
              <Tooltip formatter={(value) => `${Number(value).toFixed(1)}%`} />
              <Bar dataKey="percentage" name="Valid pixels" radius={[4, 4, 0, 0]}>
                {data.map((item, index) => <Cell key={item.name} fill={COLORS[index % COLORS.length]} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
          <p className="chart-total">Distribution total: {distributionTotal.toFixed(1)}% · valid classified pixels.</p>
        </div>
      ) : <div className="chart-empty">Land-cover values appear here after a successful classification.</div>}
      <p className="panel-note">Transparent index-threshold baseline; not a trained model or ground truth.</p>
    </article>
  );
}
