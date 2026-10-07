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
import { safeSidecarFetch } from '../lib/safeFetch';

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

function computeSessionStartTime(currentTime: number, customOffset: number | null): number {
  if (customOffset !== null && customOffset !== undefined) {
    return Math.max(0, customOffset);
  }
  return Math.max(0, currentTime - 30);
}

function generateFallbackTimestamp(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
}

function getSidecarDiagnosticHint(sidecarUrl: string): string {
  if (typeof window === 'undefined') return '';
  const isLocalSidecar = sidecarUrl.includes('localhost') || sidecarUrl.includes('127.0.0.1');
  const isExternalClient = window.location.hostname !== 'localhost' && window.location.hostname !== '127.0.0.1';
  if (isLocalSidecar && isExternalClient) {
    return ` Note: Sidecar is set to localhost, but you are accessing from ${window.location.hostname}. Please set FastAPI Sidecar URL to http://${window.location.hostname}:13380.`;
  }
  return '';
}

interface SnippetExtractOptions {
  session: AbsActiveSession;
  user: AbsUser;
  activeToken: string;
  serverUrl: string;
  sidecarUrl: string;
  useProxy: boolean;
  duration: number;
  computedStart: number;
}

function extractSidecarErrorMessage(data: unknown): string {
  if (typeof data === 'object' && data !== null) {
    const record = data as Record<string, unknown>;
    if (typeof record.detail === 'string') return record.detail;
    if (typeof record.message === 'string') return record.message;
  }
  return 'Sidecar request failed';
}

function buildSnippetFromPayload(
  snip: Record<string, unknown> | undefined,
  session: AbsActiveSession,
  user: AbsUser,
  options: { sidecarUrl: string; useProxy: boolean; duration: number; computedStart: number }
): Snippet {
  const ts = typeof snip?.timestamp === 'string' ? snip.timestamp : generateFallbackTimestamp();
  const bookTitle = typeof snip?.book_title === 'string' ? snip.book_title : session.bookTitle;
  const chapterName = typeof snip?.chapter === 'string' ? snip.chapter : session.chapterName;
  const transcript = typeof snip?.transcript === 'string' ? snip.transcript : '';

  return {
    id: `${bookTitle}-${ts}`,
    bookTitle,
    author: formatAuthors(snip?.author as string | undefined, session.author),
    chapterName,
    timestamp: ts,
    startTime: typeof snip?.start_time === 'number' ? snip.start_time : options.computedStart,
    currentTime: typeof snip?.current_time === 'number' ? snip.current_time : session.currentTime,
    libraryItemId: session.libraryItemId,
    duration: typeof snip?.duration === 'number' ? snip.duration : options.duration,
    audioUrl: getPlayableAudioUrl(snip?.audio_url as string | undefined, options.sidecarUrl, options.useProxy),
    transcript,
    markdownContent: transcript,
    createdAt: Date.now(),
    username: user.username,
  };
}

async function executeSnippetExtraction(options: SnippetExtractOptions): Promise<Snippet> {
  const { session, user, activeToken, serverUrl, sidecarUrl, useProxy, duration, computedStart } = options;

  const snippetPayload = {
    duration,
    server_url: serverUrl,
    serverUrl,
    library_item_id: session.libraryItemId,
    libraryItemId: session.libraryItemId,
    start_time: computedStart,
    startTime: computedStart,
  };

  const res = await safeSidecarFetch('/api/snippet', {
    method: 'POST',
    token: activeToken,
    serverUrl,
    sidecarUrl,
    useProxy,
    body: snippetPayload,
  });

  if (!res.ok) {
    const detail = extractSidecarErrorMessage(res.data);
    const statusCode = res.status ?? 502;
    throw new Error(`Sidecar proxy error (HTTP ${statusCode}): ${detail}`);
  }

  const payloadData = (res.data as Record<string, unknown> | undefined)?.data ?? res.data;
  const snip = (payloadData as Record<string, unknown> | undefined)?.snippet ?? payloadData;

  return buildSnippetFromPayload(
    snip as Record<string, unknown> | undefined,
    session,
    user,
    { sidecarUrl, useProxy, duration, computedStart }
  );
}

interface SessionInfoGridProps {
  session: AbsActiveSession;
}

