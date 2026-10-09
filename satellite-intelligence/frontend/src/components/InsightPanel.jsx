export default function InsightPanel({ messages }) {
  return (
    <article className="panel insight-panel">
      <div className="panel-header">
        <div><p className="eyebrow">DETERMINISTIC SUMMARY</p><h2>Satellite insight</h2></div>
        <span className="insight-icon">✧</span>
      </div>
      <ul>{messages.map((message) => <li key={message}>{message}</li>)}</ul>
      <p className="panel-note">Satellite-derived indicators only. Validate with ground observations where required.</p>
    </article>
  );
}
