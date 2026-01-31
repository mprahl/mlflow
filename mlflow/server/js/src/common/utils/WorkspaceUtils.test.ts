import { describe, jest, beforeEach, afterEach, it, expect } from '@jest/globals';
import {
  getActiveWorkspace,
  setActiveWorkspace,
  extractWorkspaceFromPathname,
  extractWorkspaceFromSearchParams,
  subscribeToWorkspaceChanges,
  setAvailableWorkspaces,
  getAvailableWorkspaces,
  prefixRouteWithWorkspace,
  prefixPathnameWithWorkspace,
  validateWorkspaceName,
  isGlobalRoute,
  removeWorkspaceQueryParam,
  WORKSPACE_NAME_MIN_LENGTH,
  WORKSPACE_NAME_MAX_LENGTH,
  WORKSPACE_QUERY_PARAM,
} from './WorkspaceUtils';
import { getWorkspacesEnabledSync } from './ServerFeaturesContext';

jest.mock('./ServerFeaturesContext', () => ({
  ...jest.requireActual<typeof import('./ServerFeaturesContext')>('./ServerFeaturesContext'),
  getWorkspacesEnabledSync: jest.fn(),
}));

const getWorkspacesEnabledSyncMock = jest.mocked(getWorkspacesEnabledSync);

describe('validateWorkspaceName', () => {
  it('accepts valid workspace names', () => {
    expect(validateWorkspaceName('my-workspace')).toEqual({ valid: true });
    expect(validateWorkspaceName('workspace1')).toEqual({ valid: true });
    expect(validateWorkspaceName('a1')).toEqual({ valid: true });
    expect(validateWorkspaceName('team-a-project-1')).toEqual({ valid: true });
  });

  it('rejects names shorter than minimum length', () => {
    const result = validateWorkspaceName('a');
    expect(result.valid).toBe(false);
    expect(result.error).toContain(`between ${WORKSPACE_NAME_MIN_LENGTH} and ${WORKSPACE_NAME_MAX_LENGTH}`);
  });

  it('rejects names longer than maximum length', () => {
    const longName = 'a'.repeat(WORKSPACE_NAME_MAX_LENGTH + 1);
    const result = validateWorkspaceName(longName);
    expect(result.valid).toBe(false);
    expect(result.error).toContain(`between ${WORKSPACE_NAME_MIN_LENGTH} and ${WORKSPACE_NAME_MAX_LENGTH}`);
  });

  it('rejects names with uppercase letters', () => {
    const result = validateWorkspaceName('MyWorkspace');
    expect(result.valid).toBe(false);
    expect(result.error).toContain('lowercase alphanumeric');
  });

  it('rejects names with consecutive hyphens', () => {
    const result = validateWorkspaceName('my--workspace');
    expect(result.valid).toBe(false);
    expect(result.error).toContain('no consecutive hyphens');
  });

  it('rejects names starting with hyphen', () => {
    const result = validateWorkspaceName('-workspace');
    expect(result.valid).toBe(false);
    expect(result.error).toContain('lowercase alphanumeric');
  });

  it('rejects names ending with hyphen', () => {
    const result = validateWorkspaceName('workspace-');
    expect(result.valid).toBe(false);
    expect(result.error).toContain('lowercase alphanumeric');
  });

  it('rejects names with spaces', () => {
    const result = validateWorkspaceName('my workspace');
    expect(result.valid).toBe(false);
    expect(result.error).toContain('lowercase alphanumeric');
  });

  it('rejects non-string values', () => {
    // @ts-expect-error Testing invalid type
    const result = validateWorkspaceName(123);
    expect(result.valid).toBe(false);
    expect(result.error).toContain('must be a string');
  });
});