const SessionInfoGrid: React.FC<SessionInfoGridProps> = ({ session }) => {
  const subtitleSuffix = session.subtitle ? `: ${session.subtitle}` : '';
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
      <div className="p-3.5 bg-[#151515] border border-neutral-700 space-y-1">
        <span className="text-neutral-500 block text-[10px] uppercase font-semibold">Book Title : Sub-Title</span>
        <span className="text-white font-bold text-sm block">
          {session.bookTitle}{subtitleSuffix}
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
  );
};

const DURATION_PRESETS = [30, 60, 90, 120] as const;

interface DurationSelectorProps {
  duration: number;
  onDurationChange: (d: number) => void;
}

const DurationSelector: React.FC<DurationSelectorProps> = ({ duration, onDurationChange }) => (
  <div>
    <label htmlFor="capture-duration-input" className="block text-xs font-medium text-neutral-300 mb-1">
      Snippet Duration (seconds)
    </label>
    <div className="flex items-center gap-2 mt-1">
      <input
        id="capture-duration-input"
        type="number"
        min="10"
        max="600"
        value={duration}
        onChange={(e) => onDurationChange(Math.max(10, Number.parseInt(e.target.value, 10) || 60))}
        className="w-20 bg-[#181818] border border-neutral-700 hover:border-neutral-500 focus:border-neutral-300 focus:bg-[#202020] px-3 py-2 text-xs text-white focus:outline-none transition-colors"
      />
      {DURATION_PRESETS.map((d) => (
        <button
          key={d}
          type="button"
          onClick={() => onDurationChange(d)}
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
);

interface CaptureActionButtonsProps {
  isExtracting: boolean;
  isSavingAbsBookmark: boolean;
  onExtract: () => void;
  onSaveAbsBookmark: () => void;
}

const CaptureActionButtons: React.FC<CaptureActionButtonsProps> = ({
  isExtracting,
  isSavingAbsBookmark,
  onExtract,
  onSaveAbsBookmark,
}) => (
  <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-2">
    <button
      id="btn-create-snippet"
      type="button"
      disabled={isExtracting}
      onClick={onExtract}
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
      onClick={onSaveAbsBookmark}
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
);

interface CaptureSnippetResultProps {
  snippet: Snippet;
  onNavigateToLibrary: () => void;
}

const CaptureSnippetResult: React.FC<CaptureSnippetResultProps> = ({ snippet, onNavigateToLibrary }) => (
  <section className="border border-neutral-700 bg-[#0d0d0d] p-5 space-y-3">
    <div className="flex items-center justify-between pb-2 border-b border-neutral-800">
      <div className="flex items-center gap-2">
        <CheckCircle2 className="w-4 h-4 text-white" />
        <h3 className="text-xs font-semibold text-white uppercase tracking-wider">
          Snippet Created Successfully
        </h3>
      </div>
      <button
        type="button"
        onClick={onNavigateToLibrary}
        className="text-xs text-white underline hover:text-neutral-300 flex items-center gap-1"
      >
        <span>View in Snippets Library</span>
        <Sparkles className="w-3 h-3" />
      </button>
    </div>

    <p className="text-xs text-neutral-300">
      &quot;{snippet.bookTitle}&quot; ({snippet.chapterName}) — {snippet.duration}s clip
    </p>

    <div className="p-3 bg-[#151515] border border-neutral-700 text-xs text-neutral-200 leading-relaxed font-mono whitespace-pre-wrap">
      {snippet.transcript}
    </div>

    {snippet.audioUrl && (
      <audio
        controls
        src={snippet.audioUrl}
        className="w-full h-8 mt-2 bg-[#181818] border border-neutral-700"
      >
        <track kind="captions" />
      </audio>
    )}
  </section>
);

const CaptureNotConnectedBanner: React.FC<{ onOpenAuthModal: () => void }> = ({ onOpenAuthModal }) => (
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
        type="button"
        onClick={onOpenAuthModal}
        className="w-full sm:w-auto px-5 py-2.5 bg-white text-black hover:bg-neutral-200 text-xs font-semibold flex items-center justify-center gap-2 transition-colors shadow-sm"
      >
        <UserIcon className="w-3.5 h-3.5" />
        <span>Connect to Audiobookshelf</span>
      </button>
    </div>
  </section>
);

interface CaptureConnectedBarProps {
  serverUrl: string;
  username: string;
  isLoadingSession: boolean;
  onRefreshSession: () => Promise<void>;
  onOpenAuthModal: () => void;
}

const CaptureConnectedBar: React.FC<CaptureConnectedBarProps> = ({
  serverUrl,
  username,
  isLoadingSession,
  onRefreshSession,
  onOpenAuthModal,
}) => {
  const syncSpinClass = isLoadingSession ? 'animate-spin' : '';
  const syncLabel = isLoadingSession ? 'Syncing...' : 'Sync Playback Position';
  return (
    <section className="border border-neutral-700 bg-[#0c0c0c] p-4">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs">
        <div className="flex flex-wrap items-center gap-2 text-neutral-300">
          <span className="text-neutral-500">Connected to:</span>
          <span className="text-white font-semibold">{serverUrl}</span>
          <span className="text-neutral-500">as</span>
          <span className="px-2 py-0.5 bg-[#181818] border border-neutral-700 text-white font-semibold">
            @{username}
          </span>
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onRefreshSession}
            disabled={isLoadingSession}
            className="px-2.5 py-1.5 bg-[#181818] border border-neutral-700 hover:border-neutral-500 text-xs text-neutral-200 hover:text-white flex items-center gap-1.5 transition-colors disabled:opacity-50"
            title="Re-query Audiobookshelf for latest playback position"
          >
            <RotateCw className={`w-3.5 h-3.5 ${syncSpinClass}`.trim()} />
            <span>{syncLabel}</span>
          </button>

          <button
            type="button"
            onClick={onOpenAuthModal}
            className="px-2.5 py-1.5 bg-[#181818] border border-neutral-700 hover:border-neutral-500 text-xs text-neutral-300 hover:text-white transition-colors"
          >
            Change User / URLs
          </button>
        </div>
      </div>
    </section>
  );
};

interface CaptureSessionNoticeProps {
  sessionError: string | null;
  serverUrl: string;
  onRefreshSession: () => Promise<void>;
}

const CaptureSessionNotice: React.FC<CaptureSessionNoticeProps> = ({
  sessionError,
  serverUrl,
  onRefreshSession,
}) => {
  if (!sessionError) return null;
  return (
    <div className="p-4 bg-[#121212] border border-neutral-700 text-xs text-neutral-200 flex items-start gap-3">
      <AlertCircle className="w-4 h-4 shrink-0 text-white mt-0.5" />
      <div className="space-y-1">
        <div className="font-semibold text-white">Audiobookshelf Listening Session Notice</div>
        <div className="text-neutral-300">{sessionError}</div>
        <div className="pt-2 flex items-center gap-3">
          <button
            type="button"
            onClick={onRefreshSession}
            className="px-3 py-1.5 bg-[#1a1a1a] border border-neutral-700 hover:border-neutral-500 text-xs text-white flex items-center gap-1.5"
          >
            <RotateCw className="w-3.5 h-3.5" />
            <span>Try Syncing Again</span>
          </button>
          <a
            href={serverUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="text-neutral-400 hover:text-white underline text-[11px] flex items-center gap-1"
          >
            <span>Open Audiobookshelf</span>
            <ExternalLink className="w-3.5 h-3.5" />
          </a>
        </div>
      </div>
    </div>
  );
};

interface ActiveSessionParametersPanelProps {
  session: AbsActiveSession;
  duration: number;
  setDuration: (d: number) => void;
  customOffset: number | null;
  setCustomOffset: (offset: number | null) => void;
  startTime: number;
  onRefreshSession: () => Promise<void>;
  currentStep: string;
  errorMsg: string | null;
  absBookmarkSuccess: string | null;
  isExtracting: boolean;
  isSavingAbsBookmark: boolean;
  onExtract: () => void;
  onSaveAbsBookmark: () => void;
}

const ActiveSessionParametersPanel: React.FC<ActiveSessionParametersPanelProps> = ({
  session,
  duration,
  setDuration,
  customOffset,
  setCustomOffset,
  startTime,
  onRefreshSession,
  currentStep,
  errorMsg,
  absBookmarkSuccess,
  isExtracting,
  isSavingAbsBookmark,
  onExtract,
  onSaveAbsBookmark,
}) => (
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
    <SessionInfoGrid session={session} />

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
        type="button"
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
        <DurationSelector duration={duration} onDurationChange={setDuration} />

        {/* Custom Start Offset */}
        <div>
          <label htmlFor="capture-custom-offset-input" className="block text-xs font-medium text-neutral-300 mb-1">
            Custom Start Offset (default: currentTime - 30s)
          </label>
          <input
            id="capture-custom-offset-input"
            type="number"
            min="0"
            placeholder={`${Math.max(0, session.currentTime - 30)} (automatic offset)`}
            value={customOffset ?? ''}
            onChange={(e) => {
              const val = e.target.value.trim();
              if (val === '') {
                setCustomOffset(null);
              } else {
                setCustomOffset(Math.max(0, Number.parseInt(val, 10) || 0));
              }
            }}
            className="w-full mt-1 bg-[#181818] border border-neutral-700 hover:border-neutral-500 focus:border-neutral-300 focus:bg-[#202020] px-3 py-2 text-xs text-white focus:outline-none transition-colors"
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

      {/* Step Indicator */}
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
      <CaptureActionButtons
        isExtracting={isExtracting}
        isSavingAbsBookmark={isSavingAbsBookmark}
        onExtract={onExtract}
        onSaveAbsBookmark={onSaveAbsBookmark}
      />
    </div>
  </section>
);

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

    const activeOffset = explicitOffset ?? customOffset;
    const computedStart = computeSessionStartTime(session.currentTime, activeOffset);
    const snipDuration = explicitDuration || duration;

    try {
      setCurrentStep('Connecting to sidecar POST /api/snippet...');

      const snippetResult = await executeSnippetExtraction({
        session,
        user,
        activeToken,
        serverUrl,
        sidecarUrl,
        useProxy,
        duration: snipDuration,
        computedStart,
      });

      setLastSnippet(snippetResult);
      onSnippetCreated(snippetResult);
      setCurrentStep('');
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to extract snippet from sidecar';
      const diagnosticHint = getSidecarDiagnosticHint(sidecarUrl);
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

    const bookmarkTime = computeSessionStartTime(session.currentTime, customOffset);
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

  const startTime = session ? computeSessionStartTime(session.currentTime, customOffset) : 0;

  return (
    <div className="max-w-4xl mx-auto space-y-6 font-mono">
      {!user ? (
        <CaptureNotConnectedBanner onOpenAuthModal={onOpenAuthModal} />
      ) : (
        <CaptureConnectedBar
          serverUrl={serverUrl}
          username={user.username}
          isLoadingSession={isLoadingSession}
          onRefreshSession={onRefreshSession}
          onOpenAuthModal={onOpenAuthModal}
        />
      )}

      {isLoadingSession && (
        <div className="p-4 bg-[#0d0d0d] border border-neutral-700 text-xs text-neutral-300 flex items-center gap-2.5">
          <RotateCw className="w-4 h-4 animate-spin text-white" />
          <span>Fetching current active listening session from Audiobookshelf...</span>
        </div>
      )}

      <CaptureSessionNotice
        sessionError={sessionError}
        serverUrl={serverUrl}
        onRefreshSession={onRefreshSession}
      />

      {session && (
        <ActiveSessionParametersPanel
          session={session}
          duration={duration}
          setDuration={setDuration}
          customOffset={customOffset}
          setCustomOffset={setCustomOffset}
          startTime={startTime}
          onRefreshSession={onRefreshSession}
          currentStep={currentStep}
          errorMsg={errorMsg}
          absBookmarkSuccess={absBookmarkSuccess}
          isExtracting={isExtracting}
          isSavingAbsBookmark={isSavingAbsBookmark}
          onExtract={handleCreateSnippet}
          onSaveAbsBookmark={handleSaveAbsBookmark}
        />
      )}

      {lastSnippet && (
        <CaptureSnippetResult
          snippet={lastSnippet}
          onNavigateToLibrary={onNavigateToLibrary}
        />
      )}
    </div>
  );
};
