import { ChartLineIcon, ForkHorizontalIcon, PlusMinusSquareIcon, UserGroupIcon } from '@databricks/design-system';

export const fleetNavItems = [
  { key: 'dashboard', label: 'Dashboard', icon: <ChartLineIcon /> },
  { key: 'traces', label: 'Traces', icon: <ForkHorizontalIcon /> },
  { key: 'evaluation-runs', label: 'Evaluation runs', icon: <PlusMinusSquareIcon /> },
  { key: 'agents', label: 'Agents', icon: <UserGroupIcon /> },
];

export const fleetSectionPath = (fleetId: string, section = 'dashboard') =>
  `/fleets/${encodeURIComponent(fleetId)}/${section}`;
