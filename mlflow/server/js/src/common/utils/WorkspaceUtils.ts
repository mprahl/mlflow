import { getWorkspacesEnabledSync } from './ServerFeaturesContext';

const WORKSPACE_STORAGE_KEY = 'mlflow.activeWorkspace';
export const WORKSPACE_QUERY_PARAM = 'workspace';

const getStoredWorkspace = () => {
  if (typeof window === 'undefined') {
    return null;
  }
  try {
    return window.localStorage.getItem(WORKSPACE_STORAGE_KEY);
  } catch {
    return null;
  }
};

let activeWorkspace: string | null = getStoredWorkspace();
let availableWorkspaces: string[] = [];

// Legacy path prefix - kept for backwards compatibility during migration
const WORKSPACE_PREFIX = '/workspaces/';

const listeners = new Set<(workspace: string | null) => void>();

/**
 * Get the currently active workspace name.
 * Returns null if workspaces feature is not enabled or no workspace is selected.
 */
export const getActiveWorkspace = () => {
  // Only return the active workspace if the workspaces feature is enabled
  if (!getWorkspacesEnabledSync()) {
    return null;
  }
  return activeWorkspace;
};

export const setActiveWorkspace = (workspace: string | null) => {
  activeWorkspace = workspace;
  if (typeof window !== 'undefined') {
    try {
      if (workspace) {
        window.localStorage.setItem(WORKSPACE_STORAGE_KEY, workspace);
      } else {
        window.localStorage.removeItem(WORKSPACE_STORAGE_KEY);
      }
    } catch {
      // no-op: localStorage might be unavailable (e.g., private browsing)
    }
  }
  listeners.forEach((listener) => listener(activeWorkspace));
};

// Workspace name validation constants (must match backend: mlflow/store/workspace/abstract_store.py)
export const WORKSPACE_NAME_PATTERN = /^(?!.*--)[a-z0-9]([-a-z0-9]*[a-z0-9])?$/;
export const WORKSPACE_NAME_MIN_LENGTH = 2;
export const WORKSPACE_NAME_MAX_LENGTH = 63;

export type WorkspaceValidationResult = {
  valid: boolean;
  error?: string;
};

/**
 * Validates a workspace name against backend rules.
 * Returns { valid: true } if valid, or { valid: false, error: "message" } if invalid.
 * Note: Reserved names are validated by the API server.
 */
export const validateWorkspaceName = (name: string): WorkspaceValidationResult => {
  if (typeof name !== 'string') {
    return { valid: false, error: 'Workspace name must be a string.' };
  }

  if (name.length < WORKSPACE_NAME_MIN_LENGTH || name.length > WORKSPACE_NAME_MAX_LENGTH) {
    return {
      valid: false,
      error: `Workspace name must be between ${WORKSPACE_NAME_MIN_LENGTH} and ${WORKSPACE_NAME_MAX_LENGTH} characters.`,
    };
  }

  if (!WORKSPACE_NAME_PATTERN.test(name)) {
    return {
      valid: false,
      error: 'Workspace name must be lowercase alphanumeric with optional single hyphens (no consecutive hyphens).',
    };
  }

  return { valid: true };
};

/**
 * Extract workspace from URL search params (query string).
 * This is the primary method for workspace extraction.
 */
export const extractWorkspaceFromSearchParams = (search: string | URLSearchParams): string | null => {
  const params = typeof search === 'string' ? new URLSearchParams(search) : search;
  const workspaceName = params.get(WORKSPACE_QUERY_PARAM);

  if (!workspaceName) {
    return null;
  }

  // Validate workspace name format
  if (!WORKSPACE_NAME_PATTERN.test(workspaceName)) {
    return null;
  }

  return workspaceName;
};

/**
 * @deprecated Use extractWorkspaceFromSearchParams instead.
 * Extract workspace from pathname (legacy path-based routing).
 * Kept for backwards compatibility during migration.
 */
export const extractWorkspaceFromPathname = (pathname: string): string | null => {
  if (!pathname || !pathname.startsWith(WORKSPACE_PREFIX)) {
    return null;
  }
  const segments = pathname.split('/');
  if (segments.length < 3 || !segments[2]) {
    return null;
  }

  let workspaceName: string;
  try {
    workspaceName = decodeURIComponent(segments[2]);
  } catch {
    // Malformed percent-encoding in URL - treat as no workspace
    return null;
  }

  // Validate workspace name format
  if (!WORKSPACE_NAME_PATTERN.test(workspaceName)) {
    return null;
  }

  return workspaceName;
};

export const subscribeToWorkspaceChanges = (listener: (workspace: string | null) => void) => {
  listeners.add(listener);
  listener(activeWorkspace);
  return () => {
    listeners.delete(listener);
  };
};

export const setAvailableWorkspaces = (workspaces: string[]) => {
  availableWorkspaces = workspaces;
};

export const getAvailableWorkspaces = () => availableWorkspaces;

const isAbsoluteUrl = (value: string) => /^[a-zA-Z][a-zA-Z\d+\-.]*:/.test(value);

/**
 * Global routes that should never have a workspace query param.
 * These are workspace-agnostic pages.
 * Note: '/' is special - it's the workspace selector without a workspace param,
 * but the workspace home page with a workspace param. Other routes listed here
 * are always global regardless of workspace param.
 */
const ALWAYS_GLOBAL_ROUTES = ['/settings'];

/**
 * Check if a pathname is a global route that shouldn't have workspace context.
 * Note: This returns true for paths that are ALWAYS global (like /settings).
 * The root path '/' is NOT included here because it's contextual:
 * - '/' without workspace param = workspace selector (global)
 * - '/' with workspace param = workspace home page (workspace-scoped)
 */
