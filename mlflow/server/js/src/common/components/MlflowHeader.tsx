import ExperimentTrackingRoutes from '../../experiment-tracking/routes';
import { Link } from '../utils/RoutingUtils';
import { HomePageDocsUrl, Version } from '../constants';
import { DarkThemeSwitch } from '@mlflow/mlflow/src/common/components/DarkThemeSwitch';
import { Button, MenuIcon, DropdownMenu, ChevronDownIcon, useDesignSystemTheme } from '@databricks/design-system';
import { MlflowLogo } from './MlflowLogo';
import React, { useEffect, useState } from 'react';

export const MlflowHeader = ({
  isDarkTheme = false,
  setIsDarkTheme = (val: boolean) => {},
  sidebarOpen,
  toggleSidebar,
}: {
  isDarkTheme?: boolean;
  setIsDarkTheme?: (isDarkTheme: boolean) => void;
  sidebarOpen: boolean;
  toggleSidebar: () => void;
}) => {
  const { theme } = useDesignSystemTheme();
  const [namespaces, setNamespaces] = useState<Record<string, string>>({});
  const [selectedNamespace, setSelectedNamespace] = useState<string>('');

  const getNamespaceFromPath = () => {
    try {
      const seg = (window.location.pathname || '').split('/').filter(Boolean)[0] || '';
      return seg;
    } catch {
      return '';
    }
  };

  const syncPathWithNamespace = (name: string, doReload: boolean) => {
    const current = getNamespaceFromPath();
    const target = `/${name}/${window.location.hash || ''}`;
    if (current !== name) {
      if (doReload) {
        window.location.href = target;
      } else {
        window.history.replaceState(null, '', target);
      }
    }
  };

  useEffect(() => {
    const defaultNamespaces: Record<string, string> = {
      namespace1: 'https://mlflow-mlflow.apps.dsp-nonfips-pool-wlggc.gcp.rh-ods.com/',
      namespace2: 'https://mlflow2-mlflow.apps.dsp-nonfips-pool-wlggc.gcp.rh-ods.com/',
    };

    // Determine initial selection from storage/cookies eagerly (before fetch)
    const initialSelection = (() => {
      const nsFromPath = getNamespaceFromPath();
      if (nsFromPath) return nsFromPath;
      try {
        const ls = localStorage.getItem('mlflow-namespace');
        if (ls) return ls;
      } catch {}
      return '';
    })();
    if (initialSelection) {
      setSelectedNamespace(initialSelection);
    }

    fetch('/namespaces.json')
      .then((r) => (r.ok ? r.json() : defaultNamespaces))
      .then((data: Record<string, string>) => {
        const ns = data && Object.keys(data).length ? data : defaultNamespaces;
        setNamespaces(ns);
        const savedNs = initialSelection;
        const first = Object.keys(ns)[0];

        if (savedNs && ns[savedNs]) {
          // Align cookies with mapping without reload and sync path
          chooseNamespace(savedNs, ns[savedNs], false);
          syncPathWithNamespace(savedNs, false);
        } else if (first) {
          // Initialize cookie and path without causing a reload-loop
          chooseNamespace(first, ns[first], false);
          syncPathWithNamespace(first, false);
        }
      })
      .catch(() => {
        // If fetch fails, fall back to defaults
        const ns = defaultNamespaces;
        setNamespaces(ns);
        const savedNs = initialSelection;
        const first = Object.keys(ns)[0];
        if (savedNs && ns[savedNs]) {
          chooseNamespace(savedNs, ns[savedNs], false);
          syncPathWithNamespace(savedNs, false);
        } else if (first) {
          // Initialize cookie and path without causing a reload-loop
          chooseNamespace(first, ns[first], false);
          syncPathWithNamespace(first, false);
        }
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const chooseNamespace = (name: string, url: string, shouldReload: boolean = true) => {
    const wasSelected = selectedNamespace;
    setSelectedNamespace(name);
    try {
      localStorage.setItem('mlflow-namespace', name);
    } catch {}
    // Update URL to include namespace segment and reload only if user actually changed it
    if (shouldReload && wasSelected !== name) {
      const target = `/${name}/#/experiments`;
      window.location.href = target;
    }
  };
  return (
    <header
      css={{
        backgroundColor: theme.colors.backgroundSecondary,
        color: theme.colors.textSecondary,
        display: 'flex',
        paddingLeft: theme.spacing.sm,
        paddingRight: theme.spacing.md,
        paddingTop: theme.spacing.sm + theme.spacing.xs,
        paddingBottom: theme.spacing.xs,
        a: {
          color: theme.colors.textSecondary,
        },
        alignItems: 'center',
      }}
    >
      <div
        css={{
          display: 'flex',
          alignItems: 'center',
        }}
      >
        <Button
          type="tertiary"
          componentId="mlflow_header.toggle_sidebar_button"
          onClick={toggleSidebar}
          aria-label="Toggle sidebar"
          aria-pressed={sidebarOpen}
          icon={<MenuIcon />}
        />
        <Link to={ExperimentTrackingRoutes.rootRoute}>
          <MlflowLogo
            css={{
              display: 'block',
              height: theme.spacing.md * 2,
              color: theme.colors.textPrimary,
            }}
          />
        </Link>
        <span
          css={{
            fontSize: theme.typography.fontSizeSm,
          }}
        >
          {Version}
        </span>
      </div>
      <div css={{ flex: 1 }} />
      <div css={{ display: 'flex', gap: theme.spacing.lg, alignItems: 'center' }}>
        <DropdownMenu.Root>
          <DropdownMenu.Trigger asChild>
            <Button componentId="mlflow_header.namespaces_dropdown" endIcon={<ChevronDownIcon />}>
              Namespaces: {selectedNamespace || 'Select'}
            </Button>
          </DropdownMenu.Trigger>
          <DropdownMenu.Content>
            <DropdownMenu.RadioGroup
              componentId="mlflow_header.namespaces_group"
              value={selectedNamespace}
              onValueChange={(value) => chooseNamespace(value, namespaces[value])}
            >
              {Object.keys(namespaces).map((name) => (
                <DropdownMenu.RadioItem key={name} value={name}>
                  {name}
                </DropdownMenu.RadioItem>
              ))}
            </DropdownMenu.RadioGroup>
          </DropdownMenu.Content>
        </DropdownMenu.Root>
        <DarkThemeSwitch isDarkTheme={isDarkTheme} setIsDarkTheme={setIsDarkTheme} />
        <a href="https://github.com/mlflow/mlflow">GitHub</a>
        <a href={HomePageDocsUrl}>Docs</a>
      </div>
    </header>
  );
};
