export default function Header({ backendStatus }) {
  const online = backendStatus === 'online';
  return (
    <header className="topbar">
      <div className="breadcrumbs">Workspace <span>/</span> Satellite monitoring</div>
      <div className="topbar-right">
        <span className={`connection-badge ${online ? 'connected' : backendStatus === 'checking' ? 'checking' : 'offline'}`}>
          <i />
          {backendStatus === 'checking' ? 'Checking backend' : online ? 'System Online' : 'Backend Offline'}
        </span>
        <span className="stage-badge">STAGE A · LOCAL</span>
        <div className="avatar">SI</div>
      </div>
    </header>
  );
}
