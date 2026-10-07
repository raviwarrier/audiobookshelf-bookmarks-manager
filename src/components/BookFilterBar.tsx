import React from 'react';
import { BookOpen, X } from 'lucide-react';
import { Snippet } from '../types';

interface BookFilterBarProps {
  uniqueBooks: string[];
  matchingBooks: string[];
  snippets: Snippet[];
  selectedBookFilter: string | null;
  bookFilterQuery: string;
  isBookDropdownOpen: boolean;
  bookDropdownRef: React.RefObject<HTMLDivElement | null>;
  onSelectBook: (book: string | null) => void;
  onFilterQueryChange: (q: string) => void;
  onSetDropdownOpen: (open: boolean) => void;
  onClearFilter: () => void;
}

interface BookDropdownListProps {
  matchingBooks: string[];
  snippets: Snippet[];
  selectedBookFilter: string | null;
  onSelectBook: (b: string) => void;
  onFilterQueryChange: (q: string) => void;
  onSetDropdownOpen: (open: boolean) => void;
}

const BookDropdownList: React.FC<BookDropdownListProps> = ({
  matchingBooks,
  snippets,
  selectedBookFilter,
  onSelectBook,
  onFilterQueryChange,
  onSetDropdownOpen,
}) => {
  return (
    <div className="absolute left-0 right-0 top-full mt-1 max-h-60 overflow-y-auto bg-[#141414] border border-neutral-700 shadow-2xl z-50 py-1 font-mono text-xs">
      <div className="px-3 py-1 text-[10px] uppercase tracking-wider text-neutral-400 border-b border-neutral-800/80 flex justify-between items-center">
        <span>Matching Books ({matchingBooks.length})</span>
        <span className="text-[10px] text-neutral-500">Click to filter</span>
      </div>
      {matchingBooks.map((bTitle) => {
        const count = snippets.filter((s) => s.bookTitle === bTitle).length;
        const isSelected = selectedBookFilter === bTitle;
        return (
          <button
            key={bTitle}
            type="button"
            onClick={() => {
              onSelectBook(bTitle);
              onFilterQueryChange('');
              onSetDropdownOpen(false);
            }}
            className={`w-full text-left px-3 py-1.5 flex items-center justify-between gap-2 border-b border-neutral-800/40 last:border-none transition-colors ${
              isSelected
                ? 'bg-neutral-800 text-white font-medium'
                : 'text-neutral-300 hover:bg-[#202020] hover:text-white'
            }`}
          >
            <span className="truncate">{bTitle}</span>
            <span className="text-[10px] text-neutral-400 bg-neutral-800 px-1.5 py-0.5 shrink-0">
              {count}
            </span>
          </button>
        );
      })}
    </div>
  );
};

export const BookFilterBar: React.FC<BookFilterBarProps> = ({
  uniqueBooks,
  matchingBooks,
  snippets,
  selectedBookFilter,
  bookFilterQuery,
  isBookDropdownOpen,
  bookDropdownRef,
  onSelectBook,
  onFilterQueryChange,
  onSetDropdownOpen,
  onClearFilter,
}) => {
  if (uniqueBooks.length === 0) return null;

  const isAllSelected = !selectedBookFilter && !bookFilterQuery.trim();
  const filterInputValue = selectedBookFilter || bookFilterQuery;
  const isInputActive = Boolean(selectedBookFilter || bookFilterQuery.trim());

  return (
    <div className="flex flex-wrap items-center gap-3 pt-1 pb-1">
      {/* All Books Button */}
      <button
        id="snippets-all-books-filter-btn"
        onClick={onClearFilter}
        className={`px-3 py-1.5 text-xs font-mono transition-colors border flex items-center gap-1.5 shrink-0 ${
          isAllSelected
            ? 'bg-neutral-200 text-black border-neutral-200 font-semibold shadow-sm'
            : 'bg-[#141414] text-neutral-300 border-neutral-700 hover:border-neutral-500 hover:text-white cursor-pointer'
        }`}
        title={`Show all snippets across all ${uniqueBooks.length} books`}
      >
        <span>All Books</span>
        <span
          className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${
            isAllSelected
              ? 'bg-neutral-300 text-black'
              : 'bg-neutral-800 text-neutral-400'
          }`}
        >
          {uniqueBooks.length}
        </span>
      </button>

      {/* Type Book Name Input & Dropdown */}
      <div ref={bookDropdownRef} className="relative flex-1 min-w-[220px] max-w-md">
        <div className="relative flex items-center">
          <BookOpen className="absolute left-2.5 w-3.5 h-3.5 text-neutral-400 pointer-events-none" />
          <input
            id="snippets-book-name-filter-input"
            type="text"
            placeholder={
              selectedBookFilter
                ? `Filtering: ${selectedBookFilter}`
                : `Type book name to filter (${uniqueBooks.length} books)...`
            }
            value={filterInputValue}
            onChange={(e) => {
              onSelectBook(null);
              onFilterQueryChange(e.target.value);
              onSetDropdownOpen(true);
            }}
            onFocus={() => onSetDropdownOpen(true)}
            className={`w-full bg-[#121212] border text-xs text-white pl-8 pr-8 py-1.5 focus:outline-none transition-colors font-mono ${
              isInputActive
                ? 'border-neutral-400 bg-[#171717]'
                : 'border-neutral-700 hover:border-neutral-500 focus:border-neutral-400'
            }`}
          />
          {filterInputValue && (
            <button
              id="snippets-clear-book-filter-btn"
              onClick={onClearFilter}
              className="absolute right-2 p-0.5 text-neutral-400 hover:text-white hover:bg-neutral-800 rounded transition-colors"
              title="Clear book filter"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          )}
        </div>

        {isBookDropdownOpen && matchingBooks.length > 0 && (
          <BookDropdownList
            matchingBooks={matchingBooks}
            snippets={snippets}
            selectedBookFilter={selectedBookFilter}
            onSelectBook={onSelectBook}
            onFilterQueryChange={onFilterQueryChange}
            onSetDropdownOpen={onSetDropdownOpen}
          />
        )}
      </div>

      {/* Active Filter Indicator Tag */}
      {isInputActive && (
        <div className="flex items-center gap-1.5 text-xs text-neutral-300 font-mono bg-[#181818] border border-neutral-700 px-2.5 py-1">
          <span className="text-neutral-400 text-[11px]">Filtered:</span>
          <span className="text-white font-semibold truncate max-w-[200px]">
            {filterInputValue}
          </span>
          <button
            onClick={onClearFilter}
            className="ml-1 text-neutral-400 hover:text-white transition-colors"
            title="Clear filter"
          >
            <X className="w-3 h-3" />
          </button>
        </div>
      )}
    </div>
  );
};
