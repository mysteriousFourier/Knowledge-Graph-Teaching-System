import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { getTtsStatus, splitTtsSegments, synthesizeTts } from "@/api/education"
import type { SpeechCue, TtsSegmentItem, TtsSynthesizeResponse } from "@/types/education"

interface UseLecturePlaybackOptions {
  segmentCount: number
  initialSegment?: number
  getSegmentText?: (segment: number) => string
  getSegmentSpeechCues?: (segment: number) => SpeechCue[] | undefined
  chapterId?: string
  getSegmentId?: (segment: number) => string
}

type PlaybackProvider = "loading" | "none" | "genie" | "genie_server" | string
type PlaybackStage = "idle" | "splitting" | "synthesizing" | "playing" | "error"

interface ChunkInfo {
  current: number
  total: number
  prefetching: boolean
  lastCacheHit: boolean
  ready: number
  pending: number
  cacheHits: number
  stage: PlaybackStage
}

export interface PlaybackProgress {
  cacheHits: number
  current: number
  isActive: boolean
  pending: number
  percent: number
  provider: string
  providerLabel: string
  ready: number
  stage: PlaybackStage
  total: number
}

export interface AudioPlaybackPosition {
  currentTime: number
  duration: number
  isReady: boolean
  percent: number
  seekable: boolean
}

export const LONG_TEXT_THRESHOLD = 120
export const TTS_CHUNK_CHARS = 120
const PREFETCH_AHEAD = 2
const SILENT_WAV_DATA_URI =
  "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAESsAACJWAAACABAAZGF0YQAAAAA="
const initialChunkInfo: ChunkInfo = {
  current: 0,
  total: 0,
  prefetching: false,
  lastCacheHit: false,
  ready: 0,
  pending: 0,
  cacheHits: 0,
  stage: "idle",
}
const initialAudioPosition: AudioPlaybackPosition = {
  currentTime: 0,
  duration: 0,
  isReady: false,
  percent: 0,
  seekable: false,
}

interface SynthesizeOptions {
  force?: boolean
}

function getErrorMessage(error: unknown, fallback: string) {
  if (error && typeof error === "object" && "response" in error) {
    const response = (error as { response?: { status?: number; data?: { detail?: string; error?: string } } }).response
    const message = response?.data?.detail || response?.data?.error
    if (message) return message
    if (response?.status) return `${fallback}，HTTP ${response.status}`
  }
  if (error && typeof error === "object" && "request" in error) {
    const code = (error as { code?: string }).code
    return code ? `${fallback}，网络错误：${code}` : `${fallback}，无法连接后端`
  }
  return error instanceof Error ? error.message : fallback
}

function getProviderLabel(provider: PlaybackProvider) {
  if (provider === "gpt_sovits_local") return "GPT-SoVITS 本地推理"
  if (provider === "gpt_sovits_server") return "GPT-SoVITS 服务推理"
  if (provider === "genie") return "Genie-TTS 本地推理"
  if (provider === "genie_server") return "Genie-TTS 服务推理"
  if (provider === "azure_speech") return "Azure Speech 云端语音"
  if (provider === "loading") return "语音状态检测中"
  if (provider === "none") return "语音接口未接入"
  return provider
}

function shouldUseSingleTtsRequest(provider: PlaybackProvider) {
  return provider === "azure_speech" || provider === "genie_server" || provider === "gpt_sovits_server"
}

export function stableTextHash(text: string) {
  let hash = 2166136261
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  return (hash >>> 0).toString(16).padStart(8, "0")
}

export function stableSpeechCueHash(speechCues?: SpeechCue[]) {
  if (!speechCues?.length) return "no-cues"
  return stableTextHash(JSON.stringify(speechCues.map((cue) => ({
    type: cue.type,
    target_text: cue.target_text,
    style: cue.style,
    priority: cue.priority,
  }))))
}

