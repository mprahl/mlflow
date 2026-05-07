/* eslint-disable @databricks/no-mock-location*/
import { describe, jest, beforeEach, test, expect, afterEach } from '@jest/globals';
import '@testing-library/jest-dom';
import userEvent from '@testing-library/user-event';
import { WorkspacesHomeView } from './WorkspacesHomeView';
import { useWorkspaces } from '../../workspaces/hooks/useWorkspaces';
import { getLastUsedWorkspace } from '../../workspaces/utils/WorkspaceUtils';
import { useUpdateWorkspace } from '../../workspaces/hooks/useUpdateWorkspace';
import { renderWithIntl, screen, waitFor } from '@mlflow/mlflow/src/common/utils/TestUtils.react18';
import { MemoryRouter } from '../../common/utils/RoutingUtils';
import { QueryClient, QueryClientProvider } from '@mlflow/mlflow/src/common/utils/reactQueryHooks';

jest.mock('../../workspaces/hooks/useWorkspaces');
jest.mock('../../workspaces/hooks/useUpdateWorkspace');
jest.mock('../../workspaces/utils/WorkspaceUtils', () => {
  const actualWorkspaceUtils = jest.requireActual<typeof import('../../workspaces/utils/WorkspaceUtils')>(
    '../../workspaces/utils/WorkspaceUtils',
  );
  return {
    ...actualWorkspaceUtils,
    getLastUsedWorkspace: jest.fn(),
    setLastUsedWorkspace: jest.fn(),
  };
});

const reloadMock = jest.fn();
const mockUpdateWorkspace = jest.fn();

