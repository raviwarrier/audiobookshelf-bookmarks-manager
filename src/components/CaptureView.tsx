import React, { useState } from 'react';
import { 
  Radio, 
  Play, 
  RotateCw, 
  CheckCircle2, 
  AlertCircle,
  ExternalLink,
  BookmarkPlus,
  User as UserIcon,
  Sliders,
  Sparkles
} from 'lucide-react';
import { AbsActiveSession, AbsUser, Snippet } from '../types';
import { createAbsBookmark, formatAuthors, getPlayableAudioUrl } from '../lib/absClient';

interface CaptureViewProps {
  user: AbsUser | null;
  activeToken: string | null;
  serverUrl: string;
  sidecarUrl: string;
  useProxy: boolean;
  session: AbsActiveSession | null;
  isLoadingSession: boolean;
  sessionError: string | null;
  onRefreshSession: () => Promise<void>;
  onOpenAuthModal: () => void;
  onSnippetCreated: (snippet: Snippet) => void;
  onNavigateToLibrary: () => void;
  onUseMockSession?: () => void;
}

// Helper to format bookmarked duration into HH:MM:SS (SSSS seconds)
function formatBookmarkedDuration(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const hrs = Math.floor(total / 3600);
  const mins = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  return `${String(hrs).padStart(2, '0')}:${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')} (${total} seconds)`;
}

