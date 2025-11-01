import {
  Button,
  ColumnsIcon,
  DropdownMenu,
  Icon,
  type IconProps,
  Typography,
  useDesignSystemTheme,
} from '@databricks/design-system';
import type { SVGProps } from 'react';
import { ColumnDef } from '@tanstack/react-table';
import { FormattedMessage } from 'react-intl';
import { EvaluationDataset, EvaluationDatasetRecord } from '../types';
import { parseJSONSafe } from '@mlflow/mlflow/src/common/utils/TagUtils';

const RowHeightSvg = (props: SVGProps<SVGSVGElement>) => (
  <svg xmlns="http://www.w3.org/2000/svg" width="1em" height="1em" fill="none" viewBox="0 0 16 16" {...props}>
    <path
      fill="currentColor"
      fillRule="evenodd"
      d="M14.25 1a.75.75 0 0 1 .75.75v12.5a.75.75 0 0 1-.75.75H1.75a.75.75 0 0 1-.75-.75V1.75A.75.75 0 0 1 1.75 1zM2.5 2.5h11V5h-11zm0 4v3h11v-3zm11 4.5h-11v2.5h11z"
      clipRule="evenodd"
    />
  </svg>
);

const RowHeightIcon = (props: IconProps) => <Icon {...props} component={RowHeightSvg} />;

const getTotalRecordsCount = (profile: string | undefined): number | undefined => {
  if (!profile) {
    return undefined;
  }

  const profileJson = parseJSONSafe(profile);
  return profileJson?.num_records ?? undefined;
};

export const ExperimentEvaluationDatasetRecordsToolbar = ({
  dataset,
  datasetRecords,
  columns,
  columnVisibility,
  setColumnVisibility,
  rowSize,
  setRowSize,
}: {
  dataset: EvaluationDataset;
  datasetRecords: EvaluationDatasetRecord[];
  columns: ColumnDef<EvaluationDatasetRecord, any>[];
  columnVisibility: Record<string, boolean>;
  setColumnVisibility: (columnVisibility: Record<string, boolean>) => void;
  rowSize: 'sm' | 'md' | 'lg';
  setRowSize: (rowSize: 'sm' | 'md' | 'lg') => void;
}) => {
  const { theme } = useDesignSystemTheme();
  const datasetName = dataset?.name;
  const profile = dataset?.profile;
  const totalRecordsCount = getTotalRecordsCount(profile);
  const loadedRecordsCount = datasetRecords.length;

  return (
    <div
      css={{
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'space-between',
        marginBottom: theme.spacing.sm,
      }}
    >
      <div
        css={{
          display: 'flex',
          flexDirection: 'column',
          paddingLeft: theme.spacing.sm,
          paddingRight: theme.spacing.sm,
        }}
      >
        <Typography.Title level={3} withoutMargins>
          {datasetName}
        </Typography.Title>
        <Typography.Text color="secondary" size="sm">
          <FormattedMessage
            defaultMessage="Displaying {loadedRecordsCount} of {totalRecordsCount, plural, =1 {1 record} other {# records}}"
            description="Label for the number of records displayed"
            values={{ loadedRecordsCount: loadedRecordsCount ?? 0, totalRecordsCount: totalRecordsCount ?? 0 }}
          />
        </Typography.Text>
      </div>
      <div css={{ display: 'flex', alignItems: 'flex-start' }}>
        <DropdownMenu.Root>
          <DropdownMenu.Trigger asChild>
            <Button componentId="mlflow.eval-datasets.records-toolbar.row-size-toggle" icon={<RowHeightIcon />} />
          </DropdownMenu.Trigger>
          <DropdownMenu.Content align="end">
            <DropdownMenu.RadioGroup
              componentId="mlflow.eval-datasets.records-toolbar.row-size-radio"
              value={rowSize}
              onValueChange={(value) => setRowSize(value as 'sm' | 'md' | 'lg')}
            >
              <DropdownMenu.Label>
                <Typography.Text color="secondary">
                  <FormattedMessage defaultMessage="Row height" description="Label for the row height radio group" />
                </Typography.Text>
              </DropdownMenu.Label>
              <DropdownMenu.RadioItem key="sm" value="sm">
                <DropdownMenu.ItemIndicator />
                <Typography.Text>
                  <FormattedMessage defaultMessage="Small" description="Small row size" />
                </Typography.Text>
              </DropdownMenu.RadioItem>
              <DropdownMenu.RadioItem key="md" value="md">
                <DropdownMenu.ItemIndicator />
                <Typography.Text>
                  <FormattedMessage defaultMessage="Medium" description="Medium row size" />
                </Typography.Text>
              </DropdownMenu.RadioItem>
              <DropdownMenu.RadioItem key="lg" value="lg">
                <DropdownMenu.ItemIndicator />
                <Typography.Text>
                  <FormattedMessage defaultMessage="Large" description="Large row size" />
                </Typography.Text>
              </DropdownMenu.RadioItem>
            </DropdownMenu.RadioGroup>
          </DropdownMenu.Content>
        </DropdownMenu.Root>
        <DropdownMenu.Root>
          <DropdownMenu.Trigger asChild>
            <Button componentId="mlflow.eval-datasets.records-toolbar.columns-toggle" icon={<ColumnsIcon />} />
          </DropdownMenu.Trigger>
          <DropdownMenu.Content>
            {columns.map((column) => (
              <DropdownMenu.CheckboxItem
                componentId="YOUR_TRACKING_ID"
                key={column.id}
                checked={columnVisibility[column.id ?? ''] ?? false}
                onCheckedChange={(checked) =>
                  setColumnVisibility({
                    ...columnVisibility,
                    [column.id ?? '']: checked,
                  })
                }
              >
                <DropdownMenu.ItemIndicator />
                <Typography.Text>{column.header}</Typography.Text>
              </DropdownMenu.CheckboxItem>
            ))}
          </DropdownMenu.Content>
        </DropdownMenu.Root>
      </div>
    </div>
  );
};
