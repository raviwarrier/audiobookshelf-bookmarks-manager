import React from 'react';
import { ArrowUpDown, Calendar, BookOpen, ArrowUp, ArrowDown } from 'lucide-react';

interface SnippetSortBarProps {
  sortField: 'date' | 'book';
  sortDirection: 'asc' | 'desc';
  sortedCount: number;
  onSelectDateSort: () => void;
  onSelectBookSort: () => void;
  onToggleDirection: () => void;
}

export const SnippetSortBar: React.FC<SnippetSortBarProps> = ({
  sortField,
  sortDirection,
  sortedCount,
  onSelectDateSort,
  onSelectBookSort,
  onToggleDirection,
}) => {
  const isDate = sortField === 'date';
  const isBook = sortField === 'book';

  return (
    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-neutral-800 pb-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-neutral-400 font-mono flex items-center gap-1.5 mr-1">
          <ArrowUpDown className="w-3.5 h-3.5 text-neutral-400" />
          <span>Sort:</span>
        </span>

        <div className="inline-flex border border-neutral-700 bg-[#121212]">
          <button
            onClick={onSelectDateSort}
            className={`px-3 py-1 text-xs font-mono flex items-center gap-1.5 transition-colors ${
              isDate
                ? 'bg-neutral-200 text-black font-semibold'
                : 'text-neutral-300 hover:text-white hover:bg-[#1a1a1a]'
            }`}
            title="Sort by Date (click to toggle ascending/descending)"
          >
            <Calendar className="w-3.5 h-3.5" />
            <span>Date</span>
            {isDate && (
              <span className="text-[10px] ml-0.5 opacity-75">
                ({sortDirection === 'desc' ? 'Newest' : 'Oldest'})
              </span>
            )}
          </button>

          <button
            onClick={onSelectBookSort}
            className={`px-3 py-1 text-xs font-mono flex items-center gap-1.5 transition-colors border-l border-neutral-700 ${
              isBook
                ? 'bg-neutral-200 text-black font-semibold'
                : 'text-neutral-300 hover:text-white hover:bg-[#1a1a1a]'
            }`}
            title="Sort by Book Title (click to toggle A-Z / Z-A)"
          >
            <BookOpen className="w-3.5 h-3.5" />
            <span>Book</span>
            {isBook && (
              <span className="text-[10px] ml-0.5 opacity-75">
                ({sortDirection === 'asc' ? 'A→Z' : 'Z→A'})
              </span>
            )}
          </button>
        </div>

        <button
          onClick={onToggleDirection}
          className="px-2.5 py-1 text-xs font-mono border border-neutral-700 bg-[#141414] hover:border-neutral-500 text-neutral-300 hover:text-white transition-colors flex items-center gap-1.5"
          title={`Current order: ${sortDirection === 'asc' ? 'Ascending' : 'Descending'}. Click to reverse.`}
        >
          {sortDirection === 'asc' ? (
            <>
              <ArrowUp className="w-3.5 h-3.5 text-neutral-200" />
              <span>Ascending</span>
            </>
          ) : (
            <>
              <ArrowDown className="w-3.5 h-3.5 text-neutral-200" />
              <span>Descending</span>
            </>
          )}
        </button>
      </div>

      <div className="text-xs text-neutral-400 font-mono">
        Showing {sortedCount} {sortedCount === 1 ? 'snippet' : 'snippets'}
      </div>
    </div>
  );
};
