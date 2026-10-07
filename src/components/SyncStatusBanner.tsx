import React from 'react';
import { RotateCw } from 'lucide-react';
import { SyncState } from '../types';

export interface SyncStatusBannerProps {
  syncState?: SyncState;
}

export const SyncStatusBanner: React.FC<SyncStatusBannerProps> = ({ syncState }) => {
  if (!syncState?.is_syncing) {
    return null;
  }

  const cutoffLabel = syncState.installation_date
    ? `${syncState.installation_date} 00:00`
    : 'installation';

  const hasSkippedCutoff =
    typeof syncState.skipped_before_cutoff === 'number' && syncState.skipped_before_cutoff > 0;

  return (
    <div className="p-2.5 bg-[#141414] border border-neutral-800 text-xs text-neutral-300 flex items-center justify-between gap-2 font-mono">
      <div className="flex items-center gap-2">
        <RotateCw className="w-3.5 h-3.5 text-emerald-400 animate-spin" />
        <span>Syncing bookmarks created on or after {cutoffLabel}...</span>
        {syncState.current_item && (
          <span className="text-neutral-400 truncate max-w-xs">{syncState.current_item}</span>
        )}
      </div>
      {hasSkippedCutoff && (
        <span className="text-[11px] text-neutral-500 shrink-0">
          {syncState.skipped_before_cutoff} older bookmarks skipped
        </span>
      )}
    </div>
  );
};
