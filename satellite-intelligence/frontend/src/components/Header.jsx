export default function Header({ backendStatus, userEmail, onSignOut, signingOut }) {
  const online = backendStatus === 'online';
  return (
    <header className="topbar">
      <div className="breadcrumbs">Workspace <span>/</span> Satellite monitoring</div>
      <div className="topbar-right">
        <span className={`connection-badge ${online ? 'connected' : backendStatus === 'checking' ? 'checking' : 'offline'}`}>
          <i />
          {backendStatus === 'checking' ? 'Checking backend' : online ? 'System Online' : 'Backend Offline'}
        </span>
        <span className="stage-badge">
          {userEmail ? 'PRIVATE WORKSPACE' : 'LOCAL DEMO · PUBLIC DATA ONLY'}
        </span>
        {userEmail && <span className="auth-user-label">{userEmail}</span>}
        {onSignOut && (
          <button className="auth-signout-button" type="button" onClick={onSignOut} disabled={signingOut}>
            {signingOut ? 'Signing out…' : 'Sign out'}
          </button>
        )}
        <div className="avatar">SI</div>
      </div>
    </header>
  );
}
