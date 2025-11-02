import { getLoggedModelArtifactLocationUrl } from './ArtifactUtils';
import { setActiveWorkspace } from './WorkspaceUtils';

describe('getLoggedModelArtifactLocationUrl', () => {
  afterEach(() => {
    setActiveWorkspace(null);
  });

  test('returns relative URL without workspace when none is set', () => {
    setActiveWorkspace(null);
    expect(getLoggedModelArtifactLocationUrl('dir/file.txt', 'model-123')).toBe(
      'ajax-api/2.0/mlflow/logged-models/model-123/artifacts/files?artifact_file_path=dir%2Ffile.txt',
    );
  });

  test('includes workspace prefix when workspace is active', () => {
    setActiveWorkspace('my workspace');
    expect(getLoggedModelArtifactLocationUrl('dir/file.txt', 'model-123')).toBe(
      'ajax-api/2.0/mlflow/workspaces/my%20workspace/logged-models/model-123/artifacts/files?artifact_file_path=dir%2Ffile.txt',
    );
  });
});

