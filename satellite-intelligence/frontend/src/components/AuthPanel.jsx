import { useState } from 'react';
import { signInWithEmail, signUpWithEmail } from '../services/supabase';

export default function AuthPanel({
  onAuthenticated,
  configurationError = false,
  sessionNotice = '',
}) {
  const [mode, setMode] = useState('sign-in');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const session = mode === 'sign-in'
        ? await signInWithEmail(email, password)
        : await signUpWithEmail(email, password);
      if (!session) {
        setNotice('Check your email to confirm your account, then sign in.');
        return;
      }
      onAuthenticated(session.user);
    } catch (authError) {
      setError(authError.message || 'Unable to authenticate with Supabase.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-shell">
      <form className="auth-card" onSubmit={submit}>
        <p className="eyebrow">PRIVATE WORKSPACE</p>
        <h1>{configurationError ? 'Authentication setup required' : 'Sign in to Satellite Vision'}</h1>
        {configurationError ? (
          <p role="alert">
            Backend authentication is required, but frontend Supabase Auth is not configured.
            Set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY in Vercel, then redeploy.
          </p>
        ) : (
          <>
            <p>Sign in to access your analyses, maps, reports, and private downloads.</p>
            {sessionNotice && <p className="auth-notice" role="status">{sessionNotice}</p>}
            <label htmlFor="auth-email">Email</label>
            <input
              id="auth-email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
            <label htmlFor="auth-password">Password</label>
            <input
              id="auth-password"
              type="password"
              autoComplete={mode === 'sign-in' ? 'current-password' : 'new-password'}
              minLength={8}
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
            {error && <p className="auth-error" role="alert">{error}</p>}
            {notice && <p className="auth-notice" role="status">{notice}</p>}
            <button className="primary-button" type="submit" disabled={busy}>
              {busy ? 'Please wait…' : mode === 'sign-in' ? 'Sign in' : 'Create account'}
            </button>
            <button
              className="auth-mode-button"
              type="button"
              disabled={busy}
              onClick={() => {
                setMode(mode === 'sign-in' ? 'sign-up' : 'sign-in');
                setError('');
                setNotice('');
              }}
            >
              {mode === 'sign-in' ? 'Need an account? Create one' : 'Already registered? Sign in'}
            </button>
          </>
        )}
      </form>
    </main>
  );
}
