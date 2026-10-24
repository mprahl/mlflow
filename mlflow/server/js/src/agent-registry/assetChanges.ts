import { agentPath } from './components';
import type { Agent, AgentAsset, AgentEvent, AgentSnapshot } from './mockData';

export type AssetChange = {
  asset: AgentAsset;
  change: 'added' | 'updated' | 'removed';
  previousVersion?: string;
};

export const getSnapshotAssetChanges = (
  agent: Agent,
  snapshot: AgentSnapshot,
  baseline?: AgentSnapshot,
): AssetChange[] => {
  const snapshotIndex = agent.snapshots.findIndex((item) => item.id === snapshot.id);
  const previous = baseline ?? agent.snapshots[snapshotIndex + 1];
  if (!previous) return [];

  const key = (asset: AgentAsset) => `${asset.kind}:${asset.name}`;
  const currentAssets = new Map(
    snapshot.assets.filter((asset) => asset.kind !== 'plugin').map((asset) => [key(asset), asset]),
  );
  const previousAssets = new Map(
    previous.assets.filter((asset) => asset.kind !== 'plugin').map((asset) => [key(asset), asset]),
  );
  const changes: AssetChange[] = [];

  for (const [assetKey, asset] of currentAssets) {
    const old = previousAssets.get(assetKey);
    if (!old) changes.push({ asset, change: 'added' });
    else if (old.version !== asset.version) {
      changes.push({ asset, change: 'updated', previousVersion: old.version });
    }
  }
  for (const [assetKey, asset] of previousAssets) {
    if (!currentAssets.has(assetKey)) changes.push({ asset, change: 'removed' });
  }
  return changes;
};

export const getAssetChangeEvents = (agent: Agent): AgentEvent[] =>
  agent.snapshots.flatMap((snapshot) =>
    getSnapshotAssetChanges(agent, snapshot).map((change) => ({
      id: `asset-${snapshot.id}-${change.asset.kind}-${change.asset.name}`,
      at: snapshot.createdAt,
      title: `${change.asset.name} ${change.change}`,
      detail:
        change.change === 'updated'
          ? `${change.asset.kind} version ${change.previousVersion} → ${change.asset.version}`
          : `${change.asset.kind} version ${change.asset.version}`,
      snapshotId: snapshot.id,
      kind: 'snapshot' as const,
      href: `${agentPath(agent.id, 'snapshots')}?snapshot=${snapshot.id}`,
    })),
  );
