import { FormUI, RHFControlledComponents, useDesignSystemTheme } from '@databricks/design-system';
import { type FieldPath, type FieldValues, useFormContext } from 'react-hook-form';
import { FormattedMessage, useIntl } from 'react-intl';
import { validateTraceArchivalRetention } from '../../common/utils/traceArchival';

type WorkspaceSettingsFieldNames<TFieldValues extends FieldValues> = {
  description: FieldPath<TFieldValues>;
  artifactRoot: FieldPath<TFieldValues>;
  traceArchivalLocation: FieldPath<TFieldValues>;
  traceArchivalRetention: FieldPath<TFieldValues>;
};

type WorkspaceSettingsFieldsProps<TFieldValues extends FieldValues> = {
  idPrefix: string;
  componentId: string;
  fieldNames: WorkspaceSettingsFieldNames<TFieldValues>;
  descriptionAutoFocus?: boolean;
  showClearHint?: boolean;
};

export const WorkspaceSettingsFields = <TFieldValues extends FieldValues>({
  idPrefix,
  componentId,
  fieldNames,
  descriptionAutoFocus = false,
  showClearHint = false,
}: WorkspaceSettingsFieldsProps<TFieldValues>) => {
  const { theme } = useDesignSystemTheme();
  const intl = useIntl();
  const { control, getFieldState, formState } = useFormContext<TFieldValues>();
  const retentionFieldState = getFieldState(fieldNames.traceArchivalRetention, formState);

  return (
    <div css={{ display: 'flex', flexDirection: 'column', gap: theme.spacing.md }}>
      {showClearHint && (
        <FormUI.Hint>
          <FormattedMessage
            defaultMessage="Clear any optional field and save to remove the workspace override."
            description="Hint for clearing optional values in edit workspace modal"
          />
        </FormUI.Hint>
      )}
      <div>
        <FormUI.Label htmlFor={`${idPrefix}.description`}>
          <FormattedMessage defaultMessage="Description" description="Label for workspace description field" />
        </FormUI.Label>
        <RHFControlledComponents.Input
          control={control}
          id={`${idPrefix}.description`}
          componentId={`${componentId}.description_input`}
          name={fieldNames.description}
          placeholder={intl.formatMessage({
            defaultMessage: 'Enter workspace description',
            description: 'Placeholder for workspace description input',
          })}
          autoFocus={descriptionAutoFocus}
        />
      </div>
      <div>
        <FormUI.Label htmlFor={`${idPrefix}.artifact_root`}>
          <FormattedMessage
            defaultMessage="Default Artifact Root"
            description="Label for workspace artifact root field"
          />
        </FormUI.Label>
        <RHFControlledComponents.Input
          control={control}
          id={`${idPrefix}.artifact_root`}
          componentId={`${componentId}.artifact_root_input`}
          name={fieldNames.artifactRoot}
          placeholder={intl.formatMessage({
            defaultMessage: 'Enter default artifact root URI',
            description: 'Placeholder for workspace artifact root input',
          })}
        />
      </div>
      <div>
        <FormUI.Label htmlFor={`${idPrefix}.trace_archival_location`}>
          <FormattedMessage
            defaultMessage="Trace Archival Location"
            description="Label for workspace trace archival location field"
          />
        </FormUI.Label>
        <FormUI.Hint>
          <FormattedMessage
            defaultMessage="Optional. Override where archived trace payloads are stored for this workspace. Leave blank to use the server default."
            description="Hint for workspace trace archival location field"
          />
        </FormUI.Hint>
        <RHFControlledComponents.Input
          control={control}
          id={`${idPrefix}.trace_archival_location`}
          componentId={`${componentId}.trace_archival_location_input`}
          name={fieldNames.traceArchivalLocation}
          placeholder={intl.formatMessage({
            defaultMessage: 'Enter trace archival location URI',
            description: 'Placeholder for workspace trace archival location input',
          })}
        />
      </div>
      <div>
        <FormUI.Label htmlFor={`${idPrefix}.trace_archival_retention`}>
          <FormattedMessage
            defaultMessage="Trace Archival Retention"
            description="Label for workspace trace archival retention field"
          />
        </FormUI.Label>
        <FormUI.Hint>
          <FormattedMessage
            defaultMessage="Optional. Override how long traces stay in the tracking store before archival. Use durations like 30d, 12h, or 15m. Leave blank to use the server default."
            description="Hint for workspace trace archival retention field"
          />
        </FormUI.Hint>
        <RHFControlledComponents.Input
          control={control}
          id={`${idPrefix}.trace_archival_retention`}
          componentId={`${componentId}.trace_archival_retention_input`}
          name={fieldNames.traceArchivalRetention}
          rules={{
            validate: (value) => {
              const result = validateTraceArchivalRetention((value as string | undefined) ?? '');
              return result.valid || result.error;
            },
          }}
          placeholder={intl.formatMessage({
            defaultMessage: 'Enter trace archival retention (for example 30d)',
            description: 'Placeholder for workspace trace archival retention input',
          })}
          validationState={retentionFieldState.error ? 'error' : undefined}
        />
        {retentionFieldState.error && <FormUI.Message type="error" message={retentionFieldState.error.message} />}
      </div>
    </div>
  );
};
