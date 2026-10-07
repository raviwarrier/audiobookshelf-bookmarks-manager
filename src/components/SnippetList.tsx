import React from 'react';
import { Snippet, AbsUser } from '../types';
import { SnippetCard } from './SnippetCard';

export interface SnippetListProps {
  snippets: Snippet[];
  user: AbsUser | null;
  hasFilterActive: boolean;
  copiedId: string | null;
  copiedCitationId: string | null;
  retryingSnippetId: string | null;
  retryNotification: { id: string; type: 'success' | 'error'; message: string } | null;
  exportingBook: string | null;
  openExportDropdownId: string | null;
  onNavigateToCapture: () => void;
  onDeleteSnippet: (id: string, snippet?: Snippet) => void;
  onCopyCitation: (snippet: Snippet) => void;
  onCopyTranscript: (id: string, text: string) => void;
  onDownloadAudio: (snippet: Snippet) => void;
  onDownloadMarkdown: (snippet: Snippet) => void;
  onRetryExtraction: (snippet: Snippet) => void;
  onOpenExpandModal: (snippet: Snippet) => void;
  onDismissRetryNotification: () => void;
  onToggleExportDropdown: (id: string) => void;
  onExportBook: (bookTitle: string, format: 'zip' | 'markdown') => void;
}

export const SnippetList: React.FC<SnippetListProps> = ({
  snippets,
  user,
  hasFilterActive,
  copiedId,
  copiedCitationId,
  retryingSnippetId,
  retryNotification,
  exportingBook,
  openExportDropdownId,
  onNavigateToCapture,
  onDeleteSnippet,
  onCopyCitation,
  onCopyTranscript,
  onDownloadAudio,
  onDownloadMarkdown,
  onRetryExtraction,
  onOpenExpandModal,
  onDismissRetryNotification,
  onToggleExportDropdown,
  onExportBook,
}) => {
  if (snippets.length === 0) {
    const emptyMsg = hasFilterActive
      ? 'No matching snippets found.'
      : 'No snippets captured yet.';

    return (
      <div className="border border-neutral-700 bg-[#0d0d0d] p-12 text-center space-y-4">
        <div className="text-neutral-400 text-sm font-mono">{emptyMsg}</div>
        <button
          onClick={onNavigateToCapture}
          className="px-4 py-2 border border-neutral-700 bg-[#161616] text-xs text-neutral-200 hover:text-white hover:border-neutral-500 transition-colors"
        >
          Go to Capture Screen →
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {snippets.map((snippet) => {
        const isAudioAvailable = Boolean(
          snippet.audioUrl && snippet.duration > 0 && snippet.extractionStatus !== 'unavailable'
        );

        return (
          <SnippetCard
            key={snippet.id}
            snippet={snippet}
            username={user?.username}
            isAudioAvailable={isAudioAvailable}
            copiedId={copiedId}
            copiedCitationId={copiedCitationId}
            retryingSnippetId={retryingSnippetId}
            retryNotification={retryNotification}
            isExporting={exportingBook === snippet.bookTitle}
            isExportDropdownOpen={openExportDropdownId === snippet.id}
            onCopyCitation={onCopyCitation}
            onCopyTranscript={onCopyTranscript}
            onDownloadAudio={onDownloadAudio}
            onDownloadMarkdown={onDownloadMarkdown}
            onRetryExtraction={onRetryExtraction}
            onOpenExpandModal={onOpenExpandModal}
            onDismissRetryNotification={onDismissRetryNotification}
            onToggleExportDropdown={(e) => {
              e.stopPropagation();
              onToggleExportDropdown(snippet.id);
            }}
            onExportBook={onExportBook}
            onDeleteSnippet={onDeleteSnippet}
          />
        );
      })}
    </div>
  );
};