export const isGlobalRoute = (pathname: string): boolean => {
  const normalizedPath = pathname.split('?')[0].split('#')[0];
  return ALWAYS_GLOBAL_ROUTES.some((route) => normalizedPath === route || normalizedPath.startsWith(route + '/'));
};

/**
 * Add workspace query param to a URL string.
 * Preserves existing query params and hash fragments.
 */
const addWorkspaceQueryParam = (url: string, workspace: string): string => {
  // Handle hash prefix (e.g., "#/experiments")
  const hashPrefix = url.startsWith('#') ? '#' : '';
  const urlWithoutHashPrefix = hashPrefix ? url.slice(1) : url;

  // Parse the URL parts
  let pathname = urlWithoutHashPrefix;
  let existingQuery = '';
  let hashFragment = '';

  // Extract hash fragment first
  const hashIndex = pathname.indexOf('#');
  if (hashIndex >= 0) {
    hashFragment = pathname.slice(hashIndex);
    pathname = pathname.slice(0, hashIndex);
  }

  // Extract existing query string
  const queryIndex = pathname.indexOf('?');
  if (queryIndex >= 0) {
    existingQuery = pathname.slice(queryIndex + 1);
    pathname = pathname.slice(0, queryIndex);
  }

  // Parse existing params and add/update workspace
  const params = new URLSearchParams(existingQuery);
  params.set(WORKSPACE_QUERY_PARAM, workspace);

  return `${hashPrefix}${pathname}?${params.toString()}${hashFragment}`;
};

/**
 * Remove workspace query param from a URL string.
 */
export const removeWorkspaceQueryParam = (url: string): string => {
  const hashPrefix = url.startsWith('#') ? '#' : '';
  const urlWithoutHashPrefix = hashPrefix ? url.slice(1) : url;

  let pathname = urlWithoutHashPrefix;
  let existingQuery = '';
  let hashFragment = '';

  const hashIndex = pathname.indexOf('#');
  if (hashIndex >= 0) {
    hashFragment = pathname.slice(hashIndex);
    pathname = pathname.slice(0, hashIndex);
  }

  const queryIndex = pathname.indexOf('?');
  if (queryIndex >= 0) {
    existingQuery = pathname.slice(queryIndex + 1);
    pathname = pathname.slice(0, queryIndex);
  }

  const params = new URLSearchParams(existingQuery);
  params.delete(WORKSPACE_QUERY_PARAM);

  const queryString = params.toString();
  return `${hashPrefix}${pathname}${queryString ? '?' + queryString : ''}${hashFragment}`;
};

/**
 * Prefix a route with workspace query param.
 * For global routes, removes any existing workspace param.
 * Relative paths (not starting with / or #) are returned unchanged.
 *
 * Special handling for root path '/':
 * - '/' without workspace param -> workspace selector (no workspace added)
 * - '/?workspace=foo' with explicit workspace -> preserve as workspace home page
 */
export const prefixRouteWithWorkspace = (to: string): string => {
  if (typeof to !== 'string' || to.length === 0) {
    return to;
  }

  if (!getWorkspacesEnabledSync() || isAbsoluteUrl(to)) {
    return to;
  }

  // Extract pathname for checks
  const hashPrefix = to.startsWith('#') ? '#' : '';
  const urlWithoutHashPrefix = hashPrefix ? to.slice(1) : to;

  // Skip relative navigation (paths not starting with /)
  // These are relative to current location and shouldn't have workspace added
  const isAbsoluteNavigation = hashPrefix !== '' || urlWithoutHashPrefix.startsWith('/');
  if (!isAbsoluteNavigation) {
    return to;
  }

  let pathname = urlWithoutHashPrefix;
  let existingQuery = '';
  const hashIndex = pathname.indexOf('#');
  if (hashIndex >= 0) {
    pathname = pathname.slice(0, hashIndex);
  }
  const queryIndex = pathname.indexOf('?');
  if (queryIndex >= 0) {
    existingQuery = pathname.slice(queryIndex + 1);
    pathname = pathname.slice(0, queryIndex);
  }

  // Check if URL already has a workspace param
  const existingParams = new URLSearchParams(existingQuery);
  const hasExplicitWorkspace = existingParams.has(WORKSPACE_QUERY_PARAM);

  // For always-global routes (like /settings), strip workspace param
  if (isGlobalRoute(pathname || '/')) {
    return removeWorkspaceQueryParam(to);
  }

  // If URL already has explicit workspace param, preserve it as-is
  // This handles explicit workspace navigation like `/?workspace=foo`
  if (hasExplicitWorkspace) {
    return to;
  }

  // For workspace-scoped routes (including root '/'), add workspace param if there's an active workspace
  // Root '/' with workspace param = workspace home page
  // Root '/' without workspace param (and no active workspace) = workspace selector
  const workspace = getActiveWorkspace();
  if (!workspace) {
    return to;
  }

  return addWorkspaceQueryParam(to, workspace);
};

/**
 * Prefix a pathname with workspace query param.
 * Similar to prefixRouteWithWorkspace but for pathname-only values.
 */
export const prefixPathnameWithWorkspace = (pathname: string | undefined): string | undefined => {
  if (!pathname) {
    return pathname;
  }
  if (!getWorkspacesEnabledSync() || isAbsoluteUrl(pathname)) {
    return pathname;
  }

  // For always-global routes (like /settings), return as-is (no workspace)
  if (isGlobalRoute(pathname)) {
    return pathname;
  }

  // For all other routes (including root '/'), add workspace param if there's an active workspace
  const workspace = getActiveWorkspace();
  if (!workspace) {
    return pathname;
  }

  // Add workspace query param
  return `${pathname}?${WORKSPACE_QUERY_PARAM}=${encodeURIComponent(workspace)}`;
};
