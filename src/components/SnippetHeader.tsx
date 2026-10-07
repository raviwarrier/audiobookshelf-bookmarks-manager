import React from 'react';
import { AlertTriangle } from 'lucide-react';
import { Snippet } from '../types';

export interface SnippetHeaderProps {
  snippet: Snippet;
  username?: string;
  isAudioAvailable: boolean;
}

export const SnippetHeader: React.FC<SnippetHeaderProps> = ({
  snippet,
  username,
  isAudioAvailable
}) => {
  const userDisplay = snippet.username || username || 'user';
  let startTimeText = 'N/A';
  if (isAudioAvailable || snippet.startTime > 0) {
    startTimeText = `${Math.round(snippet.startTime)}s`;
  }

  const durationText = isAudioAvailable ? `${snippet.duration}s` : 'N/A';
  const createdDateText = new Date(snippet.createdAt).toLocaleDateString();

  return (
    <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-2 border-b border-neutral-800 pb-3">
      <div>
        <div className="flex items-center gap-2 flex-wrap">
          <h3 className="text-sm font-semibold text-white">
            {snippet.bookTitle || 'Unknown Book'}
          </h3>
          {!isAudioAvailable && (
            <span className="text-[10px] text-amber-300 bg-amber-950/80 px-2 py-0.5 border border-amber-800/80 font-mono flex items-center gap-1">
              <AlertTriangle className="w-3 h-3 text-amber-400" />
              <span>Audio Unavailable</span>
            </span>
          )}
        </div>
        <div className="text-xs text-neutral-400 mt-0.5 flex flex-wrap items-center gap-2">
          <span>{snippet.author || 'N/A'}</span>
          <span>•</span>
          <span>{snippet.chapterName || 'N/A'}</span>
          <span className="text-[10px] text-neutral-400 bg-neutral-800 px-1.5 py-0.5 border border-neutral-700">
            @{userDisplay}
          </span>
          <span className="text-[10px] text-neutral-500 hidden sm:inline font-mono">
            {userDisplay}/bookmarks/
          </span>
        </div>
      </div>

      <div className="flex items-center gap-3 text-xs text-neutral-400 font-mono self-end sm:self-auto">
        <span>Start: {startTimeText}</span>
        <span>Duration: {durationText}</span>
        <span>{createdDateText}</span>
      </div>
    </div>
  );
};
