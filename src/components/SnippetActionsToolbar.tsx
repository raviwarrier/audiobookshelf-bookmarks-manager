import React from 'react';
import {
  FileAudio,
  FileText,
  Sliders,
  RotateCw,
  Archive,
  ChevronDown,
  Trash2
} from 'lucide-react';
import { Snippet } from '../types';

export interface SnippetActionsToolbarProps {
  snippet: Snippet;
  isAudioAvailable: boolean;
  isRetrying: boolean;
  isExporting: boolean;
  isExportDropdownOpen: boolean;
  onDownloadAudio: (snippet: Snippet) => void;
  onDownloadMarkdown: (snippet: Snippet) => void;
  onRetryExtraction: (snippet: Snippet) => void;
  onOpenExpandModal: (snippet: Snippet) => void;
  onToggleExportDropdown: (e: React.MouseEvent) => void;
  onExportBook: (bookTitle: string, format: 'zip' | 'markdown') => void;
  onDeleteSnippet: (id: string, snippet: Snippet) => void;
}

export const SnippetActionsToolbar: React.FC<SnippetActionsToolbarProps> = ({
  snippet,
  isAudioAvailable,
  isRetrying,
  isExporting,
  isExportDropdownOpen,
  onDownloadAudio,
  onDownloadMarkdown,
  onRetryExtraction,
  onOpenExpandModal,
  onToggleExportDropdown,
  onExportBook,
  onDeleteSnippet
}) => {
  const retrySpinClass = isRetrying ? 'animate-spin' : '';
  const retryButtonText = isRetrying ? 'Retrying...' : 'Retry Snipping';

  return (
    <div className="flex flex-wrap items-center justify-between gap-2 pt-2 border-t border-neutral-800 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        {isAudioAvailable ? (
          <button
            onClick={() => onDownloadAudio(snippet)}
            className="flex items-center gap-1.5 px-3 py-1.5 border border-neutral-700 bg-[#161616] hover:border-neutral-500 text-neutral-200 hover:text-white transition-colors"
          >
            <FileAudio className="w-3.5 h-3.5" />
            <span>Download .MP3</span>
          </button>
        ) : (
          <div
            className="flex items-center gap-1.5 px-3 py-1.5 border border-neutral-800 bg-[#141414] text-neutral-500 cursor-not-allowed opacity-50 font-mono"
            title="MP3 is not available for unextracted bookmark"
          >
            <FileAudio className="w-3.5 h-3.5" />
            <span>No .MP3 (N/A)</span>
          </div>
        )}

        <button
          onClick={() => onDownloadMarkdown(snippet)}
          className="flex items-center gap-1.5 px-3 py-1.5 border border-neutral-700 bg-[#161616] hover:border-neutral-500 text-neutral-200 hover:text-white transition-colors"
        >
          <FileText className="w-3.5 h-3.5" />
          <span>Download .MD</span>
        </button>

        {isAudioAvailable ? (
          <button
            onClick={() => onOpenExpandModal(snippet)}
            className="flex items-center gap-1.5 px-3 py-1.5 border border-neutral-700 bg-[#1a1a1a] hover:border-neutral-400 text-neutral-100 hover:text-white transition-colors"
            title="Adjust pre-roll & post-roll to expand snippet context"
          >
            <Sliders className="w-3.5 h-3.5 text-neutral-300" />
            <span>Adjust Duration / Context</span>
          </button>
        ) : (
          <div className="flex items-center gap-2">
            <button
              onClick={() => onRetryExtraction(snippet)}
              disabled={isRetrying}
              className="flex items-center gap-1.5 px-3 py-1.5 border border-amber-600 bg-amber-950/70 hover:bg-amber-900/90 text-amber-200 hover:text-white font-mono text-xs transition-colors disabled:opacity-50"
              title="Retry extracting audio for this bookmark"
            >
              <RotateCw className={`w-3.5 h-3.5 text-amber-400 ${retrySpinClass}`.trim()} />
              <span>{retryButtonText}</span>
            </button>
            <button
              onClick={() => onOpenExpandModal(snippet)}
              disabled={isRetrying}
              className="flex items-center gap-1.5 px-3 py-1.5 border border-neutral-700 bg-[#161616] hover:border-amber-500 text-neutral-300 hover:text-white font-mono text-xs transition-colors"
              title="Adjust pre-roll and post-roll timestamps and retry extraction"
            >
              <Sliders className="w-3.5 h-3.5 text-amber-400" />
              <span>Adjust & Retry</span>
            </button>
          </div>
        )}

        <div className="relative">
          <button
            onClick={onToggleExportDropdown}
            disabled={isExporting}
            className="flex items-center gap-1.5 px-3 py-1.5 border border-neutral-700 bg-[#161616] hover:border-neutral-400 text-neutral-200 hover:text-white transition-colors disabled:opacity-50"
            title="Export all snippets from this book"
          >
            <Archive className="w-3.5 h-3.5 text-neutral-300" />
            <span>
              {isExporting ? 'Exporting Book...' : 'Export all from this book'}
            </span>
            <ChevronDown
              className={`w-3 h-3 text-neutral-400 transition-transform ${
                isExportDropdownOpen ? 'rotate-180' : ''
              }`}
            />
          </button>

          {isExportDropdownOpen && (
            <div
              role="menu"
              tabIndex={-1}
              className="absolute left-0 mt-1 w-52 bg-[#181818] border border-neutral-700 shadow-xl z-20 font-mono py-1"
            >
              <div className="px-3 py-1.5 text-[10px] text-neutral-400 border-b border-neutral-800 uppercase tracking-wider truncate">
                {snippet.bookTitle}
              </div>
              <button
                onClick={() => onExportBook(snippet.bookTitle, 'zip')}
                className="w-full text-left px-3 py-2 text-xs text-neutral-200 hover:text-white hover:bg-[#222222] flex items-center justify-between transition-colors"
              >
                <span className="flex items-center gap-2">
                  <Archive className="w-3.5 h-3.5 text-neutral-400" />
                  <span>As Zip</span>
                </span>
                <span className="text-[10px] text-neutral-500 font-mono">.zip</span>
              </button>
              <button
                onClick={() => onExportBook(snippet.bookTitle, 'markdown')}
                className="w-full text-left px-3 py-2 text-xs text-neutral-200 hover:text-white hover:bg-[#222222] flex items-center justify-between transition-colors border-t border-neutral-800/60"
              >
                <span className="flex items-center gap-2">
                  <FileText className="w-3.5 h-3.5 text-neutral-400" />
                  <span>As .MD</span>
                </span>
                <span className="text-[10px] text-neutral-500 font-mono">.md</span>
              </button>
            </div>
          )}
        </div>
      </div>

      <div className="flex items-center gap-3">
        <button
          id={`snippet-delete-btn-${snippet.id}`}
          onClick={() => onDeleteSnippet(snippet.id, snippet)}
          className="text-neutral-400 hover:text-red-400 flex items-center gap-1 transition-colors"
          title="Delete snippet permanently"
        >
          <Trash2 className="w-3.5 h-3.5" />
          <span>Delete</span>
        </button>
      </div>
    </div>
  );
};
