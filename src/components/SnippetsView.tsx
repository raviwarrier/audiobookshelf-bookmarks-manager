import React, { useState, useEffect } from 'react';
import { 
  Search, 
  Download, 
  Copy, 
  Trash2, 
  Check, 
  FileText, 
  FileAudio,
  BookmarkPlus,
  FolderTree,
  User as UserIcon,
  RefreshCw,
  Sliders,
  Archive,
  AlertTriangle,
  RotateCw,
  X,
  BookOpen,
  ChevronDown,
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
  Calendar
} from 'lucide-react';
import { Snippet, AbsUser, SyncState } from '../types';

interface SnippetsViewProps {
  snippets: Snippet[];
  user: AbsUser | null;
  activeToken?: string | null;
  serverUrl?: string;
  sidecarUrl?: string;
  useProxy?: boolean;
  syncState?: SyncState | null;
  isTriggeringSync?: boolean;
  onTriggerSync?: () => Promise<void>;
  onDeleteSnippet: (id: string) => void;
  onNavigateToCapture: () => void;
  onRefreshSnippets?: () => Promise<void>;
  isLoadingSnippets?: boolean;
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
}) => {
  const [searchTerm, setSearchTerm] = useState('');
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [copiedCitationId, setCopiedCitationId] = useState<string | null>(null);

  // Expand / Adjust Snippet Modal State
  const [expandSnippet, setExpandSnippet] = useState<Snippet | null>(null);
  const [preRoll, setPreRoll] = useState<number>(30);
  const [postRoll, setPostRoll] = useState<number>(60);
  const [isExpanding, setIsExpanding] = useState<boolean>(false);
  const [expandError, setExpandError] = useState<string | null>(null);
  const [expandSuccess, setExpandSuccess] = useState<string | null>(null);

  // Exporting state
  const [exportingBook, setExportingBook] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const [selectedBookFilter, setSelectedBookFilter] = useState<string | null>(null);
  const [openExportDropdownId, setOpenExportDropdownId] = useState<string | null>(null);

  // Sorting state (by Date or Book, Ascending / Descending)
  const [sortField, setSortField] = useState<'date' | 'book'>('date');
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('desc');

  useEffect(() => {
    if (!openExportDropdownId) return;
    const handleGlobalClick = () => setOpenExportDropdownId(null);
    window.addEventListener('click', handleGlobalClick);
    return () => window.removeEventListener('click', handleGlobalClick);
  }, [openExportDropdownId]);

  // Group snippets by unique books
  const uniqueBooks: string[] = Array.from(new Set(snippets.map((s) => s.bookTitle).filter(Boolean)));

  const filteredSnippets = snippets.filter((s) => {
    const matchesSearch =
      s.bookTitle.toLowerCase().includes(searchTerm.toLowerCase()) ||
      s.author.toLowerCase().includes(searchTerm.toLowerCase()) ||
      s.chapterName.toLowerCase().includes(searchTerm.toLowerCase()) ||
      s.transcript.toLowerCase().includes(searchTerm.toLowerCase());
    const matchesBook = !selectedBookFilter || s.bookTitle === selectedBookFilter;
    return matchesSearch && matchesBook;
  });

  const getSnippetTime = (s: Snippet): number => {
    if (typeof s.createdAt === 'number' && !isNaN(s.createdAt) && s.createdAt > 0) {
      return s.createdAt;
    }
    if (s.timestamp) {
      const match = s.timestamp.match(/^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})/);
      if (match) {
        const d = new Date(`${match[1]}-${match[2]}-${match[3]}T${match[4]}:${match[5]}:${match[6]}`);
        if (!isNaN(d.getTime())) return d.getTime();
      }
      const d = new Date(s.timestamp);
      if (!isNaN(d.getTime())) return d.getTime();
    }
    return 0;
  };

  const sortedSnippets = [...filteredSnippets].sort((a, b) => {
    if (sortField === 'date') {
      const timeA = getSnippetTime(a);
      const timeB = getSnippetTime(b);
      if (timeA !== timeB) {
        return sortDirection === 'desc' ? timeB - timeA : timeA - timeB;
      }
      return a.bookTitle.localeCompare(b.bookTitle);
    } else {
      // Sort by book title
      const cmp = a.bookTitle.localeCompare(b.bookTitle, undefined, { sensitivity: 'base' });
      if (cmp !== 0) {
        return sortDirection === 'asc' ? cmp : -cmp;
      }
      return getSnippetTime(b) - getSnippetTime(a);
    }
  });

  const handleCopyTranscript = (id: string, text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const handleCopyCitation = (snippet: Snippet) => {
    const mins = Math.floor(snippet.startTime / 60);
    const secs = Math.floor(snippet.startTime % 60);
    const timeFormatted = `${mins}:${secs.toString().padStart(2, '0')}`;
    const citation = `> "${snippet.transcript.trim()}"\n\n— *${snippet.bookTitle}* by ${snippet.author} (${snippet.chapterName}, offset ${timeFormatted})`;
    navigator.clipboard.writeText(citation);
    setCopiedCitationId(snippet.id);
    setTimeout(() => setCopiedCitationId(null), 2500);
  };

  const handleDownloadMarkdown = (snippet: Snippet) => {
    const blob = new Blob([snippet.markdownContent], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${snippet.bookTitle.replace(/\s+/g, '_')}_${snippet.timestamp}.md`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  const handleDownloadAudio = async (snippet: Snippet) => {
    if (!snippet.audioUrl) return;
    try {
      const res = await fetch(snippet.audioUrl);
      if (res.ok) {
        const blob = await res.blob();
        const blobUrl = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = blobUrl;
        link.download = `${snippet.bookTitle.replace(/\s+/g, '_')}_${snippet.timestamp}.mp3`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(blobUrl);
        return;
      }
    } catch (err) {
      console.warn('Direct blob audio download failed, falling back to direct link:', err);
    }
    const link = document.createElement('a');
    link.href = snippet.audioUrl;
    link.download = `${snippet.bookTitle.replace(/\s+/g, '_')}_${snippet.timestamp}.mp3`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  // Open Expand Modal with calculated current values
  const openExpandModal = (snippet: Snippet) => {
    setExpandSnippet(snippet);
    // Default pre-roll is 30s, post-roll is 60s (or based on snippet duration)
    setPreRoll(30);
    setPostRoll(Math.max(30, snippet.duration - 30));
    setExpandError(null);
    setExpandSuccess(null);
  };

  // Submit expansion to backend
  const handleExecuteExpand = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!expandSnippet) return;

    setIsExpanding(true);
    setExpandError(null);
    setExpandSuccess(null);

    try {
      const payload = {
        timestamp: expandSnippet.timestamp,
        currentTime: expandSnippet.currentTime ?? (expandSnippet.startTime + expandSnippet.duration / 2),
        preRoll,
        postRoll,
        libraryItemId: expandSnippet.libraryItemId,
        bookTitle: expandSnippet.bookTitle,
        token: activeToken,
        serverUrl,
      };

      const targetEndpoint = `${sidecarUrl.replace(/\/+$/, '')}/api/snippet/expand`;

      let res: Response;
      if (useProxy) {
        res = await fetch('/api/proxy/abs', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            targetUrl: targetEndpoint,
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'Authorization': `Bearer ${activeToken || ''}`,
              'X-ABS-Server-Url': serverUrl,
            },
            body: payload,
          }),
        });
      } else {
        res = await fetch(targetEndpoint, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${activeToken || ''}`,
            'X-ABS-Server-Url': serverUrl,
          },
          body: JSON.stringify(payload),
        });
      }

      if (useProxy) {
        const proxyJson = await res.json();
        if (!proxyJson.ok) {
          throw new Error(proxyJson.data?.detail || proxyJson.message || 'Failed to expand snippet');
        }
      } else if (!res.ok) {
        const errJson = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(errJson.detail || 'Failed to expand snippet');
      }

      setExpandSuccess('Snippet successfully updated and re-transcribed!');
      setTimeout(() => {
        setExpandSnippet(null);
        setExpandSuccess(null);
      }, 1200);

      if (onRefreshSnippets) {
        await onRefreshSnippets();
      }
    } catch (err: unknown) {
      setExpandError(err instanceof Error ? err.message : 'Error updating snippet');
    } finally {
      setIsExpanding(false);
    }
  };

  // Export all snippets from the book (either as a ZIP containing MP3s + MDs or as a single combined MD)
  const handleExportBook = async (bookTitle: string, format: 'zip' | 'markdown') => {
    setExportingBook(bookTitle);
    setExportError(null);

    try {
      const tokenParam = activeToken ? `&token=${encodeURIComponent(activeToken)}` : '';
      const serverParam = serverUrl ? `&server_url=${encodeURIComponent(serverUrl)}` : '';
      const endpoint = `/api/export-book?book_title=${encodeURIComponent(bookTitle)}&format=${format}${tokenParam}${serverParam}`;
      const headers: Record<string, string> = {};
      if (activeToken) {
        headers['Authorization'] = `Bearer ${activeToken}`;
      }
      headers['X-ABS-Server-Url'] = serverUrl;

      const res = await fetch(endpoint, { headers });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ error: `Export failed with status ${res.status}` }));
        throw new Error(err.detail || err.error || err.message || `Export failed with HTTP ${res.status}`);
      }

      const blob = await res.blob();
      const ext = format === 'zip' ? 'zip' : 'md';
      const safeName = bookTitle.replace(/[^a-zA-Z0-9_-]/g, '_');
      const filename = `${safeName}_All_Snippets.${ext}`;

      const blobUrl = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = blobUrl;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(blobUrl);
    } catch (err: unknown) {
      setExportError(err instanceof Error ? err.message : 'Failed to export book snippets');
      setTimeout(() => setExportError(null), 5000);
    } finally {
      setExportingBook(null);
    }
  };

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      
      {/* Header & Search Bar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-neutral-800 pb-4">
        <div>
          <h2 className="text-base font-semibold text-white tracking-tight uppercase">
            Saved Bookmarks & Transcripts
          </h2>
          <div className="flex flex-wrap items-center gap-2 mt-1">
            <span className="text-xs text-neutral-400 font-mono">
              {snippets.length} snippets
            </span>
            <span
              className="inline-flex items-center gap-1.5 px-2 py-0.5 bg-neutral-900 border border-neutral-800 text-[11px] text-neutral-300 font-mono rounded"
              title="Immutable installation cutoff: Bookmarks created prior to first installation date at 00:00:00 are preserved and ignored during sync."
            >
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>
              <span>Cutoff: {syncState?.installation_date ? `${syncState.installation_date} 00:00` : 'First install'}</span>
              {typeof syncState?.skipped_before_cutoff === 'number' && syncState.skipped_before_cutoff > 0 && (
                <span className="text-neutral-500 border-l border-neutral-700 pl-1.5 ml-0.5">
                  {syncState.skipped_before_cutoff} historical skipped
                </span>
              )}
            </span>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <div className="relative w-full sm:w-64">
            <Search className="w-3.5 h-3.5 text-neutral-400 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder="Search transcripts or books..."
              className="w-full bg-[#181818] border border-neutral-700 hover:border-neutral-500 focus:border-neutral-300 focus:bg-[#202020] pl-8 pr-3 py-1.5 text-xs text-white placeholder-neutral-500 focus:outline-none transition-colors font-mono"
            />
          </div>

          {onTriggerSync && (
            <button
              onClick={onTriggerSync}
              disabled={isTriggeringSync || syncState?.is_syncing}
              className="px-2.5 py-1.5 border border-neutral-700 bg-[#161616] hover:bg-[#222222] hover:border-neutral-500 text-xs text-neutral-200 transition-colors flex items-center gap-1.5 disabled:opacity-50"
              title="Sync bookmarks from server"
            >
              <RotateCw className={`w-3.5 h-3.5 ${isTriggeringSync || syncState?.is_syncing ? 'animate-spin' : ''}`} />
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

      {exportError && (
        <div className="p-3 bg-neutral-950 border border-red-800 text-[11px] text-red-400 flex items-center gap-1.5">
          <AlertTriangle className="w-3.5 h-3.5" />
          <span>{exportError}</span>
        </div>
      )}

      {syncState?.is_syncing && (
        <div className="p-2.5 bg-[#141414] border border-neutral-800 text-xs text-neutral-300 flex items-center justify-between gap-2 font-mono">
          <div className="flex items-center gap-2">
            <RotateCw className="w-3.5 h-3.5 text-emerald-400 animate-spin" />
            <span>Syncing bookmarks created on or after {syncState?.installation_date ? `${syncState.installation_date} 00:00` : 'installation'}...</span>
            {syncState?.current_item && (
              <span className="text-neutral-400 truncate max-w-xs">{syncState.current_item}</span>
            )}
          </div>
          {typeof syncState?.skipped_before_cutoff === 'number' && syncState.skipped_before_cutoff > 0 && (
            <span className="text-[11px] text-neutral-500 shrink-0">
              {syncState.skipped_before_cutoff} older bookmarks skipped
            </span>
          )}
        </div>
      )}

      {/* Sorting & Filter Controls Bar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-neutral-800 pb-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-neutral-400 font-mono flex items-center gap-1.5 mr-1">
            <ArrowUpDown className="w-3.5 h-3.5 text-neutral-400" />
            <span>Sort:</span>
          </span>

          <div className="inline-flex border border-neutral-700 bg-[#121212]">
            <button
              onClick={() => {
                if (sortField === 'date') {
                  setSortDirection((prev) => (prev === 'desc' ? 'asc' : 'desc'));
                } else {
                  setSortField('date');
                  setSortDirection('desc');
                }
              }}
              className={`px-3 py-1 text-xs font-mono flex items-center gap-1.5 transition-colors ${
                sortField === 'date'
                  ? 'bg-neutral-200 text-black font-semibold'
                  : 'text-neutral-300 hover:text-white hover:bg-[#1a1a1a]'
              }`}
              title="Sort by Date (click to toggle ascending/descending)"
            >
              <Calendar className="w-3.5 h-3.5" />
              <span>Date</span>
              {sortField === 'date' && (
                <span className="text-[10px] ml-0.5 opacity-75">
                  ({sortDirection === 'desc' ? 'Newest' : 'Oldest'})
                </span>
              )}
            </button>

            <button
              onClick={() => {
                if (sortField === 'book') {
                  setSortDirection((prev) => (prev === 'asc' ? 'desc' : 'asc'));
                } else {
                  setSortField('book');
                  setSortDirection('asc');
                }
              }}
              className={`px-3 py-1 text-xs font-mono flex items-center gap-1.5 transition-colors border-l border-neutral-700 ${
                sortField === 'book'
                  ? 'bg-neutral-200 text-black font-semibold'
                  : 'text-neutral-300 hover:text-white hover:bg-[#1a1a1a]'
              }`}
              title="Sort by Book Title (click to toggle A-Z / Z-A)"
            >
              <BookOpen className="w-3.5 h-3.5" />
              <span>Book</span>
              {sortField === 'book' && (
                <span className="text-[10px] ml-0.5 opacity-75">
                  ({sortDirection === 'asc' ? 'A→Z' : 'Z→A'})
                </span>
              )}
            </button>
          </div>

          <button
            onClick={() => setSortDirection((prev) => (prev === 'asc' ? 'desc' : 'asc'))}
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
          Showing {sortedSnippets.length} {sortedSnippets.length === 1 ? 'snippet' : 'snippets'}
        </div>
      </div>

      {/* Book Filter Chips (When multiple books exist) */}
      {uniqueBooks.length > 1 && (
        <div className="flex flex-wrap items-center gap-2 pt-1 pb-1">
          <button
            onClick={() => setSelectedBookFilter(null)}
            className={`px-2.5 py-1 text-xs font-mono transition-colors border ${
              selectedBookFilter === null
                ? 'bg-neutral-200 text-black border-neutral-200 font-semibold'
                : 'bg-[#141414] text-neutral-300 border-neutral-700 hover:border-neutral-500'
            }`}
          >
            All Books ({snippets.length})
          </button>
          {uniqueBooks.map((bTitle) => {
            const count = snippets.filter((s) => s.bookTitle === bTitle).length;
            const isSelected = selectedBookFilter === bTitle;
            return (
              <button
                key={bTitle}
                onClick={() => setSelectedBookFilter(isSelected ? null : bTitle)}
                className={`px-2.5 py-1 text-xs font-mono transition-colors border truncate max-w-[260px] ${
                  isSelected
                    ? 'bg-neutral-200 text-black border-neutral-200 font-semibold'
                    : 'bg-[#141414] text-neutral-300 border-neutral-700 hover:border-neutral-500'
                }`}
                title={bTitle}
              >
                {bTitle} ({count})
              </button>
            );
          })}
        </div>
      )}

      {/* Snippet List */}
      {sortedSnippets.length === 0 ? (
        <div className="border border-neutral-700 bg-[#0d0d0d] p-12 text-center space-y-4">
          <div className="text-neutral-400 text-sm font-mono">
            {searchTerm || selectedBookFilter ? 'No matching snippets found.' : 'No snippets captured yet.'}
          </div>
          <button
            onClick={onNavigateToCapture}
            className="px-4 py-2 border border-neutral-700 bg-[#161616] text-xs text-neutral-200 hover:text-white hover:border-neutral-500 transition-colors"
          >
            Go to Capture Screen →
          </button>
        </div>
      ) : (
        <div className="space-y-4">
          {sortedSnippets.map((snippet) => {
            const isAudioAvailable = Boolean(snippet.audioUrl && snippet.duration > 0 && snippet.extractionStatus !== 'unavailable');

            return (
              <article
                key={snippet.id}
                className="border border-neutral-700 bg-[#0d0d0d] p-5 space-y-4"
              >
                {/* Snippet Header */}
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
                        @{snippet.username || user?.username || 'user'}
                      </span>
                      <span className="text-[10px] text-neutral-500 hidden sm:inline font-mono">
                        {snippet.username || user?.username || 'user'}/bookmarks/
                      </span>
                    </div>
                  </div>

                  <div className="flex items-center gap-3 text-xs text-neutral-400 font-mono self-end sm:self-auto">
                    <span>Start: {isAudioAvailable ? `${Math.round(snippet.startTime)}s` : (snippet.startTime > 0 ? `${Math.round(snippet.startTime)}s` : 'N/A')}</span>
                    <span>Duration: {isAudioAvailable ? `${snippet.duration}s` : 'N/A'}</span>
                    <span>{new Date(snippet.createdAt).toLocaleDateString()}</span>
                  </div>
                </div>

                {/* Audio Player or Unavailable Banner */}
                {isAudioAvailable ? (
                  <div className="bg-[#141414] p-2.5 border border-neutral-700">
                    <audio
                      key={`${snippet.id}-${snippet.duration}-${snippet.audioUrl}`}
                      controls
                      preload="metadata"
                      src={snippet.audioUrl}
                      className="w-full h-8 bg-[#181818]"
                    />
                  </div>
                ) : (
                  <div className="bg-[#141414] p-3 border border-amber-900/40 flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                    <div className="flex items-start sm:items-center gap-2 text-xs text-amber-300 font-mono">
                      <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5 sm:mt-0" />
                      <span>This bookmark was found in the library, but could not be extracted and transcribed (source file moved, deleted, or unmounted).</span>
                    </div>
                    <span className="text-[10px] uppercase font-mono px-2 py-0.5 bg-neutral-900 text-amber-400/90 border border-amber-800/60 shrink-0 self-start sm:self-auto">
                      Audio N/A
                    </span>
                  </div>
                )}

                {/* Transcript Text Box */}
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between text-xs text-neutral-400">
                    <span className="font-mono text-[11px] uppercase">
                      {isAudioAvailable ? 'Whisper Transcript' : 'Bookmark Details & Status'}
                    </span>
                    <div className="flex items-center gap-3">
                      {isAudioAvailable && (
                        <button
                          onClick={() => handleCopyCitation(snippet)}
                          title="Copy formatted quote citation with book title, author, and timestamp"
                          className="flex items-center gap-1 text-neutral-400 hover:text-white transition-colors text-[11px]"
                        >
                          {copiedCitationId === snippet.id ? (
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
                        onClick={() => handleCopyTranscript(snippet.id, snippet.transcript)}
                        className="flex items-center gap-1 text-neutral-300 hover:text-white transition-colors"
                      >
                        {copiedId === snippet.id ? (
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

                {/* Action Toolbar */}
                <div className="flex flex-wrap items-center justify-between gap-2 pt-2 border-t border-neutral-800 text-xs">
                  <div className="flex flex-wrap items-center gap-2">
                    {isAudioAvailable ? (
                      <button
                        onClick={() => handleDownloadAudio(snippet)}
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
                      onClick={() => handleDownloadMarkdown(snippet)}
                      className="flex items-center gap-1.5 px-3 py-1.5 border border-neutral-700 bg-[#161616] hover:border-neutral-500 text-neutral-200 hover:text-white transition-colors"
                    >
                      <FileText className="w-3.5 h-3.5" />
                      <span>Download .MD</span>
                    </button>

                    {/* Expand / Adjust Snippet Context Button */}
                    {isAudioAvailable ? (
                      <button
                        onClick={() => openExpandModal(snippet)}
                        className="flex items-center gap-1.5 px-3 py-1.5 border border-neutral-700 bg-[#1a1a1a] hover:border-neutral-400 text-neutral-100 hover:text-white transition-colors"
                        title="Adjust pre-roll & post-roll to expand snippet context"
                      >
                        <Sliders className="w-3.5 h-3.5 text-neutral-300" />
                        <span>Adjust Duration / Context</span>
                      </button>
                    ) : (
                      <div
                        className="flex items-center gap-1.5 px-3 py-1.5 border border-neutral-800 bg-[#141414] text-neutral-500 cursor-not-allowed opacity-50 font-mono"
                        title="Cannot adjust duration: audio source is not accessible"
                      >
                        <Sliders className="w-3.5 h-3.5 text-neutral-600" />
                        <span>Adjust (N/A)</span>
                      </div>
                    )}

                    {/* Export all from this book Dropdown */}
                    <div className="relative">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setOpenExportDropdownId(openExportDropdownId === snippet.id ? null : snippet.id);
                        }}
                        disabled={exportingBook === snippet.bookTitle}
                        className="flex items-center gap-1.5 px-3 py-1.5 border border-neutral-700 bg-[#161616] hover:border-neutral-400 text-neutral-200 hover:text-white transition-colors disabled:opacity-50"
                        title="Export all snippets from this book"
                      >
                        <Archive className="w-3.5 h-3.5 text-neutral-300" />
                        <span>
                          {exportingBook === snippet.bookTitle ? 'Exporting Book...' : 'Export all from this book'}
                        </span>
                        <ChevronDown
                          className={`w-3 h-3 text-neutral-400 transition-transform ${
                            openExportDropdownId === snippet.id ? 'rotate-180' : ''
                          }`}
                        />
                      </button>

                      {openExportDropdownId === snippet.id && (
                        <div
                          className="absolute left-0 mt-1 w-52 bg-[#181818] border border-neutral-700 shadow-xl z-20 font-mono py-1"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <div className="px-3 py-1.5 text-[10px] text-neutral-400 border-b border-neutral-800 uppercase tracking-wider truncate">
                            {snippet.bookTitle}
                          </div>
                          <button
                            onClick={() => {
                              setOpenExportDropdownId(null);
                              handleExportBook(snippet.bookTitle, 'zip');
                            }}
                            className="w-full text-left px-3 py-2 text-xs text-neutral-200 hover:text-white hover:bg-[#222222] flex items-center justify-between transition-colors"
                          >
                            <span className="flex items-center gap-2">
                              <Archive className="w-3.5 h-3.5 text-neutral-400" />
                              <span>As Zip</span>
                            </span>
                            <span className="text-[10px] text-neutral-500 font-mono">.zip</span>
                          </button>
                          <button
                            onClick={() => {
                              setOpenExportDropdownId(null);
                              handleExportBook(snippet.bookTitle, 'markdown');
                            }}
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
                      onClick={() => onDeleteSnippet(snippet.id)}
                      className="text-neutral-400 hover:text-red-400 flex items-center gap-1 transition-colors"
                      title="Delete snippet"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                      <span>Delete</span>
                    </button>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      )}

      {/* Expand / Adjust Snippet Duration Modal */}
      {expandSnippet && (
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-[#0e0e0e] border border-neutral-700 w-full max-w-md p-5 space-y-4 font-mono shadow-2xl relative">
            
            {/* Modal Header */}
            <div className="flex items-start justify-between border-b border-neutral-800 pb-3">
              <div>
                <h3 className="text-sm font-semibold text-white uppercase tracking-tight flex items-center gap-2">
                  <Sliders className="w-4 h-4 text-neutral-300" />
                  <span>Expand Snippet Context</span>
                </h3>
                <p className="text-xs text-neutral-400 mt-1 truncate max-w-xs">
                  {expandSnippet.bookTitle}
                </p>
              </div>
              <button
                onClick={() => !isExpanding && setExpandSnippet(null)}
                className="text-neutral-400 hover:text-white p-1"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Adjustment Form */}
            <form onSubmit={handleExecuteExpand} className="space-y-4 text-xs">
              <div>
                <label className="block text-neutral-300 mb-1.5">
                  Pre-Roll: <span className="text-white font-semibold">{preRoll}s</span> before bookmark anchor
                </label>
                <div className="flex items-center gap-3">
                  <input
                    type="range"
                    min="5"
                    max="180"
                    step="5"
                    value={preRoll}
                    onChange={(e) => setPreRoll(Number(e.target.value))}
                    className="w-full accent-neutral-200 cursor-pointer"
                  />
                  <input
                    type="number"
                    min="5"
                    max="300"
                    value={preRoll}
                    onChange={(e) => setPreRoll(Math.max(5, Number(e.target.value)))}
                    className="w-16 bg-[#161616] border border-neutral-700 px-2 py-1 text-white text-center"
                  />
                </div>
              </div>

              <div>
                <label className="block text-neutral-300 mb-1.5">
                  Post-Roll: <span className="text-white font-semibold">{postRoll}s</span> after bookmark anchor
                </label>
                <div className="flex items-center gap-3">
                  <input
                    type="range"
                    min="10"
                    max="300"
                    step="5"
                    value={postRoll}
                    onChange={(e) => setPostRoll(Number(e.target.value))}
                    className="w-full accent-neutral-200 cursor-pointer"
                  />
                  <input
                    type="number"
                    min="10"
                    max="600"
                    value={postRoll}
                    onChange={(e) => setPostRoll(Math.max(10, Number(e.target.value)))}
                    className="w-16 bg-[#161616] border border-neutral-700 px-2 py-1 text-white text-center"
                  />
                </div>
              </div>

              {/* Total Calculation Display */}
              <div className="p-2.5 bg-[#141414] border border-neutral-800 flex items-center justify-between text-[11px]">
                <span className="text-neutral-400">Total New Snippet Duration:</span>
                <span className="text-white font-semibold font-mono">
                  {preRoll + postRoll} seconds ({(preRoll + postRoll) / 60 >= 1 ? `${((preRoll + postRoll) / 60).toFixed(1)} min` : ''})
                </span>
              </div>

              {expandError && (
                <div className="p-2.5 bg-red-950/40 border border-red-800 text-red-300 text-xs">
                  {expandError}
                </div>
              )}

              {expandSuccess && (
                <div className="p-2.5 bg-emerald-950/40 border border-emerald-800 text-emerald-300 text-xs">
                  {expandSuccess}
                </div>
              )}

              {/* Action Buttons */}
              <div className="pt-3 border-t border-neutral-800 flex items-center justify-end gap-2.5">
                <button
                  type="button"
                  onClick={() => setExpandSnippet(null)}
                  disabled={isExpanding}
                  className="px-3 py-1.5 border border-neutral-700 hover:border-neutral-500 text-neutral-300 hover:text-white transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={isExpanding}
                  className="px-4 py-1.5 bg-neutral-100 text-black hover:bg-white font-semibold flex items-center gap-2 transition-colors disabled:opacity-50"
                >
                  {isExpanding ? (
                    <>
                      <RotateCw className="w-3.5 h-3.5 animate-spin" />
                      <span>Re-clipping & Transcribing...</span>
                    </>
                  ) : (
                    <>
                      <Sliders className="w-3.5 h-3.5" />
                      <span>Update & Replace Snippet</span>
                    </>
                  )}
                </button>
              </div>
            </form>

          </div>
        </div>
      )}

    </div>
  );
};
