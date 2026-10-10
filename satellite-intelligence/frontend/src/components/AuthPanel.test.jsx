import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import AuthPanel from './AuthPanel';
import { signInWithEmail, signUpWithEmail } from '../services/supabase';

vi.mock('../services/supabase', () => ({
  signInWithEmail: vi.fn(),
  signUpWithEmail: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
});

describe('authentication form', () => {
  it('shows a confirmation notice when signup requires email verification', async () => {
    signUpWithEmail.mockResolvedValue(null);
    render(<AuthPanel onAuthenticated={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: 'Need an account? Create one' }));
    fireEvent.change(screen.getByLabelText('Email'), {
      target: { value: 'new-user@example.test' },
    });
    fireEvent.change(screen.getByLabelText('Password'), {
      target: { value: 'long-test-password' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create account' }));

    expect(await screen.findByRole('status')).toHaveTextContent(
      'Check your email to confirm your account, then sign in.',
    );
    expect(signUpWithEmail).toHaveBeenCalledWith(
      'new-user@example.test',
      'long-test-password',
    );
  });

  it('reports invalid credentials without authenticating the user', async () => {
    signInWithEmail.mockRejectedValue(new Error('Invalid login credentials'));
    const onAuthenticated = vi.fn();
    render(<AuthPanel onAuthenticated={onAuthenticated} />);

    fireEvent.change(screen.getByLabelText('Email'), {
      target: { value: 'analyst@example.test' },
    });
    fireEvent.change(screen.getByLabelText('Password'), {
      target: { value: 'wrong-password' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Invalid login credentials',
    );
    expect(onAuthenticated).not.toHaveBeenCalled();
  });
});
