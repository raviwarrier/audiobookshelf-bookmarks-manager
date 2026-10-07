import React from 'react';
import { AlertTriangle, RotateCw, Sliders } from 'lucide-react';
import { Snippet } from '../types';

export interface SnippetAudioSectionProps {
  snippet: Snippet;
  isAudioAvailable: boolean;
  isRetrying: boolean;
  retryNotification: { id: string; type: 'success' | 'error'; message: string } | null;
  onRetryExtraction: (snippet: Snippet) => void;
  onOpenExpandModal: (snippet: Snippet) => void;
  onDismissRetryNotification: () => void;
}

export const SnippetAudioSection: React.FC<SnippetAudioSectionProps> = ({
  snippet,
  isAudioAvailable,
  isRetrying,
  retryNotification,
  onRetryExtraction,
  onOpenExpandModal,
  onDismissRetryNotification
}) => {
  if (isAudioAvailable) {
    return (
      <div className="bg-[#141414] p-2.5 border border-neutral-700">
        <audio
          key={`${snippet.id}-${snippet.duration}-${snippet.audioUrl}`}
          controls
          preload="metadata"
          src={snippet.audioUrl}
          className="w-full h-8 bg-[#181818]"
        >
          <track kind="captions" />
        </audio>
      </div>
    );
  }

  const hasNotification = retryNotification && retryNotification.id === snippet.id;
  const isSuccess = retryNotification?.type === 'success';

  return (
    <div className="space-y-2">
      <div className="bg-[#141414] p-3 border border-amber-900/60 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="flex items-start sm:items-center gap-2 text-xs text-amber-300 font-mono">
          <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5 sm:mt-0" />
          <span>Bookmark recorded, but audio could not be extracted (source file moved, unmounted, or slow response).</span>
        </div>
        <div className="flex items-center gap-2 shrink-0 self-start sm:self-auto flex-wrap">
          <button
            onClick={() => onRetryExtraction(snippet)}
            disabled={isRetrying}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold bg-amber-500 hover:bg-amber-400 text-black border border-amber-400 transition-colors disabled:opacity-50"
            title="Retry snipping and extracting audio for this bookmark"
          >
            <RotateCw className={`w-3.5 h-3.5 ${isRetrying ? 'animate-spin' : ''}`} />
            <span>{isRetrying ? 'Retrying Snipping...' : 'Retry Snipping'}</span>
          </button>
          <button
            onClick={() => onOpenExpandModal(snippet)}
            disabled={isRetrying}
            className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-mono bg-neutral-900 hover:bg-neutral-800 text-amber-300 border border-amber-800/80 transition-colors disabled:opacity-50"
            title="Set custom pre-roll and post-roll timestamps and retry extraction"
          >
            <Sliders className="w-3.5 h-3.5 text-amber-400" />
            <span>Adjust & Retry</span>
          </button>
        </div>
      </div>

      {hasNotification && (
        <div className={`p-2.5 text-xs font-mono border flex items-center justify-between gap-2 ${
          isSuccess
            ? 'bg-emerald-950/60 border-emerald-700 text-emerald-200'
            : 'bg-red-950/60 border-red-700 text-red-200'
        }`}>
          <span>{retryNotification.message}</span>
          <button
            onClick={onDismissRetryNotification}
            className="text-neutral-400 hover:text-white px-1"
          >
            ✕
          </button>
        </div>
      )}
    </div>
  );
};
