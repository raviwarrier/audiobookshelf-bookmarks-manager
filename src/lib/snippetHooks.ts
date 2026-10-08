import React, { useState, useEffect, useMemo, FormEvent } from 'react';
import { Snippet, SyncState, CutoffMode } from '../types';
import { safeSidecarFetch } from './safeFetch';
import { formatToSlashDate, normalizeToIsoDate } from '../components/CutoffModal';

function isValidTimestampDateChars(str: string): boolean {
  for (let i = 0; i < 15; i++) {
    const c = str.codePointAt(i);
    if (c === undefined) return false;
    if (i === 8) {
      if (c !== 95) return false;
    } else if (c < 48 || c > 57) {
      return false;
    }
  }
  return true;
}

export function parseTimestampDate(timestampStr: string): number | null {
  if (typeof timestampStr !== 'string' || timestampStr.length < 15) return null;
  if (!isValidTimestampDateChars(timestampStr)) return null;
  const y = timestampStr.slice(0, 4);
  const m = timestampStr.slice(4, 6);
  const d = timestampStr.slice(6, 8);
  const hr = timestampStr.slice(9, 11);
  const min = timestampStr.slice(11, 13);
  const sec = timestampStr.slice(13, 15);
  const dateObj = new Date(`${y}-${m}-${d}T${hr}:${min}:${sec}`);
  const t = dateObj.getTime();
  return Number.isNaN(t) ? null : t;
}

export function getSnippetTime(s: Snippet): number {
  if (typeof s.createdAt === 'number' && !Number.isNaN(s.createdAt) && s.createdAt > 0) {
    return s.createdAt;
  }
  if (!s.timestamp || typeof s.timestamp !== 'string') return 0;

  const parsed = parseTimestampDate(s.timestamp);
  if (parsed !== null && !Number.isNaN(parsed)) {
    return parsed;
  }

  const d = new Date(s.timestamp);
  const time = d.getTime();
  return !Number.isNaN(time) ? time : 0;
}

export function useSnippetSorting() {
  const [sortField, setSortField] = useState<'date' | 'book'>('date');
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('desc');

  const handleSelectDateSort = () => {
    setSortDirection(sortField === 'date' && sortDirection === 'desc' ? 'asc' : 'desc');
    setSortField('date');
  };

  const handleSelectBookSort = () => {
    setSortDirection(sortField === 'book' && sortDirection === 'asc' ? 'desc' : 'asc');
    setSortField('book');
  };

  const handleToggleDirection = () => {
    setSortDirection((prev) => (prev === 'asc' ? 'desc' : 'asc'));
  };

  return {
    sortField,
    sortDirection,
    handleSelectDateSort,
    handleSelectBookSort,
    handleToggleDirection
  };
}

function matchesSnippetSearch(s: Snippet, term: string): boolean {
  if (!term) return true;
  const searchableText = `${s.bookTitle || ''} ${s.author || ''} ${s.chapterName || ''} ${s.transcript || ''}`.toLowerCase();
  return searchableText.includes(term);
}

function matchesSnippetBook(s: Snippet, selectedBookFilter: string | null, activeBook: string): boolean {
  if (!activeBook) return true;
  const sBook = (s.bookTitle || '').toLowerCase();
  if (selectedBookFilter) {
    return sBook === selectedBookFilter.toLowerCase();
  }
  return sBook.includes(activeBook);
}

function compareSnippets(
  a: Snippet,
  b: Snippet,
  sortField: 'date' | 'book',
  sortDirection: 'asc' | 'desc'
): number {
  if (sortField === 'date') {
    const timeA = getSnippetTime(a);
    const timeB = getSnippetTime(b);
    if (timeA !== timeB) {
      return sortDirection === 'desc' ? timeB - timeA : timeA - timeB;
    }
    return a.bookTitle.localeCompare(b.bookTitle);
  }
  const cmp = a.bookTitle.localeCompare(b.bookTitle, undefined, { sensitivity: 'base' });
  if (cmp !== 0) {
    return sortDirection === 'asc' ? cmp : -cmp;
  }
  return getSnippetTime(b) - getSnippetTime(a);
}

