import React, { useEffect, useMemo, useState } from 'react';
import { DropdownMenu, Input, Tooltip, useDesignSystemTheme, LegacySkeleton } from '@databricks/design-system';

import { shouldEnableWorkspaces, setWorkspacesEnabled } from '../utils/FeatureUtils';
import { fetchAPI, getAjaxUrl } from '../utils/FetchUtils';
import {
  DEFAULT_WORKSPACE_NAME,
  extractWorkspaceFromPathname,
  setActiveWorkspace,
  buildWorkspacePath,
} from '../utils/WorkspaceUtils';
import { useLocation, useNavigate, matchPath } from '../utils/RoutingUtils';

type Workspace = {
  name: string;
  description?: string | null;
};

const WORKSPACES_ENDPOINT = 'ajax-api/2.0/mlflow/workspaces';

// Get current navigation section for smart redirect
const getNavigationSection = (pathname: string): string => {
  if (matchPath('/experiments/*', pathname) || matchPath('/compare-experiments/*', pathname)) {
    return 'experiments';
  }
  if (matchPath('/models/*', pathname)) {
    return 'models';
  }
  if (matchPath('/prompts/*', pathname)) {
    return 'prompts';
  }
  return '';
};

export const WorkspaceSelector = () => {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [filterText, setFilterText] = useState('');
  const location = useLocation();
  const navigate = useNavigate();
  const { theme } = useDesignSystemTheme();

  const workspaceFromPath = extractWorkspaceFromPathname(location.pathname);
  const currentWorkspace = workspaceFromPath ?? DEFAULT_WORKSPACE_NAME;

  // Dynamic workspace detection with timeout
  useEffect(() => {
    let isMounted = true;
    const controller = new AbortController();

    const loadWorkspaces = async () => {
      setLoading(true);
      setLoadFailed(false);

      try {
        // 3 second timeout for dynamic detection
        const timeoutId = setTimeout(() => controller.abort(), 3000);
        
        const response = await fetchAPI(getAjaxUrl(WORKSPACES_ENDPOINT), 'GET', undefined, controller.signal);
        clearTimeout(timeoutId);

        if (!isMounted) {
          return;
        }

        const fetched = Array.isArray(response?.workspaces) ? response.workspaces : [];
        const filteredWorkspaces: Workspace[] = [];
        
        for (const item of fetched as Array<Workspace | Record<string, unknown>>) {
          if (item && typeof (item as Workspace)?.name === 'string') {
            const workspaceItem = item as Workspace;
            filteredWorkspaces.push({
              name: workspaceItem.name,
              description: workspaceItem.description ?? null,
            });
          }
        }

        setWorkspaces(filteredWorkspaces);
        setWorkspacesEnabled(filteredWorkspaces.length > 0);
      } catch (error) {
        if (isMounted && !controller.signal.aborted) {
          setLoadFailed(true);
        }
        // Timeout or error means workspaces disabled
        setWorkspacesEnabled(false);
      } finally {
        if (isMounted) {
          setLoading(false);
        }
      }
    };

    loadWorkspaces().catch(() => undefined);

    return () => {
      isMounted = false;
      controller.abort();
    };
  }, []);

  // Filter workspaces based on search text
  const filteredWorkspaces = useMemo(() => {
    if (!filterText) {
      return workspaces;
    }
    return workspaces.filter(w => 
      w.name.toLowerCase().includes(filterText.toLowerCase())
    );
  }, [workspaces, filterText]);

  // Ensure current workspace is in list
  const options = useMemo(() => {
    const deduped = new Map<string, Workspace>();

    for (const workspace of filteredWorkspaces) {
      deduped.set(workspace.name, workspace);
    }

    if (deduped.size === 0 && !filterText) {
      deduped.set(DEFAULT_WORKSPACE_NAME, { name: DEFAULT_WORKSPACE_NAME, description: null });
    }

    if (currentWorkspace && !deduped.has(currentWorkspace) && !filterText) {
      deduped.set(currentWorkspace, { name: currentWorkspace, description: null });
    }

    return Array.from(deduped.values());
  }, [filteredWorkspaces, currentWorkspace, filterText]);

  const handleWorkspaceChange = (nextWorkspace: string) => {
    if (!nextWorkspace || nextWorkspace === currentWorkspace) {
      return;
    }

    setActiveWorkspace(nextWorkspace);
    
    // Smart navigation: preserve current section
    const section = getNavigationSection(location.pathname);
    const suffix = section ? `/${section}` : '';
    const targetPath = buildWorkspacePath(nextWorkspace, suffix);
    
    navigate(`/${targetPath}${location.search ?? ''}`, { replace: true });
  };

  if (!shouldEnableWorkspaces()) {
    return null;
  }

  if (loading) {
    return <LegacySkeleton />;
  }

  if (loadFailed) {
    return null;
  }

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button
          style={{
            background: 'transparent',
            border: 'none',
            color: theme.colors.textSecondary,
            cursor: 'pointer',
            fontSize: theme.typography.fontSizeSm,
          }}
        >
          {currentWorkspace}
        </button>
      </DropdownMenu.Trigger>
      
      <DropdownMenu.Content align="start">
        {/* Client-side filter */}
        <div style={{ padding: theme.spacing.sm }}>
          <Input
            componentId="workspace_selector_filter"
            placeholder="Filter workspaces..."
            value={filterText}
            onChange={(e) => setFilterText(e.target.value)}
            size="small"
          />
        </div>
        
        {options.length === 0 && filterText && (
          <DropdownMenu.Label>No workspaces found</DropdownMenu.Label>
        )}
        
        {options.map((workspace) => (
          <Tooltip 
            key={workspace.name} 
            componentId={`workspace_selector_tooltip_${workspace.name}`}
            content={workspace.description || workspace.name}
          >
            <DropdownMenu.Item
              componentId={`workspace_selector_${workspace.name}`}
              onClick={() => handleWorkspaceChange(workspace.name)}
            >
              {workspace.name}
            </DropdownMenu.Item>
          </Tooltip>
        ))}
      </DropdownMenu.Content>
    </DropdownMenu.Root>
  );
};
