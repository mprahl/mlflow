import { shouldEnableWorkspaces } from './FeatureUtils';

const WORKSPACE_STORAGE_KEY = 'mlflow.activeWorkspace';
const WORKSPACE_PREFIX = '/workspaces/';
export const DEFAULT_WORKSPACE_NAME = 'default';

// Module-level state (simpler than React Context for this use case)
let activeWorkspace: string | null = null;

// Initialize from localStorage
if (typeof window !== 'undefined') {
  try {
    activeWorkspace = window.localStorage.getItem(WORKSPACE_STORAGE_KEY);
  } catch {
    // localStorage might be unavailable (e.g., private browsing)
  }
}

// Observable pattern for workspace changes
const listeners = new Set<(workspace: string | null) => void>();

export const getActiveWorkspace = () => activeWorkspace;
export const getCurrentWorkspace = getActiveWorkspace; // Alias for compatibility

export const setActiveWorkspace = (workspace: string | null) => {
  activeWorkspace = workspace;
  
  // Persist to localStorage
  if (typeof window !== 'undefined') {
    try {
      if (workspace) {
        window.localStorage.setItem(WORKSPACE_STORAGE_KEY, workspace);
      } else {
        window.localStorage.removeItem(WORKSPACE_STORAGE_KEY);
      }
    } catch {
      // no-op
    }
  }
  
  // Notify listeners
  listeners.forEach(listener => listener(activeWorkspace));
};

export const subscribeToWorkspaceChanges = (listener: (workspace: string | null) => void) => {
  listeners.add(listener);
  listener(activeWorkspace); // Immediate notification
  return () => {
    listeners.delete(listener);
  };
};

export const extractWorkspaceFromPathname = (pathname: string): string | null => {
  if (!pathname || !pathname.startsWith(WORKSPACE_PREFIX)) {
    return null;
  }
  const segments = pathname.split('/');
  if (segments.length < 3 || !segments[2]) {
    return null;
  }
  return decodeURIComponent(segments[2]);
};

export const buildWorkspacePath = (workspace: string, suffix = '') => {
  const encodedWorkspace = encodeURIComponent(workspace);
  return `/workspaces/${encodedWorkspace}${suffix}`;
};

const isAbsoluteUrl = (value: string) => /^[a-zA-Z][a-zA-Z\d+\-.]*:/.test(value);

export const prefixRouteWithWorkspace = (to: string): string => {
  if (!to || to.length === 0) {
    return to;
  }

  if (!shouldEnableWorkspaces() || isAbsoluteUrl(to)) {
    return to;
  }

  // Handle hash routes
  const prefix = to.startsWith('#') ? '#' : '';
  const valueWithoutPrefix = prefix ? to.slice(1) : to;

  const isAbsoluteNavigation = prefix !== '' || valueWithoutPrefix.startsWith('/');
  if (!isAbsoluteNavigation) {
    return to;
  }

  // Already has workspace prefix
  if (valueWithoutPrefix.startsWith(WORKSPACE_PREFIX)) {
    return to;
  }

  const workspace = activeWorkspace || DEFAULT_WORKSPACE_NAME;
  const path = valueWithoutPrefix || '/';
  
  return `${prefix}${buildWorkspacePath(workspace, path === '/' ? '' : path)}`;
};

export const prefixPathnameWithWorkspace = (pathname: string): string => {
  if (!pathname || !shouldEnableWorkspaces()) {
    return pathname;
  }

  if (pathname.startsWith(WORKSPACE_PREFIX)) {
    return pathname;
  }

  const workspace = activeWorkspace || DEFAULT_WORKSPACE_NAME;
  return buildWorkspacePath(workspace, pathname === '/' ? '' : pathname);
};

// Prefix API URLs with workspace
export const prefixWithWorkspace = (url: string): string => {
  if (!shouldEnableWorkspaces() || typeof url !== 'string') {
    return url;
  }

  // Don't prefix workspace management endpoints
  if (url.includes('ajax-api/2.0/mlflow/workspaces')) {
    return url;
  }

  const workspace = activeWorkspace;
  if (!workspace || !url.includes('ajax-api/2.0/mlflow/')) {
    return url;
  }

  // Inject workspace into path
  return url.replace(
    'ajax-api/2.0/mlflow/',
    `ajax-api/2.0/mlflow/workspaces/${encodeURIComponent(workspace)}/`
  );
};