export function useLecturePlayback({ segmentCount, initialSegment = 0, getSegmentText, getSegmentSpeechCues, chapterId, getSegmentId }: UseLecturePlaybackOptions) {
  const [isPlaying, setIsPlaying] = useState(false)
  const [isLoadingAudio, setIsLoadingAudio] = useState(false)
  const [currentSegment, setCurrentSegmentState] = useState(initialSegment)
  const [provider, setProvider] = useState<PlaybackProvider>("loading")
  const [providerDetail, setProviderDetail] = useState("")
  const [playbackError, setPlaybackError] = useState("")
  const [chunkInfo, setChunkInfo] = useState<ChunkInfo>(initialChunkInfo)
  const [audioPosition, setAudioPosition] = useState<AudioPlaybackPosition>(initialAudioPosition)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const requestIdRef = useRef(0)
  const synthesizedRef = useRef(new Map<string, Promise<TtsSynthesizeResponse>>())
  const currentSegmentRef = useRef(initialSegment)
  const forceNextPlayRef = useRef(false)
  const pausedRef = useRef(false)

  const hasSegments = segmentCount > 0
  const providerReady = provider !== "loading" && provider !== "none"
  const providerLabel = providerReady
    ? `${getProviderLabel(provider)} 可用`
    : provider === "loading"
      ? "语音状态检测中"
      : providerDetail || "语音接口未接入"

  useEffect(() => {
    let cancelled = false
    getTtsStatus()
      .then((status) => {
        if (cancelled) return
        if (status.enabled && status.available) {
          setProvider(status.provider)
          setProviderDetail(status.detail || "语音播放可用")
        } else {
          setProvider("none")
          setProviderDetail(status.detail || "语音接口未接入")
        }
      })
      .catch((error) => {
        if (cancelled) return
        setProvider("none")
        setProviderDetail(getErrorMessage(error, "语音状态接口请求失败"))
      })
    return () => {
      cancelled = true
    }
  }, [])

  const stopAudio = useCallback(() => {
    if (!audioRef.current) return
    audioRef.current.ontimeupdate = null
    audioRef.current.onloadedmetadata = null
    audioRef.current.ondurationchange = null
    audioRef.current.onseeking = null
    audioRef.current.onseeked = null
    audioRef.current.onended = null
    audioRef.current.onerror = null
    audioRef.current.pause()
    audioRef.current.src = ""
    audioRef.current = null
    setAudioPosition(initialAudioPosition)
  }, [])

  const primeAudioForUserGesture = useCallback(() => {
    const audio = new Audio(SILENT_WAV_DATA_URI)
    audio.preload = "auto"
    audioRef.current = audio
    void audio.play().then(() => {
      audio.pause()
      audio.currentTime = 0
    }).catch(() => undefined)
    return audio
  }, [])

  const pause = useCallback(() => {
    pausedRef.current = true
    setIsPlaying(false)
    setIsLoadingAudio(false)
    audioRef.current?.pause()
  }, [])

  const cancelPlayback = useCallback(() => {
    requestIdRef.current += 1
    pausedRef.current = false
    setIsPlaying(false)
    setIsLoadingAudio(false)
    setChunkInfo(initialChunkInfo)
    stopAudio()
  }, [stopAudio])

  const setCurrentSegment = useCallback((next: number | ((current: number) => number)) => {
    cancelPlayback()
    setCurrentSegmentState((current) => {
      const value = typeof next === "function" ? next(current) : next
      const clamped = Math.min(Math.max(value, 0), Math.max(segmentCount - 1, 0))
      currentSegmentRef.current = clamped
      return clamped
    })
  }, [cancelPlayback, segmentCount])

  const seekAudio = useCallback((percent: number) => {
    const audio = audioRef.current
    if (!audio || !Number.isFinite(audio.duration) || audio.duration <= 0) return
    const clampedPercent = Math.min(Math.max(percent, 0), 100)
    const nextTime = (clampedPercent / 100) * audio.duration
    audio.currentTime = nextTime
    setAudioPosition({
      currentTime: nextTime,
      duration: audio.duration,
      isReady: true,
      percent: clampedPercent,
      seekable: true,
    })
  }, [])

  const synthesizeCached = useCallback((text: string, segmentIndex?: number, chunkIndex?: number, speechCues?: SpeechCue[], options: SynthesizeOptions = {}) => {
    const key = text.trim()
    const segmentId = typeof segmentIndex === "number" ? getSegmentId?.(segmentIndex) || `segment-${segmentIndex + 1}` : undefined
    const cueHash = stableSpeechCueHash(speechCues)
    const scopedKey = chapterId ? `${chapterId}:${segmentId || "segment"}:${chunkIndex ?? 0}:${stableTextHash(key)}:${cueHash}` : `${key}:${cueHash}`
    const existing = options.force ? undefined : synthesizedRef.current.get(scopedKey)
    if (existing) return existing
    const promise = synthesizeTts({
      text: key,
      split_sentence: true,
      chapter_id: chapterId,
      segment_id: segmentId ? `${segmentId}-chunk-${(chunkIndex ?? 0) + 1}` : undefined,
      content_hash: `${stableTextHash(key)}-${cueHash}`,
      force: options.force,
      speech_cues: speechCues,
    }).catch((error) => {
      synthesizedRef.current.delete(scopedKey)
      throw error
    })
    synthesizedRef.current.set(scopedKey, promise)
    return promise
  }, [chapterId, getSegmentId])

  const play = useCallback(async (segmentOverride?: number) => {
    if (!hasSegments) return
    setPlaybackError("")

    if (pausedRef.current && !segmentOverride && audioRef.current && audioRef.current.src && audioRef.current.src !== SILENT_WAV_DATA_URI) {
      pausedRef.current = false
      setIsPlaying(true)
      try {
        await audioRef.current.play()
      } catch (error) {
        setIsPlaying(false)
        setPlaybackError(getErrorMessage(error, "闊抽鎾斁澶辫触"))
      }
      return
    }
    pausedRef.current = false

    if (provider === "loading") {
      setIsPlaying(false)
      setPlaybackError("语音状态检测中，请稍后再试")
      return
    }

    if (!providerReady) {
      setIsPlaying(false)
      setPlaybackError(providerDetail || "语音接口未接入")
      return
    }

    const resolvedSegment = Math.min(Math.max(segmentOverride ?? currentSegmentRef.current, 0), Math.max(segmentCount - 1, 0))
    currentSegmentRef.current = resolvedSegment
    setCurrentSegmentState(resolvedSegment)
    const sourceText = getSegmentText?.(resolvedSegment)?.trim()
    if (!sourceText) {
      setPlaybackError("当前片段没有可朗读文本")
      return
    }
    const speechCues = getSegmentSpeechCues?.(resolvedSegment)?.filter((cue) => cue.target_text?.trim()) || []

    const forceSynthesis = forceNextPlayRef.current
    forceNextPlayRef.current = false
    const requestId = requestIdRef.current + 1
    requestIdRef.current = requestId
    setIsPlaying(true)
    setIsLoadingAudio(true)
    setChunkInfo({ ...initialChunkInfo, stage: "splitting" })
    stopAudio()
    const gestureAudio = primeAudioForUserGesture()

    try {
      const shouldSplit = sourceText.length > LONG_TEXT_THRESHOLD && !shouldUseSingleTtsRequest(provider)
      const splitResult = shouldSplit
        ? await splitTtsSegments({ text: sourceText, max_chars: TTS_CHUNK_CHARS, speech_cues: speechCues })
        : {
            success: true,
            segments: [{ index: 0, text: sourceText, length: sourceText.length }],
            detail: undefined,
            error: undefined,
          }
      if (requestIdRef.current !== requestId) return
      if (!splitResult.success || !splitResult.segments.length) {
        throw new Error(splitResult.detail || splitResult.error || "语音分段失败")
      }

      const chunks = splitResult.segments as TtsSegmentItem[]
      let chunkIndex = 0
      const pendingIndexes = new Set<number>()
      const prefetchedIndexes = new Set<number>()
      const readyIndexes = new Set<number>()
      const cacheHitIndexes = new Set<number>()
      setChunkInfo({ ...initialChunkInfo, total: chunks.length, stage: "synthesizing" })

      const syncProgress = (patch: Partial<ChunkInfo> = {}) => {
        if (requestIdRef.current !== requestId) return
        setChunkInfo((current) => ({
          ...current,
          current: chunkIndex,
          total: chunks.length,
          prefetching: pendingIndexes.size > 0,
          ready: readyIndexes.size,
          pending: pendingIndexes.size,
          cacheHits: cacheHitIndexes.size,
          ...patch,
        }))
      }

      const markReady = (index: number, result: TtsSynthesizeResponse) => {
        readyIndexes.add(index)
        if (result.cache_hit) cacheHitIndexes.add(index)
      }

      const prefetchChunk = (index: number) => {
        const chunk = chunks[index]
        if (prefetchedIndexes.has(index)) return
        if (!chunk) return
        prefetchedIndexes.add(index)
        pendingIndexes.add(index)
        syncProgress({ stage: "playing" })
        void synthesizeCached(chunk.text, resolvedSegment, index, shouldSplit ? undefined : speechCues, { force: forceSynthesis })
          .then((result) => {
            if (requestIdRef.current !== requestId || !result.success || !result.audio_url) return
            markReady(index, result)
          })
          .catch(() => undefined)
          .finally(() => {
            pendingIndexes.delete(index)
            syncProgress({ stage: "playing" })
          })
      }

      const prefetchAhead = (fromIndex: number) => {
        for (let index = fromIndex; index < Math.min(chunks.length, fromIndex + PREFETCH_AHEAD); index += 1) {
          prefetchChunk(index)
        }
      }

      const playChunk = async () => {
        if (requestIdRef.current !== requestId) return
        const chunk = chunks[chunkIndex]
        if (!chunk) {
          setIsPlaying(false)
          setIsLoadingAudio(false)
          setChunkInfo((current) => ({ ...current, prefetching: false, pending: 0, stage: "idle" }))
          return
        }
        pendingIndexes.add(chunkIndex)
        const currentPromise = synthesizeCached(chunk.text, resolvedSegment, chunkIndex, shouldSplit ? undefined : speechCues, { force: forceSynthesis })

        setIsLoadingAudio(true)
        syncProgress({ stage: "synthesizing" })
        const result = await currentPromise.finally(() => {
          pendingIndexes.delete(chunkIndex)
        })
        if (requestIdRef.current !== requestId) return
        if (!result.success || !result.audio_url) {
          throw new Error(result.detail || result.error || "语音生成失败")
        }
        markReady(chunkIndex, result)
        syncProgress({ lastCacheHit: !!result.cache_hit, stage: "synthesizing" })
        const audio = audioRef.current || gestureAudio || new Audio()
        const updateAudioPosition = () => {
          if (requestIdRef.current !== requestId) return
          const duration = Number.isFinite(audio.duration) ? audio.duration : 0
          const currentTime = Number.isFinite(audio.currentTime) ? audio.currentTime : 0
          setAudioPosition({
            currentTime,
            duration,
            isReady: duration > 0,
            percent: duration > 0 ? Math.min(Math.max((currentTime / duration) * 100, 0), 100) : 0,
            seekable: duration > 0,
          })
        }
        audio.src = result.audio_url
        audio.preload = "auto"
        audioRef.current = audio
        setAudioPosition(initialAudioPosition)
        audio.ontimeupdate = updateAudioPosition
        audio.onloadedmetadata = updateAudioPosition
        audio.ondurationchange = updateAudioPosition
        audio.onseeking = updateAudioPosition
        audio.onseeked = updateAudioPosition
        audio.onended = () => {
          if (requestIdRef.current !== requestId) return
          updateAudioPosition()
          chunkIndex += 1
          void playChunk()
        }
        audio.onerror = () => {
          if (requestIdRef.current !== requestId) return
          setIsPlaying(false)
          setIsLoadingAudio(false)
          setPlaybackError("音频播放失败")
        }
        await audio.play()
        if (requestIdRef.current === requestId) {
          setIsLoadingAudio(false)
          syncProgress({ stage: "playing" })
          prefetchAhead(chunkIndex + 1)
        }
      }

      await playChunk()
    } catch (error) {
      if (requestIdRef.current !== requestId) return
      setIsPlaying(false)
      setIsLoadingAudio(false)
      setChunkInfo((current) => ({ ...current, prefetching: false, pending: 0, stage: "error" }))
      setPlaybackError(getErrorMessage(error, "语音生成失败"))
    }
  }, [getSegmentSpeechCues, getSegmentText, hasSegments, providerDetail, providerReady, segmentCount, stopAudio, synthesizeCached])

  const toggle = () => {
    if (isPlaying || isLoadingAudio) {
      pause()
    } else {
      void play()
    }
  }

  const reset = (segment = 0) => {
    cancelPlayback()
    const clamped = Math.min(Math.max(segment, 0), Math.max(segmentCount - 1, 0))
    currentSegmentRef.current = clamped
    setCurrentSegmentState(clamped)
  }

  const replay = (segment = currentSegment) => {
    cancelPlayback()
    const clamped = Math.min(Math.max(segment, 0), Math.max(segmentCount - 1, 0))
    currentSegmentRef.current = clamped
    setCurrentSegmentState(clamped)
    window.setTimeout(() => {
      void play(clamped)
    }, 0)
  }

  const regenerate = (segment = currentSegment) => {
    cancelPlayback()
    const clamped = Math.min(Math.max(segment, 0), Math.max(segmentCount - 1, 0))
    const sourceText = getSegmentText?.(clamped)?.trim()
    if (!sourceText) {
      setPlaybackError("当前片段没有可朗读文本")
      return
    }
    const speechCues = getSegmentSpeechCues?.(clamped)?.filter((cue) => cue.target_text?.trim()) || []
    const cueHash = stableSpeechCueHash(speechCues)
    const segmentId = getSegmentId?.(clamped) || `segment-${clamped + 1}`
    const chunkKeyPrefix = chapterId ? `${chapterId}:${segmentId}:` : `${sourceText}:`
    for (const key of Array.from(synthesizedRef.current.keys())) {
      if (key.startsWith(chunkKeyPrefix) || key.includes(`${stableTextHash(sourceText)}:${cueHash}`)) {
        synthesizedRef.current.delete(key)
      }
    }
    currentSegmentRef.current = clamped
    setCurrentSegmentState(clamped)
    forceNextPlayRef.current = true
    window.setTimeout(() => {
      void play(clamped)
    }, 0)
  }

  useEffect(() => {
    return () => cancelPlayback()
  }, [cancelPlayback])

  const statusText = useMemo(() => {
    if (!hasSegments) return "暂无可播放内容"
    if (playbackError) return playbackError
    if (provider === "loading") return "语音状态检测中"
    if (chunkInfo.stage === "splitting") return "正在切分讲稿，准备语音队列..."
    if (isLoadingAudio) {
      if (chunkInfo.total > 1) return `准备第 ${chunkInfo.current + 1}/${chunkInfo.total} 段语音，已就绪 ${chunkInfo.ready}/${chunkInfo.total}`
      return `语音生成中，使用 ${getProviderLabel(provider)}`
    }
    if (!providerReady) return isPlaying ? "播放状态已开启，语音接口未接入" : providerDetail || "语音接口未接入"
    if (isPlaying && chunkInfo.total > 1) {
      if (chunkInfo.prefetching) return `播放第 ${chunkInfo.current + 1}/${chunkInfo.total} 段，后台生成 ${chunkInfo.pending} 段，已就绪 ${chunkInfo.ready}/${chunkInfo.total}`
      if (chunkInfo.lastCacheHit) return `播放第 ${chunkInfo.current + 1}/${chunkInfo.total} 段，从缓存播放，已就绪 ${chunkInfo.ready}/${chunkInfo.total}`
      return `播放第 ${chunkInfo.current + 1}/${chunkInfo.total} 段，已就绪 ${chunkInfo.ready}/${chunkInfo.total}`
    }
    if (isPlaying && chunkInfo.lastCacheHit) return "从缓存播放"
    return isPlaying ? "播放中" : "已暂停"
  }, [chunkInfo, hasSegments, isLoadingAudio, isPlaying, playbackError, provider, providerDetail, providerReady])

  const progress = useMemo<PlaybackProgress>(() => {
    const total = chunkInfo.total
    return {
      cacheHits: chunkInfo.cacheHits,
      current: chunkInfo.current,
      isActive: isLoadingAudio || isPlaying || chunkInfo.pending > 0 || chunkInfo.stage === "splitting",
      pending: chunkInfo.pending,
      percent: total > 0 ? Math.round((chunkInfo.ready / total) * 100) : 0,
      provider,
      providerLabel,
      ready: chunkInfo.ready,
      stage: chunkInfo.stage,
      total,
    }
  }, [chunkInfo, isLoadingAudio, isPlaying, provider, providerLabel])

  return {
    currentSegment,
    hasSegments,
    isLoadingAudio,
    isPlaying,
    pause,
    play,
    progress,
    audioPosition,
    provider,
    providerLabel,
    replay,
    regenerate,
    reset,
    seekAudio,
    setCurrentSegment,
    statusText,
    toggle,
  }
}