describe('WorkspaceUtils', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    getWorkspacesEnabledSyncMock.mockReturnValue(true);
    // Clear any stored workspace
    setActiveWorkspace(null);
    setAvailableWorkspaces([]);
    // Clear localStorage
    if (typeof window !== 'undefined') {
      window.localStorage.clear();
    }
  });

  afterEach(() => {
    setActiveWorkspace(null);
    setAvailableWorkspaces([]);
  });

  describe('getActiveWorkspace / setActiveWorkspace', () => {
    it('returns null when no workspace is set', () => {
      expect(getActiveWorkspace()).toBeNull();
    });

    it('returns workspace after setting it', () => {
      setActiveWorkspace('team-a');
      expect(getActiveWorkspace()).toBe('team-a');
    });

    it('persists workspace to localStorage', () => {
      setActiveWorkspace('team-b');
      const stored = window.localStorage.getItem('mlflow.activeWorkspace');
      expect(stored).toBe('team-b');
    });

    it('removes from localStorage when setting to null', () => {
      setActiveWorkspace('team-c');
      expect(window.localStorage.getItem('mlflow.activeWorkspace')).toBe('team-c');

      setActiveWorkspace(null);
      expect(window.localStorage.getItem('mlflow.activeWorkspace')).toBeNull();
    });

    it('notifies listeners when workspace changes', () => {
      const listener = jest.fn();
      subscribeToWorkspaceChanges(listener);

      // Initial call
      expect(listener).toHaveBeenCalledWith(null);

      setActiveWorkspace('team-d');
      expect(listener).toHaveBeenCalledWith('team-d');

      setActiveWorkspace('team-e');
      expect(listener).toHaveBeenCalledWith('team-e');
    });
  });

  describe('extractWorkspaceFromSearchParams', () => {
    it('extracts workspace from URLSearchParams', () => {
      const params = new URLSearchParams('workspace=default');
      expect(extractWorkspaceFromSearchParams(params)).toBe('default');
    });

    it('extracts workspace from query string', () => {
      expect(extractWorkspaceFromSearchParams('workspace=team-a')).toBe('team-a');
      expect(extractWorkspaceFromSearchParams('?workspace=team-b')).toBe('team-b');
    });

    it('returns null when workspace param is missing', () => {
      expect(extractWorkspaceFromSearchParams('')).toBeNull();
      expect(extractWorkspaceFromSearchParams('other=value')).toBeNull();
    });

    it('returns null for invalid workspace names', () => {
      expect(extractWorkspaceFromSearchParams('workspace=UPPERCASE')).toBeNull();
      expect(extractWorkspaceFromSearchParams('workspace=has spaces')).toBeNull();
      expect(extractWorkspaceFromSearchParams('workspace=has--double-hyphen')).toBeNull();
    });

    it('handles URL-encoded workspace names', () => {
      // Valid name that's URL-encoded
      expect(extractWorkspaceFromSearchParams('workspace=team-a')).toBe('team-a');
    });
  });

  describe('extractWorkspaceFromPathname (legacy)', () => {
    it('returns null for paths without workspace prefix', () => {
      expect(extractWorkspaceFromPathname('/experiments')).toBeNull();
      expect(extractWorkspaceFromPathname('/models/123')).toBeNull();
    });

    it('returns null for empty path', () => {
      expect(extractWorkspaceFromPathname('')).toBeNull();
    });

    it('extracts workspace name from valid workspace path', () => {
      expect(extractWorkspaceFromPathname('/workspaces/default/experiments')).toBe('default');
      expect(extractWorkspaceFromPathname('/workspaces/team-a/models')).toBe('team-a');
    });

    it('rejects URL-encoded workspace names that decode to invalid names', () => {
      // Names with spaces or slashes are not valid workspace names
      expect(extractWorkspaceFromPathname('/workspaces/team%20a/experiments')).toBeNull();
      expect(extractWorkspaceFromPathname('/workspaces/team%2Fb/experiments')).toBeNull();
    });

    it('handles URL-encoded workspace names that are valid', () => {
      // Valid workspace name that happens to be URL-encoded
      expect(extractWorkspaceFromPathname('/workspaces/team-a/experiments')).toBe('team-a');
    });

    it('returns null for malformed workspace paths', () => {
      expect(extractWorkspaceFromPathname('/workspaces/')).toBeNull();
      expect(extractWorkspaceFromPathname('/workspaces')).toBeNull();
    });
  });

  describe('isGlobalRoute', () => {
    it('returns false for root path (handled specially in prefixRouteWithWorkspace)', () => {
      // Root path is NOT in ALWAYS_GLOBAL_ROUTES - it's handled specially:
      // - '/' without workspace param = workspace selector (no workspace added)
      // - '/?workspace=foo' = workspace home (preserve workspace)
      expect(isGlobalRoute('/')).toBe(false);
    });

    it('returns true for settings path (always global)', () => {
      expect(isGlobalRoute('/settings')).toBe(true);
      expect(isGlobalRoute('/settings/general')).toBe(true);
    });

    it('returns false for workspace-scoped paths', () => {
      expect(isGlobalRoute('/experiments')).toBe(false);
      expect(isGlobalRoute('/models')).toBe(false);
      expect(isGlobalRoute('/prompts')).toBe(false);
    });

    it('ignores query params and hash', () => {
      expect(isGlobalRoute('/?workspace=default')).toBe(false);
      expect(isGlobalRoute('/settings?tab=general#section')).toBe(true);
    });
  });

  describe('removeWorkspaceQueryParam', () => {
    it('removes workspace param from query string', () => {
      expect(removeWorkspaceQueryParam('/experiments?workspace=default')).toBe('/experiments');
    });

    it('preserves other query params', () => {
      expect(removeWorkspaceQueryParam('/experiments?workspace=default&filter=active')).toBe(
        '/experiments?filter=active',
      );
    });

    it('handles hash prefix', () => {
      expect(removeWorkspaceQueryParam('#/experiments?workspace=default')).toBe('#/experiments');
    });

    it('preserves hash fragment', () => {
      expect(removeWorkspaceQueryParam('/experiments?workspace=default#section')).toBe('/experiments#section');
    });

    it('returns unchanged if no workspace param', () => {
      expect(removeWorkspaceQueryParam('/experiments?filter=active')).toBe('/experiments?filter=active');
    });
  });

  describe('subscribeToWorkspaceChanges', () => {
    it('calls listener immediately with current workspace', () => {
      setActiveWorkspace('initial');
      const listener = jest.fn();

      subscribeToWorkspaceChanges(listener);

      expect(listener).toHaveBeenCalledWith('initial');
    });

    it('calls listener when workspace changes', () => {
      const listener = jest.fn();
      subscribeToWorkspaceChanges(listener);

      listener.mockClear();

      setActiveWorkspace('changed');
      expect(listener).toHaveBeenCalledWith('changed');
    });

    it('returns unsubscribe function that removes listener', () => {
      const listener = jest.fn();
      const unsubscribe = subscribeToWorkspaceChanges(listener);

      listener.mockClear();
      unsubscribe();

      setActiveWorkspace('should-not-notify');
      expect(listener).not.toHaveBeenCalled();
    });
  });

  describe('getActiveWorkspace', () => {
    it('returns the active workspace', () => {
      expect(getActiveWorkspace()).toBeNull();

      setActiveWorkspace('workspace-1');
      expect(getActiveWorkspace()).toBe('workspace-1');
    });
  });

  describe('getAvailableWorkspaces / setAvailableWorkspaces', () => {
    it('gets and sets available workspaces', () => {
      expect(getAvailableWorkspaces()).toEqual([]);

      setAvailableWorkspaces(['default', 'team-a']);
      expect(getAvailableWorkspaces()).toEqual(['default', 'team-a']);
    });
  });

  describe('prefixRouteWithWorkspace', () => {
    beforeEach(() => {
      setActiveWorkspace('default');
    });

    it('returns original string for empty/undefined values', () => {
      expect(prefixRouteWithWorkspace('')).toBe('');
      // @ts-expect-error Testing undefined case
      expect(prefixRouteWithWorkspace(undefined)).toBeUndefined();
    });

    it('returns original string when workspaces disabled', () => {
      getWorkspacesEnabledSyncMock.mockReturnValue(false);
      expect(prefixRouteWithWorkspace('/experiments')).toBe('/experiments');
    });

    it('returns original string for absolute URLs', () => {
      expect(prefixRouteWithWorkspace('https://example.com/path')).toBe('https://example.com/path');
      expect(prefixRouteWithWorkspace('http://localhost:3000')).toBe('http://localhost:3000');
    });

    it('returns original string for relative navigation (no leading /)', () => {
      expect(prefixRouteWithWorkspace('experiments')).toBe('experiments');
      expect(prefixRouteWithWorkspace('models/123')).toBe('models/123');
    });

    it('adds workspace query param to absolute paths', () => {
      expect(prefixRouteWithWorkspace('/experiments')).toBe('/experiments?workspace=default');
      expect(prefixRouteWithWorkspace('/models/123')).toBe('/models/123?workspace=default');
    });

    it('handles hash prefix correctly', () => {
      expect(prefixRouteWithWorkspace('#/experiments')).toBe('#/experiments?workspace=default');
    });

    it('preserves existing hash fragments', () => {
      expect(prefixRouteWithWorkspace('/experiments#section')).toBe('/experiments?workspace=default#section');
    });

    it('preserves existing query params', () => {
      expect(prefixRouteWithWorkspace('/experiments?search=test')).toBe('/experiments?search=test&workspace=default');
    });

    it('handles query strings and hash together', () => {
      expect(prefixRouteWithWorkspace('/models?filter=active#top')).toBe('/models?filter=active&workspace=default#top');
    });

    it('preserves explicit workspace param in URL', () => {
      // If URL already has explicit workspace, preserve it (don't override with active workspace)
      const path = '/experiments?workspace=old-workspace';
      expect(prefixRouteWithWorkspace(path)).toBe('/experiments?workspace=old-workspace');
    });

    it('returns path without workspace param when no workspace set', () => {
      setActiveWorkspace(null);
      expect(prefixRouteWithWorkspace('/experiments')).toBe('/experiments');
    });

    it('adds workspace param to root path when workspace is active (workspace home)', () => {
      // Root path with active workspace gets workspace param added (workspace home)
      expect(prefixRouteWithWorkspace('/')).toBe('/?workspace=default');
    });

    it('does not add workspace param to root path when no workspace is active', () => {
      setActiveWorkspace(null);
      expect(prefixRouteWithWorkspace('/')).toBe('/');
    });

    it('preserves explicit workspace param on root path', () => {
      // Root path with explicit workspace is preserved as-is
      expect(prefixRouteWithWorkspace('/?workspace=old')).toBe('/?workspace=old');
    });

    it('removes workspace param for global routes (settings)', () => {
      expect(prefixRouteWithWorkspace('/settings')).toBe('/settings');
      expect(prefixRouteWithWorkspace('/settings?workspace=old')).toBe('/settings');
    });

    it('uses different workspace when set', () => {
      setActiveWorkspace('team-a');
      expect(prefixRouteWithWorkspace('/experiments')).toBe('/experiments?workspace=team-a');
    });

    it('encodes workspace name in query param', () => {
      setActiveWorkspace('team-with-hyphen');
      expect(prefixRouteWithWorkspace('/experiments')).toBe('/experiments?workspace=team-with-hyphen');
    });
  });

  describe('prefixPathnameWithWorkspace', () => {
    beforeEach(() => {
      setActiveWorkspace('default');
    });

    it('returns undefined for undefined pathname', () => {
      expect(prefixPathnameWithWorkspace(undefined)).toBeUndefined();
    });

    it('returns original for absolute URLs', () => {
      expect(prefixPathnameWithWorkspace('https://example.com')).toBe('https://example.com');
    });

    it('adds workspace query param to pathname', () => {
      expect(prefixPathnameWithWorkspace('/experiments')).toBe('/experiments?workspace=default');
      expect(prefixPathnameWithWorkspace('/models/123')).toBe('/models/123?workspace=default');
    });

    it('adds workspace param to root path when workspace is active', () => {
      expect(prefixPathnameWithWorkspace('/')).toBe('/?workspace=default');
    });

    it('returns root path unchanged when no workspace is active', () => {
      setActiveWorkspace(null);
      expect(prefixPathnameWithWorkspace('/')).toBe('/');
    });

    it('returns always-global routes unchanged (settings)', () => {
      setActiveWorkspace('default');
      expect(prefixPathnameWithWorkspace('/settings')).toBe('/settings');
    });

    it('returns pathname without workspace when feature disabled', () => {
      getWorkspacesEnabledSyncMock.mockReturnValue(false);
      expect(prefixPathnameWithWorkspace('/experiments')).toBe('/experiments');
    });

    it('uses active workspace when set', () => {
      setActiveWorkspace('team-b');
      expect(prefixPathnameWithWorkspace('/models')).toBe('/models?workspace=team-b');
    });

    it('returns pathname unchanged when no workspace set', () => {
      setActiveWorkspace(null);
      expect(prefixPathnameWithWorkspace('/experiments')).toBe('/experiments');
    });
  });
});
