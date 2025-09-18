import ExperimentTrackingRoutes from '../../experiment-tracking/routes';
import { Link } from '../utils/RoutingUtils';
import { HomePageDocsUrl, Version } from '../constants';
import { DarkThemeSwitch } from '@mlflow/mlflow/src/common/components/DarkThemeSwitch';
import { Button, Menu, SearchIcon, Dropdown, MenuIcon, useDesignSystemTheme, Input } from '@databricks/design-system';
import { MlflowLogo } from './MlflowLogo';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { getJson } from '../../common/utils/FetchUtils';

const getNsFromLocation = (): string => {
  try {
    const hash = typeof window !== 'undefined' ? window.location.hash || '' : '';
    const qIndex = hash.indexOf('?');
    if (qIndex >= 0) {
      const qs = new URLSearchParams(hash.substring(qIndex + 1));
      const ns = qs.get('ns');
      if (ns) return ns;
    }
    const search = typeof window !== 'undefined' ? window.location.search || '' : '';
    if (search) {
      const qs2 = new URLSearchParams(search.startsWith('?') ? search.substring(1) : search);
      const ns2 = qs2.get('ns');
      if (ns2) return ns2;
    }
  } catch {}
  return '';
};

const setNsInLocation = (ns: string) => {
  if (typeof window === 'undefined') return;
  const loc = window.location;
  const hash = loc.hash || '#/';
  const [path, query = ''] = hash.replace(/^#/, '').split('?');
  const qs = new URLSearchParams(query);
  qs.set('ns', ns);
  const newHash = `${path}?${qs.toString()}`;
  const basePath = loc.pathname;
  const url = basePath.endsWith('/') ? `${basePath}#${newHash}` : `${basePath}/#${newHash}`;
  // Use assign to create a history entry, then force reload to ensure data refetch under new ns
  window.location.assign(url);
  window.location.reload();
};

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
  const [namespaces, setNamespaces] = useState<string[]>([]);
  const [filter, setFilter] = useState('');
  const [selectedNs, setSelectedNs] = useState('');
  const lastNsRef = useRef<string>('');

  useEffect(() => {
    const initNs = getNsFromLocation();
    setSelectedNs(initNs);
    lastNsRef.current = initNs || lastNsRef.current;
    const onHashChange = () => setSelectedNs(getNsFromLocation());
    if (typeof window !== 'undefined') {
      window.addEventListener('hashchange', onHashChange);
    }
    return () => {
      if (typeof window !== 'undefined') {
        window.removeEventListener('hashchange', onHashChange);
      }
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    getJson({ relativeUrl: '/ajax-api/2.0/mlflow/namespaces' })
      .then((res: any) => (res.json ? res.json() : res))
      .then((data: any) => {
        if (cancelled) return;
        const list: string[] = (data?.namespaces as string[]) || [];
        const sorted = [...list].sort((a, b) => a.localeCompare(b));
        setNamespaces(sorted);
        const currentNs = getNsFromLocation();
        if (sorted.length > 0 && !currentNs) {
          // Default to first namespace and update URL
          setNsInLocation(sorted[0]);
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  // If ns disappears from URL while app is open, re-append the last known (or first available)
  useEffect(() => {
    const currentNs = getNsFromLocation();
    if (!currentNs) {
      const fallback = lastNsRef.current || namespaces[0] || '';
      if (fallback) {
        setNsInLocation(fallback);
      }
    } else {
      lastNsRef.current = currentNs;
    }
  }, [selectedNs, namespaces]);

  const filteredNamespaces = useMemo(() => {
    const f = (filter || '').toLowerCase();
    return namespaces.filter((n) => n.toLowerCase().includes(f));
  }, [namespaces, filter]);

  const onSelectNamespace = (ns: string) => {
    setSelectedNs(ns);
    setNsInLocation(ns);
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
        <Dropdown
          overlay={
            <Menu css={{ width: 280 }} data-testid="namespace-switcher-menu">
              <div css={{ padding: theme.spacing.sm }}>
                <Input
                  componentId="mlflow.namespace_switcher.filter"
                  placeholder="Filter namespaces"
                  value={filter}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setFilter(e.target.value)}
                  prefix={<SearchIcon />}
                />
              </div>
              <div css={{ maxHeight: 300, overflowY: 'auto' }}>
                {filteredNamespaces.map((ns) => (
                  <Menu.Item key={ns} onClick={() => onSelectNamespace(ns)}>
                    {ns}
                  </Menu.Item>
                ))}
                {filteredNamespaces.length === 0 && (
                  <>
                    {filter ? (
                      <Menu.Item key="use-filter" onClick={() => onSelectNamespace(filter)}>
                        Use namespace "{filter}"
                      </Menu.Item>
                    ) : (
                      <div css={{ padding: theme.spacing.sm, color: theme.colors.textSecondary }}>No namespaces</div>
                    )}
                  </>
                )}
              </div>
            </Menu>
          }
          trigger={['click']}
        >
          <Button type="tertiary" componentId="mlflow.namespace_switcher.button">
            {selectedNs || 'Select namespace'}
          </Button>
        </Dropdown>
        <DarkThemeSwitch isDarkTheme={isDarkTheme} setIsDarkTheme={setIsDarkTheme} />
        <a href="https://github.com/mlflow/mlflow">GitHub</a>
        <a href={HomePageDocsUrl}>Docs</a>
        <a href="/oauth/sign_in">Logout</a>
      </div>
    </header>
  );
};
