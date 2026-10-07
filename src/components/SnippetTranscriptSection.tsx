import React from 'react';
import { Copy, Check } from 'lucide-react';
import { Snippet } from '../types';

export interface SnippetTranscriptSectionProps {
  snippet: Snippet;
  isAudioAvailable: boolean;
  copiedId: string | null;
  copiedCitationId: string | null;
  onCopyCitation: (snippet: Snippet) => void;
  onCopyTranscript: (id: string, text: string) => void;
}

export const SnippetTranscriptSection: React.FC<SnippetTranscriptSectionProps> = ({
  snippet,
  isAudioAvailable,
  copiedId,
  copiedCitationId,
  onCopyCitation,
  onCopyTranscript
}) => {
  const isCitationCopied = copiedCitationId === snippet.id;
  const isTextCopied = copiedId === snippet.id;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-xs text-neutral-400">
        <span className="font-mono text-[11px] uppercase">
          {isAudioAvailable ? 'Whisper Transcript' : 'Bookmark Details & Status'}
        </span>
        <div className="flex items-center gap-3">
          {isAudioAvailable && (
            <button
              onClick={() => onCopyCitation(snippet)}
              title="Copy formatted quote citation with book title, author, and timestamp"
              className="flex items-center gap-1 text-neutral-400 hover:text-white transition-colors text-[11px]"
            >
              {isCitationCopied ? (
                <>
                  <Check className="w-3.5 h-3.5 text-emerald-400" />
                  <span className="text-emerald-400">Citation Copied</span>
                </>
              ) : (
                <>
                  <Copy className="w-3.5 h-3.5" />
                  <span>Cite / Quote</span>
                </>
              )}
            </button>
          )}
          <button
            onClick={() => onCopyTranscript(snippet.id, snippet.transcript)}
            className="flex items-center gap-1 text-neutral-300 hover:text-white transition-colors"
          >
            {isTextCopied ? (
              <>
                <Check className="w-3.5 h-3.5 text-white" />
                <span>Copied</span>
              </>
            ) : (
              <>
                <Copy className="w-3.5 h-3.5" />
                <span>Copy Text</span>
              </>
            )}
          </button>
        </div>
      </div>

      <div className="p-3 bg-[#151515] border border-neutral-700 text-xs text-neutral-200 leading-relaxed font-mono whitespace-pre-wrap">
        {snippet.transcript || 'No text or transcript available.'}
      </div>
    </div>
  );
};
