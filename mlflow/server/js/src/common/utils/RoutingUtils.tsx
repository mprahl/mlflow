/**
 * This file is the only one that should directly import from 'react-router-dom' module
 */
/* eslint-disable no-restricted-imports */

/**
 * Import React Router V6 parts
 */
import {
  BrowserRouter,
  MemoryRouter,
  HashRouter,
  matchPath,
  generatePath,
  Navigate,
  Route,
  UNSAFE_NavigationContext,
  NavLink,
  Outlet as OutletDirect,
  Link as LinkDirect,
  useNavigate as useNavigateDirect,
  useLocation as useLocationDirect,
  useParams as useParamsDirect,
  useSearchParams as useSearchParamsDirect,
  createHashRouter,
  RouterProvider,
  Routes,
  type To,
  type NavigateOptions,
  type Location,
  type NavigateFunction,
  type Params,
} from 'react-router-dom';

/**
 * Import React Router V5 parts
 */
import { HashRouter as HashRouterV5, Link as LinkV5, NavLink as NavLinkV5 } from 'react-router-dom';
import type { ComponentProps } from 'react';
import React from 'react';

const useLocation = useLocationDirect;

const useSearchParams = useSearchParamsDirect;

const useParams = useParamsDirect;

const getCurrentNs = (): string | null => {
  try {
    // Prefer hash query since we use HashRouter
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
  return null;
};

const augmentToWithNs = (to: To): To => {
  const ns = getCurrentNs();
  if (!ns) return to;
  if (typeof to === 'string') {
    // String path possibly with query
    const hasQuery = to.includes('?');
    const [pathOnly, query = ''] = to.split('?');
    const qs = new URLSearchParams(query);
    qs.set('ns', ns);
    return `${pathOnly}?${qs.toString()}`;
  }
  // Object form
  const searchStr = typeof to.search === 'string' ? to.search : '';
  const qs2 = new URLSearchParams(searchStr.startsWith('?') ? searchStr.substring(1) : searchStr);
  qs2.set('ns', ns);
  return { ...to, search: `?${qs2.toString()}` };
};

const useNavigate = () => {
  const navigateDirect = useNavigateDirect();
  return (to: To, options?: NavigateOptions) => navigateDirect(augmentToWithNs(to), options);
};

const Outlet = OutletDirect;

const BaseLink = LinkDirect;

const Link = React.forwardRef<HTMLAnchorElement, ComponentProps<typeof BaseLink>>((props, ref) => {
  const { to, ...rest } = props as any;
  const toWithNs = augmentToWithNs(to as To);
  return <BaseLink ref={ref} to={toWithNs as any} {...(rest as any)} />;
});
Link.displayName = 'Link';

export const createMLflowRoutePath = (routePath: string) => {
  return routePath;
};

export {
  // React Router V6 API exports
  BrowserRouter,
  MemoryRouter,
  HashRouter,
  Link,
  useNavigate,
  useLocation,
  useParams,
  useSearchParams,
  generatePath,
  matchPath,
  Navigate,
  Route,
  Routes,
  Outlet,

  // Exports used to build hash-based data router
  createHashRouter,
  RouterProvider,

  // Unsafe navigation context, will be improved after full migration to react-router v6
  UNSAFE_NavigationContext,
};

export const createLazyRouteElement = (
  // Load the module's default export and turn it into React Element
  componentLoader: () => Promise<{ default: React.ComponentType<React.PropsWithChildren<any>> }>,
) => React.createElement(React.lazy(componentLoader));
export const createRouteElement = (component: React.ComponentType<React.PropsWithChildren<any>>) =>
  React.createElement(component);

export type { Location, NavigateFunction, Params, To, NavigateOptions };
