import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import './styles.css';

class AppErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, message: '' };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, message: error.message || 'The application encountered an unexpected error.' };
  }

  componentDidCatch(error, errorInfo) {
    console.error('App error boundary:', error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', padding: 24, background: '#101719', color: '#edf5f2' }}>
          <div style={{ maxWidth: 620, border: '1px solid #2b3a3c', borderRadius: 12, background: '#172124', padding: 24 }}>
            <p style={{ fontSize: 10, letterSpacing: '0.14em', color: '#9aaea3', marginBottom: 8 }}>APPLICATION ERROR</p>
            <h1 style={{ margin: '0 0 10px', fontSize: 28 }}>Something went wrong</h1>
            <p style={{ color: '#c7d5d2', marginBottom: 0 }}>{this.state.message}</p>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <AppErrorBoundary>
      <App />
    </AppErrorBoundary>
  </React.StrictMode>,
);
