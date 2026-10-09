import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

const format = (value) => Number.isFinite(value) ? value.toFixed(3) : '--';

export default function ChangeChart({ result, currentResult, historicalMean }) {
  const meanChange = result?.mean_change;
  const currentMean = result?.current_mean ?? currentResult?.statistics?.mean;
  const previousMean = result?.historical_mean ?? historicalMean;
  const values = [
    { name: 'Historical NDVI', value: previousMean },
    { name: 'Current NDVI', value: currentMean },
    { name: 'Difference', value: meanChange },
  ].filter((entry) => Number.isFinite(entry.value));
  return (
    <article id="change" className="panel chart-panel">
      <div className="panel-header">
        <div><p className="eyebrow">TEMPORAL COMPARISON</p><h2>Vegetation change</h2></div>
        <span className={`method-badge ${result ? 'available' : ''}`}>{result ? 'AVAILABLE' : 'HISTORICAL DATA NEEDED'}</span>
      </div>
      {result ? <>
        <div className="change-content">
          <ResponsiveContainer width="100%" height={170}>
            <BarChart data={values}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#27343a" />
              <XAxis dataKey="name" stroke="#8b9aa1" tick={{ fontSize: 9 }} />
              <YAxis stroke="#8b9aa1" />
              <Tooltip formatter={(value) => format(Number(value))} />
              <Bar dataKey="value" radius={[4, 4, 0, 0]}>
                {values.map((entry) => <Cell key={entry.name} fill={entry.name === 'Difference' ? (entry.value < 0 ? '#cf736f' : '#68ae87') : '#6d9b8a'} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
          <div className="change-summary">
            <span>Mean NDVI difference <b>{format(meanChange)}</b></span>
            <span>Positive change <b>{Number.isFinite(result.positive_change_percentage) ? `${result.positive_change_percentage.toFixed(1)}%` : '--'}</b></span>
            <span>Negative change <b>{Number.isFinite(result.negative_change_percentage) ? `${result.negative_change_percentage.toFixed(1)}%` : '--'}</b></span>
          </div>
        </div>
      </> : <div className="chart-empty">Aligned historical B04 and B08 imagery is required to compare periods.</div>}
      <p className="panel-note">Change = current NDVI − historical NDVI. Not a causal assessment.</p>
    </article>
  );
}
