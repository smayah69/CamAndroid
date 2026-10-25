package com.camandroid.core

data class StreamConfig(
    val width: Int = 1280,
    val height: Int = 720,
    val fps: Int = 30,
    val quality: Int = 80,
    val camera: String = CAMERA_BACK,
    val torch: Boolean = false,
    val codec: String = CODEC_JPEG,
) {
    /** H.264 bitrate for this resolution, frame rate and quality (about 6 Mbit/s for 720p60 at quality 80). */
    val bitrate: Int
        get() {
            val bitsPerPixel = 0.03 + (quality.coerceIn(30, 100) - 30) / 70.0 * 0.12
            return (width.toLong() * height * fps * bitsPerPixel).toInt().coerceIn(500_000, 40_000_000)
        }

    companion object {
        const val CAMERA_BACK = "back"
        const val CAMERA_FRONT = "front"
        const val CODEC_JPEG = "jpeg"
        const val CODEC_H264 = "h264"
    }
}

fun interface FrameSink {
    /**
     * [data] is one JPEG image, or one H.264 access unit in Annex-B format (key frames carry SPS/PPS).
     * [rotation] is the clockwise rotation in degrees the receiver must apply to display upright.
     */
    fun onFrame(data: ByteArray, rotation: Int, flags: Int, timestampUs: Long)
}

/** A camera (or a fake) that produces encoded frames. Implementations must be thread-safe. */
interface FrameSource {
    fun cameras(): List<String>

    /** Codecs this source can produce, most preferred first. JPEG is always supported. */
    fun codecs(): List<String> = listOf(StreamConfig.CODEC_JPEG)

    fun start(config: StreamConfig, sink: FrameSink)
    fun update(config: StreamConfig)
    fun stop()

    /** Asks the encoder for a key frame as soon as possible, after the network dropped frames. */
    fun requestKeyFrame() {}
}
