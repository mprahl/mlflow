import type { TracesTableColumn } from '../shared/web-shared/genai-traces-table/types';
import { TracesTableColumnGroup, TracesTableColumnType } from '../shared/web-shared/genai-traces-table/types';
import { createCustomMetadataColumnId } from '../shared/web-shared/genai-traces-table/utils/TraceUtils';

export const AGENT_INSTANCE_ID_KEY = 'service.instance.id';

export const agentTraceColumns: TracesTableColumn[] = [
  {
    id: createCustomMetadataColumnId(AGENT_INSTANCE_ID_KEY),
    label: 'Instance ID',
    type: TracesTableColumnType.TRACE_INFO,
    group: TracesTableColumnGroup.BASE,
  },
];
