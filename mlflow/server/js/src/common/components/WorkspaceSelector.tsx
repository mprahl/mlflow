import React, { useEffect, useMemo, useState } from 'react';
import { LegacySelect, useDesignSystemTheme } from '@databricks/design-system';

import { shouldEnableWorkspaces } from '../utils/FeatureUtils';
import { fetchAPI, getAjaxUrl } from '../utils/FetchUtils';
import { DEFAULT_WORKSPACE_NAME, extractWorkspaceFromPathname, setActiveWorkspace } from '../utils/WorkspaceUtils';
import { useLocation, useNavigate } from '../utils/RoutingUtils';

type Workspace = {
  name: string;
  description?: string | null;
};

const WORKSPACES_ENDPOINT = 'ajax-api/2.0/mlflow/workspaces';

export const WorkspaceSelector = () => {
  const workspacesEnabled = shouldEnableWorkspaces();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();
  const { theme } = useDesignSystemTheme();

  const workspaceFromPath = extractWorkspaceFromPathname(location.pathname);
  const currentWorkspace = workspaceFromPath ?? DEFAULT_WORKSPACE_NAME;

  useEffect(() => {
    if (!workspacesEnabled) {
      setWorkspaces([]);
      setLoadFailed(false);
      return;
    }

    let isMounted = true;
    const loadWorkspaces = async () => {
      setLoading(true);
      setLoadFailed(false);
      try {
        const response = await fetchAPI(getAjaxUrl(WORKSPACES_ENDPOINT));
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
      } catch {
        if (isMounted) {
          setLoadFailed(true);
        }
      } finally {
        if (isMounted) {
          setLoading(false);
        }
      }
    };

    loadWorkspaces().catch(() => undefined);

    return () => {
      isMounted = false;
    };
  }, [workspacesEnabled]);

  const options = useMemo(() => {
    const deduped = new Map<string, Workspace>();

    for (const workspace of workspaces) {
      deduped.set(workspace.name, workspace);
    }

    if (deduped.size === 0) {
      deduped.set(DEFAULT_WORKSPACE_NAME, { name: DEFAULT_WORKSPACE_NAME, description: null });
    }

    if (currentWorkspace && !deduped.has(currentWorkspace)) {
      deduped.set(currentWorkspace, { name: currentWorkspace, description: null });
    }

    return Array.from(deduped.values());
  }, [workspaces, currentWorkspace]);

  const handleWorkspaceChange = (nextWorkspace?: string) => {
    if (!nextWorkspace || nextWorkspace === currentWorkspace) {
      return;
    }

    const encodedWorkspace = encodeURIComponent(nextWorkspace);
    setActiveWorkspace(nextWorkspace);
    navigate(`/workspaces/${encodedWorkspace}`);
  };

  if (!workspacesEnabled) {
    return null;
  }

  return (
    <LegacySelect
      value={currentWorkspace}
      placeholder="Select workspace"
      onChange={handleWorkspaceChange}
      loading={loading}
      css={{ minWidth: theme.spacing.lg * 5, maxWidth: theme.spacing.lg * 7 }}
      aria-label="Workspace selector"
    >
      {options.map((workspace) => (
        <LegacySelect.Option key={workspace.name} value={workspace.name}>
          {workspace.name}
        </LegacySelect.Option>
      ))}
    </LegacySelect>
  );
};
