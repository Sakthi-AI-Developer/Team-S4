export default function ErrorMessage({ message, onDismiss }) {
  if (!message) return null;
  return (
    <div className="alert error" role="alert">
      <span>!</span><p>{message}</p>
      {onDismiss && <button onClick={onDismiss} aria-label="Dismiss error">×</button>}
    </div>
  );
}
