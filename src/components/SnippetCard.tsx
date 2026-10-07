import React from 'react';
import { Snippet } from '../types';
import { SnippetHeader } from './SnippetHeader';
import { SnippetAudioSection } from './SnippetAudioSection';
import { SnippetTranscriptSection } from './SnippetTranscriptSection';
import { SnippetActionsToolbar } from './SnippetActionsToolbar';

export interface SnippetCardProps {
  snippet: Snippet;
  username?: string;
  isAudioAvailable: boolean;
  copiedId: string | null;
  copiedCitationId: string | null;
  retryingSnippetId: string | null;
  retryNotification: { id: string; type: 'success' | 'error'; message: string } | null;
  isExporting: boolean;
  isExportDropdownOpen: boolean;
  onCopyCitation: (snippet: Snippet) => void;
  onCopyTranscript: (id: string, text: string) => void;
  onDownloadAudio: (snippet: Snippet) => void;
  onDownloadMarkdown: (snippet: Snippet) => void;
  onRetryExtraction: (snippet: Snippet) => void;
  onOpenExpandModal: (snippet: Snippet) => void;
  onDismissRetryNotification: () => void;
  onToggleExportDropdown: (e: React.MouseEvent) => void;
  onExportBook: (bookTitle: string, format: 'zip' | 'markdown') => void;
  onDeleteSnippet: (id: string, snippet: Snippet) => void;
}

export const SnippetCard: React.FC<SnippetCardProps> = ({
  snippet,
  username,
  isAudioAvailable,
  copiedId,
  copiedCitationId,
  retryingSnippetId,
  retryNotification,
  isExporting,
  isExportDropdownOpen,
  onCopyCitation,
  onCopyTranscript,
  onDownloadAudio,
  onDownloadMarkdown,
  onRetryExtraction,
  onOpenExpandModal,
  onDismissRetryNotification,
  onToggleExportDropdown,
  onExportBook,
  onDeleteSnippet,
}) => {
  const isRetrying = retryingSnippetId === snippet.id;

  return (
    <article className="border border-neutral-700 bg-[#0d0d0d] p-5 space-y-4">
      <SnippetHeader
        snippet={snippet}
        username={username}
        isAudioAvailable={isAudioAvailable}
      />

      <SnippetAudioSection
        snippet={snippet}
        isAudioAvailable={isAudioAvailable}
        isRetrying={isRetrying}
        retryNotification={retryNotification}
        onRetryExtraction={onRetryExtraction}
        onOpenExpandModal={onOpenExpandModal}
        onDismissRetryNotification={onDismissRetryNotification}
      />

      <SnippetTranscriptSection
        snippet={snippet}
        isAudioAvailable={isAudioAvailable}
        copiedId={copiedId}
        copiedCitationId={copiedCitationId}
        onCopyCitation={onCopyCitation}
        onCopyTranscript={onCopyTranscript}
      />

      <SnippetActionsToolbar
        snippet={snippet}
        isAudioAvailable={isAudioAvailable}
        isRetrying={isRetrying}
        isExporting={isExporting}
        isExportDropdownOpen={isExportDropdownOpen}
        onDownloadAudio={onDownloadAudio}
        onDownloadMarkdown={onDownloadMarkdown}
        onRetryExtraction={onRetryExtraction}
        onOpenExpandModal={onOpenExpandModal}
        onToggleExportDropdown={onToggleExportDropdown}
        onExportBook={onExportBook}
        onDeleteSnippet={onDeleteSnippet}
      />
    </article>
  );
};