export const CaptureView: React.FC<CaptureViewProps> = ({
  user,
  activeToken,
  serverUrl,
  sidecarUrl,
  useProxy,
  session,
  isLoadingSession,
  sessionError,
  onRefreshSession,
  onOpenAuthModal,
  onSnippetCreated,
  onNavigateToLibrary,
  onUseMockSession,
}) => {
  // Snippet Options
  const [duration, setDuration] = useState<number>(60);
  const [customOffset, setCustomOffset] = useState<number | null>(null);

  // Execution states
  const [isExtracting, setIsExtracting] = useState(false);
  const [currentStep, setCurrentStep] = useState<string>('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [lastSnippet, setLastSnippet] = useState<Snippet | null>(null);

  // Native ABS Bookmark creation
  const [isSavingAbsBookmark, setIsSavingAbsBookmark] = useState(false);
  const [absBookmarkSuccess, setAbsBookmarkSuccess] = useState<string | null>(null);

  // Trigger Snippet Extraction & Speech Transcription on the FastAPI Sidecar
  const handleCreateSnippet = async (explicitOffset?: number, explicitDuration?: number) => {
    if (!session) {
      setErrorMsg('No active audiobook session found. Connect to Audiobookshelf first.');
      return;
    }

    if (!user || !activeToken) {
      setErrorMsg('Authentication token is missing. Please re-authenticate.');
      onOpenAuthModal();
      return;
    }

    setErrorMsg(null);
    setIsExtracting(true);

    const activeOffset = explicitOffset !== undefined ? explicitOffset : customOffset;
    const computedStart = activeOffset !== null && activeOffset !== undefined
      ? Math.max(0, activeOffset) 
      : Math.max(0, session.currentTime - 30);
    const snipDuration = explicitDuration || duration;

    try {
      setCurrentStep('Connecting to sidecar POST /api/snippet...');

      const sidecarEndpoint = `${sidecarUrl.replace(/\/+$/, '')}/api/snippet`;
      const isCloudPreview = typeof window !== 'undefined' && window.location.hostname.includes('run.app');
      const isSidecarLocal = sidecarUrl.includes('localhost') || sidecarUrl.includes('127.0.0.1');
      // Always proxy through web backend unless running inside remote cloud preview attempting to reach client-side localhost
      const shouldProxySidecar = useProxy && !(isCloudPreview && isSidecarLocal);

      const fetchUrl = shouldProxySidecar ? '/api/proxy/abs' : sidecarEndpoint;
      const snippetPayload = {
        duration: snipDuration,
        server_url: serverUrl,
        serverUrl: serverUrl,
        library_item_id: session?.libraryItemId,
        libraryItemId: session?.libraryItemId,
        start_time: computedStart,
        startTime: computedStart,
      };

      const fetchOptions: RequestInit = {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(shouldProxySidecar ? {} : { 
            Authorization: `Bearer ${activeToken}`,
            'X-ABS-Server-Url': serverUrl,
          }),
        },
        body: JSON.stringify(
          shouldProxySidecar
            ? {
                targetUrl: sidecarEndpoint,
                method: 'POST',
                headers: {
                  Authorization: `Bearer ${activeToken}`,
                  'Content-Type': 'application/json',
                  'X-ABS-Server-Url': serverUrl,
                },
                body: snippetPayload,
              }
            : snippetPayload
        ),
      };

      const res = await fetch(fetchUrl, fetchOptions);

      if (shouldProxySidecar) {
        const proxyResp = await res.json();
        if (!proxyResp.ok) {
          const detail = proxyResp.data?.detail || proxyResp.message || 'Sidecar request failed';
          throw new Error(`Sidecar proxy error (HTTP ${proxyResp.status || 502}): ${detail}`);
        }
        const snip = proxyResp.data.snippet;
        const ts = snip.timestamp || new Date().toISOString().replace(/[-:T.]/g, '').slice(0, 14);
        
        const snippetResult: Snippet = {
          id: `${snip.book_title || session.bookTitle}-${ts}`,
          bookTitle: snip.book_title || session.bookTitle,
          author: formatAuthors(snip.author, session.author),
          chapterName: snip.chapter || session.chapterName,
          timestamp: ts,
          startTime: snip.start_time ?? computedStart,
          currentTime: snip.current_time ?? session.currentTime,
          libraryItemId: session.libraryItemId,
          duration: snip.duration ?? duration,
          audioUrl: getPlayableAudioUrl(snip.audio_url, sidecarUrl, useProxy),
          transcript: snip.transcript || '',
          markdownContent: snip.transcript || '',
          createdAt: Date.now(),
          username: user.username,
        };

        setLastSnippet(snippetResult);
        onSnippetCreated(snippetResult);
      } else {
        if (!res.ok) {
          const err = await res.json().catch(() => ({ detail: res.statusText }));
          throw new Error(`Sidecar error (HTTP ${res.status}): ${err.detail || 'Request failed'}`);
        }
        const data = await res.json();
        const snip = data.snippet;
        const ts = snip.timestamp || new Date().toISOString().replace(/[-:T.]/g, '').slice(0, 14);

        const snippetResult: Snippet = {
          id: `${snip.book_title}-${ts}`,
          bookTitle: snip.book_title,
          author: formatAuthors(snip.author, session.author),
          chapterName: snip.chapter,
          timestamp: ts,
          startTime: snip.start_time,
          currentTime: snip.current_time ?? session.currentTime,
          libraryItemId: session.libraryItemId,
          duration: snip.duration,
          audioUrl: getPlayableAudioUrl(snip.audio_url, sidecarUrl, useProxy),
          transcript: snip.transcript,
          markdownContent: snip.transcript,
          createdAt: Date.now(),
          username: user.username,
        };

        setLastSnippet(snippetResult);
        onSnippetCreated(snippetResult);
      }

      setCurrentStep('');
    } catch (err: unknown) {
      // Report actual real error - never substitute with fake "Project Hail Mary" data!
      const msg = err instanceof Error ? err.message : 'Failed to extract snippet from sidecar';
      
      let diagnosticHint = '';
      if ((sidecarUrl.includes('localhost') || sidecarUrl.includes('127.0.0.1')) && typeof window !== 'undefined' && window.location.hostname !== 'localhost' && window.location.hostname !== '127.0.0.1') {
        diagnosticHint = ` Note: Sidecar is set to localhost, but you are accessing from ${window.location.hostname}. Please set FastAPI Sidecar URL to http://${window.location.hostname}:13380.`;
      }

      setErrorMsg(`${msg}.${diagnosticHint}`);
      setCurrentStep('');
    } finally {
      setIsExtracting(false);
    }
  };

  // Save native bookmark directly on the Audiobookshelf server
  const handleSaveAbsBookmark = async () => {
    if (!session || !activeToken) return;
    setIsSavingAbsBookmark(true);
    setAbsBookmarkSuccess(null);
    setErrorMsg(null);

    const bookmarkTime = customOffset !== null ? customOffset : Math.max(0, session.currentTime - 30);
    const bookmarkTitle = `Bookmark: ${session.chapterName} (${duration}s snippet)`;

    try {
      const res = await createAbsBookmark(
        serverUrl,
        activeToken,
        session.libraryItemId,
        bookmarkTime,
        bookmarkTitle,
        useProxy
      );

      if (!res.ok) {
        throw new Error(`Failed to save ABS bookmark: ${typeof res.data === 'string' ? res.data : JSON.stringify(res.data)}`);
      }

      setAbsBookmarkSuccess(`Saved bookmark to Audiobookshelf at offset ${bookmarkTime}s!`);
      setTimeout(() => setAbsBookmarkSuccess(null), 5000);
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : 'Failed to save bookmark to Audiobookshelf');
    } finally {
      setIsSavingAbsBookmark(false);
    }
  };

  const startTime = session 
    ? (customOffset !== null ? Math.max(0, customOffset) : Math.max(0, session.currentTime - 30))
    : 0;

  return (
    <div className="max-w-4xl mx-auto space-y-6 font-mono">

      {/* 1. Connection Banner / Status */}
      {!user ? (
        <section className="border border-neutral-700 bg-[#0d0d0d] p-6 text-center space-y-4 shadow-md">
          <div className="w-12 h-12 mx-auto bg-[#181818] border border-neutral-700 flex items-center justify-center text-white">
            <Radio className="w-6 h-6 animate-pulse text-white" />
          </div>
          <div className="space-y-1 max-w-md mx-auto">
            <h2 className="text-base font-semibold text-white tracking-tight uppercase">
              No Active Audiobookshelf Session
            </h2>
          </div>

          <div className="flex flex-col sm:flex-row items-center justify-center gap-3 pt-2">
            <button
              id="connect-now-btn"
              onClick={onOpenAuthModal}
              className="w-full sm:w-auto px-5 py-2.5 bg-white text-black hover:bg-neutral-200 text-xs font-semibold flex items-center justify-center gap-2 transition-colors shadow-sm"
            >
              <UserIcon className="w-3.5 h-3.5" />
              <span>Connect to Audiobookshelf</span>
            </button>

            {/* Load Test Mock Session hidden per user request (code retained):
            {onUseMockSession && (
              <button
                type="button"
                onClick={onUseMockSession}
                className="w-full sm:w-auto px-4 py-2.5 bg-[#181818] border border-neutral-700 hover:border-neutral-500 text-xs text-neutral-300 hover:text-white transition-colors"
              >
                Load Test Mock Session
              </button>
            )}
            */}
          </div>
        </section>
      ) : (
        <section className="border border-neutral-700 bg-[#0c0c0c] p-4">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs">
            <div className="flex flex-wrap items-center gap-2 text-neutral-300">
              <span className="text-neutral-500">Connected to:</span>
              <span className="text-white font-semibold">{serverUrl}</span>
              <span className="text-neutral-500">as</span>
              <span className="px-2 py-0.5 bg-[#181818] border border-neutral-700 text-white font-semibold">
                @{user.username}
              </span>
            </div>

            <div className="flex items-center gap-2">
              <button
                onClick={onRefreshSession}
                disabled={isLoadingSession}
                className="px-2.5 py-1.5 bg-[#181818] border border-neutral-700 hover:border-neutral-500 text-xs text-neutral-200 hover:text-white flex items-center gap-1.5 transition-colors disabled:opacity-50"
                title="Re-query Audiobookshelf for latest playback position"
              >
                <RotateCw className={`w-3.5 h-3.5 ${isLoadingSession ? 'animate-spin' : ''}`} />
                <span>{isLoadingSession ? 'Syncing...' : 'Sync Playback Position'}</span>
              </button>

              <button
                onClick={onOpenAuthModal}
                className="px-2.5 py-1.5 bg-[#181818] border border-neutral-700 hover:border-neutral-500 text-xs text-neutral-300 hover:text-white transition-colors"
              >
                Change User / URLs
              </button>
            </div>
          </div>
        </section>
      )}

      {/* Session Loading State */}
      {isLoadingSession && (
        <div className="p-4 bg-[#0d0d0d] border border-neutral-700 text-xs text-neutral-300 flex items-center gap-2.5">
          <RotateCw className="w-4 h-4 animate-spin text-white" />
          <span>Fetching current active listening session from Audiobookshelf...</span>
        </div>
      )}

      {/* Session Error Alert */}
      {sessionError && !session && (
        <div className="p-4 bg-[#121212] border border-neutral-700 text-xs text-neutral-200 flex items-start gap-3">
          <AlertCircle className="w-4 h-4 shrink-0 text-white mt-0.5" />
          <div className="space-y-1">
            <div className="font-semibold text-white">Audiobookshelf Listening Session Notice</div>
            <div className="text-neutral-300">{sessionError}</div>
            <div className="pt-2 flex items-center gap-3">
              <button
                onClick={onRefreshSession}
                className="px-3 py-1.5 bg-[#1a1a1a] border border-neutral-700 hover:border-neutral-500 text-xs text-white flex items-center gap-1.5"
              >
                <RotateCw className="w-3 h-3" />
                <span>Try Syncing Again</span>
              </button>
              <a
                href={serverUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="text-neutral-400 hover:text-white underline text-[11px] flex items-center gap-1"
              >
                <span>Open Audiobookshelf</span>
                <ExternalLink className="w-3 h-3" />
              </a>
            </div>
          </div>
        </div>
      )}

      {/* 2. Active Session & Bookmark Parameters */}
      {session && (
        <section className="border border-neutral-700 bg-[#0d0d0d] p-5 space-y-4">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between pb-3 border-b border-neutral-800 gap-2">
            <div className="flex items-center gap-2">
              <Radio className="w-4 h-4 text-white" />
              <h2 className="text-sm font-semibold tracking-tight text-white uppercase">
                Active Audiobook Session
              </h2>
            </div>
            <div className="flex items-center gap-2 text-xs text-neutral-400">
              <span>Bookmarked Duration:</span>
              <span className="text-white font-mono font-semibold">
                {formatBookmarkedDuration(session.currentTime)}
              </span>
            </div>
          </div>

          {/* Book Information Grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
            <div className="p-3.5 bg-[#151515] border border-neutral-700 space-y-1">
              <span className="text-neutral-500 block text-[10px] uppercase font-semibold">Book Title : Sub-Title</span>
              <span className="text-white font-bold text-sm block">
                {session.bookTitle}{session.subtitle ? `: ${session.subtitle}` : ''}
              </span>
            </div>

            <div className="p-3.5 bg-[#151515] border border-neutral-700 space-y-1">
              <span className="text-neutral-500 block text-[10px] uppercase font-semibold">Author(s)</span>
              <span className="text-neutral-200 text-sm block">{formatAuthors(session.author)}</span>
            </div>

            <div className="p-3.5 bg-[#151515] border border-neutral-700 space-y-1">
              <span className="text-neutral-500 block text-[10px] uppercase font-semibold">Chapter</span>
              <span className="text-neutral-200 block">{session.chapterName}</span>
            </div>

            <div className="p-3.5 bg-[#151515] border border-neutral-700 space-y-1">
              <span className="text-neutral-500 block text-[10px] uppercase font-semibold">Playback Position</span>
              <span className="text-neutral-300 block font-mono text-[11px]">{formatBookmarkedDuration(session.currentTime)}</span>
            </div>
          </div>

          <div className="p-3 bg-[#141414] border border-neutral-700 text-xs text-neutral-400 break-all flex items-start justify-between gap-3">
            <div className="space-y-1 min-w-0">
              <span className="text-neutral-500 block font-semibold uppercase text-[10px] tracking-wider">
                Source Audio File
              </span>
              <div className="text-neutral-200 font-mono text-[11px] leading-relaxed select-all">
                {session.audioFilePath}
              </div>
            </div>
            <button
              onClick={onRefreshSession}
              title="Refresh playback time and real audio file path"
              className="text-neutral-400 hover:text-white p-1.5 bg-[#1a1a1a] border border-neutral-700 hover:border-neutral-500 transition-colors shrink-0 flex items-center gap-1 text-[11px]"
            >
              <RotateCw className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">Sync</span>
            </button>
          </div>

          {/* Snippet Slicing Parameters */}
          <div className="pt-3 border-t border-neutral-800 space-y-4">
            <div className="flex items-center gap-2 text-xs font-semibold text-white uppercase">
              <Sliders className="w-3.5 h-3.5" />
              <span>Snippet Parameters</span>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {/* Duration */}
              <div>
                <label className="block text-xs font-medium text-neutral-300 mb-1">
                  Snippet Duration (seconds)
                </label>
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    min="10"
                    max="600"
                    value={duration}
                    onChange={(e) => setDuration(Math.max(10, parseInt(e.target.value) || 60))}
                    className="w-20 bg-[#181818] border border-neutral-700 hover:border-neutral-500 focus:border-neutral-300 focus:bg-[#202020] px-3 py-2 text-xs text-white focus:outline-none transition-colors"
                  />
                  {[30, 60, 90, 120].map((d) => (
                    <button
                      key={d}
                      type="button"
                      onClick={() => setDuration(d)}
                      className={`px-2.5 py-2 text-xs border transition-colors ${
                        duration === d
                          ? 'border-neutral-300 text-white bg-neutral-800 font-semibold'
                          : 'border-neutral-700 bg-[#161616] text-neutral-300 hover:text-white hover:border-neutral-500'
                      }`}
                    >
                      {d}s
                    </button>
                  ))}
                </div>
              </div>

              {/* Custom Start Offset */}
              <div>
                <label className="block text-xs font-medium text-neutral-300 mb-1">
                  Custom Start Offset (default: currentTime - 30s)
                </label>
                <input
                  type="number"
                  min="0"
                  placeholder={`${Math.max(0, session.currentTime - 30)} (automatic offset)`}
                  value={customOffset !== null ? customOffset : ''}
                  onChange={(e) => setCustomOffset(e.target.value === '' ? null : Math.max(0, parseInt(e.target.value) || 0))}
                  className="w-full bg-[#181818] border border-neutral-700 hover:border-neutral-500 focus:border-neutral-300 focus:bg-[#202020] px-3 py-2 text-xs text-white focus:outline-none transition-colors"
                />
              </div>
            </div>

            {/* Calculated ffmpeg Cut */}
            <div className="p-3 bg-[#151515] border border-neutral-700 text-xs flex flex-wrap items-center justify-between gap-2">
              <span className="text-neutral-400">Calculated ffmpeg Cut:</span>
              <span className="text-white font-semibold">
                -ss {startTime}s &nbsp;|&nbsp; -t {duration}s &nbsp;|&nbsp; End: {startTime + duration}s
              </span>
            </div>

            {/* Step Indicator / Status Message */}
            {currentStep && (
              <div className="p-3 bg-[#151515] border border-neutral-700 text-xs text-neutral-200 flex items-center gap-2">
                <RotateCw className="w-3.5 h-3.5 animate-spin text-white" />
                <span>{currentStep}</span>
              </div>
            )}

            {/* Error Display */}
            {errorMsg && (
              <div className="p-3.5 bg-[#181818] border border-neutral-600 text-xs text-neutral-200 flex items-start gap-2.5">
                <AlertCircle className="w-4 h-4 shrink-0 text-white mt-0.5" />
                <div className="space-y-1.5 flex-1">
                  <div className="font-semibold text-white">Extraction / Sidecar Error</div>
                  <div className="text-neutral-300 leading-relaxed">{errorMsg}</div>
                </div>
              </div>
            )}

            {/* Success notification for native ABS bookmark */}
            {absBookmarkSuccess && (
              <div className="p-3 bg-[#151515] border border-neutral-700 text-xs text-white flex items-center gap-2">
                <CheckCircle2 className="w-4 h-4 text-white" />
                <span>{absBookmarkSuccess}</span>
              </div>
            )}

            {/* Trigger Buttons */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-2">
              <button
                id="btn-create-snippet"
                disabled={isExtracting}
                onClick={handleCreateSnippet}
                className="sm:col-span-2 py-3 bg-white text-black hover:bg-neutral-200 font-semibold text-xs tracking-wide uppercase transition-colors flex items-center justify-center gap-2 disabled:opacity-50 shadow-sm"
              >
                {isExtracting ? (
                  <>
                    <RotateCw className="w-4 h-4 animate-spin" />
                    <span>Processing Extraction & Speech Transcription...</span>
                  </>
                ) : (
                  <>
                    <Play className="w-4 h-4 fill-black" />
                    <span>Create Snippet & Transcribe</span>
                  </>
                )}
              </button>

              <button
                type="button"
                disabled={isSavingAbsBookmark}
                onClick={handleSaveAbsBookmark}
                className="py-3 bg-[#181818] border border-neutral-700 hover:border-neutral-500 hover:bg-[#202020] text-xs font-semibold text-white flex items-center justify-center gap-2 transition-colors disabled:opacity-50"
                title="Save this timestamp as a bookmark directly into your Audiobookshelf server"
              >
                {isSavingAbsBookmark ? (
                  <RotateCw className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <BookmarkPlus className="w-3.5 h-3.5" />
                )}
                <span>Save to ABS Server</span>
              </button>
            </div>
          </div>
        </section>
      )}

      {/* 3. Immediate Result Confirmation */}
      {lastSnippet && (
        <section className="border border-neutral-700 bg-[#0d0d0d] p-5 space-y-3">
          <div className="flex items-center justify-between pb-2 border-b border-neutral-800">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-white" />
              <h3 className="text-xs font-semibold text-white uppercase tracking-wider">
                Snippet Created Successfully
              </h3>
            </div>
            <button
              onClick={onNavigateToLibrary}
              className="text-xs text-white underline hover:text-neutral-300 flex items-center gap-1"
            >
              <span>View in Snippets Library</span>
              <Sparkles className="w-3 h-3" />
            </button>
          </div>

          <p className="text-xs text-neutral-300">
            &quot;{lastSnippet.bookTitle}&quot; ({lastSnippet.chapterName}) — {lastSnippet.duration}s clip
          </p>

          <div className="p-3 bg-[#151515] border border-neutral-700 text-xs text-neutral-200 leading-relaxed font-mono whitespace-pre-wrap">
            {lastSnippet.transcript}
          </div>

          {lastSnippet.audioUrl && (
            <audio
              controls
              src={lastSnippet.audioUrl}
              className="w-full h-8 mt-2 bg-[#181818] border border-neutral-700"
            />
          )}
        </section>
      )}

    </div>
  );
};
