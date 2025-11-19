import { afterEach, describe, expect, test } from '@jest/globals';

import { getArtifactLocationUrl, getLoggedModelArtifactLocationUrl } from './ArtifactUtils';
import { setActiveWorkspace } from './WorkspaceUtils';

describe('ArtifactUtils workspace-aware URLs', () => {
  afterEach(() => {
    setActiveWorkspace(null);
  });

  test('getArtifactLocationUrl includes workspace prefix when active', () => {
    setActiveWorkspace('team-a');
    const url = getArtifactLocationUrl('file.txt', 'run-123');

    expect(url).toContain('workspaces/team-a/get-artifact');
    expect(url).toContain('path=file.txt');
    expect(url).toContain('run_uuid=run-123');
  });

  test('getLoggedModelArtifactLocationUrl includes workspace prefix when active', () => {
    setActiveWorkspace('team-b');
    const url = getLoggedModelArtifactLocationUrl('dir/file.txt', '42');

    expect(url).toContain('mlflow/workspaces/team-b/logged-models/42/artifacts/files');
    expect(url).toContain('artifact_file_path=dir%2Ffile.txt');
  });
});

