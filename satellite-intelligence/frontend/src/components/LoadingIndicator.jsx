export default function LoadingIndicator({ label = 'Loading' }) {
  return <span className="loading-indicator" role="status"><span className="spinner" />{label}</span>;
}