export function useSnippetFiltering(
  snippets: Snippet[],
  searchTerm: string,
  selectedBookFilter: string | null,
  bookFilterQuery: string,
  sortField: 'date' | 'book',
  sortDirection: 'asc' | 'desc'
) {
  const uniqueBooks = useMemo(() => {
    return (
      Array.from(new Set(snippets.map((s) => s.bookTitle).filter(Boolean))) as string[]
    ).sort((a, b) => a.localeCompare(b));
  }, [snippets]);

  const matchingBooks = useMemo(() => {
    const query = bookFilterQuery.trim().toLowerCase();
    return uniqueBooks.filter((b) => b.toLowerCase().includes(query));
  }, [uniqueBooks, bookFilterQuery]);

  const filteredSnippets = useMemo(() => {
    const term = searchTerm.trim().toLowerCase();
    const activeBook = (selectedBookFilter || bookFilterQuery).trim().toLowerCase();

    return snippets.filter(
      (s) => matchesSnippetSearch(s, term) && matchesSnippetBook(s, selectedBookFilter, activeBook)
    );
  }, [snippets, searchTerm, selectedBookFilter, bookFilterQuery]);

  const sortedSnippets = useMemo(() => {
    return [...filteredSnippets].sort((a, b) => compareSnippets(a, b, sortField, sortDirection));
  }, [filteredSnippets, sortField, sortDirection]);

  return { uniqueBooks, matchingBooks, filteredSnippets, sortedSnippets };
}

function validateAndFormatCutoffDate(selectedCutoffMode: CutoffMode, customDateInput: string): string | undefined {
  if (selectedCutoffMode !== 'custom_date') return undefined;

  const iso = normalizeToIsoDate(customDateInput);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(iso)) {
    throw new TypeError('Please enter a valid date in yyyy/mm/dd (e.g. 2026/09/24 or 26/09/24).');
  }
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) {
    throw new TypeError('Invalid calendar date specified.');
  }
  return iso;
}

export async function submitCutoffConfigApi(
  selectedCutoffMode: CutoffMode,
  customDateInput: string,
  activeToken?: string | null,
  serverUrl?: string,
  sidecarUrl?: string,
  useProxy?: boolean
) {
  const formattedDate = validateAndFormatCutoffDate(selectedCutoffMode, customDateInput);
  const payload = { cutoff_mode: selectedCutoffMode, custom_date: formattedDate };

  const res = await safeSidecarFetch('/api/cutoff-config', {
    method: 'POST',
    token: activeToken,
    serverUrl,
    sidecarUrl,
    useProxy,
    body: payload,
  });

  if (!res.ok || !res.data || (!res.data.status && !res.data.config)) {
    const err = res.data?.detail || res.data?.error || res.data?.message || `Failed with status ${res.status}`;
    throw new Error(typeof err === 'string' ? err : 'Failed to update cutoff configuration');
  }

  return res.data;
}

export function buildUpdatedSyncState(prev: SyncState | null | undefined, config: any): SyncState {
  const base = prev ?? {
    is_syncing: false,
    last_synced_at: null,
    total_synced: 0,
    current_item: null,
    skipped_before_cutoff: 0,
    skipped_tombstoned: 0,
  };
  return {
    ...base,
    last_error: null,
    installation_date: config.installation_date,
    cutoff_datetime: config.cutoff_datetime,
    cutoff_mode: config.cutoff_mode,
    custom_cutoff_date: config.custom_date,
    installed_at: config.installed_at,
  };
}

export function formatCitationText(snippet: Snippet): string {
  const mins = Math.floor(snippet.startTime / 60);
  const secs = Math.floor(snippet.startTime % 60);
  const timeFormatted = `${mins}:${secs.toString().padStart(2, '0')}`;
  return `> "${snippet.transcript.trim()}"\n\n— *${snippet.bookTitle}* by ${snippet.author} (${snippet.chapterName}, offset ${timeFormatted})`;
}

