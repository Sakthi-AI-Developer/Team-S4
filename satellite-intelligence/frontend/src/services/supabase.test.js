import { afterEach, describe, expect, it, vi } from 'vitest';
import { createClient } from '@supabase/supabase-js';
import { normalizeSupabaseProjectUrl, supabase } from './supabase';

const configuredUrl = import.meta.env.VITE_SUPABASE_URL?.trim();
const projectUrl = 'https://project.supabase.co';

describe('Supabase Auth URL configuration', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('initializes the configured client with the project root', () => {
    if (!configuredUrl) {
      expect(supabase).toBeNull();
      return;
    }
    expect(new URL(configuredUrl).pathname).toBe('/');
    expect(new URL(supabase.auth.url).pathname).toBe('/auth/v1');
  });

  it('constructs signup at /auth/v1/signup from a misconfigured REST URL', async () => {
    const requests = [];
    const fetchMock = vi.fn(async (input) => {
      requests.push(String(input));
      return new Response('{}', {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    });
    const clientUrl = normalizeSupabaseProjectUrl(`${projectUrl}/rest/v1/`);
    const client = createClient(clientUrl, 'test-anon-key', {
      global: { fetch: fetchMock },
      auth: { persistSession: false, autoRefreshToken: false },
    });

    expect(new URL(clientUrl).pathname).toBe('/');
    expect(new URL(client.auth.url).pathname).toBe('/auth/v1');
    await client.auth.signUp({
      email: 'auth-url-test@example.test',
      password: 'test-password-only',
    });

    expect(requests).toHaveLength(1);
    expect(new URL(requests[0]).pathname).toBe('/auth/v1/signup');
    expect(requests[0]).not.toContain('/rest/v1/auth/v1/signup');
  });

  it('normalizes endpoint paths to the Supabase project origin', () => {
    expect(normalizeSupabaseProjectUrl(`${projectUrl}/rest/v1/`)).toBe(projectUrl);
    expect(normalizeSupabaseProjectUrl(`${projectUrl}/auth/v1`)).toBe(projectUrl);
    expect(normalizeSupabaseProjectUrl('https://project.supabase.co/?ref=unexpected')).toBe('');
    expect(normalizeSupabaseProjectUrl('http://project.supabase.co')).toBe('');
  });
});
