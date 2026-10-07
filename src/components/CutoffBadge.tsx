import React from 'react';
import { Settings } from 'lucide-react';
import { SyncState } from '../types';

interface CutoffBadgeProps {
  syncState?: SyncState | null;
  onClick: () => void;
}

function getCutoffModeColor(mode?: string): string {
  if (mode === 'from_start') return 'bg-amber-400';
  if (mode === 'custom_date') return 'bg-sky-400';
  return 'bg-emerald-500';
}

function getCutoffModeLabel(syncState?: SyncState | null): string {
  if (syncState?.cutoff_mode === 'from_start') {
    return 'From start (all bookmarks)';
  }
  if (syncState?.cutoff_mode === 'custom_date') {
    return `From ${syncState.custom_cutoff_date || 'custom date'}`;
  }
  if (syncState?.installation_date) {
    return `From ${syncState.installation_date} (now)`;
  }
  return 'From install date';
}

export const CutoffBadge: React.FC<CutoffBadgeProps> = ({ syncState, onClick }) => {
  const dotColor = getCutoffModeColor(syncState?.cutoff_mode);
  const label = getCutoffModeLabel(syncState);
  const skippedCount = syncState?.skipped_before_cutoff;

  return (
    <button
      onClick={onClick}
      className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-neutral-900 hover:bg-neutral-850 border border-neutral-800 hover:border-neutral-650 text-[11px] text-neutral-300 hover:text-white font-mono rounded transition-colors group cursor-pointer"
      title="Click to configure bookmark cutoff period: 'from start', 'from yyyy/mm/dd', or 'from now'"
    >
      <span className={`w-1.5 h-1.5 rounded-full ${dotColor}`}></span>
      <span>Cutoff: {label}</span>
      {typeof skippedCount === 'number' && skippedCount > 0 && (
        <span className="text-neutral-500 border-l border-neutral-700 pl-1.5 ml-0.5">
          {skippedCount} skipped
        </span>
      )}
      <Settings className="w-3 h-3 text-neutral-500 group-hover:text-neutral-300 ml-1 transition-colors" />
    </button>
  );
};
