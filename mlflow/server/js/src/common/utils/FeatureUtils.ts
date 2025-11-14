/**
 * This file aggregates utility functions for enabling features configured by flags.
 * In the OSS version, you can override them in local development by manually changing the return values.
 */

let workspacesEnabled: boolean | null = null;

// Dynamic workspace detection
export const shouldEnableWorkspaces = (): boolean => {
  return workspacesEnabled ?? false;
};

export const setWorkspacesEnabled = (enabled: boolean) => {
  workspacesEnabled = enabled;
};

export const shouldEnableWorkspacePermissions = () => shouldEnableWorkspaces();

export const shouldEnableRunDetailsPageAutoRefresh = () => true;

export const shouldEnableChartExpressions = () => false;

export const shouldEnableRelativeTimeDateAxis = () => false;

export const shouldEnableNewDifferenceViewCharts = () => false;

export const shouldEnableDifferenceViewChartsV3 = () => false;

export const shouldEnableMinMaxMetricsOnExperimentPage = () => false;

export const shouldUseCompressedExperimentViewSharedState = () => true;

export const shouldEnableUnifiedChartDataTraceHighlight = () => true;

export const shouldUseRegexpBasedAutoRunsSearchFilter = () => false;

export const shouldUseRunRowsVisibilityMap = () => true;

export const isUnstableNestedComponentsMigrated = () => true;

export const shouldUsePredefinedErrorsInExperimentTracking = () => true;

export const isLoggedModelsFilteringAndSortingEnabled = () => false;

export const isRunPageLoggedModelsTableEnabled = () => true;

export const shouldEnableGraphQLRunDetailsPage = () => true;

export const shouldEnableGraphQLSampledMetrics = () => false;

export const shouldEnableGraphQLModelVersionsForRunDetails = () => false;

export const shouldRerunExperimentUISeeding = () => false;

export const shouldEnableExperimentKindInference = () => true;

export const shouldEnablePromptsTabOnDBPlatform = () => false;

export const shouldEnablePromptTags = () => false;

export const shouldUseSharedTaggingUI = () => false;

export const shouldDisableReproduceRunButton = () => false;

export const shouldEnablePromptLab = () => true;

export const shouldUnifyLoggedModelsAndRegisteredModels = () => false;

export const shouldUseGetLoggedModelsBatchAPI = () => false;

export const shouldShowModelsNextUI = () => true;

export const shouldEnableTracesV3View = () => true;

export const shouldEnableTraceInsights = () => false;

export const shouldEnableTracesSyncUI = () => false;

export const getEvalTabTotalTracesLimit = () => 1000;

export const isExperimentEvalResultsMonitoringUIEnabled = () => true;

export const shouldUseUnifiedArtifactBrowserForLoggedModels = () => false;

export const shouldUseUnifiedArtifactBrowserForRunDetailsPage = () => false;

export const shouldEnableRunDetailsMetadataBoxOnRunDetailsPage = () => false;

export const shouldEnableArtifactsOnRunDetailsPage = () => false;

export const shouldDisableAssessmentsPaneOnFetchFailure = () => false;

export const shouldEnableExperimentPageSideTabs = () => true;

export const shouldEnableChatSessionsTab = () => true;