export function downloadSnippetMarkdown(snippet: Snippet): void {
  const blob = new Blob([snippet.markdownContent], { type: 'text/markdown;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `${snippet.bookTitle.replace(/\s+/g, '_')}_${snippet.timestamp}.md`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export async function downloadSnippetAudioFile(snippet: Snippet): Promise<void> {
  if (!snippet.audioUrl) return;
  const filename = `${snippet.bookTitle.replace(/\s+/g, '_')}_${snippet.timestamp}.mp3`;
  const downloaded = await downloadSnippetBlob(snippet.audioUrl, filename);
  if (!downloaded) {
    const link = document.createElement('a');
    link.href = snippet.audioUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
  }
}

export function isExtractionUnavailable(result: any): boolean {
  if (!result) return false;
  return result.extraction_status === 'unavailable' || result.status === 'unextractable_saved';
}

export function getUnavailableMessage(result: any, fallback: string): string {
  if (!result) return fallback;
  return result.snippet?.raw_transcript || result.message || fallback;
}

export function useCutoffConfigManager(
  syncState: SyncState | null | undefined,
  activeToken: string | null | undefined,
  serverUrl: string,
  sidecarUrl: string,
  useProxy: boolean,
  onCutoffUpdated?: (newSyncState: SyncState) => void,
  onTriggerSync?: () => Promise<void>
) {
  const [isCutoffModalOpen, setIsCutoffModalOpen] = useState<boolean>(false);
  const [selectedCutoffMode, setSelectedCutoffMode] = useState<CutoffMode>(
    syncState?.cutoff_mode || 'from_now'
  );
  const [customDateInput, setCustomDateInput] = useState<string>(() =>
    formatToSlashDate(
      syncState?.custom_cutoff_date || syncState?.installation_date || new Date().toISOString().slice(0, 10)
    )
  );
  const [isSavingCutoff, setIsSavingCutoff] = useState<boolean>(false);
  const [cutoffSaveError, setCutoffSaveError] = useState<string | null>(null);
  const [cutoffSaveSuccess, setCutoffSaveSuccess] = useState<string | null>(null);

  useEffect(() => {
    if (syncState?.cutoff_mode) {
      setSelectedCutoffMode(syncState.cutoff_mode);
    }
    if (syncState?.custom_cutoff_date) {
      setCustomDateInput(formatToSlashDate(syncState.custom_cutoff_date));
    } else if (syncState?.installation_date) {
      setCustomDateInput((prev) => prev || formatToSlashDate(syncState.installation_date));
    }
  }, [syncState?.cutoff_mode, syncState?.custom_cutoff_date, syncState?.installation_date]);

  const handleSaveCutoffConfig = async (e: FormEvent) => {
    e.preventDefault();
    setIsSavingCutoff(true);
    setCutoffSaveError(null);
    setCutoffSaveSuccess(null);

    try {
      const data = await submitCutoffConfigApi(
        selectedCutoffMode,
        customDateInput,
        activeToken,
        serverUrl,
        sidecarUrl,
        useProxy
      );

      setCutoffSaveSuccess('Cutoff period updated successfully!');
      if (data?.config && onCutoffUpdated) {
        onCutoffUpdated(buildUpdatedSyncState(syncState, data.config));
      }

      if (onTriggerSync) {
        setTimeout(onTriggerSync, 500);
      }

      setTimeout(() => {
        setIsCutoffModalOpen(false);
        setCutoffSaveSuccess(null);
      }, 1200);
    } catch (err: unknown) {
      setCutoffSaveError(err instanceof Error ? err.message : 'Error updating cutoff configuration');
    } finally {
      setIsSavingCutoff(false);
    }
  };

  return {
    isCutoffModalOpen,
    setIsCutoffModalOpen,
    selectedCutoffMode,
    setSelectedCutoffMode,
    customDateInput,
    setCustomDateInput,
    isSavingCutoff,
    cutoffSaveError,
    cutoffSaveSuccess,
    handleSaveCutoffConfig
  };
}

export async function downloadSnippetBlob(audioUrl: string, filename: string): Promise<boolean> {
  try {
    const res = await fetch(audioUrl);
    if (!res.ok) return false;
    const blob = await res.blob();
    const blobUrl = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = blobUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(blobUrl);
    return true;
  } catch {
    return false;
  }
}

export async function exportBookArchive(
  bookTitle: string,
  format: 'zip' | 'markdown',
  activeToken?: string | null,
  sidecarUrl?: string,
  useProxy?: boolean
): Promise<void> {
  const queryParams = new URLSearchParams({
    book_title: bookTitle,
    format,
    ...(activeToken ? { token: activeToken } : {}),
  });

  const endpoint = `/api/export-book?${queryParams.toString()}`;

  const res = await fetch(endpoint, {
    headers: activeToken ? { Authorization: `Bearer ${activeToken}` } : {},
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
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
  link.remove();
  URL.revokeObjectURL(blobUrl);
}

export async function requestSnippetRetryApi(
  snippet: Snippet,
  customPreRoll: number,
  customPostRoll: number,
  activeToken?: string | null,
  serverUrl?: string,
  sidecarUrl?: string,
  useProxy?: boolean
) {
  const payload = {
    timestamp: snippet.timestamp,
    currentTime: snippet.currentTime ?? snippet.startTime,
    preRoll: customPreRoll,
    postRoll: customPostRoll,
    libraryItemId: snippet.libraryItemId,
    bookTitle: snippet.bookTitle,
    token: activeToken,
    serverUrl,
  };

  const res = await safeSidecarFetch('/api/snippet/retry', {
    method: 'POST',
    token: activeToken,
    serverUrl,
    sidecarUrl,
    useProxy,
    body: payload,
  });

  return res.data?.data || res.data;
}

export async function requestSnippetExpandApi(
  expandSnippet: Snippet,
  preRoll: number,
  postRoll: number,
  activeToken?: string | null,
  serverUrl?: string,
  sidecarUrl?: string,
  useProxy?: boolean
) {
  const anchorTime = expandSnippet.currentTime ?? (
    expandSnippet.startTime + (expandSnippet.duration > 0 ? expandSnippet.duration / 2 : 0)
  );

  const payload = {
    timestamp: expandSnippet.timestamp,
    currentTime: anchorTime,
    preRoll,
    postRoll,
    libraryItemId: expandSnippet.libraryItemId,
    bookTitle: expandSnippet.bookTitle,
    token: activeToken,
    serverUrl,
  };

  const res = await safeSidecarFetch('/api/snippet/expand', {
    method: 'POST',
    token: activeToken,
    serverUrl,
    sidecarUrl,
    useProxy,
    body: payload,
  });

  return res.data?.data || res.data;
}

export function calculateDefaultPostRoll(duration: number): number {
  return duration > 0 ? Math.max(15, duration - 30) : 30;
}

export function getErrorMessage(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

export function useSnippetOperations(
  activeToken: string | null | undefined,
  serverUrl: string,
  sidecarUrl: string,
  useProxy: boolean,
  onRefreshSnippets?: () => Promise<void>
) {
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [copiedCitationId, setCopiedCitationId] = useState<string | null>(null);
  const [expandSnippet, setExpandSnippet] = useState<Snippet | null>(null);
  const [preRoll, setPreRoll] = useState<number>(30);
  const [postRoll, setPostRoll] = useState<number>(30);
  const [isExpanding, setIsExpanding] = useState<boolean>(false);
  const [expandError, setExpandError] = useState<string | null>(null);
  const [expandSuccess, setExpandSuccess] = useState<string | null>(null);
  const [retryingSnippetId, setRetryingSnippetId] = useState<string | null>(null);
  const [retryNotification, setRetryNotification] = useState<{ id: string; type: 'success' | 'error'; message: string } | null>(null);
  const [exportingBook, setExportingBook] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const [openExportDropdownId, setOpenExportDropdownId] = useState<string | null>(null);

  useEffect(() => {
    if (!openExportDropdownId) return;
    const handleGlobalClick = () => setOpenExportDropdownId(null);
    window.addEventListener('click', handleGlobalClick);
    return () => window.removeEventListener('click', handleGlobalClick);
  }, [openExportDropdownId]);

  const handleCopyTranscript = (id: string, text: string) => {
    void navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const handleCopyCitation = (snippet: Snippet) => {
    void navigator.clipboard.writeText(formatCitationText(snippet));
    setCopiedCitationId(snippet.id);
    setTimeout(() => setCopiedCitationId(null), 2500);
  };

  const handleDownloadMarkdown = (snippet: Snippet) => {
    downloadSnippetMarkdown(snippet);
  };

  const handleDownloadAudio = async (snippet: Snippet) => {
    await downloadSnippetAudioFile(snippet);
  };

  const openExpandModal = (snippet: Snippet) => {
    setExpandSnippet(snippet);
    setPreRoll(30);
    setPostRoll(calculateDefaultPostRoll(snippet.duration));
    setExpandError(null);
    setExpandSuccess(null);
  };

  const handleRetryExtraction = async (snippet: Snippet, customPreRoll = 30, customPostRoll = 30) => {
    setRetryingSnippetId(snippet.id);
    setRetryNotification(null);

    try {
      const json = await requestSnippetRetryApi(
        snippet,
        customPreRoll,
        customPostRoll,
        activeToken,
        serverUrl,
        sidecarUrl,
        useProxy
      );

      if (isExtractionUnavailable(json)) {
        const reason = getUnavailableMessage(json, 'Source audio could not be resolved.');
        setRetryNotification({
          id: snippet.id,
          type: 'error',
          message: `Retry completed, but source audio was still unreachable: ${reason}`,
        });
      } else {
        setRetryNotification({
          id: snippet.id,
          type: 'success',
          message: 'Audio extracted and transcribed successfully!',
        });
      }

      if (onRefreshSnippets) {
        await onRefreshSnippets();
      }
    } catch (err: unknown) {
      setRetryNotification({
        id: snippet.id,
        type: 'error',
        message: getErrorMessage(err, 'Error retrying snippet extraction'),
      });
    } finally {
      setRetryingSnippetId(null);
    }
  };

  const handleExecuteExpand = async (e: FormEvent) => {
    e.preventDefault();
    if (!expandSnippet) return;

    setIsExpanding(true);
    setExpandError(null);
    setExpandSuccess(null);

    try {
      const jsonResult = await requestSnippetExpandApi(
        expandSnippet,
        preRoll,
        postRoll,
        activeToken,
        serverUrl,
        sidecarUrl,
        useProxy
      );

      if (isExtractionUnavailable(jsonResult)) {
        const fallbackMsg = getUnavailableMessage(jsonResult, 'Extraction incomplete');
        setExpandError(`Extraction finished but audio unavailable: ${fallbackMsg}`);
      } else {
        setExpandSuccess('Snippet re-clipped and transcribed successfully!');
      }

      if (onRefreshSnippets) {
        await onRefreshSnippets();
      }

      setTimeout(() => {
        setExpandSnippet(null);
        setExpandSuccess(null);
        setExpandError(null);
      }, 1500);
    } catch (err: unknown) {
      setExpandError(getErrorMessage(err, 'Failed to expand snippet context'));
    } finally {
      setIsExpanding(false);
    }
  };

  const handleExportBook = async (bookTitle: string, format: 'zip' | 'markdown') => {
    setExportingBook(bookTitle);
    setExportError(null);

    try {
      await exportBookArchive(bookTitle, format, activeToken, sidecarUrl, useProxy);
    } catch (err: unknown) {
      setExportError(getErrorMessage(err, 'Failed to export book snippets'));
      setTimeout(() => setExportError(null), 5000);
    } finally {
      setExportingBook(null);
    }
  };

  return {
    copiedId,
    copiedCitationId,
    expandSnippet,
    setExpandSnippet,
    preRoll,
    setPreRoll,
    postRoll,
    setPostRoll,
    isExpanding,
    expandError,
    expandSuccess,
    retryingSnippetId,
    retryNotification,
    setRetryNotification,
    exportingBook,
    exportError,
    setExportError,
    openExportDropdownId,
    setOpenExportDropdownId,
    handleCopyTranscript,
    handleCopyCitation,
    handleDownloadMarkdown,
    handleDownloadAudio,
    openExpandModal,
    handleRetryExtraction,
    handleExecuteExpand,
    handleExportBook,
  };
}
