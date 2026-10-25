package com.camandroid.app

import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaCodecList
import android.media.MediaFormat
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.HandlerThread
import android.util.Log
import android.view.Surface
import java.io.IOException
import java.nio.ByteBuffer

/**
 * Hardware H.264 encoder fed by the camera through an input [surface]. Emits Annex-B access
 * units; key frames are prefixed with SPS/PPS so a decoder can start from any of them.
 */
class H264Encoder(
    width: Int,
    height: Int,
    fps: Int,
    bitrate: Int,
    private val onFrame: (data: ByteArray, keyFrame: Boolean, timestampUs: Long) -> Unit,
    private val onFailure: (String) -> Unit,
) {
    private val thread = HandlerThread("h264-encoder").apply { start() }
    private val codec: MediaCodec
    val surface: Surface
    val name: String

    // Encoder-thread state.
    private var parameterSets: ByteArray? = null
    @Volatile private var released = false

    // Declared before init, which hands it to the codec.
    private val callback = object : MediaCodec.Callback() {
        override fun onInputBufferAvailable(codec: MediaCodec, index: Int) {} // surface input

        override fun onOutputBufferAvailable(codec: MediaCodec, index: Int, info: MediaCodec.BufferInfo) {
            if (released) return
            try {
                val buffer = codec.getOutputBuffer(index)
                if (buffer != null && info.size > 0) {
                    buffer.position(info.offset)
                    buffer.limit(info.offset + info.size)
                    val bytes = ByteArray(info.size).also { buffer.get(it) }
                    if (info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG != 0) {
                        parameterSets = bytes
                    } else {
                        val key = info.flags and MediaCodec.BUFFER_FLAG_KEY_FRAME != 0
                        val sets = parameterSets
                        onFrame(if (key && sets != null) sets + bytes else bytes, key, info.presentationTimeUs)
                    }
                }
                codec.releaseOutputBuffer(index, false)
            } catch (e: IllegalStateException) {
                // released while a buffer was in flight
            }
        }

        override fun onOutputFormatChanged(codec: MediaCodec, format: MediaFormat) {
            // Some encoders only report SPS/PPS here instead of in a codec-config buffer.
            val sps = format.getByteBuffer("csd-0")
            val pps = format.getByteBuffer("csd-1")
            if (parameterSets == null && sps != null && pps != null) parameterSets = sps.bytes() + pps.bytes()
        }

        override fun onError(codec: MediaCodec, e: MediaCodec.CodecException) {
            Log.e(TAG, "H.264 encoder error", e)
            if (!released) onFailure("Video encoder error: ${e.diagnosticInfo}")
        }
    }

    init {
        val info = findEncoder(width, height) ?: throw IOException("no H.264 encoder for ${width}x$height")
        name = info.name
        val caps = info.getCapabilitiesForType(MIME)
        val format = MediaFormat.createVideoFormat(MIME, width, height).apply {
            setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface)
            setInteger(MediaFormat.KEY_BIT_RATE, bitrate)
            setInteger(MediaFormat.KEY_FRAME_RATE, fps)
            setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, KEY_FRAME_INTERVAL_S)
            setInteger(MediaFormat.KEY_PRIORITY, 0) // real time
            val cbr = MediaCodecInfo.EncoderCapabilities.BITRATE_MODE_CBR
            if (caps.encoderCapabilities.isBitrateModeSupported(cbr)) setInteger(MediaFormat.KEY_BITRATE_MODE, cbr)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                setInteger(MediaFormat.KEY_MAX_B_FRAMES, 0) // B-frames add a frame of latency
                setFloat(MediaFormat.KEY_MAX_FPS_TO_ENCODER, fps.toFloat())
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) setInteger(MediaFormat.KEY_LATENCY, 1)
        }
        codec = MediaCodec.createByCodecName(info.name)
        surface = try {
            codec.setCallback(callback, Handler(thread.looper))
            codec.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
            codec.createInputSurface().also { codec.start() }
        } catch (e: Exception) {
            codec.release()
            thread.quitSafely()
            throw IOException("H.264 encoder ${info.name} rejected ${width}x$height@$fps: ${e.message}", e)
        }
    }

    fun requestKeyFrame() = setParameter(MediaCodec.PARAMETER_KEY_REQUEST_SYNC_FRAME, 0)

    fun setBitrate(bitrate: Int) = setParameter(MediaCodec.PARAMETER_KEY_VIDEO_BITRATE, bitrate)

    private fun setParameter(key: String, value: Int) {
        if (released) return
        try {
            codec.setParameters(Bundle().apply { putInt(key, value) })
        } catch (e: IllegalStateException) {
            // released concurrently
        }
    }

    /** Call once the camera no longer draws into [surface]. */
    fun release() {
        if (released) return
        released = true
        try {
            codec.stop()
        } catch (e: IllegalStateException) {
            // already stopped after an error
        }
        codec.release()
        surface.release()
        thread.quitSafely()
    }

    companion object {
        private const val TAG = "CamAndroid"
        const val MIME = MediaFormat.MIMETYPE_VIDEO_AVC
        private const val KEY_FRAME_INTERVAL_S = 2

        /** True when the phone can encode H.264; checked before offering it to the computer. */
        fun isAvailable(): Boolean = findEncoder(1280, 720) != null

        /** Prefers hardware encoders; software ones (OMX.google, c2.android) are a last resort. */
        private fun findEncoder(width: Int, height: Int): MediaCodecInfo? {
            val candidates = MediaCodecList(MediaCodecList.REGULAR_CODECS).codecInfos.filter { info ->
                info.isEncoder && info.supportedTypes.any { it.equals(MIME, ignoreCase = true) } &&
                    info.getCapabilitiesForType(MIME).videoCapabilities.isSizeSupported(width, height)
            }
            return candidates.firstOrNull { !isSoftware(it) } ?: candidates.firstOrNull()
        }

        private fun isSoftware(info: MediaCodecInfo): Boolean {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) return info.isSoftwareOnly
            val n = info.name.lowercase()
            return n.startsWith("omx.google.") || n.startsWith("c2.android.")
        }

        private fun ByteBuffer.bytes(): ByteArray = duplicate().let { b -> ByteArray(b.remaining()).also { b.get(it) } }
    }
}