describe('WorkspacesHomeView', () => {
  const mockOnCreateWorkspace = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
    jest.mocked(getLastUsedWorkspace).mockReturnValue('ml-research');
    Object.defineProperty(window, 'location', {
      value: { ...window.location, hash: '', reload: reloadMock },
      writable: true,
    });
    jest.mocked(useUpdateWorkspace).mockReturnValue({
      mutate: mockUpdateWorkspace,
      isLoading: false,
    } as any);
  });

  afterEach(() => {
    reloadMock.mockClear();
  });

  const renderComponent = () => {
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    return renderWithIntl(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <WorkspacesHomeView onCreateWorkspace={mockOnCreateWorkspace} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  };

  test('renders loading state', () => {
    jest.mocked(useWorkspaces).mockReturnValue({
      workspaces: [],
      isLoading: true,
      isError: false,
      refetch: jest.fn() as (options: any) => Promise<any>,
    });

    renderComponent();
    expect(screen.getByText('Loading workspaces...')).toBeInTheDocument();
  });

  test('renders empty state when no workspaces', () => {
    jest.mocked(useWorkspaces).mockReturnValue({
      workspaces: [],
      isLoading: false,
      isError: false,
      refetch: jest.fn() as (options: any) => Promise<any>,
    });

    renderComponent();
    expect(screen.getByText('Create your first workspace')).toBeInTheDocument();
    expect(
      screen.getByText('Create a workspace to organize and logically isolate your experiments and models.'),
    ).toBeInTheDocument();
  });

  test('calls onCreateWorkspace when create button clicked in empty state', async () => {
    jest.mocked(useWorkspaces).mockReturnValue({
      workspaces: [],
      isLoading: false,
      isError: false,
      refetch: jest.fn() as (options: any) => Promise<any>,
    });

    renderComponent();
    await userEvent.click(screen.getByText('Create workspace'));
    expect(mockOnCreateWorkspace).toHaveBeenCalledTimes(1);
  });

  test('renders workspace list with Last used badge', () => {
    jest.mocked(useWorkspaces).mockReturnValue({
      workspaces: [
        { name: 'ml-research', description: 'Research experiments for new ML models' },
        { name: 'production-models', description: 'Production-ready models' },
        { name: 'data-science-team', description: null },
      ],
      isLoading: false,
      isError: false,
      refetch: jest.fn() as (options: any) => Promise<any>,
    });

    renderComponent();

    expect(screen.getByText('ml-research')).toBeInTheDocument();
    expect(screen.getByText('Research experiments for new ML models')).toBeInTheDocument();
    expect(screen.getByText('production-models')).toBeInTheDocument();
    expect(screen.getByText('Production-ready models')).toBeInTheDocument();
    expect(screen.getByText('data-science-team')).toBeInTheDocument();
    expect(screen.getByText('Last used')).toBeInTheDocument();
  });

  test('navigates to workspace when row clicked', async () => {
    jest.mocked(useWorkspaces).mockReturnValue({
      workspaces: [{ name: 'ml-research', description: 'Research experiments' }],
      isLoading: false,
      isError: false,
      refetch: jest.fn() as (options: any) => Promise<any>,
    });

    renderComponent();

    await userEvent.click(screen.getByText('ml-research'));

    expect(window.location.hash).toBe('#/?workspace=ml-research');
    expect(window.location.reload).toHaveBeenCalled();
  });

  test('encodes workspace name in URL', async () => {
    jest.mocked(useWorkspaces).mockReturnValue({
      workspaces: [{ name: 'team-a/special', description: 'Special workspace' }],
      isLoading: false,
      isError: false,
      refetch: jest.fn() as (options: any) => Promise<any>,
    });

    renderComponent();

    await userEvent.click(screen.getByText('team-a/special'));

    expect(window.location.hash).toBe('#/?workspace=team-a%2Fspecial');
    expect(window.location.reload).toHaveBeenCalled();
  });

  test('shows create new workspace button when workspaces exist', () => {
    jest.mocked(useWorkspaces).mockReturnValue({
      workspaces: [{ name: 'ml-research', description: 'Research experiments' }],
      isLoading: false,
      isError: false,
      refetch: jest.fn() as (options: any) => Promise<any>,
    });

    renderComponent();
    expect(screen.getByText('Create new workspace')).toBeInTheDocument();
  });

  test('opens edit modal with workspace fields', async () => {
    jest.mocked(useWorkspaces).mockReturnValue({
      workspaces: [
        {
          name: 'ml-research',
          description: 'Research experiments',
          default_artifact_root: 's3://artifacts/ml-research',
          trace_archival_config: { location: 's3://archive/ml-research', retention: '30d' },
        },
      ],
      isLoading: false,
      isError: false,
      refetch: jest.fn() as any,
    });

    renderComponent();
    await userEvent.click(screen.getByRole('button', { name: 'Edit workspace' }));

    expect(screen.getByText('Edit Workspace')).toBeInTheDocument();
    expect(screen.getByDisplayValue('Research experiments')).toBeInTheDocument();
    expect(screen.getByDisplayValue('s3://artifacts/ml-research')).toBeInTheDocument();
    expect(screen.getByDisplayValue('s3://archive/ml-research')).toBeInTheDocument();
    expect(screen.getByDisplayValue('30d')).toBeInTheDocument();
    expect(screen.getByText('Clear any optional field and save to remove the workspace override.')).toBeInTheDocument();
  });

  test('saves updated fields from the edit modal', async () => {
    mockUpdateWorkspace.mockImplementation((_variables, options: any) => {
      options?.onSuccess?.({} as any, undefined as any, undefined as any);
    });
    jest.mocked(useWorkspaces).mockReturnValue({
      workspaces: [
        {
          name: 'ml-research',
          description: 'Research experiments',
          default_artifact_root: 's3://artifacts/ml-research',
        },
      ],
      isLoading: false,
      isError: false,
      refetch: jest.fn() as any,
    });

    renderComponent();
    await userEvent.click(screen.getByRole('button', { name: 'Edit workspace' }));
    await userEvent.clear(screen.getByDisplayValue('Research experiments'));
    await userEvent.type(screen.getByPlaceholderText('Enter workspace description'), 'Updated description');
    await userEvent.clear(screen.getByPlaceholderText('Enter default artifact root URI'));
    await userEvent.type(screen.getByPlaceholderText('Enter default artifact root URI'), 's3://artifacts/new-team');

    await userEvent.click(screen.getByText('Save'));

    await waitFor(() => {
      expect(mockUpdateWorkspace).toHaveBeenCalledWith(
        {
          name: 'ml-research',
          description: 'Updated description',
          default_artifact_root: 's3://artifacts/new-team',
        },
        expect.objectContaining({
          onSuccess: expect.any(Function),
          onError: expect.any(Function),
        }),
      );
    });
  });

  test('saves updated archival fields from the edit modal', async () => {
    mockUpdateWorkspace.mockImplementation((_variables, options: any) => {
      options?.onSuccess?.({} as any, undefined as any, undefined as any);
    });
    jest.mocked(useWorkspaces).mockReturnValue({
      workspaces: [
        {
          name: 'ml-research',
          description: 'Research experiments',
          default_artifact_root: 's3://artifacts/ml-research',
          trace_archival_config: { location: 's3://archive/ml-research', retention: '30d' },
        },
      ],
      isLoading: false,
      isError: false,
      refetch: jest.fn() as any,
    });

    renderComponent();
    await userEvent.click(screen.getByRole('button', { name: 'Edit workspace' }));
    await userEvent.clear(screen.getByDisplayValue('s3://archive/ml-research'));
    await userEvent.type(screen.getByPlaceholderText('Enter trace archival location URI'), 's3://archive/new-team');
    await userEvent.clear(screen.getByDisplayValue('30d'));
    await userEvent.type(screen.getByPlaceholderText('Enter trace archival retention (for example 30d)'), '14d');

    await userEvent.click(screen.getByText('Save'));

    await waitFor(() => {
      expect(mockUpdateWorkspace).toHaveBeenCalledWith(
        {
          name: 'ml-research',
          trace_archival_config: { location: 's3://archive/new-team', retention: '14d' },
        },
        expect.objectContaining({
          onSuccess: expect.any(Function),
          onError: expect.any(Function),
        }),
      );
    });
  });

  test('clears archival overrides from the edit modal', async () => {
    mockUpdateWorkspace.mockImplementation((_variables, options: any) => {
      options?.onSuccess?.({} as any, undefined as any, undefined as any);
    });
    jest.mocked(useWorkspaces).mockReturnValue({
      workspaces: [
        {
          name: 'ml-research',
          description: 'Research experiments',
          default_artifact_root: 's3://artifacts/ml-research',
          trace_archival_config: { location: 's3://archive/ml-research', retention: '30d' },
        },
      ],
      isLoading: false,
      isError: false,
      refetch: jest.fn() as any,
    });

    renderComponent();
    await userEvent.click(screen.getByRole('button', { name: 'Edit workspace' }));
    await userEvent.clear(screen.getByDisplayValue('s3://archive/ml-research'));
    await userEvent.clear(screen.getByDisplayValue('30d'));

    await userEvent.click(screen.getByText('Save'));

    await waitFor(() => {
      expect(mockUpdateWorkspace).toHaveBeenCalledWith(
        {
          name: 'ml-research',
          trace_archival_config: { location: '', retention: '' },
        },
        expect.objectContaining({
          onSuccess: expect.any(Function),
          onError: expect.any(Function),
        }),
      );
    });
  });

  test('does not save archival overrides when only whitespace changes', async () => {
    jest.mocked(useWorkspaces).mockReturnValue({
      workspaces: [
        {
          name: 'ml-research',
          description: 'Research experiments',
          default_artifact_root: 's3://artifacts/ml-research',
          trace_archival_config: { location: 's3://archive/ml-research', retention: '30d' },
        },
      ],
      isLoading: false,
      isError: false,
      refetch: jest.fn() as any,
    });

    renderComponent();
    await userEvent.click(screen.getByRole('button', { name: 'Edit workspace' }));
    await userEvent.type(screen.getByDisplayValue('s3://archive/ml-research'), ' ');
    await userEvent.type(screen.getByDisplayValue('30d'), ' ');

    await userEvent.click(screen.getByText('Save'));

    expect(mockUpdateWorkspace).not.toHaveBeenCalled();
  });

  test('shows an inline error when saving the edit modal fails', async () => {
    mockUpdateWorkspace.mockImplementation((_variables, options: any) => {
      options?.onError?.(new Error('Save failed'));
    });
    jest.mocked(useWorkspaces).mockReturnValue({
      workspaces: [{ name: 'ml-research', description: 'Research experiments' }],
      isLoading: false,
      isError: false,
      refetch: jest.fn() as any,
    });

    renderComponent();
    await userEvent.click(screen.getByRole('button', { name: 'Edit workspace' }));
    await userEvent.clear(screen.getByDisplayValue('Research experiments'));
    await userEvent.type(screen.getByPlaceholderText('Enter workspace description'), 'Updated description');
    await userEvent.click(screen.getByText('Save'));

    expect(await screen.findByText('Save failed')).toBeInTheDocument();
  });

  test('shows validation error for invalid trace archival retention in edit modal', async () => {
    jest.mocked(useWorkspaces).mockReturnValue({
      workspaces: [{ name: 'ml-research', description: 'Research experiments' }],
      isLoading: false,
      isError: false,
      refetch: jest.fn() as any,
    });

    renderComponent();
    await userEvent.click(screen.getByRole('button', { name: 'Edit workspace' }));
    await userEvent.type(screen.getByPlaceholderText('Enter trace archival retention (for example 30d)'), '30days');
    await userEvent.click(screen.getByText('Save'));

    expect(
      await screen.findByText(
        "Trace archival retention must use the format <int><unit>, where unit is one of 'm', 'h', or 'd'.",
      ),
    ).toBeInTheDocument();
    expect(mockUpdateWorkspace).not.toHaveBeenCalled();
  });

  test('renders error state', () => {
    const mockRefetch = jest.fn() as (options: any) => Promise<any>;
    jest.mocked(useWorkspaces).mockReturnValue({
      workspaces: [],
      isLoading: false,
      isError: true,
      refetch: mockRefetch as any,
    });

    renderComponent();
    expect(screen.getByText("We couldn't load your workspaces.")).toBeInTheDocument();
    expect(screen.getByText('Retry')).toBeInTheDocument();
  });

  test('calls refetch when retry button clicked', async () => {
    const mockRefetch = jest.fn() as (options: any) => Promise<any>;
    jest.mocked(useWorkspaces).mockReturnValue({
      workspaces: [],
      isLoading: false,
      isError: true,
      refetch: mockRefetch as any,
    });

    renderComponent();
    await userEvent.click(screen.getByText('Retry'));
    expect(mockRefetch).toHaveBeenCalledTimes(1);
  });
});
