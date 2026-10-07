import React, { useState, useRef, useEffect } from 'react';
import { AlertTriangle } from 'lucide-react';
import { Snippet, AbsUser, SyncState } from '../types';
import { SnippetsHeader } from './SnippetsHeader';
import { SnippetSortBar } from './SnippetSortBar';
import { BookFilterBar } from './BookFilterBar';
import { SnippetList } from './SnippetList';
import { SyncStatusBanner } from './SyncStatusBanner';
import { ExpandSnippetModal } from './ExpandSnippetModal';
import { CutoffModal } from './CutoffModal';
import {
  useSnippetSorting,
  useSnippetFiltering,
  useCutoffConfigManager,
  useSnippetOperations
} from '../lib/snippetHooks';

export interface SnippetsViewProps {
  snippets: Snippet[];
  user: AbsUser | null;
  activeToken?: string | null;
  serverUrl?: string;
  sidecarUrl?: string;
  useProxy?: boolean;
  syncState?: SyncState | null;
  isTriggeringSync?: boolean;
  onTriggerSync?: () => Promise<void>;
  onDeleteSnippet: (id: string, snippet?: Snippet) => void;
  onNavigateToCapture: () => void;
  onRefreshSnippets?: () => Promise<void>;
  isLoadingSnippets?: boolean;
  onCutoffUpdated?: (newSyncState: SyncState) => void;
}

export const SnippetsView: React.FC<SnippetsViewProps> = ({
  snippets,
  user,
  activeToken,
  serverUrl = 'http://localhost:13378',
  sidecarUrl = 'http://localhost:13380',
  useProxy = true,
  syncState,
  isTriggeringSync = false,
  onTriggerSync,
  onDeleteSnippet,
  onNavigateToCapture,
  onRefreshSnippets,
  isLoadingSnippets = false,
  onCutoffUpdated,
}) => {
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedBookFilter, setSelectedBookFilter] = useState<string | null>(null);
  const [bookFilterQuery, setBookFilterQuery] = useState<string>('');
  const [isBookDropdownOpen, setIsBookDropdownOpen] = useState<boolean>(false);
  const bookDropdownRef = useRef<HTMLDivElement>(null);

  const sorting = useSnippetSorting();

  useEffect(() => {
    const handleOutsideClick = (e: MouseEvent) => {
      if (bookDropdownRef.current && !bookDropdownRef.current.contains(e.target as Node)) {
        setIsBookDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', handleOutsideClick);
    return () => document.removeEventListener('mousedown', handleOutsideClick);
  }, []);

  const { uniqueBooks, matchingBooks, sortedSnippets } = useSnippetFiltering(
    snippets,
    searchTerm,
    selectedBookFilter,
    bookFilterQuery,
    sorting.sortField,
    sorting.sortDirection
  );

  const cutoffMgr = useCutoffConfigManager(
    syncState,
    activeToken,
    serverUrl,
    sidecarUrl,
    useProxy,
    onCutoffUpdated,
    onTriggerSync
  );

  const ops = useSnippetOperations(
    activeToken,
    serverUrl,
    sidecarUrl,
    useProxy,
    onRefreshSnippets
  );

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <SnippetsHeader
        snippetsCount={snippets.length}
        syncState={syncState}
        searchTerm={searchTerm}
        isTriggeringSync={isTriggeringSync}
        onSearchChange={setSearchTerm}
        onTriggerSync={onTriggerSync}
        onNavigateToCapture={onNavigateToCapture}
        onOpenCutoffModal={() => cutoffMgr.setIsCutoffModalOpen(true)}
      />

      {ops.exportError && (
        <div className="p-3 bg-neutral-950 border border-red-800 text-[11px] text-red-400 flex items-center gap-1.5">
          <AlertTriangle className="w-3.5 h-3.5" />
          <span>{ops.exportError}</span>
        </div>
      )}

      <SyncStatusBanner syncState={syncState} />

      <SnippetSortBar
        sortField={sorting.sortField}
        sortDirection={sorting.sortDirection}
        sortedCount={sortedSnippets.length}
        onSelectDateSort={sorting.handleSelectDateSort}
        onSelectBookSort={sorting.handleSelectBookSort}
        onToggleDirection={sorting.handleToggleDirection}
      />

      <BookFilterBar
        uniqueBooks={uniqueBooks}
        matchingBooks={matchingBooks}
        snippets={snippets}
        selectedBookFilter={selectedBookFilter}
        bookFilterQuery={bookFilterQuery}
        isBookDropdownOpen={isBookDropdownOpen}
        bookDropdownRef={bookDropdownRef}
        onSelectBook={setSelectedBookFilter}
        onFilterQueryChange={setBookFilterQuery}
        onSetDropdownOpen={setIsBookDropdownOpen}
        onClearFilter={() => {
          setSelectedBookFilter(null);
          setBookFilterQuery('');
          setIsBookDropdownOpen(false);
        }}
      />

      <SnippetList
        snippets={sortedSnippets}
        user={user}
        hasFilterActive={Boolean(searchTerm || selectedBookFilter)}
        copiedId={ops.copiedId}
        copiedCitationId={ops.copiedCitationId}
        retryingSnippetId={ops.retryingSnippetId}
        retryNotification={ops.retryNotification}
        exportingBook={ops.exportingBook}
        openExportDropdownId={ops.openExportDropdownId}
        onNavigateToCapture={onNavigateToCapture}
        onDeleteSnippet={onDeleteSnippet}
        onCopyCitation={ops.handleCopyCitation}
        onCopyTranscript={ops.handleCopyTranscript}
        onDownloadAudio={ops.handleDownloadAudio}
        onDownloadMarkdown={ops.handleDownloadMarkdown}
        onRetryExtraction={ops.handleRetryExtraction}
        onOpenExpandModal={ops.openExpandModal}
        onDismissRetryNotification={() => ops.setRetryNotification(null)}
        onToggleExportDropdown={(id) => {
          ops.setOpenExportDropdownId(ops.openExportDropdownId === id ? null : id);
        }}
        onExportBook={ops.handleExportBook}
      />

      {ops.expandSnippet && (
        <ExpandSnippetModal
          expandSnippet={ops.expandSnippet}
          preRoll={ops.preRoll}
          postRoll={ops.postRoll}
          isExpanding={ops.isExpanding}
          expandError={ops.expandError}
          expandSuccess={ops.expandSuccess}
          setPreRoll={ops.setPreRoll}
          setPostRoll={ops.setPostRoll}
          onClose={() => !ops.isExpanding && ops.setExpandSnippet(null)}
          onSubmit={ops.handleExecuteExpand}
        />
      )}

      <CutoffModal
        isOpen={cutoffMgr.isCutoffModalOpen}
        selectedCutoffMode={cutoffMgr.selectedCutoffMode}
        customDateInput={cutoffMgr.customDateInput}
        cutoffSaveError={cutoffMgr.cutoffSaveError}
        cutoffSaveSuccess={cutoffMgr.cutoffSaveSuccess}
        isSavingCutoff={cutoffMgr.isSavingCutoff}
        syncState={syncState}
        setSelectedCutoffMode={cutoffMgr.setSelectedCutoffMode}
        setCustomDateInput={cutoffMgr.setCustomDateInput}
        onClose={() => cutoffMgr.setIsCutoffModalOpen(false)}
        onSubmit={cutoffMgr.handleSaveCutoffConfig}
      />
    </div>
  );
};
