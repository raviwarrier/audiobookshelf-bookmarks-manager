import React from 'react';
import { Search, RotateCw, BookmarkPlus } from 'lucide-react';
import { SyncState } from '../types';
import { CutoffBadge } from './CutoffBadge';

export interface SnippetsHeaderProps {
  snippetsCount: number;
  syncState?: SyncState | null;
  searchTerm: string;
  isTriggeringSync?: boolean;
  onSearchChange: (val: string) => void;
  onTriggerSync?: () => Promise<void>;
  onNavigateToCapture: () => void;
  onOpenCutoffModal: () => void;
}

export const SnippetsHeader: React.FC<SnippetsHeaderProps> = ({
  snippetsCount,
  syncState,
  searchTerm,
  isTriggeringSync,
  onSearchChange,
  onTriggerSync,
  onNavigateToCapture,
  onOpenCutoffModal,
}) => {
  const isSyncSpinning = isTriggeringSync || syncState?.is_syncing;

  return (
    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-neutral-800 pb-4">
      <div>
        <h2 className="text-base font-semibold text-white tracking-tight uppercase">
          Saved Bookmarks & Transcripts
        </h2>
        <div className="flex flex-wrap items-center gap-2 mt-1">
          <span className="text-xs text-neutral-400 font-mono">
            {snippetsCount} snippets
          </span>
          <CutoffBadge syncState={syncState} onClick={onOpenCutoffModal} />
        </div>
      </div>

      <div className="flex items-center gap-3">
        <div className="relative w-full sm:w-64">
          <Search className="w-3.5 h-3.5 text-neutral-400 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            value={searchTerm}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder="Search transcripts or books..."
            className="w-full bg-[#181818] border border-neutral-700 hover:border-neutral-500 focus:border-neutral-300 focus:bg-[#202020] pl-8 pr-3 py-1.5 text-xs text-white placeholder-neutral-500 focus:outline-none transition-colors font-mono"
          />
        </div>

        {onTriggerSync && (
          <button
            onClick={onTriggerSync}
            disabled={isSyncSpinning}
            className="px-2.5 py-1.5 border border-neutral-700 bg-[#161616] hover:bg-[#222222] hover:border-neutral-500 text-xs text-neutral-200 transition-colors flex items-center gap-1.5 disabled:opacity-50"
            title="Sync bookmarks from server"
          >
            <RotateCw className={`w-3.5 h-3.5 ${isSyncSpinning ? 'animate-spin' : ''}`} />
            <span className="hidden sm:inline">Sync Server</span>
          </button>
        )}

        <button
          onClick={onNavigateToCapture}
          className="px-3 py-1.5 bg-neutral-100 text-black hover:bg-white text-xs font-semibold flex items-center gap-1.5 shrink-0 transition-colors"
        >
          <BookmarkPlus className="w-3.5 h-3.5" />
          <span>New Snippet</span>
        </button>
      </div>
    </div>
  );
};
